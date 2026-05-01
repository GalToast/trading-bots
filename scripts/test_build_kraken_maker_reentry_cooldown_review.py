#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_kraken_maker_reentry_cooldown_review as review


class KrakenMakerReentryCooldownReviewTests(unittest.TestCase):
    def test_pairs_winning_blocks_and_flags_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            events = [
                {"action": "open_maker_shadow", "product_id": "HOUSE-USD", "ts_utc": "2026-04-25T00:00:00+00:00"},
                {
                    "action": "block_maker_reentry",
                    "product_id": "HOUSE-USD",
                    "reason": "maker_rent_harvest",
                    "cooldown_polls": 60,
                    "ts_utc": "2026-04-25T00:00:20+00:00",
                },
                {
                    "action": "close_maker_shadow",
                    "product_id": "HOUSE-USD",
                    "reason": "maker_rent_harvest",
                    "net": 0.5,
                    "net_pct": 6.0,
                    "age_seconds": 20,
                    "ts_utc": "2026-04-25T00:00:21+00:00",
                },
                {"action": "open_maker_shadow", "product_id": "HOUSE-USD", "ts_utc": "2026-04-25T00:01:00+00:00"},
                {
                    "action": "block_maker_reentry",
                    "product_id": "HOUSE-USD",
                    "reason": "maker_rent_harvest",
                    "cooldown_polls": 60,
                    "ts_utc": "2026-04-25T00:01:20+00:00",
                },
                {
                    "action": "close_maker_shadow",
                    "product_id": "HOUSE-USD",
                    "reason": "maker_rent_harvest",
                    "net": 0.4,
                    "net_pct": 5.0,
                    "age_seconds": 20,
                    "ts_utc": "2026-04-25T00:01:21+00:00",
                },
            ]
            path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

            payload = review.build_payload(path)
            row = payload["products"][0]

            self.assertEqual(row["product_id"], "HOUSE-USD")
            self.assertEqual(row["paired_winning_blocks"], 2)
            self.assertTrue(row["cooldown_ab_candidate"])
            self.assertEqual(payload["summary"]["cooldown_ab_candidates"], ["HOUSE-USD"])

    def test_loss_product_is_not_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            events = [
                {
                    "action": "block_maker_reentry",
                    "product_id": "BAD-USD",
                    "reason": "maker_no_mfe_adverse_stop",
                    "cooldown_polls": 60,
                    "ts_utc": "2026-04-25T00:00:20+00:00",
                },
                {
                    "action": "close_maker_shadow",
                    "product_id": "BAD-USD",
                    "reason": "maker_no_mfe_adverse_stop",
                    "net": -0.1,
                    "net_pct": -1.0,
                    "age_seconds": 20,
                    "ts_utc": "2026-04-25T00:00:21+00:00",
                },
            ]
            path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

            payload = review.build_payload(path)
            row = payload["products"][0]

            self.assertFalse(row["cooldown_ab_candidate"])
            self.assertEqual(payload["summary"]["verdict"], "collect_more_or_keep_cooldown")


if __name__ == "__main__":
    unittest.main()
