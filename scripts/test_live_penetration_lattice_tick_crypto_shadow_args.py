#!/usr/bin/env python3
from __future__ import annotations

import sys

try:
    from live_penetration_lattice_tick_crypto_shadow import parse_args
except ModuleNotFoundError:
    from scripts.live_penetration_lattice_tick_crypto_shadow import parse_args


def test_parse_args_accepts_offensive_budget_share() -> None:
    argv_before = list(sys.argv)
    try:
        sys.argv = [
            "live_penetration_lattice_tick_crypto_shadow.py",
            "--timeframe",
            "M15",
            "--step",
            "259.43",
            "--state-path",
            "reports/test_state.json",
            "--event-path",
            "reports/test_events.jsonl",
            "--guard-open-admission",
            "--offensive-closure",
            "--offensive-budget-share",
            "0.4",
        ]
        args = parse_args()
    finally:
        sys.argv = argv_before

    assert args.offensive_closure is True
    assert args.guard_open_admission is True
    assert abs(float(args.offensive_budget_share) - 0.4) < 1e-9


def test_parse_args_accepts_burst_suppression_flags() -> None:
    argv_before = list(sys.argv)
    try:
        sys.argv = [
            "live_penetration_lattice_tick_crypto_shadow.py",
            "--timeframe",
            "M15",
            "--step",
            "259.43",
            "--state-path",
            "reports/test_state.json",
            "--event-path",
            "reports/test_events.jsonl",
            "--suppress-additional-levels-after-burst",
            "--burst-open-threshold",
            "3",
        ]
        args = parse_args()
    finally:
        sys.argv = argv_before

    assert args.suppress_additional_levels_after_burst is True
    assert int(args.burst_open_threshold) == 3


def test_parse_args_accepts_max_entry_spread_ratio() -> None:
    argv_before = list(sys.argv)
    try:
        sys.argv = [
            "live_penetration_lattice_tick_crypto_shadow.py",
            "--timeframe",
            "M15",
            "--step",
            "75",
            "--state-path",
            "reports/test_state.json",
            "--event-path",
            "reports/test_events.jsonl",
            "--max-entry-spread-ratio",
            "0.3",
        ]
        args = parse_args()
    finally:
        sys.argv = argv_before

    assert abs(float(args.max_entry_spread_ratio) - 0.3) < 1e-9


def test_parse_args_accepts_liquidity_gap_spread_flags() -> None:
    argv_before = list(sys.argv)
    try:
        sys.argv = [
            "live_penetration_lattice_tick_crypto_shadow.py",
            "--timeframe",
            "M15",
            "--step",
            "75",
            "--state-path",
            "reports/test_state.json",
            "--event-path",
            "reports/test_events.jsonl",
            "--max-entry-spread-ratio",
            "0.0",
            "--liquidity-gap-spread-multiplier",
            "2.5",
            "--liquidity-gap-spread-lookback",
            "60",
            "--liquidity-gap-spread-floor-ratio",
            "1.0",
            "--liquidity-gap-spread-max-ratio",
            "4.0",
        ]
        args = parse_args()
    finally:
        sys.argv = argv_before

    assert abs(float(args.max_entry_spread_ratio) - 0.0) < 1e-9
    assert abs(float(args.liquidity_gap_spread_multiplier) - 2.5) < 1e-9
    assert int(args.liquidity_gap_spread_lookback) == 60
    assert abs(float(args.liquidity_gap_spread_floor_ratio) - 1.0) < 1e-9
    assert abs(float(args.liquidity_gap_spread_max_ratio) - 4.0) < 1e-9


def test_parse_args_accepts_adaptive_overlay_autopilot() -> None:
    argv_before = list(sys.argv)
    try:
        sys.argv = [
            "live_penetration_lattice_tick_crypto_shadow.py",
            "--timeframe",
            "M15",
            "--step",
            "0.00030",
            "--state-path",
            "reports/test_state.json",
            "--event-path",
            "reports/test_events.jsonl",
            "--adaptive-overlay-autopilot",
        ]
        args = parse_args()
    finally:
        sys.argv = argv_before

    assert args.adaptive_overlay_autopilot is True


def test_parse_args_accepts_hybrid_raw_close_style() -> None:
    argv_before = list(sys.argv)
    try:
        sys.argv = [
            "live_penetration_lattice_tick_crypto_shadow.py",
            "--timeframe",
            "M15",
            "--step",
            "0.000619",
            "--state-path",
            "reports/test_state.json",
            "--event-path",
            "reports/test_events.jsonl",
            "--raw-close-style",
            "harvest_inner_hold_frontier",
        ]
        args = parse_args()
    finally:
        sys.argv = argv_before

    assert args.raw_close_style == "harvest_inner_hold_frontier"


def test_parse_args_accepts_session_gate() -> None:
    argv_before = list(sys.argv)
    try:
        sys.argv = [
            "live_penetration_lattice_tick_crypto_shadow.py",
            "--timeframe",
            "M15",
            "--step",
            "0.000619",
            "--state-path",
            "reports/test_state.json",
            "--event-path",
            "reports/test_events.jsonl",
            "--session-gate",
        ]
        args = parse_args()
    finally:
        sys.argv = argv_before

    assert args.session_gate is True


if __name__ == "__main__":
    test_parse_args_accepts_offensive_budget_share()
    test_parse_args_accepts_burst_suppression_flags()
    test_parse_args_accepts_max_entry_spread_ratio()
    test_parse_args_accepts_liquidity_gap_spread_flags()
    test_parse_args_accepts_adaptive_overlay_autopilot()
    test_parse_args_accepts_hybrid_raw_close_style()
    test_parse_args_accepts_session_gate()
    print("ok")
