#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from live_coinbase_futures_tick_shadow import CoinbaseFuturesTickShadowEngine, future_pnl_usd


class CoinbaseFuturesShadowTests(unittest.TestCase):
    def test_future_pnl_usd_buy_and_sell(self) -> None:
        self.assertAlmostEqual(future_pnl_usd("BUY", 100.0, 110.0, 0.01, 1), 0.1)
        self.assertAlmostEqual(future_pnl_usd("SELL", 110.0, 100.0, 0.01, 1), 0.1)

    def test_engine_opens_and_closes(self) -> None:
        engine = CoinbaseFuturesTickShadowEngine(
            product_id="BIP-20DEC30-CDE",
            timeframe_name="H1",
            step=10.0,
            max_open_per_side=5,
            variant_name="rearm_lvl2_exc2",
            momentum_gate=False,
            sell_gap=1,
            buy_gap=1,
            contracts=1,
            contract_size=0.01,
        )
        ticks = [
            {"time": 1, "time_msc": 1000, "bid": 100.0, "ask": 100.1},
            {"time": 2, "time_msc": 2000, "bid": 120.1, "ask": 120.2},
            {"time": 3, "time_msc": 3000, "bid": 109.9, "ask": 110.0},
        ]
        for tick in ticks:
            engine.process_tick(tick, event_path=None, emit=False)
        self.assertGreaterEqual(engine.state.realized_closes, 1)
        self.assertTrue(float(engine.state.realized_net_usd) > 0.0)


if __name__ == "__main__":
    unittest.main()
