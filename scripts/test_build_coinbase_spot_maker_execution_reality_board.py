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

import build_coinbase_spot_maker_execution_reality_board as board


class CoinbaseSpotMakerExecutionRealityBoardTests(unittest.TestCase):
    def test_current_maker_probe_requires_fill_haircut_survival(self) -> None:
        row = {
            "product_id": "EDGE-USD",
            "quote_currency": "USD",
            "live_tradable": True,
            "status": "ok",
            "live_route_state": "ready_direct_usd_or_stable",
            "pulse_state": "hot_momentum",
            "pulse_score": 10,
            "spread_bps": 5,
            "ret_15m_pct": 0.1,
            "ret_60m_pct": 3.0,
            "ret_4h_pct": 2.0,
            "median_range_60m_pct": 0.2,
            "p90_range_60m_pct": 0.8,
            "quote_volume_native": 2_000_000,
        }
        built = board.build_row(
            row,
            maker_fee_bps=60,
            taker_fee_bps=120,
            zero_maker_fee_bps=0,
            profit_buffer_pct=0.75,
            max_spread_bps=75,
            min_fill_score=55,
            adverse_spread_mult=1.0,
            noise_haircut_mult=0.25,
            missed_fill_haircut_pct=0.5,
        )
        self.assertIsNotNone(built)
        assert built is not None
        self.assertEqual(built["current_verdict"], "maker_taker_shadow_probe")
        self.assertGreater(built["current_maker_taker_realistic_edge_pct"], 0)

    def test_math_only_gets_rejected_when_fill_score_is_too_low(self) -> None:
        row = {
            "product_id": "CHASE-USD",
            "quote_currency": "USD",
            "live_tradable": True,
            "status": "ok",
            "live_route_state": "ready_direct_usd_or_stable",
            "pulse_state": "hot_momentum",
            "pulse_score": 10,
            "spread_bps": 20,
            "ret_15m_pct": 5.0,
            "ret_60m_pct": 5.5,
            "ret_4h_pct": 6.0,
            "median_range_60m_pct": 0.1,
            "p90_range_60m_pct": 0.2,
            "quote_volume_native": 10_000,
        }
        built = board.build_row(
            row,
            maker_fee_bps=60,
            taker_fee_bps=120,
            zero_maker_fee_bps=0,
            profit_buffer_pct=0.75,
            max_spread_bps=75,
            min_fill_score=55,
            adverse_spread_mult=1.0,
            noise_haircut_mult=0.25,
            missed_fill_haircut_pct=0.5,
        )
        self.assertIsNotNone(built)
        assert built is not None
        self.assertEqual(built["current_verdict"], "reject_post_only_fill_risk")

    def test_payload_counts_current_and_zero_maker_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pulse = Path(tmpdir) / "pulse.json"
            pulse.write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "product_id": "ZERO-USD",
                                "quote_currency": "USD",
                                "live_tradable": True,
                                "status": "ok",
                                "live_route_state": "ready_direct_usd_or_stable",
                                "spread_bps": 5,
                                "ret_15m_pct": 0.0,
                                "ret_60m_pct": 2.4,
                                "ret_4h_pct": 2.4,
                                "median_range_60m_pct": 0.1,
                                "p90_range_60m_pct": 0.5,
                                "quote_volume_native": 2_000_000,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            payload = board.build_payload(
                pulse_path=pulse,
                maker_fee_bps=60,
                taker_fee_bps=120,
                zero_maker_fee_bps=0,
                profit_buffer_pct=0.75,
                max_spread_bps=75,
                min_fill_score=55,
                adverse_spread_mult=1.0,
                noise_haircut_mult=0.25,
                missed_fill_haircut_pct=0.5,
                top=10,
            )
        self.assertEqual(payload["summary"]["rows"], 1)
        self.assertIn("maker_maker_only_needs_exit_fill_proof", payload["summary"]["current_verdict_counts"])
        self.assertIn("maker_taker_shadow_probe", payload["summary"]["zero_maker_verdict_counts"])


if __name__ == "__main__":
    unittest.main()
