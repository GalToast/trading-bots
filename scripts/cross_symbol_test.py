#!/usr/bin/env python3
"""Cross-symbol confirmed-displacement test.

Tests the confirmed-displacement recipe (confirm 1.5 pips, 2.5x ATR, retain 60, real spread)
across multiple symbols at 30 days. Uses the asymmetry lab's entry logic.

Usage: python scripts/cross_symbol_test.py [--days 30]
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from statistics import mean

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ["GBPUSD", "EURUSD", "AUDUSD", "USDCHF", "USDCAD", "USDJPY"]
PIP_SIZES = {"GBPUSD": 0.0001, "EURUSD": 0.0001, "AUDUSD": 0.0001, "USDCHF": 0.0001, "USDCAD": 0.0001, "USDJPY": 0.01}
SPREADS = {"GBPUSD": 1.0, "EURUSD": 0.8, "AUDUSD": 1.2, "USDCHF": 1.0, "USDCAD": 1.2, "USDJPY": 0.6}
UNITS_001_LOT = 1_000


def load_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1440 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])}
        for r in rates
    ]


def body_pips(bar: dict, pip: float) -> float:
    return abs(bar["close"] - bar["open"]) / pip


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


def pnl_usd_001(direction: str, entry: float, exit_price: float, spread_pips: float, pip: float) -> float:
    raw = (exit_price - entry) / pip if direction == "BUY" else (entry - exit_price) / pip
    price_move = (raw - spread_pips) * pip
    raw_usd = price_move * UNITS_001_LOT
    # For JPY pairs, convert from JPY to USD
    if pip >= 0.01:  # JPY pair
        raw_usd /= max(exit_price, 0.0001)
    return raw_usd


def simulate(symbol: str, bars: list[dict], spread_pips: float, confirm_pips: float, expansion_x: float, retain: float, floor_pips: float, pip: float, max_hold: int = 30) -> list[dict]:
    trades = []
    lookback = 20
    idx = lookback + 14
    while idx < len(bars) - max_hold - 2:
        recent = bars[idx - lookback: idx]
        struct_high = max(b["high"] for b in recent)
        struct_low = min(b["low"] for b in recent)
        cur = bars[idx]
        atr = compute_atr(bars, idx)
        atr_pips = atr / pip if atr > 0 else 10.0

        direction = None
        structure_level = None

        break_above = cur["close"] > struct_high + confirm_pips * pip
        break_below = cur["close"] < struct_low - confirm_pips * pip
        cur_body = body_pips(cur, pip)
        meets_expansion = cur_body >= expansion_x * atr_pips

        if not meets_expansion:
            idx += 1
            continue

        if break_above:
            direction = "BUY"
            structure_level = struct_high
        elif break_below:
            direction = "SELL"
            structure_level = struct_low

        if direction is None:
            idx += 1
            continue

        # 1 bar confirmation
        confirmed = True
        check_idx = idx + 1
        if check_idx < len(bars):
            check_bar = bars[check_idx]
            if direction == "BUY" and check_bar["close"] < structure_level:
                confirmed = False
            if direction == "SELL" and check_bar["close"] > structure_level:
                confirmed = False
        if not confirmed:
            idx += 1
            continue

        entry_idx = idx + 1
        if entry_idx >= len(bars) - 1:
            break
        entry_price = bars[entry_idx]["open"]

        mfe_pips = 0.0
        exit_idx = None
        exit_price = None

        for j in range(entry_idx, min(len(bars) - 1, entry_idx + max_hold)):
            bar = bars[j]
            if direction == "BUY":
                favorable = (bar["high"] - entry_price) / pip
                close_pips = (bar["close"] - entry_price) / pip
            else:
                favorable = (entry_price - bar["low"]) / pip
                close_pips = (entry_price - bar["close"]) / pip

            mfe_pips = max(mfe_pips, favorable)

            if retain is not None and mfe_pips >= 3.0:
                floor = max(floor_pips, mfe_pips * retain)
                if close_pips <= floor:
                    exit_idx = j
                    exit_price = bar["close"]
                    break

        if exit_idx is None:
            exit_idx = min(len(bars) - 1, entry_idx + max_hold - 1)
            exit_price = bars[exit_idx]["close"]

        pnl_pips = ((exit_price - entry_price) / pip if direction == "BUY" else (entry_price - exit_price) / pip) - spread_pips
        pnl_usd = pnl_usd_001(direction, entry_price, exit_price, spread_pips, pip)

        trades.append({
            "symbol": symbol, "direction": direction, "entry_price": entry_price,
            "exit_price": exit_price, "hold_bars": exit_idx - entry_idx + 1,
            "pnl_pips": pnl_pips, "pnl_usd": pnl_usd,
            "mfe_pips": max(0.0, mfe_pips),
        })
        idx = exit_idx + 1
    else:
        idx += 1

    return trades


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--symbols", nargs="+", default=None)
    args = parser.parse_args()

    mt5.initialize()

    if args.symbols:
        test_symbols = args.symbols
    else:
        test_symbols = SYMBOLS

    print(f"Cross-symbol confirmed-displacement test — {args.days} days")
    print(f"Config: confirm 1.5 pips, 2.5x ATR expansion, retain 60%, floor 0.5 pips")
    print()

    results = []

    for symbol in test_symbols:
        pip = PIP_SIZES.get(symbol, 0.0001)
        spread = SPREADS.get(symbol, 1.0)
        spread_pips = spread
        floor_pips = 0.5

        bars = load_bars(symbol, args.days)
        if not bars:
            print(f"  {symbol}: NO DATA")
            continue

        trades = simulate(symbol, bars, spread_pips, confirm_pips=1.5, expansion_x=2.5,
                          retain=0.60, floor_pips=floor_pips, pip=pip)

        if not trades:
            print(f"  {symbol}: 0 trades (spread={spread} pips)")
            results.append({
                "symbol": symbol, "spread_pips": spread, "trades": 0,
                "wr_pct": 0, "net_usd": 0, "exp_usd": 0, "avg_mfe_pips": 0,
            })
            continue

        wins = [t for t in trades if t["pnl_pips"] > 0]
        net_usd = sum(t["pnl_usd"] for t in trades)
        wr = len(wins) / len(trades) * 100
        exp_usd = net_usd / len(trades)
        avg_mfe = mean(t["mfe_pips"] for t in trades)

        print(f"  {symbol}: {len(trades)} trades, {wr:.0f}% WR, net ${net_usd:.2f}, exp ${exp_usd:.3f}, avg MFE {avg_mfe:.1f} pips")

        results.append({
            "symbol": symbol, "spread_pips": spread, "trades": len(trades),
            "wr_pct": round(wr, 1), "net_usd": round(net_usd, 2),
            "exp_usd": round(exp_usd, 3), "avg_mfe_pips": round(avg_mfe, 1),
        })

    print()

    # Summary table
    print(f"{'Symbol':>8} | {'Spread':>7} | {'Trades':>6} | {'WR':>6} | {'Net USD':>9} | {'Exp/Trade':>9} | {'Avg MFE':>8}")
    print("-" * 75)
    for r in sorted(results, key=lambda x: x["exp_usd"], reverse=True):
        print(f"{r['symbol']:>8} | {r['spread_pips']:>5.1f}p | {r['trades']:>6} | {r['wr_pct']:>5.1f}% | ${r['net_usd']:>8.2f} | ${r['exp_usd']:>8.3f} | {r['avg_mfe_pips']:>6.1f}p")

    # Save
    output_path = ROOT / "reports" / "cross_symbol_test.csv"
    output_path.parent.mkdir(exist_ok=True)
    if results:
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            writer.writeheader()
            writer.writerows(results)

    print(f"\nSaved to {output_path}")
    mt5.shutdown()


if __name__ == "__main__":
    main()
