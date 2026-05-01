#!/usr/bin/env python3
"""Per-symbol recipe optimizer for confirmed-displacement basket.

Tests all recipe combos independently on USDJPY and GBPUSD to find
the optimal confirm_pips / expansion / retain ratio for each symbol.

Usage: python scripts/optimize_symbol_recipes.py
"""
from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ["USDJPY", "GBPUSD"]
PIP_SIZES = {"USDJPY": 0.01, "GBPUSD": 0.0001}
SPREADS = {"USDJPY": 0.6, "GBPUSD": 1.0}
UNITS_001_LOT = 1_000
CONFIRMS = [1.0, 1.5, 2.0, 3.0]
EXPANSIONS = [1.5, 2.0, 2.5, 3.0]
RETAINS = [0.50, 0.60, 0.75]
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
    if pip >= 0.01:
        raw_usd /= max(exit_price, 0.0001)
    return raw_usd


def simulate(symbol: str, bars: list[dict], spread_pips: float, confirm_pips: float,
             expansion_x: float, retain: float, floor_pips: float, pip: float) -> list[dict]:
    trades = []
    lookback = 20
    idx = lookback + 14
    while idx < len(bars) - MAX_HOLD - 2:
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

        for j in range(entry_idx, min(len(bars) - 1, entry_idx + MAX_HOLD)):
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
            exit_idx = min(len(bars) - 1, entry_idx + MAX_HOLD - 1)
            exit_price = bars[exit_idx]["close"]

        pnl_pips = ((exit_price - entry_price) / pip if direction == "BUY"
                    else (entry_price - exit_price) / pip) - spread_pips
        pnl_usd = pnl_usd_001(direction, entry_price, exit_price, spread_pips, pip)

        trades.append({
            "pnl_pips": pnl_pips, "pnl_usd": pnl_usd,
            "mfe_pips": max(0.0, mfe_pips),
        })
        idx = exit_idx + 1
    else:
        idx += 1

    return trades


def score_recipes(symbol: str, days: int = 30) -> list[dict]:
    """Test all recipe combos on one symbol across multiple windows."""
    pip = PIP_SIZES[symbol]
    spread = SPREADS[symbol]
    spread_pips = spread
    floor_pips = 0.5

    # Load bars for each window
    bars_30 = load_bars(symbol, 30)
    if not bars_30:
        return []
    bars_20 = bars_30[-1440 * 20:]
    bars_10 = bars_30[-1440 * 10:]

    results = []
    total = len(CONFIRMS) * len(EXPANSIONS) * len(RETAINS)
    count = 0

    for confirm in CONFIRMS:
        for expansion in EXPANSIONS:
            for retain in RETAINS:
                count += 1
                # Run on 30-day window
                trades_30 = simulate(symbol, bars_30, spread_pips, confirm, expansion, retain, floor_pips, pip)
                if not trades_30:
                    continue
                exp_30 = mean(t["pnl_usd"] for t in trades_30)

                # Run on 20-day window
                trades_20 = simulate(symbol, bars_20, spread_pips, confirm, expansion, retain, floor_pips, pip)
                exp_20 = mean(t["pnl_usd"] for t in trades_20) if trades_20 else 0

                # Run on 10-day window
                trades_10 = simulate(symbol, bars_10, spread_pips, confirm, expansion, retain, floor_pips, pip)
                exp_10 = mean(t["pnl_usd"] for t in trades_10) if trades_10 else 0

                # Walk-forward folds: split 30d into 3x10d folds
                fold_size = 1440 * 10
                folds = []
                for i in range(3):
                    fold_bars = bars_30[i * fold_size: (i + 1) * fold_size]
                    if len(fold_bars) > 100:
                        fold_trades = simulate(symbol, fold_bars, spread_pips, confirm, expansion, retain, floor_pips, pip)
                        if fold_trades:
                            folds.append(mean(t["pnl_usd"] for t in fold_trades))

                min_fold = min(folds) if folds else -999
                min_window = min(exp_10, exp_20, exp_30)

                # Composite score: robustness matters more than headline
                score = exp_30 + min_window + min_fold

                results.append({
                    "symbol": symbol,
                    "confirm_pips": confirm,
                    "expansion_x": expansion,
                    "retain_ratio": retain,
                    "trades_30d": len(trades_30),
                    "exp_30d": round(exp_30, 3),
                    "exp_20d": round(exp_20, 3),
                    "exp_10d": round(exp_10, 3),
                    "min_window_exp": round(min_window, 3),
                    "min_fold_exp": round(min_fold, 3),
                    "score": round(score, 3),
                })
                print(f"  [{count}/{total}] {symbol} {confirm}p/{expansion}x/ret{int(retain*100)}: "
                      f"exp={exp_30:.3f} min_win={min_window:.3f} min_fold={min_fold:.3f} score={score:.3f}")

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def main():
    mt5.initialize()

    all_results = []
    for symbol in SYMBOLS:
        print(f"\n=== Optimizing {symbol} ===")
        results = score_recipes(symbol, days=30)
        all_results.extend(results)

        if results:
            print(f"\n--- Top 5 recipes for {symbol} ---")
            for r in results[:5]:
                print(f"  {r['confirm_pips']}p/{r['expansion_x']}x/ret{int(r['retain_ratio']*100)}: "
                      f"score={r['score']:.3f}, exp={r['exp_30d']:.3f}, "
                      f"trades={r['trades_30d']}, min_win={r['min_window_exp']:.3f}, min_fold={r['min_fold_exp']:.3f}")

    # Save combined results
    output_path = ROOT / "reports" / "symbol_recipe_optimization.csv"
    output_path.parent.mkdir(exist_ok=True)
    if all_results:
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(all_results[0].keys()))
            writer.writeheader()
            writer.writerows(all_results)
        print(f"\nSaved to {output_path}")

    mt5.shutdown()


if __name__ == "__main__":
    main()
