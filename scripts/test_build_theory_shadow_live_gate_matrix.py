#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_theory_shadow_live_gate_matrix as board


class BuildTheoryShadowLiveGateMatrixTests(unittest.TestCase):
    def test_build_payload_maps_current_candidates_to_stage_ladder(self) -> None:
        promotion_gate = {
            "summary": {"closest_current_live_candidate": "none_honest_yet"},
            "rows": [
                {
                    "priority": 1,
                    "candidate": "ETHUSD M5 step14 normalized control",
                    "current_stage": "tested_theory_waiting_for_positive_control_proof",
                    "promotion_verdict": "collect_positive_control_proof_before_validated_shadow",
                    "machine_truth": {"realized_closes": 12, "runtime_stale": False, "comparison_status": "ready_for_clean_control_vs_variant"},
                    "blocking_issue": "blocked_by_negative_expectancy",
                    "promotion_gate": ["collect positive proof on the aligned control"],
                },
                {
                    "priority": 2,
                    "candidate": "GBPUSD alpha=0.5 FX harvest path",
                    "current_stage": "closure_policy_diagnosis_before_live",
                    "promotion_verdict": "bucket_diagnosis_before_live",
                    "machine_truth": {"proof_closes": 111},
                    "blocking_issue": "closure tax dominates",
                    "promotion_gate": ["split the buckets"],
                },
            ],
        }

        payload = board.build_payload(promotion_gate)

        self.assertEqual(payload["closest_live_candidate"], "none_honest_yet")
        self.assertEqual(payload["rows"][0]["next_honest_stage"], "shadow")
        self.assertEqual(payload["rows"][0]["family"], "hungry_hippo / crypto_m5_control")
        self.assertEqual(payload["rows"][0]["blocking_issue"], "blocked_by_negative_expectancy")
        self.assertEqual(payload["rows"][1]["next_honest_stage"], "validated_shadow")
        self.assertIn("bucket", payload["rows"][1]["instant_disqualifier"])

    def test_render_markdown_mentions_stage_model_and_candidate(self) -> None:
        payload = {
            "generated_at": "2026-04-15T17:00:00+00:00",
            "leadership_read": ["one"],
            "global_rules": [{"rule": "family_firewall", "read": "proof stays local"}],
            "stage_model": {"tested_theory_to_shadow": ["name the family"]},
            "closest_live_candidate": "none_honest_yet",
            "rows": [
                {
                    "priority": 1,
                    "candidate": "ETHUSD M5 step14 normalized control",
                    "family": "hungry_hippo / crypto_m5_control",
                    "current_stage": "tested_theory_waiting_for_clean_control",
                    "next_honest_stage": "shadow",
                    "benchmark_to_beat": "same control",
                    "single_changed_variable": "offensive closure ON/OFF",
                    "current_truth": {"realized_closes": 11},
                    "blocking_issue": "stale runtime",
                    "gate_to_next_stage": ["restore runtime"],
                    "gate_to_validated_shadow": ["25 closes"],
                    "gate_to_live": ["same runtime path"],
                    "instant_disqualifier": "geometry drift",
                }
            ],
        }

        markdown = board.render_markdown(payload)
        self.assertIn("Theory Shadow Live Gate Matrix", markdown)
        self.assertIn("tested_theory_to_shadow", markdown)
        self.assertIn("ETHUSD M5 step14 normalized control", markdown)


if __name__ == "__main__":
    unittest.main()
