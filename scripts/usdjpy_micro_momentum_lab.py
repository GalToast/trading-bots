#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Callable, Literal

import MetaTrader5 as mt5


ROOT = Path(__file__).resolve().parent.parent
SYMBOL = "USDJPY"
PIP_SIZE = 0.01
UNITS_001_LOT = 1_000


Direction = Literal["BUY", "SELL"]
EntryKind = Literal[
    "two_bar_momentum",
    "three_bar_momentum",
    "strong_breakout",
    "acceleration",
    "resume_pullback",
]
ExitKind = Literal[
    "opp_close",
    "stall_close",
    "two_stall",
    "retain_50",
    "retain_60",
    "retain_75",
    "time_3",
]


@dataclass(frozen=True)
class LaneConfig:
    lane_id: str
    entry_kind: EntryKind
    exit_kind: ExitKind
    hypothesis: str
    min_total_pips: float = 0.0
    min_body_ratio: float = 0.0
    breakout_lookback: int = 0
    max_hold_bars: int = 8
    floor_pips: float = 0.0
    min_mfe_for_trail_pips: float = 1.0


@dataclass
class TradeResult:
    lane_id: str
    direction: Direction
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    hold_bars: int
    pnl_pips: float
    pnl_usd: float
    mfe_pips: float
    mae_pips: float
    first_progress_bar: int | None


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


def range_pips(bar: dict) -> float:
    return max((bar["high"] - bar["low"]) / PIP_SIZE, 0.01)


def direction_of_bar(bar: dict) -> Direction | None:
    if bar["close"] > bar["open"]:
        return "BUY"
    if bar["close"] < bar["open"]:
        return "SELL"
    return None


def progress_close(direction: Direction, bar: dict, prev_bar: dict) -> bool:
    if direction == "BUY":
        return bar["close"] > prev_bar["close"]
    return bar["close"] < prev_bar["close"]


def opposite_close(direction: Direction, bar: dict) -> bool:
    bar_dir = direction_of_bar(bar)
    return bar_dir is not None and bar_dir != direction


def signed_move_pips(direction: Direction, start: float, end: float) -> float:
    raw = (end - start) / PIP_SIZE
    return raw if direction == "BUY" else -raw


def pnl_usd_for_001_lot(direction: Direction, entry: float, exit_price: float, spread_pips: float) -> float:
    price_move = signed_move_pips(direction, entry, exit_price) * PIP_SIZE
    raw_jpy = price_move * UNITS_001_LOT
    spread_jpy = spread_pips * PIP_SIZE * UNITS_001_LOT
    return (raw_jpy - spread_jpy) / max(exit_price, 0.0001)


def detect_entry(bars: list[dict], idx: int, lane: LaneConfig) -> Direction | None:
    if idx < 4:
        return None

    cur = bars[idx]
    prev1 = bars[idx - 1]
    prev2 = bars[idx - 2]
    prev3 = bars[idx - 3]

    cur_dir = direction_of_bar(cur)
    prev1_dir = direction_of_bar(prev1)
    prev2_dir = direction_of_bar(prev2)

    if cur_dir is None or prev1_dir is None or prev2_dir is None:
        return None

    if lane.entry_kind == "two_bar_momentum":
        if cur_dir == prev1_dir:
            total = body_pips(cur) + body_pips(prev1)
            if total >= lane.min_total_pips:
                return cur_dir
        return None

    if lane.entry_kind == "three_bar_momentum":
        if cur_dir == prev1_dir == prev2_dir:
            total = body_pips(cur) + body_pips(prev1) + body_pips(prev2)
            if total >= lane.min_total_pips:
                return cur_dir
        return None

    if lane.entry_kind == "strong_breakout":
        body_ratio = body_pips(cur) / range_pips(cur)
        if body_ratio < lane.min_body_ratio:
            return None
        recent_high = max(b["high"] for b in bars[idx - lane.breakout_lookback : idx])
        recent_low = min(b["low"] for b in bars[idx - lane.breakout_lookback : idx])
        if cur["close"] > recent_high and body_pips(cur) >= lane.min_total_pips:
            return "BUY"
        if cur["close"] < recent_low and body_pips(cur) >= lane.min_total_pips:
            return "SELL"
        return None

    if lane.entry_kind == "acceleration":
        if cur_dir != prev1_dir:
            return None
        recent_avg = (body_pips(prev1) + body_pips(prev2) + body_pips(prev3)) / 3.0
        if recent_avg <= 0:
            return None
        if body_pips(cur) >= recent_avg * 1.4 and (body_pips(cur) + body_pips(prev1)) >= lane.min_total_pips:
            return cur_dir
        return None

    if lane.entry_kind == "resume_pullback":
        prev3_dir = direction_of_bar(prev3)
        if prev3_dir is None:
            return None
        if prev3_dir == cur_dir and prev1_dir != cur_dir:
            if cur_dir == "BUY" and cur["close"] > prev1["high"]:
                return "BUY"
            if cur_dir == "SELL" and cur["close"] < prev1["low"]:
                return "SELL"
        return None

    return None


def should_exit(
    bars: list[dict],
    idx: int,
    entry_price: float,
    direction: Direction,
    lane: LaneConfig,
    mfe_pips: float,
    stall_count: int,
) -> tuple[bool, int]:
    bar = bars[idx]
    prev_bar = bars[idx - 1]
    close_pips = signed_move_pips(direction, entry_price, bar["close"])

    if lane.exit_kind == "opp_close":
        return opposite_close(direction, bar), stall_count

    if lane.exit_kind == "stall_close":
        is_progress = progress_close(direction, bar, prev_bar)
        return (not is_progress), stall_count

    if lane.exit_kind == "two_stall":
        is_progress = progress_close(direction, bar, prev_bar)
        next_stall = 0 if is_progress else (stall_count + 1)
        return next_stall >= 2, next_stall

    if lane.exit_kind in {"retain_50", "retain_60", "retain_75"}:
        retain_ratio = {
            "retain_50": 0.50,
            "retain_60": 0.60,
            "retain_75": 0.75,
        }[lane.exit_kind]
        if mfe_pips >= lane.min_mfe_for_trail_pips:
            floor = max(lane.floor_pips, mfe_pips * retain_ratio)
            if close_pips <= floor:
                return True, stall_count
        return False, stall_count

    if lane.exit_kind == "time_3":
        is_progress = progress_close(direction, bar, prev_bar)
        next_stall = 0 if is_progress else (stall_count + 1)
        return next_stall >= 1, next_stall

    return False, stall_count


def simulate_lane(bars: list[dict], lane: LaneConfig, spread_pips: float) -> list[TradeResult]:
    trades: list[TradeResult] = []
    idx = 5
    while idx < len(bars) - 2:
        direction = detect_entry(bars, idx, lane)
        if not direction:
            idx += 1
            continue

        entry_idx = idx + 1
        entry_price = bars[entry_idx]["open"]
        mfe_pips = 0.0
        mae_pips = 0.0
        first_progress_bar: int | None = None
        stall_count = 0
        exit_idx = None
        exit_price = None

        for j in range(entry_idx, min(len(bars) - 1, entry_idx + lane.max_hold_bars + 1)):
            bar = bars[j]
            favorable = signed_move_pips(direction, entry_price, bar["high"] if direction == "BUY" else bar["low"])
            adverse = -signed_move_pips(direction, entry_price, bar["low"] if direction == "BUY" else bar["high"])
            mfe_pips = max(mfe_pips, favorable)
            mae_pips = max(mae_pips, adverse)

            close_pips = signed_move_pips(direction, entry_price, bar["close"])
            if first_progress_bar is None and close_pips > 0:
                first_progress_bar = j - entry_idx + 1

            exit_now, stall_count = should_exit(
                bars,
                j,
                entry_price,
                direction,
                lane,
                mfe_pips,
                stall_count,
            )
            timed_out = (j - entry_idx + 1) >= lane.max_hold_bars
            if exit_now or timed_out:
                exit_idx = j
                exit_price = bar["close"]
                break

        if exit_idx is None or exit_price is None:
            exit_idx = min(len(bars) - 1, entry_idx + lane.max_hold_bars)
            exit_price = bars[exit_idx]["close"]

        pnl_pips = signed_move_pips(direction, entry_price, exit_price) - spread_pips
        trades.append(
            TradeResult(
                lane_id=lane.lane_id,
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
                first_progress_bar=first_progress_bar,
            )
        )
        idx = exit_idx + 1
    return trades


def lane_set() -> list[LaneConfig]:
    return [
        LaneConfig("usd_momo_2bar_opp", "two_bar_momentum", "opp_close", "2-bar momentum, exit on first opposite close", min_total_pips=2.0, max_hold_bars=8),
        LaneConfig("usd_momo_2bar_stall", "two_bar_momentum", "stall_close", "2-bar momentum, exit on first non-progress close", min_total_pips=2.0, max_hold_bars=8),
        LaneConfig("usd_momo_2bar_ret50", "two_bar_momentum", "retain_50", "2-bar momentum, keep 50% of MFE once green", min_total_pips=2.0, max_hold_bars=8, floor_pips=0.5, min_mfe_for_trail_pips=1.0),
        LaneConfig("usd_momo_2bar_ret75", "two_bar_momentum", "retain_75", "2-bar momentum, keep 75% of MFE once green", min_total_pips=2.0, max_hold_bars=8, floor_pips=0.5, min_mfe_for_trail_pips=1.0),
        LaneConfig("usd_break_3bar_opp", "strong_breakout", "opp_close", "3-bar breakout, exit on first opposite close", min_total_pips=1.8, min_body_ratio=0.55, breakout_lookback=3, max_hold_bars=8),
        LaneConfig("usd_break_3bar_ret60", "strong_breakout", "retain_60", "3-bar breakout, keep 60% of MFE", min_total_pips=1.8, min_body_ratio=0.55, breakout_lookback=3, max_hold_bars=8, floor_pips=0.5, min_mfe_for_trail_pips=1.0),
        LaneConfig("usd_accel_twostall", "acceleration", "two_stall", "acceleration entry, exit after two stalled closes", min_total_pips=2.5, max_hold_bars=10),
        LaneConfig("usd_accel_ret75", "acceleration", "retain_75", "acceleration entry, keep 75% of MFE", min_total_pips=2.5, max_hold_bars=10, floor_pips=0.5, min_mfe_for_trail_pips=1.0),
        LaneConfig("usd_resume_pullback_opp", "resume_pullback", "opp_close", "resume after one-bar pullback, exit on opposite close", max_hold_bars=8),
        LaneConfig("usd_3bar_time3", "three_bar_momentum", "time_3", "3-bar momentum, bank quickly on first stall or 3 bars", min_total_pips=3.0, max_hold_bars=3),
    ]


def sweep_lanes() -> list[LaneConfig]:
    lanes: list[LaneConfig] = []
    for min_total in (2.0, 3.0, 4.0):
        for exit_kind in ("opp_close", "retain_60", "retain_75", "time_3"):
            for max_hold in (3, 5, 8):
                lanes.append(
                    LaneConfig(
                        lane_id=f"two_{int(min_total)}_{exit_kind}_{max_hold}",
                        entry_kind="two_bar_momentum",
                        exit_kind=exit_kind,
                        hypothesis="sweep two-bar momentum",
                        min_total_pips=min_total,
                        max_hold_bars=max_hold,
                        floor_pips=0.5,
                        min_mfe_for_trail_pips=1.0,
                    )
                )
    for min_total in (1.5, 2.0, 2.5, 3.0):
        for body_ratio in (0.55, 0.65, 0.75):
            for exit_kind in ("opp_close", "retain_60", "retain_75", "time_3"):
                for max_hold in (3, 5, 8):
                    lanes.append(
                        LaneConfig(
                            lane_id=f"break_{int(min_total*10)}_{int(body_ratio*100)}_{exit_kind}_{max_hold}",
                            entry_kind="strong_breakout",
                            exit_kind=exit_kind,
                            hypothesis="sweep strong breakout",
                            min_total_pips=min_total,
                            min_body_ratio=body_ratio,
                            breakout_lookback=3,
                            max_hold_bars=max_hold,
                            floor_pips=0.5,
                            min_mfe_for_trail_pips=1.0,
                        )
                    )
    for min_total in (2.5, 3.5, 4.5, 5.5):
        for exit_kind in ("two_stall", "retain_60", "retain_75"):
            for max_hold in (3, 5, 8):
                lanes.append(
                    LaneConfig(
                        lane_id=f"accel_{int(min_total*10)}_{exit_kind}_{max_hold}",
                        entry_kind="acceleration",
                        exit_kind=exit_kind,
                        hypothesis="sweep acceleration",
                        min_total_pips=min_total,
                        max_hold_bars=max_hold,
                        floor_pips=0.5,
                        min_mfe_for_trail_pips=1.0,
                    )
                )
    for exit_kind in ("opp_close", "retain_60", "retain_75", "time_3"):
        for max_hold in (3, 5, 8):
            lanes.append(
                LaneConfig(
                    lane_id=f"resume_{exit_kind}_{max_hold}",
                    entry_kind="resume_pullback",
                    exit_kind=exit_kind,
                    hypothesis="sweep resume pullback",
                    max_hold_bars=max_hold,
                    floor_pips=0.5,
                    min_mfe_for_trail_pips=1.0,
                )
            )
    return lanes


def summarize(trades: list[TradeResult], days: int) -> dict:
    wins = [t for t in trades if t.pnl_pips > 0]
    total_pips = sum(t.pnl_pips for t in trades)
    total_usd = sum(t.pnl_usd for t in trades)
    holds = [t.hold_bars for t in trades]
    mfe_capture = [
        (t.pnl_pips / t.mfe_pips) * 100.0
        for t in trades
        if t.mfe_pips > 0
    ]
    first_progress = [t.first_progress_bar for t in trades if t.first_progress_bar is not None]
    return {
        "trades": len(trades),
        "trades_per_day": len(trades) / max(days, 1),
        "win_rate": (len(wins) / len(trades) * 100.0) if trades else 0.0,
        "net_pips": total_pips,
        "exp_pips": total_pips / len(trades) if trades else 0.0,
        "net_usd": total_usd,
        "exp_usd": total_usd / len(trades) if trades else 0.0,
        "avg_hold_bars": mean(holds) if holds else 0.0,
        "avg_capture_pct": mean(mfe_capture) if mfe_capture else 0.0,
        "avg_first_progress_bars": mean(first_progress) if first_progress else None,
    }


def print_summary(days: int, spread_pips: float, results: list[tuple[LaneConfig, dict]]) -> None:
    print(f"USDJPY micro-momentum lab | days={days} | spread={spread_pips:.2f} pips | lot=0.01")
    print()
    print(
        f"{'lane':<24} {'trades':>6} {'/day':>6} {'wr%':>7} "
        f"{'net_pips':>10} {'exp_pips':>10} {'net_usd':>9} {'exp_usd':>9} "
        f"{'hold':>6} {'cap%':>7}"
    )
    print("-" * 108)
    for lane, stats in sorted(results, key=lambda item: (item[1]["net_usd"], item[1]["exp_usd"]), reverse=True):
        print(
            f"{lane.lane_id:<24} "
            f"{stats['trades']:>6d} "
            f"{stats['trades_per_day']:>6.1f} "
            f"{stats['win_rate']:>6.1f}% "
            f"{stats['net_pips']:>+10.1f} "
            f"{stats['exp_pips']:>+10.2f} "
            f"{stats['net_usd']:>+9.2f} "
            f"{stats['exp_usd']:>+9.2f} "
            f"{stats['avg_hold_bars']:>6.1f} "
            f"{stats['avg_capture_pct']:>6.1f}%"
        )

    print()
    print("Top 3 lanes")
    for lane, stats in sorted(results, key=lambda item: (item[1]["exp_usd"], item[1]["trades_per_day"]), reverse=True)[:3]:
        first_progress = (
            f"{stats['avg_first_progress_bars']:.1f} bars"
            if stats["avg_first_progress_bars"] is not None
            else "n/a"
        )
        print(f"- {lane.lane_id}: {lane.hypothesis}")
        print(
            f"  trades={stats['trades']} ({stats['trades_per_day']:.1f}/day), "
            f"wr={stats['win_rate']:.1f}%, exp={stats['exp_usd']:+.2f} USD/trade, "
            f"avg first progress={first_progress}"
        )


def print_sweep(days: int, spread_pips: float, results: list[tuple[LaneConfig, dict]], limit: int) -> None:
    ranked = sorted(
        results,
        key=lambda item: (
            item[1]["exp_usd"],
            item[1]["win_rate"],
            item[1]["trades_per_day"],
        ),
        reverse=True,
    )
    print(f"USDJPY micro-momentum sweep | days={days} | spread={spread_pips:.2f} pips | lot=0.01")
    print()
    print(
        f"{'lane':<28} {'trades':>6} {'/day':>6} {'wr%':>7} "
        f"{'net_usd':>9} {'exp_usd':>9} {'net_pips':>10} {'hold':>6}"
    )
    print("-" * 90)
    for lane, stats in ranked[:limit]:
        print(
            f"{lane.lane_id:<28} "
            f"{stats['trades']:>6d} "
            f"{stats['trades_per_day']:>6.1f} "
            f"{stats['win_rate']:>6.1f}% "
            f"{stats['net_usd']:>+9.2f} "
            f"{stats['exp_usd']:>+9.2f} "
            f"{stats['net_pips']:>+10.1f} "
            f"{stats['avg_hold_bars']:>6.1f}"
        )

    positives = [item for item in ranked if item[1]["exp_usd"] > 0 and item[1]["trades"] >= 25]
    print()
    print(f"Positive lanes with >=25 trades: {len(positives)}")
    for lane, stats in positives[:10]:
        print(
            f"- {lane.lane_id}: exp={stats['exp_usd']:+.2f} USD/trade, "
            f"wr={stats['win_rate']:.1f}%, trades/day={stats['trades_per_day']:.1f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Dense offline USDJPY M1 momentum lab")
    parser.add_argument("--days", type=int, default=10)
    parser.add_argument("--spread-pips", type=float, default=0.6)
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        bars = load_bars(SYMBOL, args.days)
        if not bars:
            print("No bars returned for USDJPY")
            return 1

        results: list[tuple[LaneConfig, dict]] = []
        lanes = sweep_lanes() if args.sweep else lane_set()
        for lane in lanes:
            trades = simulate_lane(bars, lane, args.spread_pips)
            results.append((lane, summarize(trades, args.days)))
        if args.sweep:
            print_sweep(args.days, args.spread_pips, results, args.limit)
        else:
            print_summary(args.days, args.spread_pips, results)
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
