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

import build_kraken_maker_mfe_exit_replay as replay


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class KrakenMakerMfeExitReplayTests(unittest.TestCase):
    def test_green_then_red_loser_can_be_replayed_as_insurance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events_path = root / "events.jsonl"
            write_jsonl(
                events_path,
                [
                    {
                        "action": "close_maker_shadow",
                        "product_id": "SAVE-USD",
                        "cost_usd": 10.0,
                        "net": -0.20,
                        "net_pct": -2.0,
                        "max_net_pct_on_cost": 0.20,
                        "max_net_pnl": 0.02,
                        "reason": "maker_no_mfe_adverse_stop",
                    },
                    {
                        "action": "close_maker_shadow",
                        "product_id": "WIN-USD",
                        "cost_usd": 10.0,
                        "net": 0.10,
                        "net_pct": 1.0,
                        "max_net_pct_on_cost": 1.0,
                        "max_net_pnl": 0.10,
                        "reason": "maker_rent_harvest",
                    },
                ],
            )

            payload = replay.build_payload(
                events_path=events_path,
                policies=[replay.ExitPolicy("insurance", 0.0, giveback_pct=0.05)],
            )

        self.assertEqual(payload["summary"]["closed_trades"], 2)
        self.assertEqual(payload["summary"]["censored_winners"], 1)
        self.assertEqual(payload["summary"]["green_then_red_losers"], 1)
        summary = payload["policy_summaries"][0]
        self.assertEqual(summary["improved_products"], ["SAVE-USD"])
        self.assertAlmostEqual(summary["simulated_net_usd"], 0.115, places=6)
        self.assertAlmostEqual(summary["delta_net_usd"], 0.215, places=6)

    def test_policy_does_not_change_when_activation_not_reached(self) -> None:
        trade = {
            "product_id": "LOW-USD",
            "cost_usd": 8.0,
            "net": -0.08,
            "net_pct": -1.0,
            "max_net_pct_on_cost": 0.04,
        }
        row = replay.replay_trade(
            trade,
            replay.ExitPolicy("needs_10bps", 0.10, giveback_pct=0.05),
        )
        self.assertFalse(row["changed"])
        self.assertEqual(row["reason"], "not_activated")
        self.assertEqual(row["simulated_net"], -0.08)


if __name__ == "__main__":
    unittest.main()
