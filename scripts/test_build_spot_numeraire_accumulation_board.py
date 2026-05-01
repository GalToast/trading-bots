#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_spot_numeraire_accumulation_board as board


class FakeKrakenClient:
    def asset_pairs(self) -> dict[str, Any]:
        return {
            "XXBTZUSD": {
                "altname": "XBTUSD",
                "wsname": "XBT/USD",
                "ordermin": "0.00001",
                "costmin": "0.50",
                "pair_decimals": 1,
                "lot_decimals": 8,
                "status": "online",
            },
            "XETHZUSD": {
                "altname": "ETHUSD",
                "wsname": "ETH/USD",
                "ordermin": "0.001",
                "costmin": "0.50",
                "pair_decimals": 2,
                "lot_decimals": 8,
                "status": "online",
            },
            "XETHXXBT": {
                "altname": "ETHXBT",
                "wsname": "ETH/XBT",
                "ordermin": "0.001",
                "costmin": "0.00001",
                "pair_decimals": 6,
                "lot_decimals": 8,
                "status": "online",
            },
        }

    def ticker(self, rest_pairs: list[str]) -> dict[str, Any]:
        rows = {
            "XXBTZUSD": {
                "a": ["80000.0", "1", "2"],
                "b": ["79900.0", "1", "2"],
                "c": ["79950.0", "1"],
                "v": ["100", "100"],
            },
            "XETHZUSD": {
                "a": ["4101.0", "1", "50"],
                "b": ["4100.0", "1", "50"],
                "c": ["4100.5", "1"],
                "v": ["1000", "1000"],
            },
            "XETHXXBT": {
                "a": ["0.050000", "1", "100"],
                "b": ["0.049990", "1", "100"],
                "c": ["0.050000", "1"],
                "v": ["1000", "1000"],
            },
        }
        return {pair: rows[pair] for pair in rest_pairs if pair in rows}


class SpotNumeraireAccumulationBoardTests(unittest.TestCase):
    def test_triangular_dislocation_can_score_positive_after_fees(self) -> None:
        payload = board.build_payload(
            client=FakeKrakenClient(),
            numeraires={"BTC"},
            quotes={"USD", "BTC"},
            stable_assets={"USD"},
            start_usd=50.0,
            taker_fee_bps=10.0,
            min_net_bps=1.0,
            chunk_size=10,
            max_routes=100,
        )

        positive = [row for row in payload["rows"] if row["executable_positive"]]

        self.assertTrue(positive)
        self.assertEqual(positive[0]["route"], "BTC->ETH->USD->BTC")
        self.assertGreater(positive[0]["numeraire_edge_bps"], 100.0)

    def test_direct_roundtrip_is_negative_after_spread_and_fees(self) -> None:
        pairs = board.load_pairs(FakeKrakenClient(), {"USD", "BTC"})
        books = board.fetch_tickers(FakeKrakenClient(), pairs, 10)
        rates = board.infer_usd_rates(pairs, books, {"USD"})
        edges = board.build_edges(pairs, books, rates)
        routes = [route for route in board.candidate_routes(edges, {"USD"}, 100) if route[0].product_id == "BTC-USD" and len(route) == 2]

        rows = board.score_routes(routes, usd_rates=rates, start_usd=50.0, taker_fee_bps=40.0, min_net_bps=1.0)

        self.assertTrue(rows)
        self.assertLess(rows[0]["numeraire_edge_bps"], 0.0)
        self.assertFalse(rows[0]["executable_positive"])
        self.assertIn("net_edge_below_threshold", rows[0]["blockers"])


if __name__ == "__main__":
    unittest.main()
