#!/usr/bin/env python3
from __future__ import annotations

import unittest
from unittest.mock import patch

import reconcile_benchmark_engines as mod


def make_candles() -> list[dict[str, float]]:
    candles = []
    price = 100.0
    for idx in range(140):
        price *= 0.985 if idx < 30 else 1.015
        candles.append(
            {
                "start": 1_700_000_000 + idx * 300,
                "open": round(price, 6),
                "high": round(price * 1.02, 6),
                "low": round(price * 0.98, 6),
                "close": round(price, 6),
            }
        )
    return candles


class ReconcileBenchmarkEnginesTests(unittest.TestCase):
    @patch("reconcile_benchmark_engines.load_candles")
    def test_reconcile_builds_model_sections(self, load_candles_mock) -> None:
        load_candles_mock.return_value = make_candles()
        payload = mod.reconcile()
        self.assertEqual(payload["symbol"], "RAVE-USD")
        self.assertIn("perfect", payload["models"])
        self.assertIn("delta", payload["models"]["realistic"])


if __name__ == "__main__":
    unittest.main()
