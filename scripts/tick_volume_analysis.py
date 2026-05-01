#!/usr/bin/env python3
"""Tick Volume Analysis — what's special about M1 data we've been ignoring?

MT5 M1 bars include tick_volume (number of price changes in that minute).
During dead markets, tick_volume drops dramatically. When it starts rising,
that's the market waking up BEFORE price moves.

This script analyzes:
1. Tick volume patterns during compression/expansion
2. Volume divergence signals (volume rising but price still flat)
3. Volume-based entry timing (enter when volume confirms price move)
4. Cross-symbol volume leadership (which symbol's volume rises first?)

Usage: python scripts/tick_volume_analysis.py [--days 30]
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
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


def sma(values: list[float], period: int) -> float:
    if len(values) < period:
        return 0.0
    return sum(values[-period:]) / period


def analyze_volume_patterns(bars: list[dict], pip: float) -> dict:
    """Analyze tick volume patterns in the M1 data."""
    if len(bars) < 300:
        return {}

    # Extract volume and range
    volumes = [b["tick_volume"] for b in bars]
    ranges = [(b["high"] - b["low"]) / pip for b in bars]

    # Volume statistics
    vol_mean = mean(volumes)
    vol_std = stdev(volumes) if len(volumes) > 1 else 0
    vol_min = min(volumes)
    vol_max = max(volumes)

    # Range statistics
    range_mean = mean(ranges)
    range_std = stdev(ranges) if len(ranges) > 1 else 0

    # Correlation between volume and range
    if len(volumes) > 1 and vol_std > 0 and range_std > 0:
        cov = sum((v - vol_mean) * (r - range_mean) for v, r in zip(volumes, ranges)) / (len(volumes) - 1)
        corr = cov / (vol_std * range_std)
    else:
        corr = 0

    # Volume during lowest range periods vs highest range periods
    sorted_by_range = sorted(zip(ranges, volumes), key=lambda x: x[0])
    bottom_10_pct = sorted_by_range[:int(len(sorted_by_range) * 0.10)]
    top_10_pct = sorted_by_range[int(len(sorted_by_range) * 0.90):]

    vol_during_low_range = mean(v for _, v in bottom_10_pct) if bottom_10_pct else 0
    vol_during_high_range = mean(v for _, v in top_10_pct) if top_10_pct else 0

    # Volume changes BEFORE range expansion
    # Look for cases where volume increases in the 3 bars before a big range bar
    volume_pre_expansion = []
    volume_pre_contraction = []

    for i in range(10, len(bars) - 1):
        current_range = ranges[i]
        prev_3_vol = volumes[i-3:i]
        prev_3_range = ranges[i-3:i]

        # If current bar is top 5% range, check if volume was rising before
        if current_range > range_mean + 2 * range_std:
            vol_before = mean(prev_3_vol)
            vol_after = volumes[i]
            volume_pre_expansion.append((vol_before, vol_after, current_range))

        # If current bar is bottom 5% range
        if current_range < range_mean - 1.5 * range_std:
            vol_before = mean(prev_3_vol)
            vol_after = volumes[i]
            volume_pre_contraction.append((vol_before, vol_after, current_range))

    return {
        "vol_mean": round(vol_mean, 1),
        "vol_std": round(vol_std, 1),
        "vol_min": vol_min,
        "vol_max": vol_max,
        "range_mean": round(range_mean, 1),
        "vol_corr_with_range": round(corr, 3),
        "vol_during_low_range": round(vol_during_low_range, 1),
        "vol_during_high_range": round(vol_during_high_range, 1),
        "vol_ratio_high_low": round(vol_during_high_range / vol_during_low_range, 1) if vol_during_low_range > 0 else 0,
        "n_expansion_samples": len(volume_pre_expansion),
        "avg_vol_before_expansion": round(mean(vb for vb, _, _ in volume_pre_expansion), 1) if volume_pre_expansion else 0,
        "avg_vol_during_expansion": round(mean(va for _, va, _ in volume_pre_expansion), 1) if volume_pre_expansion else 0,
    }


def simulate_volume_based_strategy(
    symbol: str, bars: list[dict], spread_pips: float, pip: float,
    vol_lookback: int = 20,
    vol_expansion_threshold: float = 1.5,
    min_range_pips: float = 2.0,
    target_pips: float = 5.0,
    stop_pips: float = 3.0,
    max_hold: int = 15,
) -> list[dict]:
    """Trade based on volume expansion in M1 bars.

    Entry: Volume is Xx the recent average AND bar range > minimum
    Exit: Target or stop or max hold
    """
    trades = []
    volumes = [b["tick_volume"] for b in bars]

    idx = vol_lookback + 20  # Warmup

    while idx < len(bars) - max_hold - 2:
        # Volume analysis
        recent_vol = volumes[idx - vol_lookback: idx]
        vol_avg = mean(recent_vol)
        current_vol = volumes[idx]

        if vol_avg <= 0:
            idx += 1
            continue

        vol_ratio = current_vol / vol_avg

        # Check for volume expansion + minimum range
        cur_bar = bars[idx]
        cur_range = (cur_bar["high"] - cur_bar["low"]) / pip

        if vol_ratio >= vol_expansion_threshold and cur_range >= min_range_pips:
            # Enter in direction of the bar
            if cur_bar["close"] > cur_bar["open"]:
                direction = "BUY"
                entry_price = cur_bar["close"]
            else:
                direction = "SELL"
                entry_price = cur_bar["close"]

            entry_idx = idx + 1
            if entry_idx >= len(bars) - 1:
                break

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

                # Target
                if mfe_pips >= target_pips:
                    exit_idx = j
                    exit_price = entry_price + target_pips * pip if direction == "BUY" else entry_price - target_pips * pip
                    break

                # Stop
                if adverse >= stop_pips:
                    exit_idx = j
                    exit_price = entry_price - stop_pips * pip if direction == "BUY" else entry_price + stop_pips * pip
                    break

            if exit_idx is None:
                exit_idx = min(len(bars) - 1, entry_idx + max_hold - 1)
                exit_price = bars[exit_idx]["close"]

            pnl_pips = ((exit_price - entry_price) / pip if direction == "BUY"
                        else (entry_price - exit_price) / pip) - spread_pips
            pnl_usd = pnl_pips * pip * UNITS_001_LOT / (max(exit_price, 0.0001) if pip >= 0.01 else 1.0)

            trades.append({
                "symbol": symbol, "direction": direction,
                "entry_idx": entry_idx, "exit_idx": exit_idx,
                "hold_bars": exit_idx - entry_idx + 1,
                "pnl_pips": pnl_pips, "pnl_usd": pnl_usd,
                "mfe_pips": mfe_pips, "mae_pips": mae_pips,
                "vol_ratio": round(vol_ratio, 1),
                "entry_range_pips": round(cur_range, 1),
            })

            # Skip ahead to avoid re-entering on same volume spike
            idx += 3
        else:
            idx += 1

    return trades


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--symbols", nargs="+", default=None)
    args = parser.parse_args()

    mt5.initialize()

    symbols = args.symbols if args.symbols else ALL_FX_SYMBOLS

    print(f"Tick Volume Analysis — {args.days} days, {len(symbols)} symbols")
    print()

    # Part 1: Volume pattern analysis
    print("=== VOLUME PATTERNS ===")
    for symbol in symbols:
        pip = PIP_SIZES.get(symbol, 0.0001)
        bars = load_bars(symbol, args.days)
        if not bars or len(bars) < 300:
            continue

        patterns = analyze_volume_patterns(bars, pip)
        if patterns:
            print(f"  {symbol:>8}: vol_mean={patterns['vol_mean']}, "
                  f"corr={patterns['vol_corr_with_range']:+.3f}, "
                  f"vol_ratio_high/low={patterns['vol_ratio_high_low']:.1f}x, "
                  f"vol_before_expansion={patterns['avg_vol_before_expansion']}, "
                  f"vol_during_expansion={patterns['avg_vol_during_expansion']}")

    print()

    # Part 2: Volume-based strategy grid search
    print("=== VOLUME STRATEGY GRID SEARCH ===")
    all_results = []

    for symbol in symbols:
        pip = PIP_SIZES.get(symbol, 0.0001)
        spread = SPREADS.get(symbol, 1.0)
        bars = load_bars(symbol, args.days)
        if not bars or len(bars) < 300:
            continue

        print(f"  Testing {symbol}...", end=" ", flush=True)

        best_result = None
        best_score = -999

        # Grid search over volume parameters
        for vol_lb in [10, 20, 30, 50]:
            for vol_exp in [1.3, 1.5, 1.8, 2.0, 2.5]:
                for min_range in [1.0, 2.0, 3.0, 4.0]:
                    for target in [3.0, 5.0, 7.0]:
                        for stop in [2.0, 3.0, 4.0]:
                            trades = simulate_volume_based_strategy(
                                symbol, bars, spread, pip,
                                vol_lookback=vol_lb,
                                vol_expansion_threshold=vol_exp,
                                min_range_pips=min_range,
                                target_pips=target,
                                stop_pips=stop,
                            )
                            if len(trades) < 5:
                                continue

                            wins = [t for t in trades if t["pnl_usd"] > 0]
                            net_usd = sum(t["pnl_usd"] for t in trades)
                            wr = len(wins) / len(trades) * 100
                            exp_usd = net_usd / len(trades)

                            # Score: expectancy weighted by trade count
                            score = exp_usd * min(len(trades) / 15, 1.0)

                            if score > best_score:
                                best_score = score
                                best_result = {
                                    "symbol": symbol,
                                    "vol_lookback": vol_lb,
                                    "vol_expansion": vol_exp,
                                    "min_range_pips": min_range,
                                    "target_pips": target,
                                    "stop_pips": stop,
                                    "trades": len(trades),
                                    "trades_per_day": round(len(trades) / args.days, 1),
                                    "wr_pct": round(wr, 1),
                                    "net_usd": round(net_usd, 2),
                                    "exp_usd": round(exp_usd, 3),
                                    "avg_hold_bars": round(mean(t["hold_bars"] for t in trades), 1),
                                }

        if best_result:
            all_results.append(best_result)
            print(f"✅ exp=${best_result['exp_usd']:+.3f} n={best_result['trades']} wr={best_result['wr_pct']:.0f}%")
        else:
            print("❌ no viable configs")

    # Sort and output
    all_results.sort(key=lambda r: r["exp_usd"], reverse=True)

    if all_results:
        fieldnames = list(all_results[0].keys())
        print("\n" + ",".join(fieldnames))
        for r in all_results:
            print(",".join(str(r[k]) for k in fieldnames))

        output_path = ROOT / "reports" / "volume_strategy_backtest.csv"
        output_path.parent.mkdir(exist_ok=True)
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_results)
        print(f"\nSaved to {output_path}")

        print("\n=== TOP 20 VOLUME STRATEGY CONFIGS ===")
        print(f"{'Symbol':>8} | {'Params':>22} | {'Trd/Day':>7} | {'WR':>5} | {'Exp/Trade':>9} | {'Net USD':>9}")
        print("-" * 80)
        for r in all_results[:20]:
            params = f"v{r['vol_lookback']}x{r['vol_expansion']}r{r['min_range_pips']}"
            print(f"{r['symbol']:>8} | {params:>22} | {r['trades_per_day']:>7.1f} | {r['wr_pct']:>4.1f}% | ${r['exp_usd']:>8.3f} | ${r['net_usd']:>8.2f}")
    else:
        print("No viable volume strategy configs found.")

    mt5.shutdown()


if __name__ == "__main__":
    main()
