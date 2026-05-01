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

import build_kraken_vulture_reversal_replay as vulture


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
            }
        }


def write_cache(path: Path, bids: list[float], asks: list[float] | None = None) -> None:
    asks = asks or [bid + 0.01 for bid in bids]
    rows = [{"ts": 1000.0 + (idx * 10.0), "bid": bid, "ask": ask} for idx, (bid, ask) in enumerate(zip(bids, asks))]
    path.write_text(json.dumps({"samples": {"AAAUSD": rows}, "updated_at": "test"}, indent=2), encoding="utf-8")


class KrakenVultureReversalReplayTests(unittest.TestCase):
    def test_causal_dump_recovery_scores_positive_after_next_sample_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            write_cache(cache_path, [1.00, 1.00, 1.00, 0.90, 0.91, 0.98, 1.02])

            payload = vulture.build_payload(
                client=FakeKrakenClient(),
                cache_path=cache_path,
                products={"AAA-USD"},
                quote_currencies=set(),
                horizons=[20.0, 30.0],
                lookback_samples=3,
                min_dump_bps=500.0,
                max_spread_bps=500.0,
                taker_fee_bps=10.0,
                start_usd=50.0,
                min_net_bps=10.0,
                cooldown_samples=1,
            )

            self.assertGreater(payload["summary"]["net_positive_price_only"], 0)
            self.assertGreater(payload["summary"]["best_net_bps"], 500.0)
            self.assertIn("fillability_unproven", payload["rows"][0]["blockers"])

    def test_flat_recovery_is_blocked_by_fees_and_never_green(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            write_cache(cache_path, [1.00, 1.00, 1.00, 0.90, 0.901, 0.902, 0.903])

            payload = vulture.build_payload(
                client=FakeKrakenClient(),
                cache_path=cache_path,
                products={"AAA-USD"},
                quote_currencies=set(),
                horizons=[20.0, 30.0],
                lookback_samples=3,
                min_dump_bps=500.0,
                max_spread_bps=500.0,
                taker_fee_bps=40.0,
                start_usd=50.0,
                min_net_bps=10.0,
                cooldown_samples=1,
            )

            self.assertEqual(payload["summary"]["net_positive_price_only"], 0)
            self.assertLess(payload["summary"]["best_net_bps"], 0.0)
            self.assertIn("never_fee_green", payload["rows"][0]["blockers"])

    def test_empty_products_can_filter_by_quote_currency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            write_cache(cache_path, [1.00, 1.00, 1.00, 0.90, 0.91, 0.98, 1.02])

            payload = vulture.build_payload(
                client=FakeKrakenClient(),
                cache_path=cache_path,
                products=set(),
                quote_currencies={"USD"},
                horizons=[20.0],
                lookback_samples=3,
                min_dump_bps=500.0,
                max_spread_bps=500.0,
                taker_fee_bps=10.0,
                start_usd=50.0,
                min_net_bps=10.0,
                cooldown_samples=1,
            )

            self.assertEqual(payload["summary"]["products_loaded"], 1)
            self.assertIn("AAA-USD", payload["by_product"])


if __name__ == "__main__":
    unittest.main()
