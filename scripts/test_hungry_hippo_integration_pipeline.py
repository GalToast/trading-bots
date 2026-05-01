#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import hungry_hippo_integration_pipeline as pipeline


ROOT = Path(__file__).resolve().parent.parent


class HungryHippoIntegrationPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.atr_params = json.loads((ROOT / "reports" / "hungry_hippo_atr_step_params.json").read_text(encoding="utf-8"))
        self.regime_signal = json.loads((ROOT / "reports" / "regime_signal.json").read_text(encoding="utf-8"))
        self.rearm_params = json.loads((ROOT / "reports" / "hungry_hippo_rearm_params.json").read_text(encoding="utf-8"))

    def test_resolve_rearm_policy_uses_guardrail_metadata(self) -> None:
        policy = pipeline.resolve_rearm_policy(self.rearm_params, "GBPUSD")
        self.assertEqual(policy["guardrail_status"], "aligned")
        self.assertTrue(policy["auto_rearm_allowed"])
        self.assertEqual(policy["session_window"], "06:00-10:00+13:00-17:00")

    def test_validation_carries_rearm_policy(self) -> None:
        validation = pipeline.validate_symbol_deploy("GBPUSD", self.atr_params, self.regime_signal, self.rearm_params)
        self.assertTrue(validation["deployable"])
        self.assertEqual(validation["rearm_policy"]["guardrail_status"], "aligned")

    def test_config_embeds_guardrail_and_rearm_enablement(self) -> None:
        validation = pipeline.validate_symbol_deploy("GBPUSD", self.atr_params, self.regime_signal, self.rearm_params)
        config = pipeline.build_hungry_hippo_config("GBPUSD", validation, self.rearm_params)
        self.assertEqual(config["guardrails"]["rearm_guardrail_status"], "aligned")
        self.assertTrue(config["guardrails"]["auto_rearm_allowed"])
        self.assertTrue(config["rearm"]["enabled"])
        self.assertEqual(config["rearm"]["guardrail_status"], "aligned")

    def test_non_gbp_symbol_builds_with_its_own_identity(self) -> None:
        validation = pipeline.validate_symbol_deploy("NAS100", self.atr_params, self.regime_signal, self.rearm_params)
        config = pipeline.build_hungry_hippo_config("NAS100", validation, self.rearm_params)
        self.assertEqual(config["symbol"], "NAS100")
        self.assertEqual(config["comment_prefix"], "HH-NAS100")
        self.assertEqual(config["timeframe"], "M15")
        self.assertEqual(config["hungry_hippo_metadata"]["asset_class"], "index")

    def test_missing_symbol_components_degrade_to_research_only(self) -> None:
        validation = pipeline.validate_symbol_deploy("AUDUSD", self.atr_params, self.regime_signal, self.rearm_params)
        config = pipeline.build_hungry_hippo_config("AUDUSD", validation, self.rearm_params)
        self.assertFalse(validation["deployable"])
        self.assertIn("not found in ATR params", validation["reason"])
        self.assertEqual(config["symbol"], "AUDUSD")
        self.assertFalse(config["deployable"])
        self.assertEqual(config["rearm"]["enabled"], False)
        self.assertEqual(config["hungry_hippo_metadata"]["validation_status"], "research_only_component_gap_or_guardrail_block")


if __name__ == "__main__":
    unittest.main()
