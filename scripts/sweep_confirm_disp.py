#!/usr/bin/env python3
"""Parameter sweep for confirmed-displacement breakout + retain 75% exit.

Grid search over:
- Confirmation distance: how far price must break beyond structure (pips)
- Expansion threshold: ATR multiplier for "large displacement"
- Confirmation window: how many bars the break must hold

Tests each combo at real spread against 10 days of USDJPY M1 data.

Usage: python scripts/sweep_confirm_disp.py [--days 10] [--spread 0.6]

Output:
- CSV to stdout with ranked results
- Heatmap text visualization
- Saved to reports/sweep_confirm_disp.csv
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
SYMBOL = "USDJPY"
PIP_SIZE = 0.01
UNITS_001_LOT = 1_000


@dataclass
class TradeResult:
    direction: str
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    hold_bars: int
    pnl_pips: float
    pnl_usd: float
    mfe_pips: float
    mae_pips: float


def load_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1440 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "tick_volume": int(r[5]),
        }
        for r in rates
    ]


def body_pips(bar: dict) -> float:
    return abs(bar["close"] - bar["open"]) / PIP_SIZE


def direction_of_bar(bar: dict) -> str | None:
    if bar["close"] > bar["open"]:
        return "BUY"
    if bar["close"] < bar["open"]:
        return "SELL"
    return None


def compute_atr(bars: list[dict], idx: int, period: int = 14) -> float:
    """Compute ATR at a given bar index."""
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


def pnl_usd_for_001_lot(direction: str, entry: float, exit_price: float, spread_pips: float) -> float:
    raw = (exit_price - entry) / PIP_SIZE if direction == "BUY" else (entry - exit_price) / PIP_SIZE
    price_move = (raw - spread_pips) * PIP_SIZE
    raw_jpy = price_move * UNITS_001_LOT
    return (raw_jpy) / max(exit_price, 0.0001)


def simulate_confirm_disp(
    bars: list[dict],
    confirm_pips: float,
    expansion_mult: float,
    confirm_window: int,
    spread_pips: float,
) -> list[TradeResult]:
    """Simulate confirmed-displacement breakout with retain-75% exit.

    Entry logic:
    1. Compute recent structure high/low over last 20 bars
    2. Check if current bar breaks beyond structure by >= confirm_pips
    3. Check if current bar body >= expansion_mult * ATR (large displacement)
    4. Confirm the break holds for confirm_window bars (price stays beyond structure)
    5. Enter at open of next bar after confirmation

    Exit logic: retain 75% of MFE (trail fires at max(0.03 USD, 75% of peak))
    """
    trades: list[TradeResult] = []
    lookback = 20
    max_hold = 30  # ~30 min max hold

    idx = lookback + 14  # Need lookback + ATR period
    while idx < len(bars) - max_hold - confirm_window:
        # Structure
        recent = bars[idx - lookback : idx]
        struct_high = max(b["high"] for b in recent)
        struct_low = min(b["low"] for b in recent)

        cur = bars[idx]
        atr = compute_atr(bars, idx)
        atr_pips = atr / PIP_SIZE if atr > 0 else 10.0

        # Check breakout above structure high
        break_above = cur["close"] > struct_high + confirm_pips * PIP_SIZE
        # Check breakout below structure low
        break_below = cur["close"] < struct_low - confirm_pips * PIP_SIZE

        # Check displacement (body size vs ATR)
        cur_body_pips = body_pips(cur)
        meets_expansion = cur_body_pips >= expansion_mult * atr_pips

        if not meets_expansion:
            idx += 1
            continue

        if break_above:
            direction = "BUY"
            structure_level = struct_high
        elif break_below:
            direction = "SELL"
            structure_level = struct_low
        else:
            idx += 1
            continue

        # Confirm: price stays beyond structure for confirm_window bars
        confirmed = True
        for w in range(1, confirm_window + 1):
            check_idx = idx + w
            if check_idx >= len(bars):
                confirmed = False
                break
            check_bar = bars[check_idx]
            if direction == "BUY" and check_bar["close"] < structure_level:
                confirmed = False
                break
            if direction == "SELL" and check_bar["close"] > structure_level:
                confirmed = False
                break

        if not confirmed:
            idx += 1
            continue

        # Enter at open of next bar after confirmation
        entry_idx = idx + confirm_window
        if entry_idx >= len(bars) - 1:
            break
        entry_price = bars[entry_idx]["open"]

        # Simulate trade
        mfe_pips = 0.0
        mae_pips = 0.0
        exit_idx = None
        exit_price = None

        for j in range(entry_idx, min(len(bars) - 1, entry_idx + max_hold)):
            bar = bars[j]
            if direction == "BUY":
                favorable = (bar["high"] - entry_price) / PIP_SIZE
                adverse = (entry_price - bar["low"]) / PIP_SIZE
            else:
                favorable = (entry_price - bar["low"]) / PIP_SIZE
                adverse = (bar["high"] - entry_price) / PIP_SIZE

            mfe_pips = max(mfe_pips, favorable)
            mae_pips = max(mae_pips, adverse)

            # Retain 75% exit: trail fires when price drops below 75% of peak MFE
            close_pips = (bar["close"] - entry_price) / PIP_SIZE if direction == "BUY" else (entry_price - bar["close"]) / PIP_SIZE
            floor_pips = mfe_pips * 0.75

            if mfe_pips >= 3.0 and close_pips <= floor_pips:
                exit_idx = j
                exit_price = bar["close"]
                break

        if exit_idx is None:
            # Time exit
            exit_idx = min(len(bars) - 1, entry_idx + max_hold - 1)
            exit_price = bars[exit_idx]["close"]

        pnl_pips = ((exit_price - entry_price) / PIP_SIZE if direction == "BUY" else (entry_price - exit_price) / PIP_SIZE) - spread_pips

        trades.append(TradeResult(
            direction=direction,
            entry_idx=entry_idx,
            exit_idx=exit_idx,
            entry_price=entry_price,
            exit_price=exit_price,
            hold_bars=exit_idx - entry_idx + 1,
            pnl_pips=pnl_pips,
            pnl_usd=pnl_usd_for_001_lot(direction, entry_price, exit_price, spread_pips),
            mfe_pips=max(0.0, mfe_pips),
            mae_pips=max(0.0, mae_pips),
        ))

        idx = exit_idx + 1
    else:
        idx += 1

    return trades


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=10)
    parser.add_argument("--spread", type=float, default=0.6)
    args = parser.parse_args()

    mt5.initialize()
    bars = load_bars(SYMBOL, args.days)
    if not bars:
        print(f"No bars loaded for {SYMBOL}. Is MT5 running?")
        sys.exit(1)

    print(f"Confirmed-displacement sweep — {SYMBOL}, {args.days} days, {len(bars)} bars, spread={args.spread} pips")
    print()

    # Parameter grid
    confirm_distances = [0.5, 1.0, 1.5, 2.0, 3.0, 5.0]
    expansion_thresholds = [1.0, 1.2, 1.5, 2.0, 2.5]
    confirmation_windows = [1, 2, 3, 5]

    results = []

    for cd in confirm_distances:
        for et in expansion_thresholds:
            for cw in confirmation_windows:
                trades = simulate_confirm_disp(bars, cd, et, cw, args.spread)
                if not trades:
                    continue

                wins = [t for t in trades if t.pnl_pips > 0]
                net_pips = sum(t.pnl_pips for t in trades)
                net_usd = sum(t.pnl_usd for t in trades)
                wr = len(wins) / len(trades) * 100
                exp_usd = net_usd / len(trades)
                avg_hold = mean(t.hold_bars for t in trades)
                avg_mfe = mean(t.mfe_pips for t in trades)
                avg_mae = mean(t.mae_pips for t in trades)
                cap_pct = (sum(t.pnl_pips for t in wins) / sum(t.mfe_pips for t in wins) * 100) if wins and sum(t.mfe_pips for t in wins) > 0 else 0

                results.append({
                    "confirm_pips": cd,
                    "expansion_x": et,
                    "confirm_window": cw,
                    "trades": len(trades),
                    "trades_per_day": len(trades) / args.days,
                    "wr_pct": round(wr, 1),
                    "net_pips": round(net_pips, 1),
                    "net_usd": round(net_usd, 2),
                    "exp_usd": round(exp_usd, 3),
                    "avg_hold_bars": round(avg_hold, 1),
                    "avg_mfe_pips": round(avg_mfe, 1),
                    "avg_mae_pips": round(avg_mae, 1),
                    "cap_pct": round(cap_pct, 1),
                })

    # Sort by net USD descending
    results.sort(key=lambda r: r["net_usd"], reverse=True)

    # Output
    fieldnames = list(results[0].keys()) if results else []
    print(",".join(fieldnames))
    for r in results:
        print(",".join(str(r[k]) for k in fieldnames))

    print(f"\nSaved {len(results)} combos to reports/sweep_confirm_disp.csv", file=sys.stderr)

    # Save
    output_path = ROOT / "reports" / "sweep_confirm_disp.csv"
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    # Text heatmap: best combos by confirm_pips x expansion
    print("\n=== HEATMAP: Net USD by Confirm Distance x Expansion (window=1) ===", file=sys.stderr)
    w1_results = [r for r in results if r["confirm_window"] == 1]
    if w1_results:
        # Group by confirm_pips x expansion
        grid = {}
        for r in w1_results:
            grid[(r["confirm_pips"], r["expansion_x"])] = r["net_usd"]

        # Header
        expansions = sorted(set(r["expansion_x"] for r in w1_results))
        confirms = sorted(set(r["confirm_pips"] for r in w1_results))
        header_label = "Confirm\\Exp"
        header = f"{header_label:>12}"
        for e in expansions:
            header += f" | {e:>7.1f}x"
        print(header, file=sys.stderr)
        print("-" * len(header), file=sys.stderr)

        for c in confirms:
            row = f"{c:>10.1f} pips"
            for e in expansions:
                val = grid.get((c, e), 0.0)
                if val > 0:
                    row += f" | ${val:>6.2f}"
                elif val > -10:
                    row += f" | ${val:>6.2f}"
                else:
                    row += f" | ${val:>6.2f}"
            print(row, file=sys.stderr)

    mt5.shutdown()


if __name__ == "__main__":
    main()
