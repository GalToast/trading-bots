#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_hungry_hippo_shapeshifter_guardrail_audit as audit


class HungryHippoShapeshifterGuardrailAuditTests(unittest.TestCase):
    def test_extract_reference_config_handles_restart_args_config(self) -> None:
        payload = {
            "restart_args": [
                "scripts/live_penetration_lattice_tick_shadow.py",
                "--symbol", "USDJPY",
                "--step-sell", "0.12",
                "--step-buy", "0.24",
                "--raw-close-alpha", "0.3",
            ]
        }

        row = audit.extract_reference_config(Path("configs/hungry_hippo_usdjpy_deploy.json"), payload)

        self.assertEqual(row["symbol"], "USDJPY")
        self.assertEqual(row["kind"], "deploy")
        self.assertEqual(row["step_mode"], "sell_tight")
        self.assertEqual(row["close_alpha"], "0.3")

    def test_evaluate_row_uses_non_gbp_deploy_reference_for_contradiction(self) -> None:
        row = audit.evaluate_row(
            symbol="USDJPY",
            selector_row={
                "control_mode": "trend_follow",
                "personality": "BREAKOUT",
                "step_sell": 0.30,
                "step_buy": 0.10,
            },
            shapeshifter_row={
                "personality_name": "BREAKOUT",
                "step_mode": "buy_tight",
                "step_sell": 0.30,
                "step_buy": 0.10,
                "close_alpha": 0.3,
                "deployable": True,
            },
            regime_row={"control_mode": "trend_follow"},
            rearm_row={"canonical_guardrail_status": "aligned", "auto_rearm_allowed": True},
            reference_config={
                "path": "configs/hungry_hippo_usdjpy_deploy.json",
                "kind": "deploy",
                "close_alpha": "0.3",
                "step_sell": "0.10",
                "step_buy": "0.30",
                "step_mode": "sell_tight",
            },
        )

        self.assertEqual(row["status"], "contradiction")
        self.assertEqual(row["reference_config_kind"], "deploy")
        self.assertIn("USDJPY deploy config is `sell_tight`", row["notes"][0])

    def test_expected_promotability_and_conflicts(self) -> None:
        payload = audit.build_payload()
        rows = {row["symbol"]: row for row in payload["rows"]}

        self.assertEqual(rows["GBPUSD"]["status"], "contradiction")
        self.assertEqual(rows["NAS100"]["status"], "blocked_by_guardrail")
        self.assertEqual(rows["ETHUSD"]["status"], "blocked_by_guardrail")
        self.assertEqual(rows["EURUSD"]["status"], "blocked_by_guardrail")
        self.assertEqual(rows["XRPUSD"]["status"], "promotable_now")
        self.assertNotIn("SOLUSD", rows)
        self.assertEqual(rows["GBPUSD"]["reference_config_kind"], "deploy")


if __name__ == "__main__":
    unittest.main()
