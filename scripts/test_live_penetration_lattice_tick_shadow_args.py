#!/usr/bin/env python3
from __future__ import annotations

import sys

from live_penetration_lattice_tick_shadow import parse_args


def test_parse_args_accepts_adaptive_overlay_flags() -> None:
    argv_before = list(sys.argv)
    try:
        sys.argv = [
            "live_penetration_lattice_tick_shadow.py",
            "--symbols",
            "EURUSD",
            "GBPUSD",
            "--state-path",
            "reports/test_state.json",
            "--event-path",
            "reports/test_events.jsonl",
            "--cluster-aware-escape",
            "--cluster-fill-tolerance",
            "0.0002",
            "--guard-open-admission",
            "--suppress-additional-levels-after-burst",
            "--burst-open-threshold",
            "3",
            "--max-entry-spread-ratio",
            "0.3",
            "--liquidity-gap-spread-multiplier",
            "2.5",
            "--liquidity-gap-spread-lookback",
            "60",
            "--liquidity-gap-spread-floor-ratio",
            "1.0",
            "--adaptive-overlay-autopilot",
            "--proven-step-ceiling",
            "0.0003",
            "--proven-step-buy-ceiling",
            "0.0004",
            "--proven-step-sell-ceiling",
            "0.0002",
        ]
        args = parse_args()
    finally:
        sys.argv = argv_before

    assert args.cluster_aware_escape is True
    assert abs(float(args.cluster_fill_tolerance) - 0.0002) < 1e-9
    assert args.guard_open_admission is True
    assert args.suppress_additional_levels_after_burst is True
    assert int(args.burst_open_threshold) == 3
    assert abs(float(args.max_entry_spread_ratio) - 0.3) < 1e-9
    assert abs(float(args.liquidity_gap_spread_multiplier) - 2.5) < 1e-9
    assert int(args.liquidity_gap_spread_lookback) == 60
    assert abs(float(args.liquidity_gap_spread_floor_ratio) - 1.0) < 1e-9
    assert args.adaptive_overlay_autopilot is True
    assert abs(float(args.proven_step_ceiling) - 0.0003) < 1e-12
    assert abs(float(args.proven_step_buy_ceiling) - 0.0004) < 1e-12
    assert abs(float(args.proven_step_sell_ceiling) - 0.0002) < 1e-12


if __name__ == "__main__":
    test_parse_args_accepts_adaptive_overlay_flags()
    print("ok")
