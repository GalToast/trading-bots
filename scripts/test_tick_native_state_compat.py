#!/usr/bin/env python3
from __future__ import annotations

import MetaTrader5 as mt5

from live_penetration_lattice_shadow import default_apex_mix
from tick_penetration_lattice_core import bounded_engine_from_args, engine_from_args


def main() -> int:
    if not mt5.initialize():
        raise SystemExit("MetaTrader5 initialize() failed")
    try:
        raw = engine_from_args(
            symbol="EURUSD",
            timeframe_name="M1",
            step=0.0003,
            max_open_per_side=20,
            variant_name="rearm_lvl2_exc2",
            momentum_gate=False,
            cooldown_bars=0,
            sell_gap=1,
            buy_gap=1,
        )
        raw.load_snapshot(
            {
                "anchor": 1.1700,
                "next_sell_level": 1.1703,
                "next_buy_level": 1.1697,
                "open_tickets": [
                    {
                        "direction": "BUY",
                        "entry_price": 1.1697,
                        "opened_time": 123,
                        "level_idx": 2,
                    }
                ],
                "rearm_tokens": [
                    {
                        "direction": "BUY",
                        "level": 1.1697,
                        "level_idx": 2,
                        "armed": True,
                    }
                ],
            }
        )
        raw_snap = raw.snapshot()
        assert abs(raw.state.open_tickets[0]["trigger_level"] - 1.1697) < 1e-9
        assert abs(raw_snap["open_tickets"][0]["entry_price"] - 1.1697) < 1e-9
        assert raw_snap["open_realism_mode"] == "tick_native"
        assert float(raw_snap["reconcile_open_max_drift_px"]) > 0.0

        fixed_raw = engine_from_args(
            symbol="EURUSD",
            timeframe_name="M1",
            step=0.0003,
            max_open_per_side=20,
            variant_name="rearm_lvl2_exc2",
            momentum_gate=False,
            cooldown_bars=0,
            sell_gap=1,
            buy_gap=1,
            allow_dynamic_geometry=False,
        )
        fixed_raw.load_snapshot(
            {
                "anchor": 1.1700,
                "next_sell_level": 1.1703,
                "next_buy_level": 1.1697,
                "base_step_sell_px": 0.0009,
                "base_step_buy_px": 0.0001,
            }
        )
        assert abs(fixed_raw.base_step_sell_px - 0.0003) < 1e-9
        assert abs(fixed_raw.base_step_buy_px - 0.0003) < 1e-9

        bounded_cfg = default_apex_mix()["USDJPY"][1]
        bounded = bounded_engine_from_args(
            symbol="USDJPY",
            timeframe_name="M1",
            cfg=bounded_cfg,
            variant_name="rearm_lvl2_exc2",
            close_gap=1,
        )
        bounded.load_snapshot(
            {
                "anchor": 159.300,
                "next_sell_level": 159.305,
                "next_buy_level": 159.295,
                "open_tickets": [
                    {
                        "direction": "SELL",
                        "entry_price": 159.305,
                        "opened_time": 456,
                        "level_idx": 1,
                    }
                ],
            }
        )
        bounded_snap = bounded.snapshot()
        assert abs(bounded.state.open_tickets[0]["trigger_level"] - 159.305) < 1e-9
        assert abs(bounded_snap["open_tickets"][0]["entry_price"] - 159.305) < 1e-9
        assert bounded_snap["close_realism_mode"] == "tick_native"
        assert float(bounded_snap["reconcile_open_max_drift_px"]) > 0.0
        print("ok")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
