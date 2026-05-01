from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_adaptive_foundational_gap_status_board as board


class BuildAdaptiveFoundationalGapStatusBoardTests(unittest.TestCase):
    def test_build_payload_summarizes_foundational_gaps(self) -> None:
        payload = board.build_payload(
            perfection={
                "summary": {
                    "total_score": 9,
                    "max_score": 14,
                    "highest_priority_runtime_obligation_class": "prove_guarded_open_admission_with_cluster_escape",
                },
                "categories": [
                    {"category_id": "state_reading_honesty", "verdict": "strong"},
                    {"category_id": "telemetry_explainability", "verdict": "strong"},
                    {"category_id": "forward_proof_status", "verdict": "mixed"},
                ],
            },
            incumbent_study={
                "summary": {
                    "study_ready_symbols": ["BTCUSD"],
                    "comparable_symbols": ["BTCUSD", "GBPUSD", "NZDUSD"],
                    "family_coverage": {
                        "crypto": "ready_candidate_present",
                        "fx": "blocked_candidate_present",
                        "index": "prior_only",
                        "commodity": "missing",
                    },
                }
            },
            seat_board={
                "summary": {
                    "objective_comparison_ready_symbols": ["BTCUSD", "EURUSD"],
                    "challenger_comparable_symbols": ["BTCUSD", "EURUSD", "USDCAD"],
                },
                "rows": [
                    {"symbol": "GBPUSD", "seat_execution_gate_status": "ready_for_seat_execution"},
                    {"symbol": "USDJPY", "seat_execution_gate_status": "ready_for_seat_execution"},
                    {"symbol": "BTCUSD", "seat_execution_gate_status": "queue_backed_preparatory_only"},
                ],
            },
            acceptance={
                "summary": {
                    "overlay_governed_candidate_count": 3,
                    "verdict_counts": {"shadow_ready": 2, "research_only": 2, "promotion_ready": 0},
                }
            },
            shared_score={
                "summary": {
                    "scored_symbols": ["BTCUSD", "NZDUSD"],
                    "shared_score_ready_symbols": ["BTCUSD", "NZDUSD"],
                    "adaptive_leading_symbols": [],
                    "incumbent_leading_symbols": ["BTCUSD"],
                    "missing_adaptive_score_symbols": ["EURUSD", "GBPUSD"],
                }
            },
        )

        self.assertEqual(payload["summary"]["overall_verdict"], "formalization_program_active_not_closed")
        self.assertEqual(payload["summary"]["next_formalization_gap"], "state_space_model")
        self.assertEqual(payload["summary"]["highest_urgency_gap"], "objective_function")
        self.assertEqual(payload["summary"]["authority_blocking_gap"], "forward_superiority")
        self.assertEqual(payload["summary"]["execution_ready_symbols"], ["GBPUSD", "USDJPY"])
        rows = {row["gap_id"]: row for row in payload["gaps"]}
        self.assertEqual(rows["state_space_model"]["bridge_status"], "telemetry_ready_formal_state_model_missing")
        self.assertEqual(rows["objective_function"]["bridge_status"], "proxy_present_shared_score_partial")
        self.assertEqual(rows["cross_family_control_law"]["current_verdict"], "aspirational_only")
        self.assertEqual(rows["forward_superiority"]["current_verdict"], "missing_and_blocking_authority")

    def test_render_markdown_mentions_key_sections(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T16:40:00Z",
                "summary": {
                    "overall_verdict": "formalization_program_active_not_closed",
                    "next_formalization_gap": "state_space_model",
                    "highest_urgency_gap": "objective_function",
                    "authority_blocking_gap": "forward_superiority",
                    "execution_ready_symbols": ["GBPUSD", "USDJPY"],
                    "study_ready_symbols": ["BTCUSD"],
                    "shared_score_ready_symbols": ["BTCUSD", "NZDUSD"],
                },
                "leadership_read": ["one"],
                "gaps": [
                    {
                        "title": "Validated State-Space Model",
                        "gap_id": "state_space_model",
                        "dependency_rank": 1,
                        "urgency": "high",
                        "current_verdict": "missing_but_instrumentable",
                        "bridge_status": "telemetry_ready_formal_state_model_missing",
                        "current_read": "current read",
                        "next_action": "next action",
                        "supporting_evidence": ["e1"],
                        "source_surfaces": ["reports/a.json"],
                        "closure_requirements": ["req1", "req2"],
                    }
                ],
                "current_task_translation": [{"gap_id": "state_space_model", "read": "formalize it"}],
                "notes": ["note"],
            }
        )

        self.assertIn("Adaptive Foundational Gap Status Board", markdown)
        self.assertIn("state_space_model", markdown)
        self.assertIn("formalize it", markdown)
        self.assertIn("execution_ready_symbols", markdown)


if __name__ == "__main__":
    unittest.main()
