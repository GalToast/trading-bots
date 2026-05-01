#!/usr/bin/env python3
from __future__ import annotations

import inspect
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from tick_penetration_lattice_core import (
    engine_from_args,
    normalize_raw_close_style,
    offensive_budget_cap_usd,
    offensive_budget_remaining_usd,
    select_close_positions,
)


def test_normalize_raw_close_style() -> None:
    assert normalize_raw_close_style("outer") == "outer"
    assert normalize_raw_close_style("INNER") == "inner"
    assert normalize_raw_close_style(None) == "all_profitable"
    assert normalize_raw_close_style("harvest_inner_hold_frontier") == "harvest_inner_hold_frontier"


def test_select_close_positions() -> None:
    assert select_close_positions(5, 2, "outer", [0, 2, 3]) == [0]
    assert select_close_positions(5, 2, "inner", [0, 1, 4]) == [1]
    assert select_close_positions(5, 2, "all_profitable", [0, 2, 4]) == [0, 2, 4]
    assert select_close_positions(5, 2, "outer", [2, 3]) == []
    assert select_close_positions(5, 1, "harvest_inner_hold_frontier", [0, 1, 2, 3]) == [1, 2, 3]
    assert select_close_positions(5, 1, "stack_depth_scaled_gap", [0, 1, 2, 3]) == [1, 2, 3]
    assert select_close_positions(9, 1, "stack_depth_scaled_gap", [0, 1, 2, 3]) == [2, 3]
    assert select_close_positions(3, 1, "range_sweep_trend_reclaim", [0, 1, 2]) == [0, 1, 2]
    assert select_close_positions(5, 1, "range_sweep_trend_reclaim", [0, 1, 2]) == [0]


def test_offensive_budget_helpers() -> None:
    assert offensive_budget_cap_usd(100.0, 0.25) == 25.0
    assert offensive_budget_remaining_usd(100.0, 7.5, 0.25) == 17.5
    assert offensive_budget_remaining_usd(10.0, 20.0, 0.25) == 0.0


def test_engine_from_args_exposes_offensive_budget_share() -> None:
    params = inspect.signature(engine_from_args).parameters
    assert "offensive_budget_share" in params
    assert "offensive_closure_enabled" in params
    assert "guard_open_admission" in params


if __name__ == "__main__":
    test_normalize_raw_close_style()
    test_select_close_positions()
    test_offensive_budget_helpers()
    test_engine_from_args_exposes_offensive_budget_share()
    print("ok")
