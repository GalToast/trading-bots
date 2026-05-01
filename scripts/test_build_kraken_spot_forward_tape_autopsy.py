import argparse
import json
import tempfile
import unittest
from pathlib import Path

from build_kraken_spot_forward_tape_autopsy import build, bucket, parse_horizons


class KrakenSpotForwardTapeAutopsyTests(unittest.TestCase):
    def test_bucket_edges_are_stable(self) -> None:
        self.assertEqual(bucket(15.0, [15.0, 30.0]), "-inf..15")
        self.assertEqual(bucket(16.0, [15.0, 30.0]), "15..30")
        self.assertEqual(bucket(31.0, [15.0, 30.0]), ">30")

    def test_parse_horizons_sorts_and_dedupes(self) -> None:
        self.assertEqual(parse_horizons("600,60,180,60"), [60, 180, 600])

    def test_build_autopsy_tracks_oracle_best_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tape = root / "tape.jsonl"
            json_path = root / "autopsy.json"
            csv_path = root / "autopsy.csv"
            md_path = root / "autopsy.md"
            rows = [
                {
                    "entry_at": "2026-04-24T00:00:00+00:00",
                    "product_id": "AAA-USD",
                    "status": "complete",
                    "entry_bid": 10.0,
                    "entry_ask": 10.1,
                    "entry_best_move_window": "last",
                    "entry_verdict": "clears_both_fee_models",
                    "entry_spread_bps": 10.0,
                    "entry_best_move_bps": 200.0,
                    "entry_kraken_edge_bps": 80.0,
                    "entry_row": {"source": "websocket_ticker", "samples": 20},
                    "marks": {
                        "60": {"net_pnl": -0.5, "exit_bid": 10.05},
                        "180": {"net_pnl": 0.25, "exit_bid": 10.2},
                    },
                },
                {
                    "entry_at": "2026-04-24T00:01:00+00:00",
                    "product_id": "BBB-USD",
                    "status": "complete",
                    "entry_bid": 20.0,
                    "entry_ask": 20.1,
                    "entry_best_move_window": "5m",
                    "entry_verdict": "kraken_fee_flip_candidate",
                    "entry_spread_bps": 20.0,
                    "entry_best_move_bps": 250.0,
                    "entry_kraken_edge_bps": 90.0,
                    "entry_row": {"source": "rest_ticker", "samples": 5},
                    "marks": {
                        "60": {"net_pnl": -0.75, "exit_bid": 19.9},
                        "180": {"net_pnl": -1.0, "exit_bid": 19.8},
                    },
                },
            ]
            tape.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            payload = build(
                argparse.Namespace(
                    tape_path=str(tape),
                    json_path=str(json_path),
                    csv_path=str(csv_path),
                    md_path=str(md_path),
                    horizons_seconds="60,180",
                )
            )
            self.assertEqual(payload["summary"]["entries"], 2)
            self.assertEqual(payload["summary"]["oracle_green_entries"], 1)
            self.assertEqual(payload["rows"][0]["oracle_best_horizon"], 180)
            self.assertTrue(json_path.exists())
            self.assertTrue(csv_path.exists())
            self.assertTrue(md_path.exists())


if __name__ == "__main__":
    unittest.main()
