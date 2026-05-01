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

import build_kraken_maker_gate_ab_review as review


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class KrakenMakerGateAbReviewTests(unittest.TestCase):
    def test_gate_replay_pairs_open_close_and_scores_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events_path = root / "events.jsonl"
            board_path = root / "board.json"
            write_jsonl(
                events_path,
                [
                    {
                        "action": "open_maker_shadow",
                        "product_id": "WIDE-USD",
                        "ts_utc": "2026-04-24T00:00:00+00:00",
                        "entry_price": 1.0,
                        "ask_at_entry": 1.02,
                        "mer": 3.0,
                        "mode": "systemic",
                    },
                    {
                        "action": "close_maker_shadow",
                        "product_id": "WIDE-USD",
                        "ts_utc": "2026-04-24T00:01:00+00:00",
                        "net": 0.1,
                        "net_pct": 1.0,
                        "reason": "maker_rent_harvest",
                    },
                    {
                        "action": "open_maker_shadow",
                        "product_id": "TIGHT-USD",
                        "ts_utc": "2026-04-24T00:02:00+00:00",
                        "entry_price": 1.0,
                        "ask_at_entry": 1.003,
                        "mer": 4.0,
                        "mode": "systemic",
                    },
                    {
                        "action": "close_maker_shadow",
                        "product_id": "TIGHT-USD",
                        "ts_utc": "2026-04-24T00:03:00+00:00",
                        "net": -0.2,
                        "net_pct": -2.0,
                        "reason": "maker_no_mfe_adverse_stop",
                    },
                ],
            )
            board_path.write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "product_id": "WIDE-USD",
                                "playbook": "maker_harvest",
                                "mer": 3.0,
                                "spread_bps": 120.0,
                                "atr_12_bps": 30.0,
                            },
                            {
                                "product_id": "LOWATR-USD",
                                "playbook": "maker_harvest",
                                "mer": 3.0,
                                "spread_bps": 120.0,
                                "atr_12_bps": 10.0,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            payload = review.build_payload(
                events_path=events_path,
                board_path=board_path,
                gates=review.DEFAULT_GATES,
            )

            loose = next(row for row in payload["gate_summaries"] if row["gate"] == "loose_spread50_mer2p5")
            tight = next(row for row in payload["gate_summaries"] if row["gate"] == "tight_spread100_mer3p5")
            self.assertEqual(loose["admitted_trades"], 1)
            self.assertEqual(loose["admitted_losses"], 0)
            self.assertEqual(loose["avoided_losers"], 1)
            self.assertEqual(tight["admitted_trades"], 0)
            self.assertEqual(tight["missed_winners"], 1)
            self.assertEqual(payload["current_gate_counts"]["loose_spread50_mer2p5_atr20"], 1)

    def test_write_reports_outputs_decision_tape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "summary": {"historical_trades": 0, "current_board_rows": 1, "current_decision_rows": 1},
                "gate_summaries": [
                    {
                        "gate": "gate_a",
                        "min_spread_bps": 50.0,
                        "min_mer": 2.5,
                        "min_atr_12_bps": 0.0,
                        "admitted_trades": 0,
                        "admitted_net_usd": 0.0,
                        "admitted_avg_net_pct": 0.0,
                        "admitted_win_rate": 0.0,
                        "admitted_losses": 0,
                        "missed_winners": 0,
                        "avoided_losers": 0,
                    }
                ],
                "current_gate_counts": {"gate_a": 1},
                "current_gate_products": {"gate_a": ["WIDE-USD"]},
                "current_decisions": [
                    {
                        "generated_at": "2026-04-24T00:00:00+00:00",
                        "product_id": "WIDE-USD",
                        "mer": 3.0,
                        "spread_bps": 120.0,
                        "atr_12_bps": 30.0,
                        "pulse_score": 0.0,
                        "machinegun_score": 10.0,
                        "gate_passes": {"gate_a": True},
                        "gate_reasons": {"gate_a": "pass"},
                    }
                ],
            }

            review.write_reports(
                payload,
                json_path=root / "out.json",
                md_path=root / "out.md",
                csv_path=root / "out.csv",
                tape_path=root / "out.jsonl",
            )

            self.assertTrue((root / "out.json").exists())
            self.assertTrue((root / "out.md").exists())
            self.assertTrue((root / "out.csv").exists())
            tape_rows = (root / "out.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(tape_rows), 1)
            self.assertEqual(json.loads(tape_rows[0])["product_id"], "WIDE-USD")


if __name__ == "__main__":
    unittest.main()
