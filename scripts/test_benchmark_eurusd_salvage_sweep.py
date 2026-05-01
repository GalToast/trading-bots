from __future__ import annotations

import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark_eurusd_salvage_sweep import simulate_close_policy


class _Info:
    point = 0.00001
    digits = 5
    spread = 10


class EurusdSalvageSweepTest(unittest.TestCase):
    def test_sell_step_changes_sell_side_results(self) -> None:
        bars = [
            {"open": 1.1000, "high": 1.1000, "low": 1.1000, "close": 1.1000},
            {"open": 1.1000, "high": 1.10020, "low": 1.09950, "close": 1.10000},
        ]
        cfg = type("Cfg", (), {"max_open_per_side": 50})()

        with (
            patch("benchmark_fx_fixed_step_close_policy.spread_price", return_value=0.0),
            patch(
                "benchmark_fx_fixed_step_close_policy.unit_pnl_usd",
                side_effect=lambda _symbol, direction, entry, exit, _spread: (entry - exit) * 100000
                if direction == "SELL"
                else (exit - entry) * 100000,
            ),
        ):
            tight_sell = simulate_close_policy(
                "EURUSD",
                bars,
                _Info(),
                cfg,
                close_style="all_profitable",
                close_gap=1,
                close_alpha=1.0,
                step_buy_pips=1.0,
                step_sell_pips=0.5,
            )
            wide_sell = simulate_close_policy(
                "EURUSD",
                bars,
                _Info(),
                cfg,
                close_style="all_profitable",
                close_gap=1,
                close_alpha=1.0,
                step_buy_pips=1.0,
                step_sell_pips=1.5,
            )

        self.assertNotEqual(tight_sell["combined_net_usd"], wide_sell["combined_net_usd"])
        self.assertNotEqual(tight_sell["realized_closes"], wide_sell["realized_closes"])


if __name__ == "__main__":
    unittest.main()
