#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from dataclasses import asdict
from statistics import mean

import MetaTrader5 as mt5

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.usdjpy_asymmetry_lab import Lane, load_bars, simulate_lane, summarize


SPREAD_PIPS = 0.6
WINDOWS = (20, 30, 60)


def candidate_lanes(mode: str) -> list[Lane]:
    lanes: list[Lane] = []
    if mode == "tiny":
        confirm_pips_values = (1.5, 2.0, 2.5)
        confirm_window_values = (1, 2)
        range_expansion_values = (1.4, 1.6)
        body_pips_values = (3.5, 4.0)
        body_ratio_values = (0.70, 0.75)
        burst_values = (1.05, 1.10)
    elif mode == "narrow":
        confirm_pips_values = (1.5, 2.0, 2.5)
        confirm_window_values = (1, 2, 3)
        range_expansion_values = (1.4, 1.6, 1.8)
        body_pips_values = (3.5, 4.0, 4.5)
        body_ratio_values = (0.70, 0.75, 0.80)
        burst_values = (1.05, 1.10)
    else:
        confirm_pips_values = (1.0, 1.5, 2.0, 2.5, 3.0)
        confirm_window_values = (1, 2, 3)
        range_expansion_values = (1.2, 1.4, 1.6, 1.8)
        body_pips_values = (3.5, 4.0, 4.5, 5.0)
        body_ratio_values = (0.70, 0.75, 0.80)
        burst_values = (1.05, 1.10, 1.20)

    for confirm_pips in confirm_pips_values:
        for confirm_window in confirm_window_values:
            for min_range_expansion in range_expansion_values:
                for min_body_pips in body_pips_values:
                    for body_ratio in body_ratio_values:
                        for burst in burst_values:
                            lane_id = (
                                f"cd_{str(confirm_pips).replace('.', '')}"
                                f"_w{confirm_window}"
                                f"_rx{int(min_range_expansion*10)}"
                                f"_b{str(min_body_pips).replace('.', '')}"
                                f"_r{int(body_ratio*100)}"
                                f"_v{int(burst*100)}"
                            )
                            lanes.append(
                                Lane(
                                    lane_id=lane_id,
                                    entry_kind="confirmed_displacement",
                                    exit_kind="retain_75",
                                    hypothesis="confirmed displacement sweep",
                                    lookback=8,
                                    min_body_pips=min_body_pips,
                                    min_body_ratio=body_ratio,
                                    volume_burst_ratio=burst,
                                    min_range_expansion=min_range_expansion,
                                    confirm_pips=confirm_pips,
                                    confirm_window_bars=confirm_window,
                                    max_hold_bars=6,
                                    floor_pips=0.5,
                                )
                            )
    return lanes


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Sweep confirmed displacement parameters")
    parser.add_argument("--mode", choices=("tiny", "narrow", "full"), default="narrow")
    args = parser.parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        bars_60 = load_bars(60)
        if not bars_60:
            print("No USDJPY bars loaded")
            return 1

        ranked: list[tuple[float, float, float, int, Lane, dict[int, dict]]] = []
        for lane in candidate_lanes(args.mode):
            by_window: dict[int, dict] = {}
            valid = True
            for window in WINDOWS:
                sub = bars_60[-1440 * window :]
                stats = summarize(simulate_lane(sub, lane, SPREAD_PIPS), window)
                by_window[window] = stats
                if stats["trades"] < 25:
                    valid = False
            if not valid:
                continue
            exp_values = [by_window[w]["exp_usd"] for w in WINDOWS]
            trade_count = by_window[60]["trades"]
            ranked.append(
                (
                    min(exp_values),
                    mean(exp_values),
                    by_window[60]["exp_usd"],
                    trade_count,
                    lane,
                    by_window,
                )
            )

        ranked.sort(reverse=True, key=lambda row: (row[0], row[1], row[2], row[3]))

        print("USDJPY confirmed-displacement sweep")
        print(f"Spread: {SPREAD_PIPS:.2f} pips | Windows: {WINDOWS} days")
        print()
        print(
            f"{'lane':<28} {'min_exp':>8} {'avg_exp':>8} {'exp60':>8} "
            f"{'tr20':>6} {'tr30':>6} {'tr60':>6}"
        )
        print("-" * 84)
        for min_exp, avg_exp, exp60, _, lane, by_window in ranked[:25]:
            print(
                f"{lane.lane_id:<28} "
                f"{min_exp:>+8.3f} "
                f"{avg_exp:>+8.3f} "
                f"{exp60:>+8.3f} "
                f"{by_window[20]['trades']:>6d} "
                f"{by_window[30]['trades']:>6d} "
                f"{by_window[60]['trades']:>6d}"
            )

        positives = [row for row in ranked if row[0] > 0]
        print()
        print(f"Stable positive candidates across all windows: {len(positives)}")
        for min_exp, avg_exp, exp60, _, lane, by_window in positives[:10]:
            print(
                f"- {lane.lane_id}: min_exp={min_exp:+.3f}, avg_exp={avg_exp:+.3f}, exp60={exp60:+.3f} | "
                f"confirm={lane.confirm_pips}p window={lane.confirm_window_bars} "
                f"range_x={lane.min_range_expansion} body={lane.min_body_pips} "
                f"ratio={lane.min_body_ratio} burst={lane.volume_burst_ratio}"
            )
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
