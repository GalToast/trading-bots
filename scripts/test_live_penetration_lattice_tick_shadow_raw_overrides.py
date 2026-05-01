#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from live_penetration_lattice_shadow import RawConfig
from live_penetration_lattice_tick_shadow import default_raw_gap_for_cfg, load_raw_symbol_overrides


def test_load_raw_symbol_overrides() -> None:
    overrides = load_raw_symbol_overrides(
        Path("configs/fx_raw_symbol_overrides_close_policy_mixed.json")
    )
    assert overrides == {
        "EURUSD": {
            "raw_close_alpha": 0.5,
            "raw_close_style": "outer",
            "raw_sell_gap": 2,
            "raw_buy_gap": 2,
        },
        "GBPUSD": {
            "raw_close_alpha": 0.5,
            "raw_close_style": "all_profitable",
            "raw_sell_gap": 1,
            "raw_buy_gap": 1,
        },
    }


def test_default_raw_gap_for_cfg_uses_close_mode_when_no_explicit_gap() -> None:
    cfg = RawConfig(step_pips=2.0, max_open_per_side=20, close_mode="two_level")
    assert default_raw_gap_for_cfg(cfg, side="sell") == 2
    assert default_raw_gap_for_cfg(cfg, side="buy") == 2


def test_default_raw_gap_for_cfg_prefers_explicit_side_gap() -> None:
    cfg = RawConfig(step_pips=2.0, max_open_per_side=20, close_mode="one_level")
    object.__setattr__(cfg, "sell_gap", 3)
    object.__setattr__(cfg, "buy_gap", 1)
    assert default_raw_gap_for_cfg(cfg, side="sell") == 3
    assert default_raw_gap_for_cfg(cfg, side="buy") == 1


if __name__ == "__main__":
    test_load_raw_symbol_overrides()
    test_default_raw_gap_for_cfg_uses_close_mode_when_no_explicit_gap()
    test_default_raw_gap_for_cfg_prefers_explicit_side_gap()
    print("ok")
