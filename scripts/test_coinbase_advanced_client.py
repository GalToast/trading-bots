#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import coinbase_advanced_client as client_mod


def _decode_segment(segment: str) -> dict[str, object]:
    padding = "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + padding).decode("utf-8"))


class CoinbaseAdvancedClientTests(unittest.TestCase):
    def test_normalize_product_id(self) -> None:
        self.assertEqual(client_mod.normalize_product_id("btcusd"), "BTC-USD")
        self.assertEqual(client_mod.normalize_product_id("eth/usdc"), "ETH-USDC")

    def test_missing_auth_raises(self) -> None:
        client = client_mod.CoinbaseAdvancedClient(api_key_name="", api_key_secret="")
        with self.assertRaises(client_mod.CoinbaseAdvancedAuthError):
            client._build_jwt(method="GET", host="api.coinbase.com", request_path="/api/v3/brokerage/accounts")

    def test_build_jwt_with_ed25519_key(self) -> None:
        private_key = ed25519.Ed25519PrivateKey.generate()
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        client = client_mod.CoinbaseAdvancedClient(
            api_key_name="organizations/test/apiKeys/test-key",
            api_key_secret=pem,
        )
        token = client._build_jwt(method="GET", host="api.coinbase.com", request_path="/api/v3/brokerage/accounts")
        header_b64, payload_b64, _ = token.split(".")
        header = _decode_segment(header_b64)
        payload = _decode_segment(payload_b64)
        self.assertEqual(header["kid"], "organizations/test/apiKeys/test-key")
        self.assertEqual(payload["sub"], "organizations/test/apiKeys/test-key")
        self.assertEqual(payload["iss"], "cdp")
        self.assertEqual(payload["uri"], "GET api.coinbase.com/api/v3/brokerage/accounts")

    def test_network_error_is_wrapped(self) -> None:
        client = client_mod.CoinbaseAdvancedClient()
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            with self.assertRaises(client_mod.CoinbaseAdvancedClientError) as ctx:
                client.public_exchange_ticker("BTC-USD")
        self.assertIn("Network error calling /products/BTC-USD/ticker", str(ctx.exception))

    def test_signed_request_uses_path_without_query_in_jwt_uri(self) -> None:
        private_key = ed25519.Ed25519PrivateKey.generate()
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        client = client_mod.CoinbaseAdvancedClient(
            api_key_name="organizations/test/apiKeys/test-key",
            api_key_secret=pem,
        )

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{}'

        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["auth"] = req.headers.get("Authorization")
            return FakeResponse()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client.list_products(get_all_products=False, limit=20)

        parsed = urlparse(captured["url"])
        self.assertEqual(parse_qs(parsed.query)["get_all_products"], ["false"])
        self.assertEqual(parse_qs(parsed.query)["limit"], ["20"])
        token = str(captured["auth"]).split(" ", 1)[1]
        payload = _decode_segment(token.split(".")[1])
        self.assertEqual(payload["uri"], "GET api.coinbase.com/api/v3/brokerage/products")

    def test_market_candles_uses_public_market_endpoint(self) -> None:
        client = client_mod.CoinbaseAdvancedClient(api_key_name="", api_key_secret="")

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"candles":[]}'

        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["auth"] = req.headers.get("Authorization")
            return FakeResponse()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            payload = client.market_candles("xrpusd", start=100, end=200, granularity="ONE_MINUTE", limit=350)

        self.assertEqual(payload, {"candles": []})
        parsed = urlparse(captured["url"])
        self.assertEqual(parsed.path, "/api/v3/brokerage/market/products/XRP-USD/candles")
        query = parse_qs(parsed.query)
        self.assertEqual(query["start"], ["100"])
        self.assertEqual(query["end"], ["200"])
        self.assertEqual(query["granularity"], ["ONE_MINUTE"])
        self.assertEqual(query["limit"], ["350"])
        self.assertIsNone(captured["auth"])

    def test_transaction_summary_uses_signed_spot_fee_endpoint(self) -> None:
        private_key = ed25519.Ed25519PrivateKey.generate()
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        client = client_mod.CoinbaseAdvancedClient(
            api_key_name="organizations/test/apiKeys/test-key",
            api_key_secret=pem,
        )

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"fee_tier":{"taker_fee_rate":"0.0060","maker_fee_rate":"0.0040"}}'

        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["auth"] = req.headers.get("Authorization")
            return FakeResponse()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            payload = client.transaction_summary(product_type="SPOT")

        self.assertEqual(payload["fee_tier"]["taker_fee_rate"], "0.0060")
        parsed = urlparse(captured["url"])
        self.assertEqual(parsed.path, "/api/v3/brokerage/transaction_summary")
        self.assertEqual(parse_qs(parsed.query)["product_type"], ["SPOT"])
        self.assertTrue(str(captured["auth"]).startswith("Bearer "))


if __name__ == "__main__":
    unittest.main()
