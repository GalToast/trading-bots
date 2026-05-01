#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import mean

import MetaTrader5 as mt5

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.usdjpy_asymmetry_lab import Lane, Trade, build_lanes, load_bars, simulate_lane


SPREAD_PIPS = 0.6
DEFAULT_WARMUP_DAYS = 20
DEFAULT_WINDOW_TRADES = 8
DEFAULT_MIN_SAMPLES = 4
FALLBACK_LANE_ID = "confirm_disp_break_ret75"
LANE_POOLS = {
    "all": None,
    "core": {
        "ctrl_break_ret75",
        "confirm_disp_break_ret75",
        "displace_break_ret75",
        "displace_break_time3",
        "vol_expand_break_ret75",
    },
    "tight": {
        "confirm_disp_break_ret75",
        "displace_break_ret75",
        "displace_break_time3",
    },
}


@dataclass(frozen=True)
class SelectorTrade:
    selector_id: str
    lane_id: str
    entry_idx: int
    exit_idx: int
    pnl_usd: float
    hold_bars: int


def score_recent(trades: list[Trade], before_idx: int, window_trades: int, min_samples: int) -> tuple[float, int]:
    closed = [trade for trade in trades if trade.exit_idx < before_idx]
    if len(closed) < min_samples:
        return float("-inf"), len(closed)
    recent = closed[-window_trades:]
    return mean(trade.pnl_usd for trade in recent), len(recent)


def summarize(selected: list[SelectorTrade]) -> dict:
    wins = [trade for trade in selected if trade.pnl_usd > 0]
    return {
        "trades": len(selected),
        "net_usd": sum(trade.pnl_usd for trade in selected),
        "exp_usd": (sum(trade.pnl_usd for trade in selected) / len(selected)) if selected else 0.0,
        "wr": (len(wins) / len(selected) * 100.0) if selected else 0.0,
        "avg_hold": mean(trade.hold_bars for trade in selected) if selected else 0.0,
    }


def evaluate_fixed_lane(trades: list[Trade], warmup_idx: int) -> list[SelectorTrade]:
    return [
        SelectorTrade(
            selector_id="fixed",
            lane_id=trade.lane_id,
            entry_idx=trade.entry_idx,
            exit_idx=trade.exit_idx,
            pnl_usd=trade.pnl_usd,
            hold_bars=trade.hold_bars,
        )
        for trade in trades
        if trade.entry_idx >= warmup_idx
    ]


def evaluate_opportunity_selector(
    lane_trades: dict[str, list[Trade]],
    warmup_idx: int,
    window_trades: int,
    min_samples: int,
) -> list[SelectorTrade]:
    grouped: dict[int, list[Trade]] = defaultdict(list)
    for trades in lane_trades.values():
        for trade in trades:
            if trade.entry_idx >= warmup_idx:
                grouped[trade.entry_idx].append(trade)

    selected: list[SelectorTrade] = []
    cursor = warmup_idx - 1
    for entry_idx in sorted(grouped):
        if entry_idx <= cursor:
            continue
        candidates = grouped[entry_idx]
        ranked: list[tuple[float, int, int, Trade]] = []
        for trade in candidates:
            score, samples = score_recent(lane_trades[trade.lane_id], entry_idx, window_trades, min_samples)
            ranked.append((score, samples, -trade.hold_bars, trade))
        ranked.sort(reverse=True, key=lambda item: (item[0], item[1], item[2], item[3].lane_id))

        chosen = None
        if ranked and ranked[0][0] != float("-inf"):
            chosen = ranked[0][3]
        else:
            chosen = next((trade for trade in candidates if trade.lane_id == FALLBACK_LANE_ID), None)
            if chosen is None:
                chosen = max(candidates, key=lambda trade: trade.pnl_usd)

        selected.append(
            SelectorTrade(
                selector_id=f"opp_recent{window_trades}",
                lane_id=chosen.lane_id,
                entry_idx=chosen.entry_idx,
                exit_idx=chosen.exit_idx,
                pnl_usd=chosen.pnl_usd,
                hold_bars=chosen.hold_bars,
            )
        )
        cursor = chosen.exit_idx
    return selected


def evaluate_owner_selector(
    lane_trades: dict[str, list[Trade]],
    warmup_idx: int,
    window_trades: int,
    min_samples: int,
) -> list[SelectorTrade]:
    next_positions = {lane_id: 0 for lane_id in lane_trades}
    selected: list[SelectorTrade] = []
    cursor = warmup_idx - 1

    while True:
        owner_rank: list[tuple[float, int, str]] = []
        for lane_id, trades in lane_trades.items():
            score, samples = score_recent(trades, cursor + 1, window_trades, min_samples)
            owner_rank.append((score, samples, lane_id))
        owner_rank.sort(reverse=True, key=lambda item: (item[0], item[1], item[2]))

        chosen_lane_id = owner_rank[0][2] if owner_rank and owner_rank[0][0] != float("-inf") else FALLBACK_LANE_ID
        trades = lane_trades.get(chosen_lane_id, [])
        pos = next_positions.get(chosen_lane_id, 0)
        while pos < len(trades) and trades[pos].entry_idx <= cursor:
            pos += 1
        next_positions[chosen_lane_id] = pos
        if pos >= len(trades):
            break

        chosen = trades[pos]
        if chosen.entry_idx < warmup_idx:
            cursor = chosen.exit_idx
            next_positions[chosen_lane_id] = pos + 1
            continue

        selected.append(
            SelectorTrade(
                selector_id=f"owner_recent{window_trades}",
                lane_id=chosen.lane_id,
                entry_idx=chosen.entry_idx,
                exit_idx=chosen.exit_idx,
                pnl_usd=chosen.pnl_usd,
                hold_bars=chosen.hold_bars,
            )
        )
        cursor = chosen.exit_idx
        next_positions[chosen_lane_id] = pos + 1
    return selected


def print_summary_table(title: str, rows: list[tuple[str, dict, Counter[str]]]) -> None:
    print(title)
    print(f"{'strategy':<28} {'trades':>6} {'wr%':>7} {'net_usd':>9} {'exp_usd':>9} {'avg_hold':>9} {'top_lanes':<28}")
    print("-" * 110)
    for label, stats, picks in rows:
        top = ", ".join(f"{lane}:{count}" for lane, count in picks.most_common(3)) if picks else "-"
        print(
            f"{label:<28} {stats['trades']:>6d} {stats['wr']:>6.1f}% "
            f"{stats['net_usd']:>+9.2f} {stats['exp_usd']:>+9.2f} {stats['avg_hold']:>9.1f} {top:<28}"
        )
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Walk-forward recency selector for USDJPY asymmetry lanes")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--spread-pips", type=float, default=SPREAD_PIPS)
    parser.add_argument("--warmup-days", type=int, default=DEFAULT_WARMUP_DAYS)
    parser.add_argument("--window-trades", type=int, default=DEFAULT_WINDOW_TRADES)
    parser.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    parser.add_argument("--lane-pool", choices=tuple(LANE_POOLS), default="all")
    args = parser.parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        bars = load_bars(args.days)
        if not bars:
            print("No USDJPY bars loaded")
            return 1

        warmup_idx = min(len(bars) - 1, max(0, args.warmup_days * 1440))
        lanes = build_lanes()
        pool = LANE_POOLS[args.lane_pool]
        if pool is not None:
            lanes = [lane for lane in lanes if lane.lane_id in pool]
        lane_trades: dict[str, list[Trade]] = {
            lane.lane_id: simulate_lane(bars, lane, args.spread_pips)
            for lane in lanes
        }

        rows: list[tuple[str, dict, Counter[str]]] = []
        for lane in lanes:
            fixed = evaluate_fixed_lane(lane_trades[lane.lane_id], warmup_idx)
            rows.append((lane.lane_id, summarize(fixed), Counter(trade.lane_id for trade in fixed)))

        opp = evaluate_opportunity_selector(lane_trades, warmup_idx, args.window_trades, args.min_samples)
        owner = evaluate_owner_selector(lane_trades, warmup_idx, args.window_trades, args.min_samples)
        rows.extend(
            [
                (f"opp_recent{args.window_trades}", summarize(opp), Counter(trade.lane_id for trade in opp)),
                (f"owner_recent{args.window_trades}", summarize(owner), Counter(trade.lane_id for trade in owner)),
            ]
        )

        rows.sort(key=lambda item: (item[1]["exp_usd"], item[1]["net_usd"]), reverse=True)
        print(
            f"USDJPY recency selector lab | days={args.days} | spread={args.spread_pips:.2f} pips | "
            f"warmup={args.warmup_days}d | window={args.window_trades} trades | min_samples={args.min_samples} | "
            f"pool={args.lane_pool}"
        )
        print()
        print_summary_table("Walk-forward comparison", rows)

        best = rows[0]
        print("Best performer")
        print(
            f"- {best[0]} | trades={best[1]['trades']} | wr={best[1]['wr']:.1f}% | "
            f"net={best[1]['net_usd']:+.2f} | exp={best[1]['exp_usd']:+.3f}"
        )
        print()
        print("Selector detail")
        for label, stats, picks in rows:
            if not label.startswith(("opp_recent", "owner_recent")):
                continue
            print(
                f"- {label}: net={stats['net_usd']:+.2f}, exp={stats['exp_usd']:+.3f}, "
                f"picks={dict(picks.most_common(5))}"
            )
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
