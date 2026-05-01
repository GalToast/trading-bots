#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_btc_downtrend_config_reconciliation_board as board


class BuildBtcDowntrendConfigReconciliationBoardTests(unittest.TestCase):
    def test_build_payload_detects_expected_mismatches(self) -> None:
        handoff = {
            "current_truth": {"regime_signal": {"action_bias": "SELL", "control_mode": "bounce_reversal"}},
            "proposed_downtrend_shape": {
                "timeframe": "M15",
                "computed_buy_step": 389.14393,
                "computed_sell_step": 129.71464,
                "alpha": 0.3,
                "rearm_variant": "rearm_lvl2_exc1",
                "max_open_per_side": 6,
                "sell_gap": 1,
                "buy_gap": 1,
            },
        }
        config = {
            "name": "shadow_btcusd_m15_sell_tight_v1",
            "enabled": True,
            "restart_args": [
                "--timeframe", "M15",
                "--step-buy", "389.14",
                "--step-sell", "129.71",
                "--max-open-per-side", "12",
                "--raw-close-alpha", "0.3",
                "--raw-rearm-variant", "rearm_lvl2_exc2",
                "--raw-sell-gap", "1",
                "--raw-buy-gap", "1",
            ],
        }

        payload = board.build_payload(handoff, config)

        self.assertEqual(payload["summary"]["status"], "needs_reconcile")
        mismatches = {row["field"] for row in payload["recommended_canonicalization"]}
        self.assertIn("enabled", mismatches)
        self.assertIn("max_open_per_side", mismatches)
        self.assertIn("rearm_variant", mismatches)

    def test_render_markdown_mentions_status(self) -> None:
        payload = {
            "generated_at": "2026-04-15T03:00:00+00:00",
            "summary": {"status": "needs_reconcile", "match_count": 6, "mismatch_count": 3, "current_action_bias": "SELL", "current_control_mode": "bounce_reversal", "config_name": "shadow_btcusd_m15_sell_tight_v1"},
            "leadership_read": ["one"],
            "comparisons": [{"field": "enabled", "handoff": False, "config": True, "match": False}],
            "recommended_canonicalization": [{"field": "enabled", "recommended_value": False, "why": "keep shadow only"}],
        }

        markdown = board.render_markdown(payload)
        self.assertIn("BTC Downtrend Config Reconciliation Board", markdown)
        self.assertIn("needs_reconcile", markdown)
        self.assertIn("enabled", markdown)


if __name__ == "__main__":
    unittest.main()
