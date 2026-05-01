#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_atr_expansion_rescue_sweep as sweep


class CoinbaseAtrExpansionRescueSweepTests(unittest.TestCase):
    def test_precomputed_atr_backtest_matches_strategy_library(self) -> None:
        candles = []
        price = 100.0
        for idx in range(64):
            drift = 0.6 if idx % 7 in {1, 2, 3} else -0.15
            open_price = price
            close_price = open_price + drift + ((idx % 5) * 0.05)
            high_price = max(open_price, close_price) + 0.8 + ((idx % 3) * 0.15)
            low_price = min(open_price, close_price) - 0.5 - ((idx % 4) * 0.07)
            candles.append(
                {
                    "start": 1_710_000_000 + idx * 300,
                    "open": f"{open_price:.4f}",
                    "high": f"{high_price:.4f}",
                    "low": f"{low_price:.4f}",
                    "close": f"{close_price:.4f}",
                    "volume": "1000",
                }
            )
            price = close_price

        arrays = sweep.build_candle_arrays(candles)
        signal_map = sweep.build_atr_signal_map(arrays)
        fast = sweep.run_precomputed_atr_backtest(
            arrays,
            signal_map[(14, 1.25)],
            tp_pct=10.0,
            sl_pct=4.0,
            max_hold=24,
        )
        slow = sweep.strategy_lib.atr_expansion(
            candles,
            atr_period=14,
            atr_mult=1.25,
            tp_pct=10.0,
            sl_pct=4.0,
            max_hold=24,
            fee_rate=sweep.FEE_RATE,
            starting_cash=sweep.STARTING_CASH,
            entry_slip=0.0,
            exit_slip=0.0,
            fill_prob=1.0,
        )

        self.assertEqual(fast["net_pnl"], slow["net_pnl"])
        self.assertEqual(fast["trades"], slow["trades"])
        self.assertEqual(fast["wins"], slow["wins"])
        self.assertEqual(fast["losses"], slow["losses"])
        self.assertEqual(fast["signals"], slow["signals"])
        self.assertEqual(fast["total_fees"], slow["total_fees"])

    def test_load_target_coins_prefers_frontier_crossovers_then_weak_board_fill(self) -> None:
        original_load_json = sweep.load_json
        try:
            def fake_load_json(path: Path):
                if path == sweep.FRONTIER_PATH:
                    return {
                        "coin_rows": [
                            {"coin": "XRP-USD", "best_family": "vwap_reversion", "best_net_pnl": -2.76},
                            {"coin": "DOGE-USD", "best_family": "vwap_reversion", "best_net_pnl": -6.71},
                            {"coin": "FARTCOIN-USD", "best_family": "atr_expansion", "best_net_pnl": 1.04},
                            {"coin": "PRL-USD", "best_family": "range_breakout", "best_net_pnl": 27.05},
                            {"coin": "SUP-USD", "best_family": "range_breakout", "best_net_pnl": 35.69},
                        ]
                    }
                if path == sweep.VOL_FRONTIER_PATH:
                    return {
                        "coin_rows": [
                            {"coin": "XRP-USD", "best_vol_net_pnl": 1.38, "beats_family_frontier": True},
                            {"coin": "FARTCOIN-USD", "best_vol_net_pnl": 1.04, "beats_family_frontier": True},
                            {"coin": "DOGE-USD", "best_vol_net_pnl": -1.16, "beats_family_frontier": True},
                            {"coin": "PRL-USD", "best_vol_net_pnl": -0.69, "beats_family_frontier": False},
                            {"coin": "SUP-USD", "best_vol_net_pnl": 9.85, "beats_family_frontier": False},
                        ]
                    }
                return {}

            sweep.load_json = fake_load_json
            self.assertEqual(
                sweep.load_target_coins(limit=4),
                ["XRP-USD", "FARTCOIN-USD", "DOGE-USD", "PRL-USD"],
            )
        finally:
            sweep.load_json = original_load_json

    def test_build_leadership_read_calls_out_rescues(self) -> None:
        rows = [
            {
                "coin": "XRP-USD",
                "best_net_pnl": 14.0,
                "best_atr_period": 8,
                "best_atr_mult": 1.1,
                "best_tp_pct": 8.0,
                "best_sl_pct": 2.0,
                "best_max_hold": 24,
                "beats_frontier_after_sweep": True,
                "frontier_best_family": "vwap_reversion",
                "profitable_rate": 32.0,
            },
            {
                "coin": "DOGE-USD",
                "best_net_pnl": 3.5,
                "best_atr_period": 10,
                "best_atr_mult": 1.25,
                "best_tp_pct": 6.0,
                "best_sl_pct": 2.0,
                "best_max_hold": 24,
                "beats_frontier_after_sweep": False,
                "frontier_best_family": "vwap_reversion",
                "profitable_rate": 12.5,
            },
        ]

        lines = sweep.build_leadership_read(rows)

        self.assertTrue(any("XRP" in line for line in lines))
        self.assertTrue(any("beats its current family-frontier champion" in line for line in lines))
        self.assertTrue(any("DOGE" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
