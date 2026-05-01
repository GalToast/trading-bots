from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_max_profit_taskboard_bridge as board


class BuildMaxProfitTaskboardBridgeTests(unittest.TestCase):
    def test_build_payload_surfaces_stale_queue_decision_when_packet_has_moved(self) -> None:
        payload = board.build_payload(
            task_store={
                "tasks": [
                    {"id": 82, "title": "GBP", "status": "blocked", "owner": "codex_gbp_packet", "blocking_decision_id": "12", "evidence": {"symbol": "GBPUSD"}},
                    {"id": 83, "title": "USDJPY", "status": "blocked", "owner": "", "blocking_decision_id": "12", "evidence": {"symbol": "USDJPY"}},
                    {
                        "id": 84,
                        "title": "Umbrella",
                        "status": "blocked",
                        "owner": "",
                        "blocking_decision_id": "13",
                        "evidence": {
                            "symbols": ["AUDUSD", "XRPUSD", "NZDUSD"],
                            "child_tasks": [85, 86, 87, 88],
                            "seat_execution_gate_status": "actionable_but_missing_queue_contract",
                            "highest_missing_symbol": "AUDUSD",
                            "promotion_statuses": {"AUDUSD": "add_launch_contract_row", "XRPUSD": "add_launch_contract_row", "NZDUSD": "add_contract_row_alongside_existing_symbol_work"},
                        },
                    },
                    {"id": 85, "title": "USDCAD", "status": "blocked", "owner": "", "blocking_decision_id": "13", "evidence": {"symbol": "USDCAD", "seat_execution_gate_status": "actionable_but_missing_queue_contract"}},
                    {"id": 86, "title": "AUDUSD", "status": "blocked", "owner": "", "blocking_decision_id": "13", "evidence": {"symbol": "AUDUSD", "seat_execution_gate_status": "actionable_but_missing_queue_contract"}},
                    {"id": 87, "title": "XRPUSD", "status": "blocked", "owner": "", "blocking_decision_id": "13", "evidence": {"symbol": "XRPUSD", "seat_execution_gate_status": "actionable_but_missing_queue_contract"}},
                    {"id": 88, "title": "NZDUSD", "status": "blocked", "owner": "", "blocking_decision_id": "13", "evidence": {"symbol": "NZDUSD", "seat_execution_gate_status": "actionable_but_missing_queue_contract"}},
                    {"id": 89, "title": "BTC", "status": "in_progress", "owner": "codex_lattice_0416", "blocking_decision_id": "", "evidence": {"symbol": "BTCUSD"}},
                ],
                "decisions": [
                    {"id": 12, "status": "open", "recommended_option": "advance_gbpusd_first", "related_task_ids": [82, 83]},
                    {"id": 13, "status": "open", "recommended_option": "adopt_usdcad_first", "related_task_ids": [85, 86, 87, 88]},
                ],
            },
            next_action_board={
                "rows": [
                    {
                        "symbol": "GBPUSD",
                        "seat_execution_gate_status": "ready_for_seat_execution",
                        "queue_task_id": "gbpusd_adaptive_comparison_packet",
                        "next_action_class": "shadow_compare_and_score",
                        "max_profit_posture": "launch_now",
                        "launch_read": "GBP first.",
                    },
                    {
                        "symbol": "USDJPY",
                        "seat_execution_gate_status": "ready_for_seat_execution",
                        "queue_task_id": "usdjpy_bounded_forward_proof",
                        "next_action_class": "prove_executability_and_survival_before_promotion",
                        "max_profit_posture": "launch_now",
                        "launch_read": "USDJPY second.",
                    },
                    {
                        "symbol": "USDCAD",
                        "seat_execution_gate_status": "monitor_only",
                        "queue_task_id": "usdcad_first_live_seat_contract",
                        "next_action_class": "formalize_first_live_seat_contract",
                        "max_profit_posture": "observe_only",
                        "launch_read": "USDCAD already queue-backed; no longer missing from the packet.",
                    },
                    {
                        "symbol": "AUDUSD",
                        "seat_execution_gate_status": "actionable_but_missing_queue_contract",
                        "queue_task_id": "",
                        "next_action_class": "",
                        "max_profit_posture": "queue_contract_missing",
                        "launch_read": "AUDUSD current missing queue row.",
                    },
                    {
                        "symbol": "XRPUSD",
                        "seat_execution_gate_status": "actionable_but_missing_queue_contract",
                        "queue_task_id": "",
                        "next_action_class": "",
                        "max_profit_posture": "queue_contract_missing",
                        "launch_read": "XRPUSD current missing queue row.",
                    },
                    {
                        "symbol": "NZDUSD",
                        "seat_execution_gate_status": "actionable_but_missing_queue_contract",
                        "queue_task_id": "",
                        "next_action_class": "",
                        "max_profit_posture": "queue_contract_missing",
                        "launch_read": "NZDUSD current missing queue row.",
                    },
                ]
            },
            queue_packet_board={
                "rows": [
                    {"symbol": "AUDUSD", "task_id": "audusd_first_live_seat_proof_contract", "proposal_rank": 1, "proposal_status": "proposal_ready_for_launch_contract", "next_action_class": "formalize_first_seat_proof_contract", "proposal_read": "AUDUSD packet."},
                    {"symbol": "XRPUSD", "task_id": "xrpusd_first_live_seat_proof_contract", "proposal_rank": 2, "proposal_status": "proposal_ready_for_launch_contract", "next_action_class": "formalize_first_seat_proof_contract", "proposal_read": "XRPUSD packet."},
                    {"symbol": "NZDUSD", "task_id": "nzdusd_queue_contract", "proposal_rank": 3, "proposal_status": "proposal_ready", "next_action_class": "formalize_queue_contract", "proposal_read": "NZDUSD packet."},
                ],
                "summary": {"highest_ready_symbol": "AUDUSD"},
            },
            queue_adoption_board={
                "rows": [
                    {"symbol": "AUDUSD", "queue_adoption_status": "proposal_missing_from_queue", "adoption_read": "AUDUSD adoption."},
                    {"symbol": "XRPUSD", "queue_adoption_status": "proposal_missing_from_queue", "adoption_read": "XRPUSD adoption."},
                    {"symbol": "NZDUSD", "queue_adoption_status": "proposal_missing_symbol_has_other_queue_work", "adoption_read": "NZDUSD adoption."},
                ],
                "summary": {"highest_missing_symbol": "AUDUSD"},
            },
            queue_promotion_board={
                "rows": [
                    {"symbol": "AUDUSD", "promotion_class": "add_launch_contract_row", "promotion_read": "AUDUSD promote."},
                    {"symbol": "XRPUSD", "promotion_class": "add_launch_contract_row", "promotion_read": "XRPUSD promote."},
                    {"symbol": "NZDUSD", "promotion_class": "add_contract_row_alongside_existing_symbol_work", "promotion_read": "NZDUSD promote."},
                ],
                "summary": {"highest_promotion_symbol": "AUDUSD"},
            },
            btc_control_board={
                "summary": {
                    "symbol": "BTCUSD",
                    "seat_execution_gate_status": "queue_backed_preparatory_only",
                    "queue_task_id": "btc_restore_comparison_shadow",
                    "max_profit_posture": "preparatory_only",
                    "contract_read": "BTC prep.",
                },
                "control_branch": {"next_action_class": "control_shadow_and_collect_path_safety_evidence"},
            },
        )

        self.assertEqual(payload["summary"]["recommended_execution_ready_symbol"], "GBPUSD")
        self.assertEqual(payload["summary"]["recommended_queue_symbol"], "AUDUSD")
        self.assertEqual(payload["summary"]["recommended_queue_task_id"], 86)
        self.assertEqual(payload["summary"]["queue_decision_alignment_status"], "decision_stale_vs_surface")
        self.assertEqual(payload["rows"][0]["task_id"], 89)
        indexed = {row["task_id"]: row for row in payload["rows"]}
        self.assertEqual(indexed[82]["bridge_status"], "recommended_default_waiting_decision")
        self.assertEqual(indexed[83]["bridge_status"], "alternate_execution_ready_waiting_decision")
        self.assertEqual(indexed[85]["bridge_status"], "already_adopted_queue_row_decision_stale")
        self.assertEqual(indexed[85]["max_profit_posture"], "observe_only")
        self.assertEqual(indexed[86]["promotion_class"], "add_launch_contract_row")
        self.assertEqual(indexed[86]["bridge_status"], "surface_recommended_queue_row_decision_stale")
        self.assertIn("AUDUSD", indexed[84]["taskboard_read"])
        self.assertEqual(indexed[88]["queue_adoption_status"], "proposal_missing_symbol_has_other_queue_work")
        self.assertIn("task `82`", indexed[82]["taskboard_read"])
        self.assertIn("decision `13`", indexed[86]["taskboard_read"])

    def test_render_markdown_mentions_recommended_defaults(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00Z",
                "summary": {
                    "tracked_task_count": 2,
                    "decision_blocked_task_count": 1,
                    "active_preparatory_task_id": 89,
                    "recommended_execution_ready_task_id": 82,
                    "recommended_queue_task_id": 85,
                    "queue_decision_alignment_status": "aligned_with_surface",
                },
                "leadership_read": ["one"],
                "decisions": [
                    {
                        "decision_id": 12,
                        "status": "open",
                        "recommended_option": "advance_gbpusd_first",
                        "recommended_symbol": "GBPUSD",
                        "surface_recommended_symbol": "GBPUSD",
                        "surface_alignment_status": "aligned_with_surface",
                        "related_task_ids": [82, 83],
                    }
                ],
                "rows": [
                    {
                        "bridge_rank": 1,
                        "task_id": 89,
                        "symbol": "BTCUSD",
                        "task_group": "preparatory_control_contract",
                        "bridge_status": "active_preparatory_in_progress",
                        "task_status": "in_progress",
                        "task_owner": "codex_lattice_0416",
                        "blocking_decision_id": "",
                        "title": "BTC",
                        "decision_status": "",
                        "decision_recommended_option": "",
                        "seat_execution_gate_status": "queue_backed_preparatory_only",
                        "queue_task_id": "btc_restore_comparison_shadow",
                        "next_action_class": "control_shadow",
                        "max_profit_posture": "preparatory_only",
                        "taskboard_read": "prep",
                        "source_read": "btc",
                    },
                    {
                        "bridge_rank": 2,
                        "task_id": 82,
                        "symbol": "GBPUSD",
                        "task_group": "execution_ready_seat",
                        "bridge_status": "recommended_default_waiting_decision",
                        "task_status": "blocked",
                        "task_owner": "codex_gbp_packet",
                        "blocking_decision_id": "12",
                        "title": "GBP",
                        "decision_status": "open",
                        "decision_recommended_option": "advance_gbpusd_first",
                        "seat_execution_gate_status": "ready_for_seat_execution",
                        "queue_task_id": "gbpusd_adaptive_comparison_packet",
                        "next_action_class": "shadow_compare_and_score",
                        "max_profit_posture": "launch_now",
                        "taskboard_read": "gbp",
                        "source_read": "source",
                    },
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("Max Profit Taskboard Bridge", markdown)
        self.assertIn("advance_gbpusd_first", markdown)
        self.assertIn("recommended_default_waiting_decision", markdown)


if __name__ == "__main__":
    unittest.main()
