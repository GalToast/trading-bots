from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_max_profit_queue_contract_packet as board


class BuildMaxProfitQueueContractPacketTests(unittest.TestCase):
    def test_build_payload_keeps_contract_gap_order(self) -> None:
        payload = board.build_payload(
            {
                "rows": [
                    {
                        "symbol": "USDCAD",
                        "proposed_queue_task_id": "usdcad_first_live_seat_contract",
                        "proposed_queue_title": "Formalize the USDCAD first live-seat decision contract",
                        "proposed_queue_lane": "shadow HH",
                        "proposed_next_action_class": "formalize_first_live_seat_contract",
                        "best_challenger_lane": "lane1",
                        "best_challenger_family": "hungry_hippo_shadow",
                        "best_challenger_candidate_class": "ready_for_shadow_discussion",
                        "best_challenger_runtime_status": "forward_proof_started",
                        "seat_verdict": "no_live_seat",
                        "seat_unblocker_action": "prepare_first_live_seat_case",
                    },
                    {
                        "symbol": "NZDUSD",
                        "proposed_queue_task_id": "nzdusd_telemetry_contract",
                        "proposed_queue_title": "Enrich telemetry for the NZDUSD challenger before seat judgment",
                        "proposed_queue_lane": "shadow FX",
                        "proposed_next_action_class": "formalize_telemetry_enrichment_contract",
                        "best_challenger_lane": "lane2",
                        "best_challenger_family": "adaptive_shadow",
                        "best_challenger_candidate_class": "research_only",
                        "best_challenger_runtime_status": "already_running_monitor_only",
                        "seat_verdict": "provisional_live_seat",
                        "seat_unblocker_action": "enrich_challenger_telemetry_first",
                    },
                ]
            }
        )

        summary = payload["summary"]
        self.assertEqual(summary["highest_ready_symbol"], "USDCAD")
        self.assertEqual(summary["proposal_symbols"], ["USDCAD", "NZDUSD"])

        indexed = {row["symbol"]: row for row in payload["rows"]}
        self.assertEqual(indexed["USDCAD"]["proposal_status"], "proposal_ready")
        self.assertEqual(indexed["NZDUSD"]["proposal_status"], "proposal_ready")
        self.assertEqual(indexed["NZDUSD"]["next_action_class"], "formalize_telemetry_enrichment_contract")

    def test_render_markdown_mentions_packet(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00Z",
                "summary": {
                    "proposal_symbols": ["USDCAD"],
                    "highest_ready_symbol": "USDCAD",
                },
                "leadership_read": ["one"],
                "rows": [
                    {
                        "proposal_rank": 1,
                        "symbol": "USDCAD",
                        "proposal_status": "proposal_ready",
                        "task_id": "usdcad_first_live_seat_contract",
                        "title": "Formalize the USDCAD first live-seat decision contract",
                        "lane": "shadow HH",
                        "next_action_class": "formalize_first_live_seat_contract",
                        "seat_verdict": "no_live_seat",
                        "seat_unblocker_action": "prepare_first_live_seat_case",
                        "best_challenger_lane": "lane1",
                        "best_challenger_family": "hungry_hippo_shadow",
                        "best_challenger_candidate_class": "ready_for_shadow_discussion",
                        "best_challenger_runtime_status": "forward_proof_started",
                        "proposal_read": "proposal read",
                    }
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("Max Profit Queue Contract Packet", markdown)
        self.assertIn("usdcad_first_live_seat_contract", markdown)
        self.assertIn("formalize_first_live_seat_contract", markdown)


if __name__ == "__main__":
    unittest.main()
