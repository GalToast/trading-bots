#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import sys
from statistics import mean

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.usdjpy_asymmetry_lab import (
    Lane,
    Trade,
    build_lanes,
    load_bars,
    simulate_lane,
)
import MetaTrader5 as mt5


TARGET_LANES = {
    "ctrl_break_ret75",
    "stoprun_reclaim_opp",
    "confirm_disp_break_ret75",
}


@dataclass
class TradeRow:
    lane_id: str
    entry_idx: int
    entry_time: int
    session: str
    vol_bucket: str
    range_bucket: str
    pnl_usd: float
    hold_bars: int


def session_bucket(ts_utc: int) -> str:
    hour = datetime.fromtimestamp(ts_utc, tz=timezone.utc).hour
    if 0 <= hour < 8:
        return "asian"
    if 8 <= hour < 13:
        return "london"
    if 13 <= hour < 17:
        return "ny"
    return "off"


def range_pips(bar: dict) -> float:
    return max((bar["high"] - bar["low"]) / 0.01, 0.01)


def avg_range(bars: list[dict], start: int, end: int) -> float:
    window = bars[max(0, start):end]
    if not window:
        return 0.0
    return mean(range_pips(bar) for bar in window)


def classify_trade(bars: list[dict], trade: Trade) -> TradeRow:
    entry_bar = bars[trade.entry_idx]
    recent_avg = avg_range(bars, trade.entry_idx - 20, trade.entry_idx)
    current_rng = range_pips(entry_bar)
    expansion = (current_rng / recent_avg) if recent_avg > 0 else 0.0

    if expansion >= 1.8:
        vol_bucket = "high_expansion"
    elif expansion >= 1.2:
        vol_bucket = "medium_expansion"
    else:
        vol_bucket = "low_expansion"

    if recent_avg >= 4.5:
        range_bucket = "hot"
    elif recent_avg >= 3.0:
        range_bucket = "warm"
    else:
        range_bucket = "cold"

    return TradeRow(
        lane_id=trade.lane_id,
        entry_idx=trade.entry_idx,
        entry_time=entry_bar["time"],
        session=session_bucket(entry_bar["time"]),
        vol_bucket=vol_bucket,
        range_bucket=range_bucket,
        pnl_usd=trade.pnl_usd,
        hold_bars=trade.hold_bars,
    )


def summarize(rows: list[TradeRow]) -> dict:
    wins = [row for row in rows if row.pnl_usd > 0]
    return {
        "count": len(rows),
        "net": sum(row.pnl_usd for row in rows),
        "exp": (sum(row.pnl_usd for row in rows) / len(rows)) if rows else 0.0,
        "wr": (len(wins) / len(rows) * 100.0) if rows else 0.0,
        "avg_hold": mean([row.hold_bars for row in rows]) if rows else 0.0,
    }


def choose_mapping(train_rows: list[TradeRow], min_samples: int = 8) -> dict[tuple[str, str, str], str | None]:
    grouped: dict[tuple[str, str, str, str], list[TradeRow]] = defaultdict(list)
    for row in train_rows:
        grouped[(row.session, row.vol_bucket, row.range_bucket, row.lane_id)].append(row)

    mapping: dict[tuple[str, str, str], str | None] = {}
    buckets = {(row.session, row.vol_bucket, row.range_bucket) for row in train_rows}
    for bucket in sorted(buckets):
        candidates: list[tuple[float, float, str]] = []
        for lane_id in TARGET_LANES:
            rows = grouped.get((*bucket, lane_id), [])
            stats = summarize(rows)
            if stats["count"] >= min_samples:
                candidates.append((stats["exp"], stats["net"], lane_id))
        if not candidates:
            mapping[bucket] = None
            continue
        best = max(candidates)
        mapping[bucket] = best[2] if best[0] > 0 else None
    return mapping


def print_bucket_table(train_rows: list[TradeRow], mapping: dict[tuple[str, str, str], str | None]) -> None:
    grouped: dict[tuple[str, str, str, str], list[TradeRow]] = defaultdict(list)
    for row in train_rows:
        grouped[(row.session, row.vol_bucket, row.range_bucket, row.lane_id)].append(row)

    print("Training bucket winners")
    print(f"{'bucket':<38} {'choice':<24} {'samples':>7} {'exp_usd':>9} {'net_usd':>9}")
    print("-" * 92)
    for bucket in sorted(mapping):
        lane_id = mapping[bucket]
        label = f"{bucket[0]}|{bucket[1]}|{bucket[2]}"
        if lane_id is None:
            print(f"{label:<38} {'NO_TRADE':<24} {0:>7} {0.0:>+9.2f} {0.0:>+9.2f}")
            continue
        stats = summarize(grouped[(bucket[0], bucket[1], bucket[2], lane_id)])
        print(f"{label:<38} {lane_id:<24} {stats['count']:>7} {stats['exp']:>+9.2f} {stats['net']:>+9.2f}")
    print()


def main() -> int:
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        bars = load_bars(60)
        lanes = [lane for lane in build_lanes() if lane.lane_id in TARGET_LANES]
        rows: list[TradeRow] = []
        for lane in lanes:
            trades = simulate_lane(bars, lane, 0.6)
            rows.extend(classify_trade(bars, trade) for trade in trades)

        if not rows:
            print("No router rows")
            return 1

        split_time = sorted({row.entry_time for row in rows})[len({row.entry_time for row in rows}) // 2]
        train_rows = [row for row in rows if row.entry_time <= split_time]
        test_rows = [row for row in rows if row.entry_time > split_time]

        mapping = choose_mapping(train_rows)
        print_bucket_table(train_rows, mapping)

        chosen_test_rows = [
            row for row in test_rows
            if mapping.get((row.session, row.vol_bucket, row.range_bucket)) == row.lane_id
        ]
        all_test_by_lane: dict[str, list[TradeRow]] = defaultdict(list)
        for row in test_rows:
            all_test_by_lane[row.lane_id].append(row)

        print("Out-of-sample comparison")
        print(f"{'strategy':<24} {'trades':>6} {'wr%':>7} {'net_usd':>9} {'exp_usd':>9} {'avg_hold':>9}")
        print("-" * 72)
        router_stats = summarize(chosen_test_rows)
        print(
            f"{'router':<24} {router_stats['count']:>6} {router_stats['wr']:>6.1f}% "
            f"{router_stats['net']:>+9.2f} {router_stats['exp']:>+9.2f} {router_stats['avg_hold']:>9.1f}"
        )
        for lane_id in sorted(TARGET_LANES):
            stats = summarize(all_test_by_lane[lane_id])
            print(
                f"{lane_id:<24} {stats['count']:>6} {stats['wr']:>6.1f}% "
                f"{stats['net']:>+9.2f} {stats['exp']:>+9.2f} {stats['avg_hold']:>9.1f}"
            )
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
