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

import build_kraken_maker_reality_cap_audit as audit


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class KrakenMakerRealityCapAuditTests(unittest.TestCase):
    def test_cap_breach_winner_is_charged_as_stop_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            write_jsonl(
                events_path,
                [
                    {
                        "action": "close_maker_shadow",
                        "product_id": "MIRACLE-USD",
                        "cost_usd": 10.0,
                        "net": 0.40,
                        "net_pct": 4.0,
                        "min_net_pct_on_cost": -4.0,
                    },
                    {
                        "action": "close_maker_shadow",
                        "product_id": "CLEAN-USD",
                        "cost_usd": 10.0,
                        "net": 0.10,
                        "net_pct": 1.0,
                        "min_net_pct_on_cost": -0.5,
                    },
                ],
            )

            row = audit.audit_lane("test", events_path, cap_pct=3.0)

        self.assertEqual(row.closes, 2)
        self.assertEqual(row.booked_wins, 2)
        self.assertEqual(row.cap_breach_closes, 1)
        self.assertEqual(row.adjusted_wins, 1)
        self.assertEqual(row.adjusted_losses, 1)
        self.assertAlmostEqual(row.realized_net_usd, 0.50)
        self.assertAlmostEqual(row.cap_breach_adjusted_loss_usd, -0.30)
        self.assertAlmostEqual(row.adjusted_net_usd, -0.20)
        self.assertEqual(row.sample_breaches[0]["adjusted_stop_net"], -0.30)

    def test_maker_dependent_wins_are_counted_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            write_jsonl(
                events_path,
                [
                    {
                        "action": "close_maker_shadow",
                        "product_id": "ASKGREEN-USD",
                        "cost_usd": 12.0,
                        "net": 0.06,
                        "net_pct": 0.5,
                        "min_net_pct_on_cost": -0.25,
                        "bid_taker_net_pct": -0.7,
                    },
                    {
                        "action": "close_maker_shadow",
                        "product_id": "BIDGREEN-USD",
                        "cost_usd": 12.0,
                        "net": 0.06,
                        "net_pct": 0.5,
                        "min_net_pct_on_cost": -0.25,
                        "bid_taker_net_pct": 0.2,
                    },
                ],
            )

            row = audit.audit_lane("test", events_path, cap_pct=3.0)

        self.assertEqual(row.maker_dependent_wins, 1)
        self.assertEqual(row.cap_breach_closes, 0)
        self.assertAlmostEqual(row.adjusted_net_usd, 0.12)

    def test_build_payload_summarizes_multiple_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.jsonl"
            second = root / "second.jsonl"
            write_jsonl(
                first,
                [
                    {
                        "action": "close_maker_shadow",
                        "cost_usd": 20.0,
                        "net": 0.20,
                        "net_pct": 1.0,
                        "min_net_pct_on_cost": -3.2,
                    }
                ],
            )
            write_jsonl(
                second,
                [
                    {
                        "action": "close_maker_shadow",
                        "cost_usd": 5.0,
                        "net": -0.05,
                        "net_pct": -1.0,
                        "min_net_pct_on_cost": -0.5,
                    }
                ],
            )

            payload = audit.build_payload({"first": first, "second": second}, cap_pct=3.0)

        self.assertEqual(payload["summary"]["total_closes"], 2)
        self.assertEqual(payload["summary"]["total_cap_breaches"], 1)
        self.assertAlmostEqual(payload["summary"]["total_booked_net_usd"], 0.15)
        self.assertAlmostEqual(payload["summary"]["total_adjusted_net_usd"], -0.65)
        self.assertAlmostEqual(payload["summary"]["total_cap_breach_adjusted_loss_usd"], -0.60)


if __name__ == "__main__":
    unittest.main()
