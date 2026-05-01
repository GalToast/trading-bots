#!/usr/bin/env python3
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import live_counter_cascade_runner as runner


class LiveCounterCascadeRunnerTests(unittest.TestCase):
    def test_deal_net_usd_sums_all_components(self) -> None:
        fill = {"profit": 1.25, "commission": -0.1, "swap": 0.05, "fee": -0.02}
        self.assertAlmostEqual(runner.deal_net_usd(fill), 1.18, places=6)

    def test_is_exit_deal_accepts_out_and_out_by(self) -> None:
        with (
            patch.object(runner.mt5, "DEAL_ENTRY_OUT", 1, create=True),
            patch.object(runner.mt5, "DEAL_ENTRY_OUT_BY", 3, create=True),
        ):
            self.assertTrue(runner.is_exit_deal({"entry": 1}))
            self.assertTrue(runner.is_exit_deal({"entry": 3}))
            self.assertFalse(runner.is_exit_deal({"entry": 0}))

    def test_live_broker_deals_filters_symbol_magic_and_prefix(self) -> None:
        class Deal(types.SimpleNamespace):
            pass

        deals = [
            Deal(ticket=1, symbol="GBPUSD", magic=941999, comment="CC-S1", entry=0, profit=0.0, commission=0.0, swap=0.0, fee=0.0, type=0, order=10, position_id=10, time=1, time_msc=1, price=1.35),
            Deal(ticket=2, symbol="GBPUSD", magic=941999, comment="CC-exit", entry=1, profit=0.2, commission=-0.02, swap=0.0, fee=0.0, type=1, order=11, position_id=10, time=2, time_msc=2, price=1.35),
            Deal(ticket=3, symbol="EURUSD", magic=941999, comment="CC-exit", entry=1, profit=0.2, commission=0.0, swap=0.0, fee=0.0, type=1, order=12, position_id=12, time=3, time_msc=3, price=1.08),
            Deal(ticket=4, symbol="GBPUSD", magic=12, comment="CC-exit", entry=1, profit=0.2, commission=0.0, swap=0.0, fee=0.0, type=1, order=13, position_id=13, time=4, time_msc=4, price=1.35),
            Deal(ticket=5, symbol="GBPUSD", magic=941999, comment="OTHER", entry=1, profit=0.2, commission=0.0, swap=0.0, fee=0.0, type=1, order=14, position_id=14, time=5, time_msc=5, price=1.35),
        ]

        with patch.object(runner.mt5, "history_deals_get", return_value=deals):
            result = runner.live_broker_deals(
                symbol="GBPUSD",
                live_magic=941999,
                started_at="2026-04-17T00:00:00+00:00",
                comment_prefix="CC",
            )

        self.assertEqual([row["ticket"] for row in result], [1, 2])

    def test_exact_logged_deals_resolves_logged_broker_fills(self) -> None:
        temp_path = SCRIPTS_DIR / "tmp_counter_cascade_exec_log.jsonl"
        temp_path.write_text(
            "\n".join(
                [
                    json_line
                    for json_line in (
                        '{"symbol":"GBPUSD","result":{"attempts":[{"deal":101}],"broker_fill":{"ticket":101,"symbol":"GBPUSD","magic":941999,"comment":"CC-exit","entry":1,"profit":0.2,"commission":-0.05,"swap":0.0,"fee":0.0}}}',
                        '{"symbol":"GBPUSD","result":{"attempts":[{"deal":101}],"broker_fill":{"ticket":101,"symbol":"GBPUSD","magic":941999,"comment":"CC-exit","entry":1,"profit":0.2,"commission":-0.05,"swap":0.0,"fee":0.0}}}',
                    )
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            with patch.object(runner, "broker_deal_snapshot", return_value=None):
                deals = runner.exact_logged_deals(temp_path, symbol="GBPUSD", live_magic=941999, comment_prefix="CC")
        finally:
            temp_path.unlink(missing_ok=True)

        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0]["ticket"], 101)


if __name__ == "__main__":
    unittest.main()
