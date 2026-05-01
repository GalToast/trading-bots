from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_max_profit_queue_adoption_board as board


class BuildMaxProfitQueueAdoptionBoardTests(unittest.TestCase):
    def test_build_payload_detects_missing_and_related_symbol_work(self) -> None:
        payload = board.build_payload(
            {
                "rows": [
                    {
                        "proposal_rank": 1,
                        "symbol": "USDCAD",
                        "task_id": "usdcad_first_live_seat_contract",
                        "title": "Formalize the USDCAD first live-seat decision contract",
                        "lane": "shadow HH",
                        "next_action_class": "formalize_first_live_seat_contract",
                        "proposal_status": "proposal_ready",
                    },
                    {
                        "proposal_rank": 2,
                        "symbol": "NZDUSD",
                        "task_id": "nzdusd_queue_contract",
                        "title": "Define the NZDUSD max-profit queue contract",
                        "lane": "shadow FX",
                        "next_action_class": "formalize_queue_contract",
                        "proposal_status": "proposal_ready",
                    },
                ]
            },
            {
                "tasks": [
                    {
                        "task_id": "usdjpy_bounded_forward_proof",
                        "status": "ready",
                        "lane": "shadow FX",
                        "title": "Run fresh USDJPY bounded forward proof",
                    },
                    {
                        "task_id": "nzdusd_transfer_probe",
                        "status": "completed",
                        "lane": "shadow FX",
                        "title": "Launch NZDUSD adapt-first transfer probe",
                    },
                ]
            },
        )

        summary = payload["summary"]
        self.assertEqual(summary["adopted_count"], 0)
        self.assertEqual(summary["missing_count"], 2)
        self.assertEqual(summary["highest_missing_symbol"], "USDCAD")

        indexed = {row["symbol"]: row for row in payload["rows"]}
        self.assertEqual(indexed["USDCAD"]["queue_adoption_status"], "proposal_missing_from_queue")
        self.assertEqual(
            indexed["NZDUSD"]["queue_adoption_status"],
            "proposal_missing_symbol_has_other_queue_work",
        )
        self.assertEqual(indexed["NZDUSD"]["related_symbol_queue_task_ids"], ["nzdusd_transfer_probe"])

    def test_render_markdown_mentions_adoption_status(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00Z",
                "summary": {
                    "adopted_count": 0,
                    "missing_count": 1,
                    "highest_missing_symbol": "USDCAD",
                },
                "leadership_read": ["one"],
                "rows": [
                    {
                        "proposal_rank": 1,
                        "symbol": "USDCAD",
                        "proposal_status": "proposal_ready",
                        "queue_adoption_status": "proposal_missing_from_queue",
                        "task_id": "usdcad_first_live_seat_contract",
                        "title": "Formalize the USDCAD first live-seat decision contract",
                        "lane": "shadow HH",
                        "next_action_class": "formalize_first_live_seat_contract",
                        "queue_task_status": "",
                        "queue_task_lane": "",
                        "related_symbol_queue_task_ids": [],
                        "related_symbol_queue_statuses": [],
                        "adoption_read": "missing",
                    }
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("Max Profit Queue Adoption Board", markdown)
        self.assertIn("proposal_missing_from_queue", markdown)
        self.assertIn("usdcad_first_live_seat_contract", markdown)


if __name__ == "__main__":
    unittest.main()
