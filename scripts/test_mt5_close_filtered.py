#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(SCRIPTS_DIR / "operators") not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR / "operators"))

import mt5_close_filtered as close_filtered


class _Pos:
    def __init__(self, *, ticket: int, symbol: str, magic: int, type_: int, comment: str = "") -> None:
        self.ticket = ticket
        self.symbol = symbol
        self.magic = magic
        self.type = type_
        self.comment = comment
        self.volume = 0.01
        self.price_open = 1.2345
        self.profit = 12.3


class Mt5CloseFilteredTests(unittest.TestCase):
    def test_describe_retcode_uses_mt5_symbolic_name_when_available(self) -> None:
        original = getattr(close_filtered.mt5, "TRADE_RETCODE_MARKET_CLOSED", None)
        close_filtered.mt5.TRADE_RETCODE_MARKET_CLOSED = 10018
        try:
            self.assertEqual(
                close_filtered.describe_retcode(10018),
                "10018(TRADE_RETCODE_MARKET_CLOSED)",
            )
        finally:
            if original is None:
                delattr(close_filtered.mt5, "TRADE_RETCODE_MARKET_CLOSED")
            else:
                close_filtered.mt5.TRADE_RETCODE_MARKET_CLOSED = original

    def test_position_matches_requires_filter_hit(self) -> None:
        pos = _Pos(ticket=1, symbol="BTCUSD", magic=0, type_=0, comment="manual")
        self.assertTrue(
            close_filtered.position_matches(
                pos,
                tickets={1},
                magics=set(),
                symbols=set(),
                comment_contains="",
            )
        )
        self.assertFalse(
            close_filtered.position_matches(
                pos,
                tickets=set(),
                magics=set(),
                symbols=set(),
                comment_contains="",
            )
        )

    def test_select_positions_filters_by_magic_symbol_and_comment(self) -> None:
        positions = [
            _Pos(ticket=1, symbol="BTCUSD", magic=0, type_=0, comment="manual"),
            _Pos(ticket=2, symbol="USDJPY", magic=941777, type_=1, comment="PLIVE-LATTICE-S"),
            _Pos(ticket=3, symbol="USDJPY", magic=941777, type_=1, comment="other"),
        ]
        by_magic = close_filtered.select_positions(
            positions,
            tickets=set(),
            magics={941777},
            symbols=set(),
            comment_contains="",
        )
        self.assertEqual([pos.ticket for pos in by_magic], [2, 3])

        by_comment = close_filtered.select_positions(
            positions,
            tickets=set(),
            magics={941777},
            symbols={"USDJPY"},
            comment_contains="LATTICE",
        )
        self.assertEqual([pos.ticket for pos in by_comment], [2])

    def test_validate_expectations_rejects_count_drift(self) -> None:
        positions = [
            _Pos(ticket=1, symbol="BTCUSD", magic=0, type_=0, comment="manual"),
            _Pos(ticket=2, symbol="BTCUSD", magic=0, type_=0, comment="manual"),
        ]
        ok, detail = close_filtered.validate_expectations(positions, expect_count=1)
        self.assertFalse(ok)
        self.assertIn("expected_match_count=1", detail)

        ok, detail = close_filtered.validate_expectations(positions, expect_count=2)
        self.assertTrue(ok)
        self.assertEqual(detail, "")


if __name__ == "__main__":
    unittest.main()
