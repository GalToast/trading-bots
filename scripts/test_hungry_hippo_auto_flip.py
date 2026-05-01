#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import hungry_hippo_auto_flip as auto_flip


class HungryHippoAutoFlipTests(unittest.TestCase):
    def test_compute_flipped_config_updates_nested_geometry(self) -> None:
        config = {
            "symbol": "EURUSD",
            "geometry": {
                "step": 0.0004,
                "step_buy": 0.0006,
                "step_sell": 0.0003,
                "asymmetric": True,
                "asymmetry_ratio": 2.0,
            },
        }

        flipped = auto_flip.compute_flipped_config(config, "SELL-tight", 0.0)

        self.assertEqual(flipped["geometry"]["step_buy"], 0.0003)
        self.assertEqual(flipped["geometry"]["step_sell"], 0.0006)
        self.assertTrue(flipped["_auto_flipped"])
        self.assertEqual(flipped["step_buy"], 0.0003)
        self.assertEqual(flipped["step_sell"], 0.0006)


if __name__ == "__main__":
    unittest.main()
