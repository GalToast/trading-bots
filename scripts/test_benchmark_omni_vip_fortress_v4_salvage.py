#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark_omni_vip_fortress_v4_salvage import btc_gate_pass, gate_pass, magnetic_pass


class OmniVIPFortressV4SalvageTests(unittest.TestCase):
    def test_magnetic_pass_uses_round_number_proximity(self) -> None:
        self.assertTrue(magnetic_pass(0.2501, 0.005))
        self.assertFalse(magnetic_pass(0.263, 0.0025))

    def test_btc_gate_pass_uses_same_timestamp_lookup(self) -> None:
        btc_lookup = {
            100: {"time": 100, "open": 70000.0, "close": 70006.0},
            200: {"time": 200, "open": 70000.0, "close": 70003.0},
        }
        self.assertTrue(btc_gate_pass(btc_lookup, 100, 5.0))
        self.assertFalse(btc_gate_pass(btc_lookup, 200, 5.0))
        self.assertFalse(btc_gate_pass(btc_lookup, 300, 5.0))

    def test_combo_gate_requires_all_components(self) -> None:
        btc_lookup = {100: {"time": 100, "open": 70000.0, "close": 70006.0}}
        candle = {"time": 100, "open": 0.2498, "close": 0.2501}
        self.assertTrue(gate_pass("combo", candle, btc_lookup=btc_lookup, magnetic_proximity=0.005, btc_threshold_usd=5.0))
        self.assertFalse(gate_pass("combo", {"time": 100, "open": 0.2502, "close": 0.2495}, btc_lookup=btc_lookup, magnetic_proximity=0.005, btc_threshold_usd=5.0))


if __name__ == "__main__":
    unittest.main()
