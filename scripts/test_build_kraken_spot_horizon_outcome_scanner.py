#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_kraken_spot_horizon_outcome_scanner as scanner


class FakeKrakenClient:
    def asset_pairs(self) -> dict[str, Any]:
        return {
            "AAAUSD": {
                "altname": "AAAUSD",
                "wsname": "AAA/USD",
                "ordermin": "1",
                "costmin": "1",
                "pair_decimals": 4,
                "lot_decimals": 8,
                "status": "online",
            },
            "AAABTC": {
                "altname": "AAABTC",
                "wsname": "AAA/BTC",
                "ordermin": "1",
                "costmin": "0.00001",
                "pair_decimals": 8,
                "lot_decimals": 8,
                "status": "online",
            },
        }


def write_cache(path: Path, rows: list[tuple[float, float, float]]) -> None:
    payload = {"samples": {"AAAUSD": [{"ts": ts, "bid": bid, "ask": ask} for ts, bid, ask in rows]}}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class KrakenSpotHorizonOutcomeScannerTests(unittest.TestCase):
    def test_momentum_signal_records_fee_paid_target_and_mfe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            write_cache(
                cache_path,
                [
                    (0.0, 1.00, 1.01),
                    (60.0, 1.03, 1.04),
                    (120.0, 1.06, 1.07),
                    (300.0, 1.15, 1.16),
                    (600.0, 1.20, 1.21),
                ],
            )

            payload = scanner.build_payload(
                client=FakeKrakenClient(),
                cache_path=cache_path,
                products={"AAA-USD"},
                quote_currencies=set(),
                signal_lookbacks=[60.0],
                horizons=[180.0, 480.0],
                min_signal_bps=100.0,
                max_spread_bps=200.0,
                taker_fee_bps=10.0,
                start_usd=50.0,
                target_net_bps=50.0,
                stop_loss_bps=150.0,
                cooldown_seconds=1000.0,
                max_horizon_lag_seconds=400.0,
            )

            self.assertGreater(payload["summary"]["events_scored"], 0)
            self.assertGreater(payload["summary"]["net_positive_price_only"], 0)
            self.assertGreater(payload["summary"]["best_mfe_bps"], payload["summary"]["best_net_bps"] - 1.0)
            self.assertTrue(payload["rows"][0]["target_before_stop"])
            self.assertIn("fillability_unproven", payload["rows"][0]["blockers"])

    def test_stop_before_target_is_labeled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            write_cache(
                cache_path,
                [
                    (0.0, 1.00, 1.01),
                    (60.0, 1.03, 1.04),
                    (120.0, 1.06, 1.07),
                    (300.0, 0.98, 0.99),
                    (600.0, 0.90, 0.91),
                ],
            )

            payload = scanner.build_payload(
                client=FakeKrakenClient(),
                cache_path=cache_path,
                products={"AAA-USD"},
                quote_currencies=set(),
                signal_lookbacks=[60.0],
                horizons=[300.0],
                min_signal_bps=100.0,
                max_spread_bps=200.0,
                taker_fee_bps=10.0,
                start_usd=50.0,
                target_net_bps=50.0,
                stop_loss_bps=150.0,
                cooldown_seconds=1000.0,
                max_horizon_lag_seconds=400.0,
            )

            self.assertEqual(payload["summary"]["net_positive_price_only"], 0)
            self.assertTrue(payload["rows"][0]["stop_before_target"])
            self.assertIn("stop_before_target", payload["rows"][0]["blockers"])

    def test_empty_products_filters_by_quote_currency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            write_cache(
                cache_path,
                [
                    (0.0, 1.00, 1.01),
                    (60.0, 1.03, 1.04),
                    (120.0, 1.08, 1.09),
                    (300.0, 1.10, 1.11),
                    (600.0, 1.12, 1.13),
                ],
            )

            payload = scanner.build_payload(
                client=FakeKrakenClient(),
                cache_path=cache_path,
                products=set(),
                quote_currencies={"USD"},
                signal_lookbacks=[60.0],
                horizons=[180.0, 480.0],
                min_signal_bps=100.0,
                max_spread_bps=200.0,
                taker_fee_bps=10.0,
                start_usd=50.0,
                target_net_bps=50.0,
                stop_loss_bps=150.0,
                cooldown_seconds=1.0,
                max_horizon_lag_seconds=400.0,
            )

            self.assertEqual(payload["summary"]["products_loaded"], 1)
            self.assertEqual(payload["rows"][0]["product_id"], "AAA-USD")


if __name__ == "__main__":
    unittest.main()
