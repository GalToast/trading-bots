#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_kraken_grid_optimizer as optimizer


def candle(t: float, o: float, h: float, l: float, c: float) -> optimizer.Candle:
    return optimizer.Candle(t=t, o=o, h=h, l=l, c=c, v=1.0)


class KrakenGridOptimizerTests(unittest.TestCase):
    def test_same_candle_buy_and_high_does_not_close_new_position(self) -> None:
        candles = [
            candle(0, 100, 100, 100, 100),
            candle(60, 100, 105, 95, 100),
        ]

        result = optimizer.simulate_grid(
            candles,
            spacing_bps=500.0,
            levels=1,
            entry_offset_mult=1.0,
            initial_capital=100.0,
            maker_fee_bps=0.0,
            exit_fee_bps=0.0,
            liquidation_fee_bps=0.0,
            mark_haircut_bps=0.0,
            enable_inventory_sweeps=False,
            sweep_min_inventory_net_bps=0.0,
            sweep_max_inventory_pct=50.0,
            sweep_cooldown_candles=0,
            force_final_liquidation=False,
            recenter_when_flat=True,
        )

        self.assertEqual(result["buys"], 1)
        self.assertEqual(result["closes"], 0)
        self.assertEqual(result["open_positions"], 1)

    def test_prior_open_position_can_close_on_later_high(self) -> None:
        candles = [
            candle(0, 100, 100, 100, 100),
            candle(60, 100, 100, 95, 96),
            candle(120, 96, 105, 96, 104),
        ]

        result = optimizer.simulate_grid(
            candles,
            spacing_bps=500.0,
            levels=1,
            entry_offset_mult=1.0,
            initial_capital=100.0,
            maker_fee_bps=0.0,
            exit_fee_bps=0.0,
            liquidation_fee_bps=0.0,
            mark_haircut_bps=0.0,
            enable_inventory_sweeps=False,
            sweep_min_inventory_net_bps=0.0,
            sweep_max_inventory_pct=50.0,
            sweep_cooldown_candles=0,
            force_final_liquidation=False,
            recenter_when_flat=True,
        )

        self.assertEqual(result["buys"], 1)
        self.assertEqual(result["closes"], 1)
        self.assertEqual(result["open_positions"], 0)
        self.assertAlmostEqual(result["final_return_pct"], 5.0, places=5)

    def test_open_inventory_is_marked_to_liquidation_value(self) -> None:
        candles = [
            candle(0, 100, 100, 100, 100),
            candle(60, 100, 100, 95, 95),
            candle(120, 95, 95, 90, 90),
        ]

        result = optimizer.simulate_grid(
            candles,
            spacing_bps=500.0,
            levels=1,
            entry_offset_mult=1.0,
            initial_capital=100.0,
            maker_fee_bps=0.0,
            exit_fee_bps=0.0,
            liquidation_fee_bps=100.0,
            mark_haircut_bps=0.0,
            enable_inventory_sweeps=False,
            sweep_min_inventory_net_bps=0.0,
            sweep_max_inventory_pct=50.0,
            sweep_cooldown_candles=0,
            force_final_liquidation=False,
            recenter_when_flat=True,
        )

        self.assertEqual(result["closes"], 0)
        self.assertEqual(result["open_positions"], 1)
        self.assertLess(result["final_return_pct"], 0)
        self.assertGreater(result["open_inventory_pct"], 99)

    def test_inventory_sweep_banks_green_open_inventory(self) -> None:
        candles = [
            candle(0, 100, 100, 100, 100),
            candle(60, 100, 100, 95, 101),
        ]

        result = optimizer.simulate_grid(
            candles,
            spacing_bps=500.0,
            levels=1,
            entry_offset_mult=1.0,
            initial_capital=100.0,
            maker_fee_bps=0.0,
            exit_fee_bps=0.0,
            liquidation_fee_bps=0.0,
            mark_haircut_bps=0.0,
            enable_inventory_sweeps=True,
            sweep_min_inventory_net_bps=0.0,
            sweep_max_inventory_pct=50.0,
            sweep_cooldown_candles=0,
            force_final_liquidation=False,
            recenter_when_flat=True,
        )

        self.assertEqual(result["buys"], 1)
        self.assertEqual(result["target_closes"], 0)
        self.assertEqual(result["sweep_closes"], 1)
        self.assertEqual(result["sweep_count"], 1)
        self.assertEqual(result["open_positions"], 0)
        self.assertGreater(result["final_return_pct"], 0)

    def test_build_payload_loads_pulse_cache_and_filters_quotes(self) -> None:
        payload = {
            "version": 1,
            "entries": {
                "AAA-USD|1m|6h": {
                    "candles": [
                        {"start": 60, "open": 1, "high": 1, "low": 1, "close": 1},
                        {"start": 120, "open": 1, "high": 1, "low": 0.95, "close": 0.96},
                        {"start": 180, "open": 0.96, "high": 1.01, "low": 0.96, "close": 1.0},
                    ]
                },
                "BBB-EUR|1m|6h": {
                    "candles": [
                        {"start": 60, "open": 1, "high": 1, "low": 1, "close": 1},
                        {"start": 120, "open": 1, "high": 1, "low": 0.95, "close": 0.96},
                        {"start": 180, "open": 0.96, "high": 1.01, "low": 0.96, "close": 1.0},
                    ]
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "pulse.json"
            cache_path.write_text(json.dumps(payload), encoding="utf-8")
            result = optimizer.build_payload(
                cache_path=cache_path,
                products=set(),
                quote_currencies={"USD"},
                spacings_bps=[500.0],
                levels_values=[1],
                entry_offset_mults=[1.0],
                exit_models=["maker"],
                initial_capital=100.0,
                maker_fee_bps=0.0,
                taker_fee_bps=0.0,
                mark_haircut_bps=0.0,
                min_candles=2,
                min_closes=1,
                max_ending_inventory_pct=25.0,
                max_drawdown_pct=15.0,
                enable_inventory_sweeps=False,
                sweep_min_inventory_net_bps=0.0,
                sweep_max_inventory_pct=50.0,
                sweep_cooldown_candles=0,
                force_final_liquidation=False,
                recenter_when_flat=True,
            )

        self.assertEqual(result["summary"]["products_loaded"], 2)
        self.assertEqual(result["summary"]["products_selected"], 1)
        self.assertEqual(result["rows"][0]["product_id"], "AAA-USD")


if __name__ == "__main__":
    unittest.main()
