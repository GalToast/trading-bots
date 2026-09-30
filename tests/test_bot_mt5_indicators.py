"""Characterization tests for the bot.mt5 package extracted from mt5_bot_v10.py.

These pin the CURRENT behavior of the pure leaf functions (indicators and
symbol/session classifiers). They were written during the monolith ->
package refactor: if a future change alters any of these outputs, the test
failure forces a conscious decision rather than a silent behavior change.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.mt5.indicators import calc_atr, calc_ema, calc_rsi
from bot.mt5.sessions import (
    is_asian_session,
    is_commodity,
    is_crypto,
    is_exotic,
    is_good_session,
    is_london_session,
    is_msls_symbol_valid,
    is_off_session,
    is_overlap_session,
)


class CalcRsiTests(unittest.TestCase):
    def test_insufficient_data_returns_neutral(self) -> None:
        self.assertEqual(calc_rsi([100.0, 101.0], period=14), 50.0)
        self.assertEqual(calc_rsi([], period=14), 50.0)

    def test_all_gains_returns_100(self) -> None:
        self.assertEqual(calc_rsi([100 + i for i in range(20)], period=14), 100.0)

    def test_all_losses_returns_0(self) -> None:
        self.assertEqual(calc_rsi([120 - i for i in range(20)], period=14), 0.0)

    def test_flat_series_returns_100(self) -> None:
        # Quirk of the implementation: avg_loss == 0 -> 100.0 even with no gains.
        self.assertEqual(calc_rsi([100.0] * 20, period=14), 100.0)

    def test_mixed_series_pinned_value(self) -> None:
        closes = [100, 102, 101, 103, 102, 104, 103, 105, 104, 106, 105, 107, 106, 108, 107]
        self.assertAlmostEqual(calc_rsi(closes, period=14), 66.66666666666666, places=9)


class CalcAtrTests(unittest.TestCase):
    def _bars(self, n: int, lo: float = 100.0, hi: float = 102.0) -> list:
        return [{"h": hi + i, "l": lo + i, "c": lo + 1 + i} for i in range(n)]

    def test_insufficient_bars_returns_zero(self) -> None:
        self.assertEqual(calc_atr(self._bars(5), period=14), 0.0)
        self.assertEqual(calc_atr([], period=14), 0.0)

    def test_constant_range_equals_range(self) -> None:
        self.assertAlmostEqual(calc_atr(self._bars(20), period=14), 2.0, places=9)

    def test_gap_expands_true_range(self) -> None:
        bars = self._bars(20)
        bars[10]["h"] = 120.0  # gap up widens the true range of bar 10
        self.assertGreater(calc_atr(bars, period=14), 2.0)


class CalcEmaTests(unittest.TestCase):
    def test_constant_series_returns_value(self) -> None:
        self.assertAlmostEqual(calc_ema([5.0] * 10, 3), 5.0, places=9)

    def test_rising_series_pinned_value(self) -> None:
        self.assertAlmostEqual(calc_ema([1, 2, 3, 4, 5], 3), 4.0, places=9)

    def test_short_series_falls_back_to_mean(self) -> None:
        self.assertAlmostEqual(calc_ema([1, 2], 3), 1.5, places=9)

    def test_empty_series_returns_zero(self) -> None:
        self.assertEqual(calc_ema([], 3), 0)


class SymbolClassifierTests(unittest.TestCase):
    def test_is_crypto(self) -> None:
        self.assertTrue(is_crypto("BTCUSD"))
        self.assertTrue(is_crypto("ETHUSDT"))
        self.assertFalse(is_crypto("EURUSD"))
        self.assertFalse(is_crypto("XAUUSD"))

    def test_is_commodity(self) -> None:
        self.assertTrue(is_commodity("XAUUSD"))
        self.assertTrue(is_commodity("XAGUSD"))
        self.assertFalse(is_commodity("EURUSD"))

    def test_is_exotic(self) -> None:
        self.assertTrue(is_exotic("USDZAR"))
        self.assertTrue(is_exotic("EURTRY"))
        self.assertFalse(is_exotic("EURUSD"))
        self.assertFalse(is_exotic("GBPJPY"))

    def test_is_msls_symbol_valid(self) -> None:
        for sym in ("NAS100", "US30", "AUDCHF", "EURJPY", "XAUUSD"):
            self.assertTrue(is_msls_symbol_valid(sym), sym)
        self.assertFalse(is_msls_symbol_valid("EURUSD"))

    def test_is_good_session_always_true(self) -> None:
        # Session gating is disabled in the implementation.
        self.assertTrue(is_good_session("EURUSD"))
        self.assertTrue(is_good_session("BTCUSD"))


class SessionWindowTests(unittest.TestCase):
    def test_session_predicates_return_bool(self) -> None:
        for fn in (is_overlap_session, is_off_session, is_asian_session, is_london_session):
            self.assertIsInstance(fn(), bool, fn.__name__)


if __name__ == "__main__":
    unittest.main()
