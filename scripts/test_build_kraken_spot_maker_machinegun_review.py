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

import build_kraken_spot_maker_machinegun_review as review


class KrakenMakerMachinegunReviewTests(unittest.TestCase):
    def test_flags_legacy_microcap_trail_and_missing_mer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state = root / "state.json"
            events = root / "events.jsonl"
            state.write_text(
                json.dumps(
                    {
                        "state": {
                            "cash_usd": 20,
                            "realized_net_usd": 0,
                            "realized_closes": 0,
                            "active_positions": {
                                "AKE-USD": {
                                    "entry_price": 0.00038148,
                                    "cost_usd": 80,
                                    "trail_giveback_pct": 0.25,
                                    "entry_mer": 0,
                                    "entry_tail_prob": 0,
                                    "entry_fast_green_prob": 0,
                                }
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            events.write_text(json.dumps({"action": "open_maker_shadow", "product_id": "AKE-USD"}) + "\n", encoding="utf-8")
            payload = review.build_payload(state_path=state, events_path=events, microcap_trail_floor_pct=2.5)

        self.assertEqual(payload["summary"]["proof_verdict"], "no_close_proof")
        self.assertIn("legacy_microcap_trail_too_tight", payload["summary"]["risk_flags"])
        self.assertIn("missing_mer_join", payload["summary"]["risk_flags"])

    def test_flags_non_maker_open_and_over_cap(self) -> None:
        positions = {}
        for idx in range(7):
            playbook = "frontier_machinegun" if idx == 0 else "maker_harvest"
            positions[f"P{idx}-USD"] = {
                "entry_price": 1.0,
                "cost_usd": 10,
                "trail_giveback_pct": 2.5,
                "entry_mer": 1.0,
                "entry_tail_prob": 0.7,
                "entry_fast_green_prob": 0.1,
                "playbook": playbook,
            }
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state = root / "state.json"
            events = root / "events.jsonl"
            state.write_text(
                json.dumps(
                    {
                        "state": {
                            "cash_usd": 20,
                            "maker_fee_bps": 25,
                            "realized_net_usd": 1,
                            "realized_closes": 1,
                            "active_positions": positions,
                        }
                    }
                ),
                encoding="utf-8",
            )
            events.write_text("", encoding="utf-8")
            payload = review.build_payload(state_path=state, events_path=events, microcap_trail_floor_pct=2.5)

        self.assertIn("non_maker_playbook_open", payload["summary"]["risk_flags"])
        self.assertIn("open_position_count_over_idiosyncratic_cap", payload["summary"]["risk_flags"])
        self.assertEqual(payload["summary"]["proof_verdict"], "green_with_execution_risks")

    def test_flags_position_cost_over_max_quote_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state = root / "state.json"
            events = root / "events.jsonl"
            state.write_text(
                json.dumps(
                    {
                        "state": {
                            "cash_usd": 80,
                            "realized_net_usd": 0.1,
                            "realized_closes": 1,
                            "maker_fee_bps": 25,
                            "max_quote_usd": 8,
                            "active_positions": {
                                "KSM-USD": {
                                    "entry_price": 4.85,
                                    "cost_usd": 14.73,
                                    "trail_giveback_pct": 2.5,
                                    "entry_mer": 4.8,
                                    "entry_tail_prob": 0.8,
                                    "entry_fast_green_prob": 0.1,
                                    "playbook": "maker_harvest",
                                }
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            events.write_text("", encoding="utf-8")
            payload = review.build_payload(state_path=state, events_path=events, microcap_trail_floor_pct=2.5)

        self.assertIn("position_cost_over_max_quote_cap", payload["summary"]["risk_flags"])
        self.assertEqual(payload["summary"]["proof_verdict"], "green_with_execution_risks")


if __name__ == "__main__":
    unittest.main()
