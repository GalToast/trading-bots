#!/usr/bin/env python3
"""Cross-product sweep: entry architecture × exit retain ratio.

Tests whether the optimal exit depends on the entry type.

Entry architectures:
1. ctrl_break (baseline breakout — no displacement filter)
2. confirm_disp_1.5_2.5 (confirmed displacement: 1.5 pips, 2.5x ATR, 1 bar)
3. confirm_disp_3.0_2.5 (confirmed displacement: 3.0 pips, 2.5x ATR, 1 bar)

Exit variants:
1. baseline (no retain, trail as-is)
2. retain_50 (trail at 50% of peak, min $0.03)
3. retain_60 (trail at 60% of peak, min $0.03)
4. retain_75 (trail at 75% of peak, min $0.03)

Grid: 3 entries × 4 exits = 12 combos
Tests each at real spread against 10/20/30-day windows.

Usage: python scripts/cross_product_sweep.py [--days 10] [--spread 0.6]
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
    return raw_jpy / max(exit_price, 0.0001)


def simulate_combo(
    bars: list[dict],
    entry_type: str,
    retain_ratio: float | None,
    floor_usd: float,
    spread_pips: float,
    confirm_pips: float = 0,
    expansion_x: float = 0,
) -> list[TradeResult]:
    """Simulate one entry × exit combo."""
    trades: list[TradeResult] = []
    lookback = 20
    max_hold = 30

    idx = lookback + 14
    while idx < len(bars) - max_hold - 2:
        recent = bars[idx - lookback : idx]
        struct_high = max(b["high"] for b in recent)
        struct_low = min(b["low"] for b in recent)
        cur = bars[idx]
        atr = compute_atr(bars, idx)
        atr_pips = atr / PIP_SIZE if atr > 0 else 10.0

        direction = None
        structure_level = None

        if entry_type == "ctrl_break":
            # Baseline: just break structure with any meaningful body
            cur_body = body_pips(cur)
            if cur_body < 2.0:  # minimum 2 pip body
                idx += 1
                continue
            if cur["close"] > struct_high:
                direction = "BUY"
                structure_level = struct_high
            elif cur["close"] < struct_low:
                direction = "SELL"
                structure_level = struct_low

        elif entry_type.startswith("confirm_disp"):
            # Confirmed displacement
            break_above = cur["close"] > struct_high + confirm_pips * PIP_SIZE
            break_below = cur["close"] < struct_low - confirm_pips * PIP_SIZE
            cur_body = body_pips(cur)
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

        # For confirmed displacement, need 1 bar confirmation
        if entry_type.startswith("confirm_disp"):
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
        else:
            entry_idx = idx + 1

        if entry_idx >= len(bars) - 1:
            break
        entry_price = bars[entry_idx]["open"]

        # Simulate trade with exit logic
        mfe_pips = 0.0
        mae_pips = 0.0
        exit_idx = None
        exit_price = None

        for j in range(entry_idx, min(len(bars) - 1, entry_idx + max_hold)):
            bar = bars[j]
            if direction == "BUY":
                favorable = (bar["high"] - entry_price) / PIP_SIZE
                adverse = (entry_price - bar["low"]) / PIP_SIZE
                close_pips = (bar["close"] - entry_price) / PIP_SIZE
            else:
                favorable = (entry_price - bar["low"]) / PIP_SIZE
                adverse = (bar["high"] - entry_price) / PIP_SIZE
                close_pips = (entry_price - bar["close"]) / PIP_SIZE

            mfe_pips = max(mfe_pips, favorable)
            mae_pips = max(mae_pips, adverse)

            # Exit logic
            if retain_ratio is not None and mfe_pips >= 3.0:
                floor_pips = mfe_pips * retain_ratio
                if close_pips <= floor_pips:
                    exit_idx = j
                    exit_price = bar["close"]
                    break

        if exit_idx is None:
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
    parser.add_argument("--symbol", default="USDJPY")
    args = parser.parse_args()

    mt5.initialize()
    bars = load_bars(args.symbol, args.days)
    if not bars:
        print(f"No bars loaded for {args.symbol}. Is MT5 running?")
        sys.exit(1)

    print(f"Cross-product sweep — {args.symbol}, {args.days} days, {len(bars)} bars")
    print()

    # Entry configs
    entries = [
        ("ctrl_break", "Baseline breakout", 0, 0),
        ("confirm_disp_1.5_2.5", "Confirm 1.5pip 2.5xATR", 1.5, 2.5),
        ("confirm_disp_3.0_2.5", "Confirm 3.0pip 2.5xATR", 3.0, 2.5),
    ]

    # Exit configs
    exits = [
        ("baseline", None, 0.0),
        ("retain_50", 0.50, 0.03),
        ("retain_60", 0.60, 0.03),
        ("retain_75", 0.75, 0.03),
    ]

    results = []

    for entry_id, entry_label, confirm_pips, expansion_x in entries:
        for exit_id, retain, floor in exits:
            trades = simulate_combo(bars, entry_id, retain, floor, args.spread, confirm_pips, expansion_x)
            if not trades:
                continue

            wins = [t for t in trades if t.pnl_pips > 0]
            net_usd = sum(t.pnl_usd for t in trades)
            wr = len(wins) / len(trades) * 100
            exp_usd = net_usd / len(trades)
            avg_hold = mean(t.hold_bars for t in trades)
            avg_mfe = mean(t.mfe_pips for t in trades)
            total_pips = sum(t.pnl_pips for t in trades)
            cap_pct = (sum(t.pnl_pips for t in wins) / sum(t.mfe_pips for t in wins) * 100) if wins and sum(t.mfe_pips for t in wins) > 0 else 0

            results.append({
                "entry": entry_id,
                "exit": exit_id,
                "trades": len(trades),
                "wr_pct": round(wr, 1),
                "net_usd": round(net_usd, 2),
                "exp_usd": round(exp_usd, 3),
                "total_pips": round(total_pips, 1),
                "avg_hold_bars": round(avg_hold, 1),
                "avg_mfe_pips": round(avg_mfe, 1),
                "cap_pct": round(cap_pct, 1),
            })

    # Sort by exp_usd descending
    results.sort(key=lambda r: r["exp_usd"], reverse=True)

    if not results:
        print("No trades found. Exiting.")
        mt5.shutdown()
        return

    # Output
    fieldnames = list(results[0].keys())
    print(",".join(fieldnames))
    for r in results:
        print(",".join(str(r[k]) for k in fieldnames))

    output_path = ROOT / "reports" / "cross_product_sweep.csv"
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    # Text heatmap: entry × exit
    print(f"\n=== HEATMAP: Exp/USD by Entry × Exit ({args.days}-day) ===", file=sys.stderr)
    exit_ids = ["baseline", "retain_50", "retain_60", "retain_75"]
    entry_ids = ["ctrl_break", "confirm_disp_1.5_2.5", "confirm_disp_3.0_2.5"]
    grid = {}
    for r in results:
        grid[(r["entry"], r["exit"])] = r["exp_usd"]

    hdr_label = "Entry\\Exit"
    header = f"{hdr_label:>25}"
    for e in exit_ids:
        header += f" | {e:>10}"
    print(header, file=sys.stderr)
    print("-" * len(header), file=sys.stderr)

    for entry in entry_ids:
        row = f"{entry:>25}"
        for exit_ in exit_ids:
            val = grid.get((entry, exit_), 0.0)
            row += f" | ${val:>9.3f}"
        print(row, file=sys.stderr)

    print(f"\nSaved to {output_path}", file=sys.stderr)

    mt5.shutdown()


if __name__ == "__main__":
    main()
