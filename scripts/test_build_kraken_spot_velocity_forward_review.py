import unittest

from build_kraken_spot_velocity_forward_review import candidate_rows, held_review


class KrakenSpotVelocityForwardReviewTests(unittest.TestCase):
    def test_candidate_rows_filters_wide_and_negative(self) -> None:
        board = {
            "rows": [
                {"product_id": "A-USD", "can_trade_starting_cash": True, "spread_bps": 10, "kraken_edge_bps": 5},
                {"product_id": "B-USD", "can_trade_starting_cash": True, "spread_bps": 110, "kraken_edge_bps": 50},
                {"product_id": "C-USD", "can_trade_starting_cash": True, "spread_bps": 10, "kraken_edge_bps": -1},
            ]
        }
        rows = candidate_rows(board, max_spread_bps=100.0, min_kraken_edge_bps=0.0)
        self.assertEqual([row["product_id"] for row in rows], ["A-USD"])

    def test_held_review_flat(self) -> None:
        review = held_review({"position": None})
        self.assertEqual(review["held_status"], "flat")
        self.assertEqual(review["held_product"], "")


if __name__ == "__main__":
    unittest.main()
