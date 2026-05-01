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

import build_kraken_maker_red_packet_autopsy as autopsy


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class KrakenMakerRedPacketAutopsyTests(unittest.TestCase):
    def test_pairs_loss_and_scores_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            write_jsonl(
                events_path,
                [
                    {
                        "action": "open_maker_shadow",
                        "product_id": "FOLKS-USD",
                        "ts_utc": "2026-04-25T00:00:00+00:00",
                        "board_spread_bps": 140,
                        "live_spread_bps": 44,
                        "mer": 9.6,
                    },
                    {
                        "action": "close_maker_shadow",
                        "product_id": "FOLKS-USD",
                        "opened_at": "2026-04-25T00:00:00+00:00",
                        "ts_utc": "2026-04-25T00:02:00+00:00",
                        "reason": "maker_no_mfe_adverse_stop",
                        "net": -0.08,
                        "net_pct": -1.0,
                        "age_seconds": 120,
                        "max_net_pct_on_cost": -0.1,
                        "spread_bps": 45,
                    },
                ],
            )

            payload = autopsy.build_payload(lanes=[{"lane": "test", "events_path": events_path}])

            self.assertEqual(payload["summary"]["losses"], 1)
            self.assertEqual(payload["losses"][0]["product_id"], "FOLKS-USD")
            self.assertIn("entry_live_spread_below_50bps", payload["losses"][0]["potential_blockers"])
            blocker = {row["rule"]: row for row in payload["candidate_blockers"]}["entry_live_spread_bps_lt_50"]
            self.assertEqual(blocker["blocked_losses"], 1)

    def test_writes_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "generated_at": "now",
                "summary": {"verdict": "no_red_packets", "trades": 0, "losses": 0, "loss_products": [], "read": "test"},
                "lanes": [],
                "losses": [],
                "candidate_blockers": [],
            }
            json_path = root / "autopsy.json"
            md_path = root / "autopsy.md"

            autopsy.write_reports(payload, json_path=json_path, md_path=md_path)

            self.assertTrue(json_path.exists())
            self.assertIn("Kraken Maker Red Packet Autopsy", md_path.read_text(encoding="utf-8"))

    def test_classifies_green_then_red_after_exit_miss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            write_jsonl(
                events_path,
                [
                    {
                        "action": "open_maker_shadow",
                        "product_id": "FOLKS-USD",
                        "ts_utc": "2026-04-25T04:24:46+00:00",
                        "board_spread_bps": 138.65,
                        "live_spread_bps": 96.0,
                    },
                    {
                        "action": "maker_exit_miss",
                        "product_id": "FOLKS-USD",
                        "ts_utc": "2026-04-25T04:25:07+00:00",
                        "reason": "maker_rent_harvest",
                        "max_net_pct_on_cost": 0.4533,
                    },
                    {
                        "action": "close_maker_shadow",
                        "product_id": "FOLKS-USD",
                        "opened_at": "2026-04-25T04:24:46+00:00",
                        "ts_utc": "2026-04-25T04:25:48+00:00",
                        "reason": "maker_green_then_red_insurance",
                        "net": -0.04677,
                        "net_pct": -0.5846,
                        "exit_fee_bps": 40.0,
                        "spread_bps": 70.25,
                        "max_net_pct_on_cost": 0.4533,
                    },
                ],
            )

            payload = autopsy.build_payload(lanes=[{"lane": "test", "events_path": events_path}])
            blockers = payload["losses"][0]["potential_blockers"]
            rules = {row["rule"]: row for row in payload["candidate_blockers"]}

            self.assertIn("maker_exit_miss_before_close", blockers)
            self.assertIn("positive_mfe_closed_red", blockers)
            self.assertIn("green_then_red_insurance_loss", blockers)
            self.assertEqual(rules["maker_exit_miss_before_close"]["blocked_losses"], 1)
            self.assertEqual(rules["positive_mfe_taker_insurance_loss"]["blocked_losses"], 1)


if __name__ == "__main__":
    unittest.main()
