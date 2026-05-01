from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_max_profit_contract_gap_board as board


class BuildMaxProfitContractGapBoardTests(unittest.TestCase):
    def test_build_payload_orders_contract_gaps_by_leverage(self) -> None:
        payload = board.build_payload(
            seat_board={
                "rows": [
                    {
                        "symbol": "USDCAD",
                        "seat_verdict": "no_live_seat",
                        "best_challenger_lane": "penetration_lattice_shadow_usdcad_m15_hh_breakout_v1",
                        "best_challenger_label": "USDCAD HH breakout shadow",
                        "best_challenger_family": "hungry_hippo_shadow",
                        "best_challenger_candidate_class": "ready_for_shadow_discussion",
                        "best_challenger_runtime_status": "forward_proof_started",
                        "seat_unblocker_action": "prepare_first_live_seat_case",
                        "seat_unblocker_read": "prepare seat case",
                    },
                    {
                        "symbol": "NZDUSD",
                        "seat_verdict": "provisional_live_seat",
                        "best_challenger_lane": "shadow_nzdusd_m15_asym",
                        "best_challenger_label": "NZD transfer probe",
                        "best_challenger_family": "adaptive_shadow",
                        "best_challenger_candidate_class": "research_only",
                        "best_challenger_runtime_status": "already_running_monitor_only",
                        "seat_unblocker_action": "enrich_challenger_telemetry_first",
                        "seat_unblocker_read": "enrich telemetry",
                    },
                    {
                        "symbol": "AUDUSD",
                        "seat_verdict": "no_live_seat",
                        "best_challenger_lane": "penetration_lattice_shadow_audusd_m15_hh_breakout_v1",
                        "best_challenger_label": "AUD HH breakout shadow",
                        "best_challenger_family": "hungry_hippo_shadow",
                        "best_challenger_candidate_class": "ready_for_shadow_discussion",
                        "best_challenger_runtime_status": "not_launched_yet",
                        "seat_unblocker_action": "launch_challenger_proof",
                        "seat_unblocker_read": "launch proof",
                    },
                ]
            },
            next_action_board={
                "summary": {
                    "queue_contract_missing_symbols": ["AUDUSD", "NZDUSD", "USDCAD"],
                }
            },
        )

        summary = payload["summary"]
        self.assertEqual(summary["highest_contract_gap_symbol"], "USDCAD")
        self.assertEqual(summary["contract_gap_symbols"], ["USDCAD", "NZDUSD", "AUDUSD"])

        indexed = {row["symbol"]: row for row in payload["rows"]}
        self.assertEqual(indexed["USDCAD"]["proposed_queue_task_id"], "usdcad_first_live_seat_contract")
        self.assertEqual(indexed["USDCAD"]["proposed_queue_lane"], "shadow HH")
        self.assertEqual(indexed["NZDUSD"]["proposed_next_action_class"], "formalize_telemetry_enrichment_contract")
        self.assertEqual(indexed["AUDUSD"]["proposed_queue_task_id"], "audusd_first_live_seat_proof_contract")

    def test_render_markdown_mentions_backlog(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00Z",
                "summary": {
                    "contract_gap_symbols": ["USDCAD"],
                    "highest_contract_gap_symbol": "USDCAD",
                },
                "leadership_read": ["one"],
                "rows": [
                    {
                        "symbol": "USDCAD",
                        "seat_verdict": "no_live_seat",
                        "seat_unblocker_action": "prepare_first_live_seat_case",
                        "seat_unblocker_read": "seat read",
                        "best_challenger_lane": "lane",
                        "best_challenger_label": "label",
                        "best_challenger_family": "hungry_hippo_shadow",
                        "best_challenger_candidate_class": "ready_for_shadow_discussion",
                        "best_challenger_runtime_status": "forward_proof_started",
                        "proposed_queue_task_id": "usdcad_first_live_seat_contract",
                        "proposed_queue_title": "Formalize the USDCAD first live-seat decision contract",
                        "proposed_queue_lane": "shadow HH",
                        "proposed_next_action_class": "formalize_first_live_seat_contract",
                        "contract_gap_read": "gap read",
                    }
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("Max Profit Contract Gap Board", markdown)
        self.assertIn("usdcad_first_live_seat_contract", markdown)
        self.assertIn("formalize_first_live_seat_contract", markdown)


if __name__ == "__main__":
    unittest.main()
