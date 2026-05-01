#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import burst_fade_god_reclaimer_live as god_reclaimer
from burst_fade_god_reclaimer_live import GodReclaimerShadowEngine


class GodReclaimerShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._products = list(god_reclaimer.PRODUCTS)
        self._product_params = deepcopy(god_reclaimer.PRODUCT_PARAMS)

    def tearDown(self) -> None:
        god_reclaimer.PRODUCTS[:] = self._products
        god_reclaimer.PRODUCT_PARAMS.clear()
        god_reclaimer.PRODUCT_PARAMS.update(self._product_params)

    def test_opens_long_only_reclaim_after_downside_flush(self) -> None:
        engine = GodReclaimerShadowEngine(starting_cash=48.0, max_concurrent=1, reclaim_floor=0.6)
        engine.last_close_by_pid["TEST-USD"] = 100.0
        god_reclaimer.PRODUCTS[:] = ["TEST-USD"]
        god_reclaimer.PRODUCT_PARAMS.clear()
        god_reclaimer.PRODUCT_PARAMS["TEST-USD"] = {"bt": 2.0, "t": 1.0, "s": 0.3}
        engine.process_tick({
            "TEST-USD": [{
                "start": 1,
                "open": 100.0,
                "high": 100.2,
                "low": 96.5,
                "close": 98.8,
            }]
        })
        self.assertEqual(len(engine.positions), 1)
        pos = engine.positions[0]
        self.assertGreater(pos["target"], pos["entry"])
        self.assertLess(pos["stop"], pos["entry"])

    def test_closes_target_with_positive_pnl(self) -> None:
        engine = GodReclaimerShadowEngine(starting_cash=48.0, max_concurrent=1, reclaim_floor=0.6)
        engine.positions = [{
            "pid": "TEST-USD",
            "entry": 100.0,
            "target": 105.0,
            "stop": 97.0,
            "quote": 47.5,
            "flush_pct": 3.0,
            "reclaim_pct": 1.5,
            "close_location": 0.7,
        }]
        engine.process_tick({
            "TEST-USD": [{
                "start": 2,
                "open": 100.0,
                "high": 105.5,
                "low": 99.0,
                "close": 104.0,
            }]
        })
        self.assertEqual(len(engine.positions), 0)
        self.assertEqual(engine.realized_closes, 1)
        self.assertGreater(engine.realized_net, 0.0)


if __name__ == "__main__":
    unittest.main()
