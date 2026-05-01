import unittest

from kraken_spot_client import KrakenPair
from build_coinbase_kraken_spot_overlap_board import (
    build_rows,
    kraken_catalog_from_pairs,
    merge_candidates,
    split_product_id,
)


class CoinbaseKrakenSpotOverlapBoardTests(unittest.TestCase):
    def test_split_product_id_normalizes_common_aliases(self) -> None:
        self.assertEqual(split_product_id("XBT-USD"), ("BTC", "USD"))
        self.assertEqual(split_product_id("doge/usdt"), ("DOGE", "USDT"))

    def test_kraken_catalog_prefers_usd_quote(self) -> None:
        pairs = [
            KrakenPair("BADGERUSDT", "BADGERUSDT", "BADGER/USDT", "BADGER", "USDT", 1.0, 0.0, 0.1, 8, 4, "online"),
            KrakenPair("BADGERUSD", "BADGERUSD", "BADGER/USD", "BADGER", "USD", 1.0, 0.0, 0.1, 8, 4, "online"),
        ]
        catalog = kraken_catalog_from_pairs(pairs)
        self.assertEqual(catalog["BADGER"][0]["product_id"], "BADGER-USD")

    def test_build_rows_marks_coinbase_only_and_velocity(self) -> None:
        candidates = merge_candidates(
            [
                {"source": "tail", "product_id": "BADGER-USD", "score": 0.9, "signal_state": "historical"},
                {"source": "tail", "product_id": "BOBBOB-USD", "score": 0.99, "signal_state": "historical"},
            ]
        )
        catalog = kraken_catalog_from_pairs(
            [KrakenPair("BADGERUSD", "BADGERUSD", "BADGER/USD", "BADGER", "USD", 1.0, 0.0, 0.1, 8, 4, "online")]
        )
        rows = build_rows(
            candidates,
            catalog,
            {"BADGER-USD": {"product_id": "BADGER-USD", "signal_state": "live_hot", "spread_bps": 10}},
            {"BADGER-USD": {"product_id": "BADGER-USD", "verdict": "clears_both_fee_models", "kraken_edge_bps": 25}},
        )
        by_product = {row["product_id"]: row for row in rows}
        self.assertEqual(by_product["BADGER-USD"]["kraken_route_state"], "kraken_velocity_board")
        self.assertEqual(by_product["BOBBOB-USD"]["kraken_route_state"], "coinbase_only_no_kraken_spot_match")


if __name__ == "__main__":
    unittest.main()
