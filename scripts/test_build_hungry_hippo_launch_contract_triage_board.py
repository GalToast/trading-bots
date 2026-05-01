from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.build_hungry_hippo_launch_contract_triage_board as board


class BuildHungryHippoLaunchContractTriageBoardTests(unittest.TestCase):
    def test_infer_triage_category_distinguishes_historical_research_and_retune(self) -> None:
        retire = board.infer_triage_category(
            {
                "runner_family": "legacy_bar_shadow",
                "hard_fail_reasons": ["legacy_bar_runner_not_current_escape_contract", "atr_micro_step_without_forward_proof"],
                "symbol": "NAS100",
            },
            {"validation_status": "shadow_launch"},
        )
        research = board.infer_triage_category(
            {
                "runner_family": "tick_shadow",
                "hard_fail_reasons": ["atr_micro_step_without_forward_proof"],
                "symbol": "US30",
            },
            {"validation_status": "shadow_config_build_ready"},
        )
        retune = board.infer_triage_category(
            {
                "runner_family": "tick_crypto_shadow",
                "hard_fail_reasons": ["alpha_below_floor"],
                "symbol": "XAUUSD",
            },
            {"validation_status": "shadow_launch"},
        )

        self.assertEqual(retire[0], "retire_historical")
        self.assertEqual(research[0], "keep_blocked_research")
        self.assertEqual(retune[0], "retune_or_demote")

    def test_build_payload_classifies_current_fail_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            configs = root / "configs"
            reports.mkdir()
            configs.mkdir()

            launch_payload = {
                "rows": [
                    {
                        "config_path": "configs\\hungry_hippo_ethusd_m5_step3p0_retuned_shadow.json",
                        "name": "hungry_hippo_ethusd_m5_step3p0_retuned_shadow",
                        "symbol": "ETHUSD",
                        "timeframe": "M5",
                        "runner_family": "tick_crypto_shadow",
                        "verdict": "fail",
                        "hard_fail_reasons": [
                            "missing_escape_hatch_flag",
                            "missing_escape_max_bars",
                            "missing_escape_max_loss",
                            "crypto_step_below_5_floor",
                        ],
                        "advisory_reasons": ["gate_hard_block_but_current_control_is_shadow_only"],
                    },
                    {
                        "config_path": "configs\\hungry_hippo_nas100_m15_breakout_buy_shadow.json",
                        "name": "shadow_nas100_m15_hungry_hippo_breakout_buy_v1",
                        "symbol": "NAS100",
                        "timeframe": "M15",
                        "runner_family": "tick_shadow",
                        "verdict": "fail",
                        "hard_fail_reasons": ["atr_micro_step_without_forward_proof"],
                        "advisory_reasons": ["symbol_hard_blocked_by_live_gate"],
                    },
                    {
                        "config_path": "configs\\hungry_hippo_xauusd_consolidation_shadow.json",
                        "name": "shadow_xauusd_m15_consolidation_vacuum_v1",
                        "symbol": "XAUUSD",
                        "timeframe": "M15",
                        "runner_family": "tick_crypto_shadow",
                        "verdict": "fail",
                        "hard_fail_reasons": ["alpha_below_floor"],
                        "advisory_reasons": ["symbol_hard_blocked_by_live_gate"],
                    },
                ]
            }
            (reports / "hungry_hippo_launch_safety_validation.json").write_text(
                json.dumps(launch_payload),
                encoding="utf-8",
            )
            (configs / "hungry_hippo_ethusd_m5_step3p0_retuned_shadow.json").write_text(
                json.dumps({"hungry_hippo_metadata": {"validation_status": "shadow_only_retuned_handoff_2026_04_15", "deploy_priority": 1}}),
                encoding="utf-8",
            )
            (configs / "hungry_hippo_nas100_m15_breakout_buy_shadow.json").write_text(
                json.dumps({"hungry_hippo_metadata": {"validation_status": "shadow_config_parked_2026_04_15", "deploy_priority": 4}}),
                encoding="utf-8",
            )
            (configs / "hungry_hippo_xauusd_consolidation_shadow.json").write_text(
                json.dumps({"hungry_hippo_metadata": {"validation_status": "shadow_launch", "deploy_priority": 1}}),
                encoding="utf-8",
            )

            with mock.patch.object(board, "ROOT", root), mock.patch.object(
                board, "LAUNCH_SAFETY_PATH", reports / "hungry_hippo_launch_safety_validation.json"
            ):
                payload = board.build_payload(launch_payload)

        self.assertEqual(payload["summary"]["launch_contract_fail_count"], 3)
        self.assertEqual(payload["summary"]["triage_category_counts"]["retire_historical"], 1)
        self.assertEqual(payload["summary"]["triage_category_counts"]["keep_blocked_research"], 1)
        self.assertEqual(payload["summary"]["triage_category_counts"]["retune_or_demote"], 1)

    def test_render_markdown_mentions_categories(self) -> None:
        text = board.render_markdown(
            {
                "generated_at": "2026-04-16T04:15:00+00:00",
                "leadership_read": ["one"],
                "summary": {
                    "launch_contract_fail_count": 2,
                    "triage_category_counts": {"retire_historical": 1, "keep_blocked_research": 1},
                    "retire_historical": ["a"],
                    "keep_blocked_research": ["b"],
                    "retune_or_demote": [],
                },
                "rows": [
                    {
                        "config_path": "configs/a.json",
                        "triage_category": "retire_historical",
                        "symbol": "ETHUSD",
                        "runner_family": "tick_crypto_shadow",
                        "validation_status": "shadow_only_retuned_handoff_2026_04_15",
                        "hard_fail_reasons": ["missing_escape_hatch_flag"],
                        "recommended_action": "Keep disabled.",
                    }
                ],
            }
        )

        self.assertIn("Hungry Hippo Launch Contract Triage Board", text)
        self.assertIn("retire_historical", text)
        self.assertIn("keep_blocked_research", text)


if __name__ == "__main__":
    unittest.main()
