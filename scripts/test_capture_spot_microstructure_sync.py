#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from capture_spot_microstructure_sync import normalize_coinbase_pricebooks, normalize_kraken_ticker_payload


class CaptureSpotMicrostructureSyncTests(unittest.TestCase):
    def test_normalize_kraken_ticker_payload_maps_alias_and_prices(self) -> None:
        payload = {
            "result": {
                "XXBTZUSD": {
                    "c": ["71684.5", "1"],
                    "b": ["71684.4", "1"],
                    "a": ["71684.6", "1"],
                }
            }
        }
        out = normalize_kraken_ticker_payload(payload, {"XXBTZUSD": "BTC-USD"})
        self.assertEqual(set(out.keys()), {"BTC-USD"})
        self.assertEqual(out["BTC-USD"]["last"], 71684.5)
        self.assertEqual(out["BTC-USD"]["mid"], 71684.5)

    def test_normalize_coinbase_pricebooks_extracts_bid_ask_and_sizes(self) -> None:
        payload = {
            "pricebooks": [
                {
                    "product_id": "RAVE-USD",
                    "bids": [{"price": "2.51", "size": "102.4"}],
                    "asks": [{"price": "2.53", "size": "88.9"}],
                }
            ]
        }
        out = normalize_coinbase_pricebooks(payload)
        self.assertEqual(set(out.keys()), {"RAVE-USD"})
        self.assertEqual(out["RAVE-USD"]["bid"], 2.51)
        self.assertEqual(out["RAVE-USD"]["ask"], 2.53)
        self.assertEqual(out["RAVE-USD"]["bid_size"], 102.4)
        self.assertAlmostEqual(out["RAVE-USD"]["mid"], 2.52)


if __name__ == "__main__":
    unittest.main()
