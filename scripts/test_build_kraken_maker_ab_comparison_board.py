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

import build_kraken_maker_ab_comparison_board as board


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class KrakenMakerAbComparisonBoardTests(unittest.TestCase):
    def test_summarizes_green_parallel_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "state.json"
            events_path = root / "events.jsonl"
            write_json(
                state_path,
                {
                    "state": {
                        "cash_usd": 101.0,
                        "realized_net_usd": 1.0,
                        "realized_closes": 2,
                        "active_positions": {},
                    }
                },
            )
            write_jsonl(
                events_path,
                [
                    {"ts_utc": "2026-04-25T00:00:00+00:00", "action": "open_maker_shadow", "product_id": "HOUSE-USD"},
                    {"ts_utc": "2026-04-25T00:00:00+00:00", "action": "open_maker_shadow", "product_id": "FOLKS-USD"},
                    {"ts_utc": "2026-04-25T00:00:20+00:00", "action": "close_maker_shadow", "product_id": "HOUSE-USD", "net": 0.8, "net_pct": 10.0},
                    {"ts_utc": "2026-04-25T00:00:21+00:00", "action": "close_maker_shadow", "product_id": "FOLKS-USD", "net": 0.2, "net_pct": 2.0},
                ],
            )

            row = board.summarize_lane(
                {
                    "lane": "parallel",
                    "hypothesis": "test",
                    "state_path": state_path,
                    "events_path": events_path,
                }
            )

            self.assertEqual(row["realized_closes"], 2)
            self.assertEqual(row["wins"], 2)
            self.assertEqual(row["losses"], 0)
            self.assertEqual(row["max_concurrent_positions"], 2)
            self.assertEqual(row["verdict"], "promising_collect_more")

    def test_payload_marks_dead_hypotheses(self) -> None:
        payload = board.build_payload(lanes=[])

        self.assertEqual(payload["summary"]["lanes"], 0)
        killed = {row["hypothesis"] for row in payload["kill_or_park"]}
        self.assertIn("loose gate", killed)
        self.assertIn("BMB/no-fill spread-only promotion", killed)
        self.assertIn("global cooldown mutation", killed)

    def test_writes_markdown_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = board.build_payload(lanes=[])
            json_path = root / "board.json"
            md_path = root / "board.md"

            board.write_reports(payload, json_path=json_path, md_path=md_path)

            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            self.assertIn("Kraken Maker A/B Comparison Board", md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
