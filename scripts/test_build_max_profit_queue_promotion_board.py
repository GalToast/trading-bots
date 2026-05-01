from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_max_profit_queue_promotion_board as board


class BuildMaxProfitQueuePromotionBoardTests(unittest.TestCase):
    def test_build_payload_prioritizes_ready_missing_row_first(self) -> None:
        payload = board.build_payload(
            {
                "rows": [
                    {
                        "proposal_rank": 1,
                        "symbol": "USDCAD",
                        "task_id": "usdcad_first_live_seat_contract",
                        "proposal_status": "proposal_ready",
                        "title": "Formalize the USDCAD first live-seat decision contract",
                        "lane": "shadow HH",
                        "next_action_class": "formalize_first_live_seat_contract",
                    },
                    {
                        "proposal_rank": 2,
                        "symbol": "NZDUSD",
                        "task_id": "nzdusd_queue_contract",
                        "proposal_status": "proposal_ready",
                        "title": "Define the NZDUSD max-profit queue contract",
                        "lane": "shadow FX",
                        "next_action_class": "formalize_queue_contract",
                    },
                ]
            },
            {
                "rows": [
                    {
                        "proposal_rank": 1,
                        "symbol": "USDCAD",
                        "task_id": "usdcad_first_live_seat_contract",
                        "proposal_status": "proposal_ready",
                        "queue_adoption_status": "proposal_missing_from_queue",
                        "related_symbol_queue_task_ids": [],
                    },
                    {
                        "proposal_rank": 2,
                        "symbol": "NZDUSD",
                        "task_id": "nzdusd_queue_contract",
                        "proposal_status": "proposal_ready",
                        "queue_adoption_status": "proposal_missing_symbol_has_other_queue_work",
                        "related_symbol_queue_task_ids": ["nzdusd_transfer_probe"],
                    },
                ]
            },
        )

        self.assertEqual(payload["summary"]["highest_promotion_symbol"], "USDCAD")
        indexed = {row["symbol"]: row for row in payload["rows"]}
        self.assertEqual(indexed["USDCAD"]["promotion_class"], "promote_to_queue_now")
        self.assertEqual(
            indexed["NZDUSD"]["promotion_class"],
            "add_contract_row_alongside_existing_symbol_work",
        )

    def test_render_markdown_mentions_promotion_class(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00Z",
                "summary": {
                    "promotion_candidate_count": 1,
                    "highest_promotion_symbol": "USDCAD",
                },
                "leadership_read": ["one"],
                "rows": [
                    {
                        "promotion_rank": 1,
                        "symbol": "USDCAD",
                        "proposal_status": "proposal_ready",
                        "queue_adoption_status": "proposal_missing_from_queue",
                        "promotion_class": "promote_to_queue_now",
                        "task_id": "usdcad_first_live_seat_contract",
                        "title": "Formalize the USDCAD first live-seat decision contract",
                        "lane": "shadow HH",
                        "next_action_class": "formalize_first_live_seat_contract",
                        "related_symbol_queue_task_ids": [],
                        "promotion_read": "promote",
                    }
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("Max Profit Queue Promotion Board", markdown)
        self.assertIn("promote_to_queue_now", markdown)
        self.assertIn("usdcad_first_live_seat_contract", markdown)


if __name__ == "__main__":
    unittest.main()
