#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_spot_venue_handoff_policy as policy


class SpotVenueHandoffPolicyTests(unittest.TestCase):
    def test_uses_kraken_product_id_for_overlap_routes(self) -> None:
        rows = policy.kraken_overlap_actions(
            [
                {
                    "product_id": "XBT-USD",
                    "kraken_product_id": "BTC-USD",
                    "coinbase_signal_state": "live_hot",
                    "kraken_route_state": "kraken_live_radar",
                    "can_trade_100": True,
                    "kraken_edge_bps": 12.5,
                    "kraken_spread_bps": 3.0,
                    "candidate_score": 20,
                }
            ]
        )

        self.assertEqual(rows[0]["product_id"], "BTC-USD")
        self.assertEqual(rows[0]["source_product_id"], "XBT-USD")
        self.assertEqual(rows[0]["action"], "kraken_taker_shadow")

    def test_extreme_spread_maker_is_proof_only(self) -> None:
        radar = {"BMB-USD": {"product_id": "BMB-USD", "can_trade_starting_cash": True}}
        rows = policy.kraken_maker_actions(
            [
                {
                    "product_id": "BMB-USD",
                    "mer": 1.7,
                    "spread_bps": 2200,
                    "atr_12_bps": 1200,
                    "pulse_score": 90,
                }
            ],
            radar,
            min_mer=0.5,
            max_maker_spread_bps=750,
        )

        self.assertEqual(rows[0]["action"], "kraken_maker_proof_only")
        self.assertEqual(rows[0]["proof_status"], "proof_only_extreme_spread")

    def test_current_fee_coinbase_candidate_is_shadow_ready(self) -> None:
        rows = policy.coinbase_actions(
            [
                {
                    "product_id": "SPX-USD",
                    "current_verdict": "maker_taker_shadow_probe",
                    "current_maker_taker_realistic_edge_pct": 0.7,
                    "current_maker_entry_fill_score": 81,
                    "spread_bps": 15,
                }
            ]
        )

        self.assertEqual(rows[0]["action"], "coinbase_maker_taker_shadow")
        self.assertEqual(rows[0]["proof_status"], "shadow_ready")

    def test_build_payload_dedupes_and_sorts_shadow_ready_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coinbase = root / "coinbase.json"
            route = root / "route.json"
            overlap = root / "overlap.json"
            maker = root / "maker.json"
            radar = root / "radar.json"
            coinbase.write_text(json.dumps({"rows": []}), encoding="utf-8")
            route.write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "kraken_product_id": "AKE-USD",
                                "coinbase_product_id": "AKE-USD",
                                "route_verdict": "kraken_fee_flip_candidate",
                                "can_trade_starting_cash": True,
                                "kraken_edge_bps": 80,
                                "kraken_spread_bps": 15,
                                "route_score": 10,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            overlap.write_text(json.dumps({"rows": []}), encoding="utf-8")
            maker.write_text(json.dumps({"rows": []}), encoding="utf-8")
            radar.write_text(json.dumps({"rows": []}), encoding="utf-8")

            payload = policy.build_payload(
                coinbase_maker_reality_path=coinbase,
                kraken_route_path=route,
                overlap_path=overlap,
                kraken_maker_opportunity_path=maker,
                kraken_radar_path=radar,
            )

        self.assertEqual(payload["actions"][0]["action"], "kraken_taker_shadow")
        self.assertEqual(payload["proof_counts"]["shadow_ready"], 1)

    def test_loss_tracker_block_demotes_shadow_ready_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coinbase = root / "coinbase.json"
            route = root / "route.json"
            overlap = root / "overlap.json"
            maker = root / "maker.json"
            radar = root / "radar.json"
            tracker = root / "loss_tracker.json"
            coinbase.write_text(json.dumps({"rows": []}), encoding="utf-8")
            route.write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "kraken_product_id": "BERT-USD",
                                "coinbase_product_id": "BERT-USD",
                                "route_verdict": "kraken_fee_flip_candidate",
                                "can_trade_starting_cash": True,
                                "kraken_edge_bps": 100,
                                "kraken_spread_bps": 20,
                                "route_score": 15,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            overlap.write_text(json.dumps({"rows": []}), encoding="utf-8")
            maker.write_text(json.dumps({"rows": []}), encoding="utf-8")
            radar.write_text(json.dumps({"rows": []}), encoding="utf-8")
            tracker.write_text(
                json.dumps(
                    {
                        "blocked_until": {"BERT-USD": time.time() + 3600},
                        "consecutive_losses": {"BERT-USD": 3},
                        "total_losses": {"BERT-USD": 7},
                    }
                ),
                encoding="utf-8",
            )

            payload = policy.build_payload(
                coinbase_maker_reality_path=coinbase,
                kraken_route_path=route,
                overlap_path=overlap,
                kraken_maker_opportunity_path=maker,
                kraken_radar_path=radar,
                loss_tracker_paths=[tracker],
            )

        self.assertEqual(payload["actions"][0]["action"], "kraken_death_spiral_blocked")
        self.assertEqual(payload["actions"][0]["proof_status"], "death_spiral_blocked")
        self.assertIn("BERT-USD", payload["loss_tracker_blocks"])


if __name__ == "__main__":
    unittest.main()
