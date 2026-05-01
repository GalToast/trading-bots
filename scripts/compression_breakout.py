#!/usr/bin/env python3
"""Compression Breakout — trades the END of dead markets, not the dead market itself.

Technique:
1. Track rolling ATR and detect when it's in bottom X% of its recent range (compression)
2. Wait for the FIRST bar that expands beyond Yx the compression ATR (market waking up)
3. Enter in the direction of the expansion bar
4. Exit at Zx the compression ATR (big target relative to recent volatility)

This is fundamentally different from confirmed-displacement:
- Confirmed-displacement uses FIXED thresholds (2.0-2.5x ATR expansion)
- Compression breakout uses RELATIVE thresholds (expansion from extreme compression)
- It fires when the market transitions from dead → alive, which happens at session opens

Usage: python scripts/compression_breakout.py [--days 30] [--symbols SYM1 SYM2]
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent

# All testable FX symbols
ALL_FX_SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
    "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "NZDJPY", "CHFJPY", "CADJPY",
    "AUDCAD", "AUDCHF", "AUDNZD", "CADCHF", "GBPAUD", "GBPCAD", "GBPCHF",
    "GBPNZD", "NZDCAD", "NZDCHF",
]

SPREADS = {
    "EURUSD": 0.8, "GBPUSD": 1.0, "USDJPY": 0.6, "USDCHF": 1.0,
    "AUDUSD": 1.2, "NZDUSD": 1.5, "USDCAD": 1.2,
    "EURGBP": 1.0, "EURJPY": 1.2, "GBPJPY": 1.5, "AUDJPY": 1.5,
    "NZDJPY": 2.0, "CHFJPY": 1.5, "CADJPY": 1.5,
    "AUDCAD": 1.5, "AUDCHF": 1.5, "AUDNZD": 2.0, "CADCHF": 1.5,
    "GBPAUD": 2.0, "GBPCAD": 2.0, "GBPCHF": 1.5, "GBPNZD": 2.5,
    "NZDCAD": 2.0, "NZDCHF": 2.0,
}

PIP_SIZES = {}
for s in ALL_FX_SYMBOLS:
    PIP_SIZES[s] = 0.01 if "JPY" in s else 0.0001

UNITS_001_LOT = 1_000
MAX_HOLD = 30


def load_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1440 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])}
        for r in rates
    ]


def compute_atr(bars: list[dict], idx: int, period: int = 14) -> float:
    if idx < period:
        return 0.0
    trs = []
    for i in range(idx - period + 1, idx + 1):
        tr = bars[i]["high"] - bars[i]["low"]
        if i > 0:
            tr = max(tr, abs(bars[i]["high"] - bars[i - 1]["close"]))
            tr = max(tr, abs(bars[i]["low"] - bars[i - 1]["close"]))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


def bar_range_pips(bar: dict, pip: float) -> float:
    return (bar["high"] - bar["low"]) / pip


def pnl_usd_001(direction: str, entry: float, exit_price: float, spread_pips: float, pip: float) -> float:
    raw = (exit_price - entry) / pip if direction == "BUY" else (entry - exit_price) / pip
    price_move = (raw - spread_pips) * pip
    raw_usd = price_move * UNITS_001_LOT
    if pip >= 0.01:
        raw_usd /= max(exit_price, 0.0001)
    return raw_usd


def simulate_compression_breakout(
    symbol: str, bars: list[dict], spread_pips: float, pip: float,
    atr_period: int = 14,
    compression_window: int = 200,
    compression_percentile: float = 0.10,
    expansion_mult: float = 1.5,
    target_mult: float = 3.0,
    stop_mult: float = 2.0,
    max_hold: int = 20,
) -> list[dict]:
    """Simulate compression breakout strategy."""
    trades = []
    compressed = False
    compression_atr = 0.0
    compression_atr_pips = 0.0

    # Warmup: need enough bars for compression window + ATR period
    idx = compression_window + atr_period

    while idx < len(bars) - max_hold - 2:
        # Compute current ATR
        current_atr = compute_atr(bars, idx, atr_period)
        if current_atr <= 0:
            idx += 1
            continue
        current_atr_pips = current_atr / pip

        # Build ATR history for compression detection
        atr_history = []
        for i in range(idx - compression_window, idx):
            a = compute_atr(bars, i, atr_period)
            if a > 0:
                atr_history.append(a / pip)

        if len(atr_history) < compression_window * 0.8:
            idx += 1
            continue

        # Sort ATR history and find the compression threshold
        sorted_atr = sorted(atr_history)
        compression_threshold = sorted_atr[int(len(sorted_atr) * compression_percentile)]

        # Check if we're in compression
        if current_atr_pips <= compression_threshold and not compressed:
            compressed = True
            compression_atr_pips = current_atr_pips
            idx += 1
            continue

        # If compressed, look for expansion
        if compressed:
            cur = bars[idx]
            cur_range = bar_range_pips(cur, pip)

            # Expansion signal: current bar range > expansion_mult * compression ATR
            if cur_range >= expansion_mult * compression_atr_pips:
                # Determine direction from bar close vs open
                if cur["close"] > cur["open"]:
                    direction = "BUY"
                    entry_price = cur["close"]
                else:
                    direction = "SELL"
                    entry_price = cur["close"]

                entry_idx = idx + 1
                if entry_idx >= len(bars) - 1:
                    break

                # Set targets based on compression ATR
                target_pips = compression_atr_pips * target_mult
                stop_pips = compression_atr_pips * stop_mult

                # Ensure minimum targets (at least spread + 2 pips)
                min_target = spread_pips + 2.0
                if target_pips < min_target:
                    target_pips = min_target
                if stop_pips < 1.5:
                    stop_pips = 1.5

                # Simulate trade
                mfe_pips = 0.0
                mae_pips = 0.0
                exit_idx = None
                exit_price = None

                for j in range(entry_idx, min(len(bars) - 1, entry_idx + max_hold)):
                    bar = bars[j]
                    if direction == "BUY":
                        favorable = (bar["high"] - entry_price) / pip
                        adverse = (entry_price - bar["low"]) / pip
                    else:
                        favorable = (entry_price - bar["low"]) / pip
                        adverse = (bar["high"] - entry_price) / pip

                    mfe_pips = max(mfe_pips, favorable)
                    mae_pips = max(mae_pips, adverse)

                    # Check target
                    if mfe_pips >= target_pips:
                        exit_idx = j
                        if direction == "BUY":
                            exit_price = entry_price + target_pips * pip
                        else:
                            exit_price = entry_price - target_pips * pip
                        break

                    # Check stop
                    if adverse >= stop_pips:
                        exit_idx = j
                        if direction == "BUY":
                            exit_price = entry_price - stop_pips * pip
                        else:
                            exit_price = entry_price + stop_pips * pip
                        break

                if exit_idx is None:
                    exit_idx = min(len(bars) - 1, entry_idx + max_hold - 1)
                    exit_price = bars[exit_idx]["close"]

                pnl_pips = ((exit_price - entry_price) / pip if direction == "BUY"
                            else (entry_price - exit_price) / pip) - spread_pips
                pnl_usd = pnl_usd_001(direction, entry_price, exit_price, spread_pips, pip)

                trades.append({
                    "symbol": symbol, "direction": direction,
                    "entry_idx": entry_idx, "exit_idx": exit_idx,
                    "hold_bars": exit_idx - entry_idx + 1,
                    "pnl_pips": pnl_pips, "pnl_usd": pnl_usd,
                    "mfe_pips": mfe_pips, "mae_pips": mae_pips,
                    "compression_atr_pips": round(compression_atr_pips, 2),
                    "target_pips": round(target_pips, 1),
                    "stop_pips": round(stop_pips, 1),
                })

                compressed = False

            # If bar doesn't expand but we're still compressed, continue
            # If bar expands but not enough, keep watching
            idx += 1
            continue

        idx += 1

    return trades


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--symbols", nargs="+", default=None)
    args = parser.parse_args()

    mt5.initialize()

    symbols = args.symbols if args.symbols else ALL_FX_SYMBOLS

    print(f"Compression Breakout Backtest — {args.days} days, {len(symbols)} symbols")
    print()

    all_results = []

    for symbol in symbols:
        pip = PIP_SIZES.get(symbol, 0.0001)
        spread = SPREADS.get(symbol, 1.0)

        bars = load_bars(symbol, args.days)
        if not bars or len(bars) < 300:
            print(f"  {symbol}: INSUFFICIENT DATA")
            continue

        print(f"  Testing {symbol} ({len(bars)} bars, spread={spread}p)...", end=" ", flush=True)

        # Grid search over key parameters
        best_result = None
        best_score = -999

        # Test different compression percentiles, expansion multipliers, and target multipliers
        for comp_pct in [0.05, 0.10, 0.15, 0.20]:
            for exp_mult in [1.3, 1.5, 2.0, 2.5]:
                for tgt_mult in [2.0, 3.0, 4.0, 5.0]:
                    for stop_mult in [1.5, 2.0, 2.5]:
                        trades = simulate_compression_breakout(
                            symbol, bars, spread, pip,
                            compression_percentile=comp_pct,
                            expansion_mult=exp_mult,
                            target_mult=tgt_mult,
                            stop_mult=stop_mult,
                        )
                        if len(trades) < 5:
                            continue

                        wins = [t for t in trades if t["pnl_usd"] > 0]
                        net_usd = sum(t["pnl_usd"] for t in trades)
                        wr = len(wins) / len(trades) * 100
                        exp_usd = net_usd / len(trades)

                        # Score: prioritize expectancy with minimum trade count
                        score = exp_usd * min(len(trades) / 20, 1.0)

                        if score > best_score:
                            best_score = score
                            best_result = {
                                "symbol": symbol,
                                "comp_pct": comp_pct,
                                "exp_mult": exp_mult,
                                "tgt_mult": tgt_mult,
                                "stop_mult": stop_mult,
                                "trades": len(trades),
                                "trades_per_day": round(len(trades) / args.days, 1),
                                "wr_pct": round(wr, 1),
                                "net_usd": round(net_usd, 2),
                                "exp_usd": round(exp_usd, 3),
                                "avg_hold_bars": round(mean(t["hold_bars"] for t in trades), 1),
                                "avg_compression_atr": round(mean(t["compression_atr_pips"] for t in trades), 2),
                            }

        if best_result:
            all_results.append(best_result)
            print(f"✅ exp=${best_result['exp_usd']:+.3f} n={best_result['trades']} wr={best_result['wr_pct']:.0f}%")
        else:
            print("❌ no viable configs")

    # Sort by exp_usd descending
    all_results.sort(key=lambda r: r["exp_usd"], reverse=True)

    # Output
    if all_results:
        fieldnames = list(all_results[0].keys())
        print("\n" + ",".join(fieldnames))
        for r in all_results:
            print(",".join(str(r[k]) for k in fieldnames))

        output_path = ROOT / "reports" / "compression_breakout.csv"
        output_path.parent.mkdir(exist_ok=True)
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_results)
        print(f"\nSaved to {output_path}")

        # Print summary
        print("\n=== TOP 15 COMPRESSION BREAKOUT CONFIGS ===")
        print(f"{'Symbol':>8} | {'Params':>18} | {'Trd/Day':>7} | {'WR':>5} | {'Exp/Trade':>9} | {'Net USD':>9} | {'Avg Cmpr':>8}")
        print("-" * 90)
        for r in all_results[:15]:
            params = f"c{r['comp_pct']}e{r['exp_mult']}t{r['tgt_mult']}"
            print(f"{r['symbol']:>8} | {params:>18} | {r['trades_per_day']:>7.1f} | {r['wr_pct']:>4.1f}% | ${r['exp_usd']:>8.3f} | ${r['net_usd']:>8.2f} | {r['avg_compression_atr']:>6.1f}p")
    else:
        print("No viable configurations found.")

    mt5.shutdown()


if __name__ == "__main__":
    main()
