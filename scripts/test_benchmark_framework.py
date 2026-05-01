#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import benchmark_framework as mod


def make_rsi_candles() -> list[dict[str, float]]:
    candles: list[dict[str, float]] = []
    price = 100.0
    start_ts = 1_767_273_200  # 2026-01-01 13:00:00 UTC
    for idx in range(120):
        if idx < 25:
            price *= 0.985
        elif idx < 80:
            price *= 1.02
        else:
            price *= 0.995
        candles.append(
            {
                "start": start_ts + idx * 300,
                "open": round(price, 6),
                "high": round(price * 1.03, 6),
                "low": round(price * 0.97, 6),
                "close": round(price, 6),
            }
        )
    return candles


def make_warp_candles() -> list[dict[str, float]]:
    candles: list[dict[str, float]] = []
    price = 10.0
    start_ts = 1_767_273_200  # 2026-01-01 13:00:00 UTC
    for idx in range(80):
        price *= 1.03 if idx % 2 == 0 else 0.965
        candles.append(
            {
                "start": start_ts + idx * 300,
                "open": round(price, 6),
                "high": round(price * 1.02, 6),
                "low": round(price * 0.98, 6),
                "close": round(price, 6),
            }
        )
    return candles


class BenchmarkFrameworkTests(unittest.TestCase):
    def test_rsi_strategy_execution_model_changes_results(self) -> None:
        candles = make_rsi_candles()
        shadow = mod.rsi_rave_strategy(candles, execution=mod.ExecutionConfig(1.0, 0, 0.0, 0.0, "shadow"))
        no_fill = mod.rsi_rave_strategy(candles, execution=mod.ExecutionConfig(0.0, 0, 0.0, 0.0, "no_fill"))
        self.assertGreater(shadow["trades"], 0)
        self.assertEqual(no_fill["trades"], 0)
        self.assertNotEqual(shadow["net_pnl"], no_fill["net_pnl"])

    def test_strict_warp_execution_model_changes_results(self) -> None:
        candles = make_warp_candles()
        shadow = mod.strict_warp_strategy(candles, execution=mod.ExecutionConfig(1.0, 0, 0.0, 0.0, "shadow"))
        no_fill = mod.strict_warp_strategy(candles, execution=mod.ExecutionConfig(0.0, 0, 0.0, 0.0, "no_fill"))
        self.assertGreater(shadow["trades"], 0)
        self.assertEqual(no_fill["trades"], 0)

    @patch("benchmark_framework.load_candles")
    def test_run_benchmark_applies_execution_model(self, load_candles_mock) -> None:
        candles = make_rsi_candles()
        load_candles_mock.return_value = candles
        original_models = mod.EXECUTION_MODELS.copy()
        try:
            mod.EXECUTION_MODELS["shadow"] = mod.ExecutionConfig(1.0, 0, 0.0, 0.0, "shadow")
            mod.EXECUTION_MODELS["realistic"] = mod.ExecutionConfig(0.0, 0, 0.0, 0.0, "realistic")
            shadow = mod.run_benchmark("T001", "RAVE-USD", mod.rsi_rave_strategy, "shadow")
            realistic = mod.run_benchmark("T002", "RAVE-USD", mod.rsi_rave_strategy, "realistic")
        finally:
            mod.EXECUTION_MODELS.clear()
            mod.EXECUTION_MODELS.update(original_models)
        self.assertGreater(shadow.trades, 0)
        self.assertEqual(realistic.trades, 0)


if __name__ == "__main__":
    unittest.main()
