#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_live_btcusd_m5_survivability_board as board


class LiveBTCUSDM5SurvivabilityBoardTests(unittest.TestCase):
    def test_pct_of_equity_handles_zero_and_normal_values(self) -> None:
        self.assertEqual(board.pct_of_equity(100.0, 0.0), 0.0)
        self.assertEqual(board.pct_of_equity(68.3, 68300.0), 0.1)
        self.assertEqual(board.pct_of_equity(-683.0, 68300.0), -1.0)

    def test_mark_engine_counts_sides_and_combines_realized_with_floating(self) -> None:
        engine = SimpleNamespace(
            state=SimpleNamespace(
                realized_net_usd=25.0,
                open_tickets=[
                    {"direction": "BUY", "fill_price": 100.0},
                    {"direction": "BUY", "fill_price": 105.0},
                    {"direction": "SELL", "fill_price": 120.0},
                ],
                max_open_total=3,
                next_buy_level=99.0,
                next_sell_level=121.0,
            )
        )

        def fake_tick_pnl(_symbol: str, direction: str, fill_price: float, mark_price: float) -> float:
            sign = 1.0 if direction == "BUY" else -1.0
            return round((mark_price - fill_price) * sign, 2)

        with patch.object(board, "tick_pnl_usd", side_effect=fake_tick_pnl):
            marked = board.mark_engine(engine, bid_px=110.0, ask_px=111.0)

        self.assertEqual(marked["buy_count"], 2)
        self.assertEqual(marked["sell_count"], 1)
        self.assertEqual(marked["open_count"], 3)
        self.assertEqual(marked["max_open_total"], 3)
        self.assertAlmostEqual(marked["floating_usd"], 24.0, places=2)
        self.assertAlmostEqual(marked["net_usd"], 49.0, places=2)

    def test_registry_lane_finds_live_btc_m5_entry(self) -> None:
        row = board.registry_lane()
        self.assertEqual(row.get("name"), board.LANE_NAME)

    def test_write_outputs_renders_inactive_board_without_stress_section(self) -> None:
        payload = {
            "generated_at": "2026-04-15T23:10:00+00:00",
            "lane": board.LANE_NAME,
            "lane_status": "inactive",
            "pause_note": "paused_for_test",
            "current_bid": 75000.0,
            "current_ask": 75100.0,
            "account": {
                "balance": 68000.0,
                "equity": 68100.0,
                "margin": 0.0,
                "margin_free": 68100.0,
                "margin_level": 0.0,
            },
            "current_lane": {
                "realized_usd": 0.0,
                "floating_usd": 0.0,
                "net_usd": 0.0,
                "net_pct_equity": 0.0,
                "open_count": 0,
                "buy_count": 0,
                "sell_count": 0,
                "max_open_total": 0,
                "next_buy_level": 0.0,
                "next_sell_level": 0.0,
            },
            "scenarios": [],
        }
        json_path = Path("tmp_survivability.json")
        md_path = Path("tmp_survivability.md")
        try:
            with patch.object(board, "JSON_PATH", json_path), patch.object(board, "MD_PATH", md_path):
                board.write_outputs(payload)
            rendered = md_path.read_text(encoding="utf-8")
            self.assertIn("Lane status: `inactive`", rendered)
            self.assertIn("paused_for_test", rendered)
            self.assertNotIn("## Directional Stress", rendered)
        finally:
            json_path.unlink(missing_ok=True)
            md_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
