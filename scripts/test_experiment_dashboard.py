from __future__ import annotations

import unittest
from unittest.mock import patch

import scripts.experiment_dashboard as dashboard


class ExperimentDashboardTests(unittest.TestCase):
    def test_btc_dashboard_uses_close_mix_truth_when_all_closes_are_escape(self) -> None:
        def fake_load_json(path):
            name = path.name
            if name == "penetration_lattice_shadow_btcusd_m15_sell_tight_v2_state.json":
                return {
                    "symbols": {
                        "BTCUSD": {
                            "realized_closes": 9,
                            "realized_net_usd": -163.73,
                            "anchor_resets": 11,
                            "open_tickets": [],
                        }
                    },
                    "metadata": {"step_sell": 259.43},
                    "runner": {"heartbeat_at": "2026-04-15T18:31:00+00:00"},
                }
            if name == "btc_sell_tight_comparison_latest.json":
                return {
                    "v2_close_mix": {
                        "total_close_events": 9,
                        "harvest_closes": 0,
                        "escape_tier2_surgical_closes": 9,
                        "close_mix_status": "zero_harvest_all_escape_so_far",
                    },
                    "comparison": {
                        "decision_status": "proof_started_but_all_closes_are_escape_tier2_surgical",
                        "decision_summary": "Proof has started, but every realized close so far is an escape_tier2_surgical exit with zero close_ticket harvests.",
                    },
                }
            return None

        with patch.object(dashboard, "load_json", side_effect=fake_load_json):
            btc = dashboard.check_btc_sell_tight_v2()

        self.assertEqual(
            btc["gate"],
            "Proof active; all close events are still escape_tier2_surgical, waiting for first harvest",
        )
        self.assertEqual(btc["harvest_closes"], 0)
        self.assertEqual(btc["escape_tier2_surgical_closes"], 9)
        self.assertIn("Zero close_ticket harvests", btc["note"])
        self.assertIn("first close_ticket harvest", btc["milestone"])


if __name__ == "__main__":
    unittest.main()
