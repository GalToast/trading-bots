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

import build_kraken_maker_hot_products_scan as scan


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class KrakenMakerHotProductsScanTests(unittest.TestCase):
    def test_classifies_admitted_active_reentry_near_and_spread_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            board_path = root / "board.json"
            state_path = root / "state.json"
            events_path = root / "events.jsonl"
            write_json(
                board_path,
                {
                    "rows": [
                        {"rank": 1, "product_id": "HOUSE-USD", "playbook": "maker_harvest", "spread_bps": 600, "mer": 4.0},
                        {"rank": 2, "product_id": "FOLKS-USD", "playbook": "maker_harvest", "spread_bps": 140, "mer": 9.0},
                        {"rank": 3, "product_id": "BTR-USD", "playbook": "maker_harvest", "spread_bps": 104, "mer": 3.7},
                        {"rank": 4, "product_id": "DOG-USD", "playbook": "maker_harvest", "spread_bps": 98, "mer": 2.0},
                        {"rank": 5, "product_id": "BMB-USD", "playbook": "maker_harvest", "spread_bps": 520, "mer": 0.5},
                    ]
                },
            )
            write_json(
                state_path,
                {
                    "state": {
                        "active_positions": {"FOLKS-USD": {}},
                        "reentry_blocks": {"HOUSE-USD": 3},
                    }
                },
            )
            write_jsonl(
                events_path,
                [
                    {"action": "close_maker_shadow", "product_id": "BTR-USD", "net": 0.4, "net_pct": 5.0},
                    {"action": "close_maker_shadow", "product_id": "BTR-USD", "net": 0.3, "net_pct": 4.0},
                    {
                        "action": "open_maker_shadow",
                        "product_id": "EVENT-USD",
                        "playbook": "maker_harvest",
                        "board_spread_bps": 150,
                        "mer": 4.0,
                        "live_spread_bps": 130,
                    },
                ],
            )

            payload = scan.build_payload(board_path=board_path, state_path=state_path, events_path=events_path)
            by_product = {row["product_id"]: row for row in payload["rows"]}

            self.assertEqual(by_product["BTR-USD"]["classification"], "admitted_now")
            self.assertEqual(by_product["FOLKS-USD"]["classification"], "active_position")
            self.assertEqual(by_product["HOUSE-USD"]["classification"], "reentry_blocked")
            self.assertEqual(by_product["DOG-USD"]["classification"], "near_miss")
            self.assertEqual(by_product["BMB-USD"]["classification"], "spread_only_proof_candidate")
            self.assertEqual(by_product["BTR-USD"]["closes"], 2)
            self.assertEqual(by_product["EVENT-USD"]["classification"], "admitted_now")
            self.assertEqual(by_product["EVENT-USD"]["source"], "recent_open_event")

    def test_writes_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "generated_at": "now",
                "summary": {
                    "bottleneck": "candidate_supply_available",
                    "rows_scanned": 0,
                    "classification_counts": {},
                    "admitted_now": [],
                    "active_or_blocked": [],
                    "near_misses": [],
                    "spread_only_proof_candidates": [],
                    "read": "test",
                },
                "rows": [],
            }
            json_path = root / "scan.json"
            md_path = root / "scan.md"

            scan.write_reports(payload, json_path=json_path, md_path=md_path)

            self.assertTrue(json_path.exists())
            self.assertIn("Kraken Maker Hot Products Scan", md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
