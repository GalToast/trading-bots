#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_universal_hungry_hippo_configs as universal


class BuildUniversalHungryHippoConfigsTests(unittest.TestCase):
    def test_control_mode_maps_to_real_regime_bucket(self) -> None:
        self.assertEqual(universal.map_control_mode_to_regime_key("trend_follow", "trending"), "TREND")
        self.assertEqual(universal.map_control_mode_to_regime_key("wait_extreme_confirmation", "trending"), "EXTREME")
        self.assertEqual(universal.map_control_mode_to_regime_key("unknown", "ranging"), "CHOP")

    def test_build_symbol_config_can_fallback_without_extreme_results(self) -> None:
        config = universal.build_symbol_config(
            "AUDUSD",
            {"symbols": {}},
            {"symbols": [{"symbol": "AUDUSD", "atr_current": 0.001, "max_open_per_side": 12}]},
            {"rows": [{"symbol": "AUDUSD", "control_mode": "trend_follow", "normalized_regime": "trending"}]},
        )
        self.assertIsNotNone(config)
        self.assertEqual(config["symbol"], "AUDUSD")
        self.assertEqual(config["regime"]["regime"], "TREND")
        self.assertTrue(config["deployable"])


if __name__ == "__main__":
    unittest.main()
