#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_hungry_hippo_deployment_safety_gate_board as board


class BuildHungryHippoDeploymentSafetyGateBoardTests(unittest.TestCase):
    def test_build_payload_marks_hard_blocks_and_manual_review(self) -> None:
        spread_robustness = {
            "ETHUSD": {"status": "SPREAD-LOSS", "verdict": "losing"},
            "GBPUSD": {"status": "SPREAD-RISK", "verdict": "borderline"},
            "NAS100": {"status": "ROBUST", "verdict": "safe"},
        }
        atr_params = {
            "symbols": [
                {"symbol": "ETHUSD", "atr_current": 8.65, "step": 7.78, "step_buy": 9.08, "step_sell": 6.49},
                {"symbol": "GBPUSD", "atr_current": 0.000404, "step": 0.000202, "step_buy": 0.000269, "step_sell": 0.000134},
                {"symbol": "NAS100", "atr_current": 23.73, "step": 7.12, "step_buy": 7.12, "step_sell": 7.12},
            ]
        }
        atr_audit = {
            "rows": [
                {"symbol": "ETHUSD", "status": "manual_review_required", "note": "directional in neutral"},
                {"symbol": "GBPUSD", "status": "aligned", "note": "ok"},
                {"symbol": "NAS100", "status": "manual_review_required", "note": "breakout but symmetric"},
            ]
        }
        guardrail_audit = {
            "rows": [
                {"symbol": "ETHUSD", "status": "blocked_by_guardrail", "notes": ["blocked"]},
                {"symbol": "GBPUSD", "status": "contradiction", "notes": ["contradiction"]},
                {"symbol": "NAS100", "status": "promotable_now", "notes": ["compatible"]},
            ]
        }
        readiness_board = {
            "rows": [
                {"candidate": "GBPUSD alpha=0.5 FX harvest path", "evidence": {"closes": 111}},
                {"candidate": "NAS100 asym breakout family lane", "evidence": {"closes": 36}},
            ]
        }

        payload = board.build_payload(spread_robustness, atr_params, atr_audit, guardrail_audit, readiness_board)
        rows = {row["symbol"]: row for row in payload["rows"]}

        self.assertEqual(rows["ETHUSD"]["deployment_verdict"], "hard_block")
        self.assertIn("spread_loss", rows["ETHUSD"]["hard_block_reasons"])
        self.assertIn("blocked_by_guardrail", rows["ETHUSD"]["hard_block_reasons"])

        self.assertEqual(rows["GBPUSD"]["deployment_verdict"], "manual_review")
        self.assertIn("spread_risk", rows["GBPUSD"]["manual_review_reasons"])
        self.assertIn("guardrail_contradiction", rows["GBPUSD"]["manual_review_reasons"])

        self.assertEqual(rows["NAS100"]["deployment_verdict"], "manual_review")
        self.assertEqual(rows["NAS100"]["proof_closes"], 36)

    def test_eth_shadow_control_scopes_archival_spread_loss(self) -> None:
        spread_robustness = {"ETHUSD": {"status": "SPREAD-LOSS", "verdict": "step5 loses on spread"}}
        atr_params = {
            "symbols": [
                {"symbol": "ETHUSD", "atr_current": 8.65, "step": 7.78, "step_buy": 9.08, "step_sell": 6.49},
            ]
        }
        atr_audit = {"rows": [{"symbol": "ETHUSD", "status": "manual_review_required", "note": "directional in neutral"}]}
        guardrail_audit = {"rows": [{"symbol": "ETHUSD", "status": "blocked_by_guardrail", "notes": ["blocked"]}]}
        readiness_board = {"rows": []}
        eth_first_pilot_board = {
            "comparison_status": "blocked_until_control_normalized",
            "normalization_recommendation": {"recommended_control_step": 14.0},
            "control_options": [{"option": "B_use_step14_as_control", "verdict": "recommended_current_control"}],
        }
        eth_control_state = {
            "metadata": {"step": 14.0},
            "symbols": {"ETHUSD": {"realized_closes": 11, "realized_net_usd": 19.53}},
        }
        eth_control_gate = {
            "summary": {
                "verdict": "blocked_by_negative_expectancy",
                "comparison_status": "ready_for_clean_control_vs_variant",
                "realized_closes": 36,
                "realized_net_usd": -314.29,
            }
        }

        payload = board.build_payload(
            spread_robustness,
            atr_params,
            atr_audit,
            guardrail_audit,
            readiness_board,
            eth_first_pilot_board,
            eth_control_state,
            eth_control_gate,
        )
        row = payload["rows"][0]

        self.assertEqual(row["deployment_verdict"], "hard_block")
        self.assertEqual(row["effective_spread_status"], "CONTROL-UNDER-TEST")
        self.assertNotIn("spread_loss", row["hard_block_reasons"])
        self.assertIn("archival_spread_loss_not_current_control", row["manual_review_reasons"])
        self.assertIn("step 14", row["spread_scope_note"])
        self.assertIn("proof_verdict=blocked_by_negative_expectancy", row["control_context"])
        self.assertIn("comparison_status=ready_for_clean_control_vs_variant", row["control_context"])
        self.assertIn("realized_closes=36", row["control_context"])
        self.assertIn("realized_net_usd=-314.29", row["control_context"])

    def test_shadow_context_override_is_symbol_agnostic(self) -> None:
        overrides = board.build_shadow_context_overrides(
            {
                "summary": {"first_pilot": "NAS100 M15 breakout control"},
                "comparison_status": "waiting_for_more_proof",
                "normalization_recommendation": {"recommended_control_step": 60.0},
                "control_options": [{"option": "B_use_step14_as_control", "verdict": "not_applicable_but_present"}],
            },
            {
                "metadata": {"step": 60.0},
                "symbols": {"NAS100": {"realized_closes": 12, "realized_net_usd": 44.0}},
            },
            {
                "summary": {
                    "verdict": "shadow_only",
                    "comparison_status": "waiting_for_more_proof",
                    "realized_closes": 12,
                    "realized_net_usd": 44.0,
                }
            },
        )

        self.assertIn("NAS100", overrides)
        self.assertEqual(overrides["NAS100"]["effective_spread_status"], "CONTROL-UNDER-TEST")
        self.assertIn("Aligned NAS100 control", overrides["NAS100"]["control_context"])

    def test_render_markdown_mentions_safety_rules(self) -> None:
        payload = {
            "generated_at": "2026-04-15T01:00:00+00:00",
            "leadership_read": ["one"],
            "safety_rules": ["No spread-loss configs."],
            "summary": {"symbol_count": 1, "deployment_verdict_counts": {"hard_block": 1}, "hard_block_symbols": ["ETHUSD"]},
            "rows": [
                {
                    "symbol": "ETHUSD",
                    "deployment_verdict": "hard_block",
                    "spread_status": "SPREAD-LOSS",
                    "effective_spread_status": "CONTROL-UNDER-TEST",
                    "guardrail_status": "blocked_by_guardrail",
                    "atr_status": "manual_review_required",
                    "proof_closes": 0,
                    "ratio_to_atr": 0.4,
                    "hard_block_reasons": ["spread_loss"],
                    "manual_review_reasons": [],
                    "spread_scope_note": "scope note",
                    "control_context": "context",
                }
            ],
        }

        markdown = board.render_markdown(payload)
        self.assertIn("Hungry Hippo Deployment Safety Gate Board", markdown)
        self.assertIn("No spread-loss configs.", markdown)
        self.assertIn("ETHUSD", markdown)
        self.assertIn("hard_block", markdown)
        self.assertIn("CONTROL-UNDER-TEST", markdown)

    def test_build_payload_uses_dynamic_shadow_context_sources(self) -> None:
        payload = board.build_payload(
            {},
            {"symbols": []},
            {"rows": []},
            {"rows": []},
            {"rows": []},
            shadow_context_sources=[
                "reports/nas100_first_pilot_comparison_board.json",
                "reports/penetration_lattice_shadow_nas100_control_state.json",
                "reports/nas100_control_proof_gate_board.json",
            ],
        )

        self.assertIn("reports/nas100_first_pilot_comparison_board.json", payload["sources"])
        self.assertIn("reports/nas100_control_proof_gate_board.json", payload["sources"])
        self.assertNotIn("reports/eth_m5_first_pilot_comparison_board.json", payload["sources"])


if __name__ == "__main__":
    unittest.main()
