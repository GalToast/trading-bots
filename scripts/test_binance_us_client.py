#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock
import urllib.error


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import binance_us_client as client_mod


class BinanceUSClientTests(unittest.TestCase):
    def test_normalize_symbol(self) -> None:
        self.assertEqual(client_mod.normalize_symbol("btc/usd"), "BTCUSD")
        self.assertEqual(client_mod.normalize_symbol("eth-usdt"), "ETHUSDT")

    def test_encode_params_skips_none(self) -> None:
        self.assertEqual(client_mod.encode_params({"symbol": "BTCUSD", "quantity": None, "side": "BUY"}), "symbol=BTCUSD&side=BUY")

    def test_signed_params_adds_signature(self) -> None:
        client = client_mod.BinanceUSClient(
            api_key="k",
            api_secret="secret",
            base_url="https://api.binance.us",
            recv_window_ms=5000,
        )
        signed = client._signed_params({"symbol": "BTCUSD", "side": "BUY"})
        self.assertIn("signature", signed)
        self.assertEqual(signed["symbol"], "BTCUSD")
        self.assertEqual(signed["side"], "BUY")

    def test_missing_auth_raises(self) -> None:
        client = client_mod.BinanceUSClient(api_key="", api_secret="")
        with self.assertRaises(client_mod.BinanceUSAuthError):
            client._signed_params({"symbol": "BTCUSD"})

    def test_network_error_is_wrapped(self) -> None:
        client = client_mod.BinanceUSClient(api_key="", api_secret="", base_url="https://api.binance.us")
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            with self.assertRaises(client_mod.BinanceUSClientError) as ctx:
                client.ping()
        self.assertIn("Network error calling /api/v3/ping", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
