import unittest

from kraken_spot_client import normalize_pair_name, parse_pair, parse_ticker


class KrakenSpotClientTests(unittest.TestCase):
    def test_normalize_pair_name(self) -> None:
        self.assertEqual(normalize_pair_name("XBTUSD"), "BTC/USD")
        self.assertEqual(normalize_pair_name("ETH/USDT"), "ETH/USDT")
        self.assertEqual(normalize_pair_name("doge-usd"), "DOGE/USD")

    def test_parse_pair_uses_wsname(self) -> None:
        pair = parse_pair(
            "XXBTZUSD",
            {
                "altname": "XBTUSD",
                "wsname": "XBT/USD",
                "ordermin": "0.00005",
                "costmin": "5",
                "pair_decimals": 1,
                "lot_decimals": 8,
                "status": "online",
            },
        )
        self.assertIsNotNone(pair)
        assert pair is not None
        self.assertEqual(pair.base, "BTC")
        self.assertEqual(pair.quote, "USD")
        self.assertEqual(pair.wsname, "XBT/USD")
        self.assertEqual(pair.order_min, 0.00005)
        self.assertEqual(pair.cost_min, 5.0)

    def test_parse_pair_skips_dark_pool(self) -> None:
        self.assertIsNone(parse_pair("XXBTZUSD.d", {"altname": "XBTUSD.d", "wsname": "XBT/USD.d"}))

    def test_parse_ticker(self) -> None:
        ticker = parse_ticker(
            "XXBTZUSD",
            "XBT/USD",
            {
                "a": ["66001.0", "1", "1.0"],
                "b": ["66000.0", "1", "1.0"],
                "c": ["66000.5", "0.01"],
                "v": ["10", "25"],
            },
        )
        self.assertIsNotNone(ticker)
        assert ticker is not None
        self.assertEqual(ticker.bid, 66000.0)
        self.assertEqual(ticker.ask, 66001.0)
        self.assertEqual(ticker.volume_24h, 25.0)


if __name__ == "__main__":
    unittest.main()
