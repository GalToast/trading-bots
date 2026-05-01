#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from statistics import mean
from typing import Literal

import MetaTrader5 as mt5


SYMBOL = "USDJPY"
PIP = 0.01
UNITS_001_LOT = 1_000

Direction = Literal["BUY", "SELL"]
EntryKind = Literal[
    "control_breakout",
    "stop_run_reclaim",
    "strict_displacement",
    "confirmed_displacement",
    "failed_continuation_fade",
]
ExitKind = Literal["opp_close", "retain_60", "retain_75", "time_3"]


@dataclass(frozen=True)
class Lane:
    lane_id: str
    entry_kind: EntryKind
    exit_kind: ExitKind
    hypothesis: str
    lookback: int = 6
    min_body_pips: float = 3.0
    min_body_ratio: float = 0.65
    sweep_pips: float = 0.6
    reclaim_close_pips: float = 0.2
    max_hold_bars: int = 6
    floor_pips: float = 0.5
    min_mfe_for_trail_pips: float = 1.0
    volume_burst_ratio: float = 1.15
    min_range_expansion: float = 0.0
    confirm_pips: float = 0.0
    confirm_window_bars: int = 2


@dataclass
class Trade:
    lane_id: str
    direction: Direction
    entry_idx: int
    exit_idx: int
    hold_bars: int
    pnl_pips: float
    pnl_usd: float
    mfe_pips: float
    mae_pips: float
    first_green_bars: int | None


def load_bars(days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M1, 0, 1440 * days)
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
    return abs(bar["close"] - bar["open"]) / PIP


def range_pips(bar: dict) -> float:
    return max((bar["high"] - bar["low"]) / PIP, 0.01)


def bar_dir(bar: dict) -> Direction | None:
    if bar["close"] > bar["open"]:
        return "BUY"
    if bar["close"] < bar["open"]:
        return "SELL"
    return None


def signed_pips(direction: Direction, start: float, end: float) -> float:
    move = (end - start) / PIP
    return move if direction == "BUY" else -move


def pnl_usd_001(direction: Direction, entry: float, exit_price: float, spread_pips: float) -> float:
    net_pips = signed_pips(direction, entry, exit_price) - spread_pips
    price_move = net_pips * PIP
    raw_jpy = price_move * UNITS_001_LOT
    return raw_jpy / max(exit_price, 0.0001)


def avg_volume(bars: list[dict], start: int, end: int) -> float:
    window = bars[start:end]
    if not window:
        return 0.0
    return mean(bar["tick_volume"] for bar in window)


def avg_range_pips(bars: list[dict], start: int, end: int) -> float:
    window = bars[start:end]
    if not window:
        return 0.0
    return mean(range_pips(bar) for bar in window)


def detect_control_breakout(bars: list[dict], idx: int, lane: Lane) -> Direction | None:
    if idx < lane.lookback:
        return None
    cur = bars[idx]
    prior = bars[idx - lane.lookback : idx]
    prior_high = max(b["high"] for b in prior)
    prior_low = min(b["low"] for b in prior)
    body = body_pips(cur)
    ratio = body / range_pips(cur)
    if body < lane.min_body_pips or ratio < lane.min_body_ratio:
        return None
    if cur["close"] > prior_high:
        return "BUY"
    if cur["close"] < prior_low:
        return "SELL"
    return None


def detect_stop_run_reclaim(bars: list[dict], idx: int, lane: Lane) -> Direction | None:
    if idx < lane.lookback:
        return None
    cur = bars[idx]
    prior = bars[idx - lane.lookback : idx]
    prior_high = max(b["high"] for b in prior)
    prior_low = min(b["low"] for b in prior)
    direction = bar_dir(cur)
    if direction == "BUY":
        swept = cur["low"] <= (prior_low - lane.sweep_pips * PIP)
        reclaimed = cur["close"] >= (prior_low + lane.reclaim_close_pips * PIP)
        if swept and reclaimed and body_pips(cur) >= 1.5:
            return "BUY"
    if direction == "SELL":
        swept = cur["high"] >= (prior_high + lane.sweep_pips * PIP)
        reclaimed = cur["close"] <= (prior_high - lane.reclaim_close_pips * PIP)
        if swept and reclaimed and body_pips(cur) >= 1.5:
            return "SELL"
    return None


def detect_strict_displacement(bars: list[dict], idx: int, lane: Lane) -> Direction | None:
    if idx < lane.lookback + 1:
        return None
    cur = bars[idx]
    prior = bars[idx - lane.lookback : idx]
    prior_high = max(b["high"] for b in prior)
    prior_low = min(b["low"] for b in prior)
    body = body_pips(cur)
    ratio = body / range_pips(cur)
    vol = avg_volume(bars, idx - lane.lookback, idx)
    avg_rng = avg_range_pips(bars, idx - lane.lookback, idx)
    burst = cur["tick_volume"] >= vol * lane.volume_burst_ratio if vol > 0 else False
    expanded = (range_pips(cur) / avg_rng) >= lane.min_range_expansion if avg_rng > 0 and lane.min_range_expansion > 0 else True
    if body < lane.min_body_pips or ratio < lane.min_body_ratio or not burst or not expanded:
        return None
    if cur["close"] > prior_high:
        return "BUY"
    if cur["close"] < prior_low:
        return "SELL"
    return None


def detect_failed_continuation_fade(bars: list[dict], idx: int, lane: Lane) -> Direction | None:
    if idx < lane.lookback + 1:
        return None
    prev = bars[idx - 1]
    cur = bars[idx]
    prior = bars[idx - 1 - lane.lookback : idx - 1]
    if len(prior) < lane.lookback:
        return None
    prior_high = max(b["high"] for b in prior)
    prior_low = min(b["low"] for b in prior)
    prev_body = body_pips(prev)
    prev_ratio = prev_body / range_pips(prev)
    if prev_body < lane.min_body_pips or prev_ratio < lane.min_body_ratio:
        return None

    if prev["close"] > prior_high:
        failed = cur["close"] < prior_high and cur["close"] < prev["open"]
        if failed:
            return "SELL"
    if prev["close"] < prior_low:
        failed = cur["close"] > prior_low and cur["close"] > prev["open"]
        if failed:
            return "BUY"
    return None


def detect_entry(bars: list[dict], idx: int, lane: Lane) -> Direction | None:
    if lane.entry_kind == "control_breakout":
        return detect_control_breakout(bars, idx, lane)
    if lane.entry_kind == "stop_run_reclaim":
        return detect_stop_run_reclaim(bars, idx, lane)
    if lane.entry_kind == "strict_displacement":
        return detect_strict_displacement(bars, idx, lane)
    if lane.entry_kind == "confirmed_displacement":
        return detect_strict_displacement(bars, idx, lane)
    if lane.entry_kind == "failed_continuation_fade":
        return detect_failed_continuation_fade(bars, idx, lane)
    return None


def find_entry_plan(
    bars: list[dict],
    idx: int,
    lane: Lane,
) -> tuple[Direction, int, float] | None:
    direction = detect_entry(bars, idx, lane)
    if not direction:
        return None

    if lane.entry_kind != "confirmed_displacement":
        entry_idx = idx + 1
        if entry_idx >= len(bars):
            return None
        return direction, entry_idx, bars[entry_idx]["open"]

    signal_close = bars[idx]["close"]
    target = signal_close + lane.confirm_pips * PIP if direction == "BUY" else signal_close - lane.confirm_pips * PIP
    end_idx = min(len(bars), idx + 1 + lane.confirm_window_bars)
    for entry_idx in range(idx + 1, end_idx):
        bar = bars[entry_idx]
        if direction == "BUY" and bar["high"] >= target:
            return direction, entry_idx, target
        if direction == "SELL" and bar["low"] <= target:
            return direction, entry_idx, target
    return None


def should_exit(
    lane: Lane,
    direction: Direction,
    entry_price: float,
    bars: list[dict],
    idx: int,
    mfe_pips: float,
) -> bool:
    bar = bars[idx]
    prev = bars[idx - 1]
    close_pips = signed_pips(direction, entry_price, bar["close"])
    current_dir = bar_dir(bar)

    if lane.exit_kind == "opp_close":
        return current_dir is not None and current_dir != direction

    if lane.exit_kind == "time_3":
        progressed = signed_pips(direction, prev["close"], bar["close"]) > 0
        return not progressed

    if lane.exit_kind in {"retain_60", "retain_75"} and mfe_pips >= lane.min_mfe_for_trail_pips:
        keep = 0.60 if lane.exit_kind == "retain_60" else 0.75
        floor = max(lane.floor_pips, mfe_pips * keep)
        return close_pips <= floor

    return False


def simulate_lane(bars: list[dict], lane: Lane, spread_pips: float) -> list[Trade]:
    trades: list[Trade] = []
    idx = lane.lookback + 2
    while idx < len(bars) - 2:
        entry_plan = find_entry_plan(bars, idx, lane)
        if not entry_plan:
            idx += 1
            continue

        direction, entry_idx, entry_price = entry_plan
        exit_idx = None
        exit_price = None
        mfe_pips = 0.0
        mae_pips = 0.0
        first_green_bars = None

        for j in range(entry_idx, min(len(bars) - 1, entry_idx + lane.max_hold_bars + 1)):
            bar = bars[j]
            favorable = signed_pips(direction, entry_price, bar["high"] if direction == "BUY" else bar["low"])
            adverse = -signed_pips(direction, entry_price, bar["low"] if direction == "BUY" else bar["high"])
            mfe_pips = max(mfe_pips, favorable)
            mae_pips = max(mae_pips, adverse)
            close_pips = signed_pips(direction, entry_price, bar["close"])
            if first_green_bars is None and close_pips > 0:
                first_green_bars = j - entry_idx + 1
            if should_exit(lane, direction, entry_price, bars, j, mfe_pips):
                exit_idx = j
                exit_price = bar["close"]
                break
            if (j - entry_idx + 1) >= lane.max_hold_bars:
                exit_idx = j
                exit_price = bar["close"]
                break

        if exit_idx is None or exit_price is None:
            exit_idx = min(len(bars) - 1, entry_idx + lane.max_hold_bars)
            exit_price = bars[exit_idx]["close"]

        trades.append(
            Trade(
                lane_id=lane.lane_id,
                direction=direction,
                entry_idx=entry_idx,
                exit_idx=exit_idx,
                hold_bars=exit_idx - entry_idx + 1,
                pnl_pips=signed_pips(direction, entry_price, exit_price) - spread_pips,
                pnl_usd=pnl_usd_001(direction, entry_price, exit_price, spread_pips),
                mfe_pips=max(0.0, mfe_pips),
                mae_pips=max(0.0, mae_pips),
                first_green_bars=first_green_bars,
            )
        )
        idx = exit_idx + 1

    return trades


def build_lanes() -> list[Lane]:
    return [
        Lane(
            "ctrl_break_ret75",
            "control_breakout",
            "retain_75",
            "Current best breakout-style control with aggressive peak retention",
            lookback=6,
            min_body_pips=3.0,
            min_body_ratio=0.65,
            max_hold_bars=5,
            floor_pips=0.5,
        ),
        Lane(
            "ctrl_break_time3",
            "control_breakout",
            "time_3",
            "Current breakout control with fast bank-on-stall exit",
            lookback=6,
            min_body_pips=3.0,
            min_body_ratio=0.65,
            max_hold_bars=3,
        ),
        Lane(
            "stoprun_reclaim_opp",
            "stop_run_reclaim",
            "opp_close",
            "Sweep and reclaim level, then bank until first opposite close",
            lookback=8,
            sweep_pips=0.8,
            reclaim_close_pips=0.3,
            max_hold_bars=6,
        ),
        Lane(
            "stoprun_reclaim_ret60",
            "stop_run_reclaim",
            "retain_60",
            "Sweep and reclaim level, then keep 60% of MFE",
            lookback=8,
            sweep_pips=0.8,
            reclaim_close_pips=0.3,
            max_hold_bars=6,
            floor_pips=0.5,
        ),
        Lane(
            "displace_break_ret75",
            "strict_displacement",
            "retain_75",
            "Only trade large volume-backed displacement breaks",
            lookback=8,
            min_body_pips=5.0,
            min_body_ratio=0.80,
            volume_burst_ratio=1.20,
            max_hold_bars=6,
            floor_pips=0.5,
        ),
        Lane(
            "vol_expand_break_ret75",
            "strict_displacement",
            "retain_75",
            "Only take displacement breaks when bar range expands far beyond recent baseline",
            lookback=8,
            min_body_pips=4.0,
            min_body_ratio=0.75,
            volume_burst_ratio=1.05,
            min_range_expansion=1.6,
            max_hold_bars=6,
            floor_pips=0.5,
        ),
        Lane(
            "confirm_disp_break_ret75",
            "confirmed_displacement",
            "retain_75",
            "Wait for a further confirmed push after strict displacement before entering",
            lookback=8,
            min_body_pips=4.0,
            min_body_ratio=0.75,
            volume_burst_ratio=1.10,
            min_range_expansion=1.4,
            confirm_pips=2.0,
            confirm_window_bars=2,
            max_hold_bars=6,
            floor_pips=0.5,
        ),
        Lane(
            "displace_break_time3",
            "strict_displacement",
            "time_3",
            "Large displacement break, bank quickly if follow-through stalls",
            lookback=8,
            min_body_pips=5.0,
            min_body_ratio=0.80,
            volume_burst_ratio=1.20,
            max_hold_bars=3,
        ),
        Lane(
            "failed_cont_fade_time3",
            "failed_continuation_fade",
            "time_3",
            "Fade failed breakout continuation and bank on first stall",
            lookback=8,
            min_body_pips=4.0,
            min_body_ratio=0.75,
            max_hold_bars=4,
        ),
        Lane(
            "failed_cont_fade_ret60",
            "failed_continuation_fade",
            "retain_60",
            "Fade failed breakout continuation with retained MFE exit",
            lookback=8,
            min_body_pips=4.0,
            min_body_ratio=0.75,
            max_hold_bars=5,
            floor_pips=0.5,
        ),
    ]


def summarize(trades: list[Trade], days: int) -> dict:
    wins = [t for t in trades if t.pnl_usd > 0]
    capture = [(t.pnl_pips / t.mfe_pips) * 100.0 for t in trades if t.mfe_pips > 0]
    first_green = [t.first_green_bars for t in trades if t.first_green_bars is not None]
    return {
        "trades": len(trades),
        "per_day": len(trades) / max(days, 1),
        "wr": (len(wins) / len(trades) * 100.0) if trades else 0.0,
        "net_usd": sum(t.pnl_usd for t in trades),
        "exp_usd": (sum(t.pnl_usd for t in trades) / len(trades)) if trades else 0.0,
        "net_pips": sum(t.pnl_pips for t in trades),
        "avg_hold": mean([t.hold_bars for t in trades]) if trades else 0.0,
        "avg_mfe_capture": mean(capture) if capture else 0.0,
        "avg_first_green": mean(first_green) if first_green else None,
    }


def print_results(days: int, spread_pips: float, rows: list[tuple[Lane, dict]]) -> None:
    print(f"USDJPY asymmetry lab | days={days} | spread={spread_pips:.2f} pips | lot=0.01")
    print()
    print(
        f"{'lane':<24} {'trades':>6} {'/day':>6} {'wr%':>7} {'net_usd':>9} "
        f"{'exp_usd':>9} {'net_pips':>10} {'hold':>6} {'cap%':>7}"
    )
    print("-" * 92)
    for lane, stats in sorted(rows, key=lambda item: (item[1]["exp_usd"], item[1]["net_usd"]), reverse=True):
        print(
            f"{lane.lane_id:<24} {stats['trades']:>6d} {stats['per_day']:>6.1f} "
            f"{stats['wr']:>6.1f}% {stats['net_usd']:>+9.2f} {stats['exp_usd']:>+9.2f} "
            f"{stats['net_pips']:>+10.1f} {stats['avg_hold']:>6.1f} {stats['avg_mfe_capture']:>6.1f}%"
        )
    print()
    print("Notes")
    ranked = sorted(rows, key=lambda item: (item[1]["exp_usd"], item[1]["net_usd"]), reverse=True)
    for lane, stats in ranked[:3]:
        avg_fg = f"{stats['avg_first_green']:.1f} bars" if stats["avg_first_green"] is not None else "n/a"
        print(
            f"- {lane.lane_id}: {lane.hypothesis} | "
            f"exp={stats['exp_usd']:+.2f} USD/trade | wr={stats['wr']:.1f}% | first_green={avg_fg}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest asymmetry-based USDJPY architectures")
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument("--spread-pips", type=float, default=0.6)
    args = parser.parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        bars = load_bars(args.days)
        if not bars:
            print("No USDJPY bars loaded")
            return 1

        results: list[tuple[Lane, dict]] = []
        for lane in build_lanes():
            trades = simulate_lane(bars, lane, args.spread_pips)
            results.append((lane, summarize(trades, args.days)))
        print_results(args.days, args.spread_pips, results)
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
