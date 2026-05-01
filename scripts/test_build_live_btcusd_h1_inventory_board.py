import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

MODULE_PATH = Path(__file__).with_name("build_live_btcusd_h1_inventory_board.py")
SPEC = importlib.util.spec_from_file_location("build_live_btcusd_h1_inventory_board", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
board = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(board)


class LiveBTCUSDH1InventoryBoardTests(unittest.TestCase):
    def test_build_position_rows_maps_state_ticket_and_distance(self) -> None:
        positions = [
            SimpleNamespace(ticket=1001, type=0, price_open=73000.0, volume=0.01, profit=-215.23, comment="PLIVE-BTC-B"),
            SimpleNamespace(ticket=1002, type=1, price_open=72800.0, volume=0.01, profit=179.68, comment="PLIVE-BTC-S"),
        ]
        state_tickets = {
            1001: {"level_idx": 0, "trigger_level": 72966.59, "entry_fill_price": 73002.22},
            1002: {"level_idx": 0, "trigger_level": 73056.59, "entry_fill_price": 72817.81},
        }
        rows = board.build_position_rows(positions=positions, state_tickets=state_tickets, bid=72780.0, ask=72785.0)
        self.assertEqual(len(rows), 2)
        worst = rows[0]
        hedge = rows[1]
        self.assertEqual(worst["ticket"], 1001)
        self.assertEqual(worst["direction"], "BUY")
        self.assertEqual(worst["level_idx"], 0)
        self.assertAlmostEqual(worst["distance_to_open_points"], -220.0, places=2)
        self.assertEqual(hedge["ticket"], 1002)
        self.assertEqual(hedge["direction"], "SELL")
        self.assertAlmostEqual(hedge["distance_to_open_points"], 15.0, places=2)

    def test_side_summary_and_clusters_capture_concentration(self) -> None:
        rows = [
            {"ticket": 1, "direction": "BUY", "open_price": 71678.76, "volume": 0.01, "profit": -82.88, "level_idx": 22},
            {"ticket": 2, "direction": "BUY", "open_price": 71678.76, "volume": 0.01, "profit": -82.88, "level_idx": 23},
            {"ticket": 3, "direction": "SELL", "open_price": 72817.81, "volume": 0.01, "profit": 179.68, "level_idx": 0},
        ]
        buy_side = board.summarize_side(rows, "BUY")
        self.assertEqual(buy_side["count"], 2)
        self.assertAlmostEqual(buy_side["floating_usd"], -165.76, places=2)
        self.assertAlmostEqual(buy_side["weighted_open_price"], 71678.76, places=2)

        clusters = board.cluster_rows(rows)
        self.assertEqual(len(clusters), 2)
        buy_cluster = next(row for row in clusters if row["direction"] == "BUY")
        self.assertEqual(buy_cluster["count"], 2)
        self.assertAlmostEqual(buy_cluster["floating_usd"], -165.76, places=2)
        self.assertEqual(buy_cluster["level_idx_min"], 22)
        self.assertEqual(buy_cluster["level_idx_max"], 23)


if __name__ == "__main__":
    unittest.main()
