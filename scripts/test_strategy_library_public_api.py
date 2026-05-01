#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import strategy_library as strategy_lib


def sample_candles() -> list[dict[str, str]]:
    closes = [
        100.0, 99.0, 98.0, 96.0, 95.0, 97.0, 99.0, 101.0, 100.0, 98.0,
        96.0, 94.0, 95.0, 97.0, 100.0, 102.0, 101.0, 99.0, 97.0, 95.0,
        94.0, 96.0, 98.0, 100.0, 103.0, 101.0, 99.0, 97.0, 95.0, 96.0,
        98.0, 101.0, 104.0, 102.0, 100.0, 98.0, 97.0, 99.0, 102.0, 105.0,
        103.0, 101.0, 99.0, 97.0, 98.0, 100.0, 103.0, 106.0, 104.0, 102.0,
        100.0, 98.0, 99.0, 101.0, 104.0, 107.0, 105.0, 103.0, 101.0, 100.0,
    ]
    candles: list[dict[str, str]] = []
    for idx, close in enumerate(closes):
        candles.append(
            {
                "start": str(1_700_000_000 + idx * 300),
                "open": str(close - 0.4),
                "high": str(close + 0.8),
                "low": str(close - 1.2),
                "close": str(close),
                "volume": str(100 + (idx % 5) * 20 + (200 if idx in {11, 28, 43} else 0)),
            }
        )
    return candles


class StrategyLibraryPublicApiTests(unittest.TestCase):
    def test_additional_public_wrappers_return_backtest_shape(self) -> None:
        candles = sample_candles()
        runs = [
            strategy_lib.vwap_reversion(candles, vwap_window=12, vwap_dev_pct=1.5, tp_pct=4.0, sl_pct=3.0, max_hold=18, fee_rate=0.004, starting_cash=48.0),
            strategy_lib.volume_spike_reversion(candles, rsi_period=3, os_thresh=40, vol_mult=1.5, vol_lookback=10, tp_pct=8.0, sl_pct=4.0, max_hold=18, fee_rate=0.004, starting_cash=48.0),
            strategy_lib.multi_tf_rsi(candles, rsi_period=3, os_thresh=45, tp_pct=8.0, sl_pct=4.0, max_hold=18, fee_rate=0.004, starting_cash=48.0),
            strategy_lib.atr_expansion(candles, atr_period=10, atr_mult=1.1, tp_pct=6.0, sl_pct=3.0, max_hold=18, fee_rate=0.004, starting_cash=48.0),
            strategy_lib.keltner_breakout(candles, k_period=10, k_mult=1.0, tp_pct=6.0, sl_pct=3.0, max_hold=18, fee_rate=0.004, starting_cash=48.0),
            strategy_lib.hist_vol_squeeze(candles, hv_period=10, tp_pct=8.0, sl_pct=4.0, max_hold=18, fee_rate=0.004, starting_cash=48.0),
            strategy_lib.opening_range_breakout(candles, opening_bars=6, breakout_buffer_pct=0.1, tp_pct=6.0, sl_pct=3.0, max_hold=18, fee_rate=0.004, starting_cash=48.0),
            strategy_lib.regime_gated_momentum(candles, lookback=8, ema_period=12, atr_period=6, trend_lookback=6, min_atr_pct=0.4, min_trend_pct=0.2, min_ema_slope_pct=0.001, tp_pct=6.0, sl_pct=3.0, max_hold=18, fee_rate=0.004, starting_cash=48.0),
        ]

        for result in runs:
            self.assertIn("net_pnl", result)
            self.assertIn("trades", result)
            self.assertIn("total_fees", result)
            self.assertGreaterEqual(result["signals"], 0)
            self.assertGreaterEqual(result["trades"], 0)


if __name__ == "__main__":
    unittest.main()
