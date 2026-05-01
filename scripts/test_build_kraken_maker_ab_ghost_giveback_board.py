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

import build_kraken_maker_ab_ghost_giveback_board as board


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class KrakenMakerAbGhostGivebackBoardTests(unittest.TestCase):
    def test_negative_ghost_delta_supports_banking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            write_jsonl(
                events_path,
                [
                    {
                        "action": "post_close_ghost_mark",
                        "horizon_seconds": 30,
                        "close_reason": "maker_rent_harvest",
                        "delta_net_vs_actual": -0.10,
                        "delta_net_pct_vs_actual": -1.0,
                    },
                    {
                        "action": "post_close_ghost_mark",
                        "horizon_seconds": 60,
                        "close_reason": "maker_rent_harvest",
                        "delta_net_vs_actual": -0.20,
                        "delta_net_pct_vs_actual": -2.0,
                    },
                ],
            )

            payload = board.build_payload(
                [{"lane": "test", "events_path": events_path}]
            )

            row = payload["lanes"][0]
            self.assertEqual(row["ghost_marks"], 2)
            self.assertEqual(row["verdict"], "banking_supported")
            self.assertEqual(payload["summary"]["banking_supported_lanes"], ["test"])

    def test_positive_ghost_delta_flags_hold_longer_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            write_jsonl(
                events_path,
                [
                    {
                        "action": "post_close_ghost_mark",
                        "horizon_seconds": 30,
                        "close_reason": "maker_rent_harvest",
                        "delta_net_vs_actual": 0.10,
                        "delta_net_pct_vs_actual": 1.0,
                    },
                    {
                        "action": "post_close_ghost_mark",
                        "horizon_seconds": 60,
                        "close_reason": "maker_rent_harvest",
                        "delta_net_vs_actual": 0.20,
                        "delta_net_pct_vs_actual": 2.0,
                    },
                ],
            )

            payload = board.build_payload(
                [{"lane": "test", "events_path": events_path}]
            )

            row = payload["lanes"][0]
            self.assertEqual(row["verdict"], "hold_longer_candidate")
            self.assertEqual(payload["summary"]["hold_longer_candidate_lanes"], ["test"])

    def test_writes_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = board.build_payload(lanes=[])
            json_path = root / "board.json"
            md_path = root / "board.md"

            board.write_reports(payload, json_path=json_path, md_path=md_path)

            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            self.assertIn("Kraken Maker A/B Ghost Giveback Board", md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
