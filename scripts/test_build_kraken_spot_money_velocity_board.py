import unittest

from build_kraken_spot_money_velocity_board import best_move, classify


class KrakenSpotMoneyVelocityBoardTests(unittest.TestCase):
    def test_best_move_picks_largest_window(self) -> None:
        window, value = best_move({"move_last_bps": 1, "ret_30s_bps": 3, "ret_60s_bps": 2, "ret_5m_bps": 9})
        self.assertEqual(window, "5m")
        self.assertEqual(value, 9)

    def test_classify_fee_flip(self) -> None:
        self.assertEqual(classify(10, -150, 3, True), "kraken_fee_flip_candidate")

    def test_classify_warming_and_min_size(self) -> None:
        self.assertEqual(classify(10, -150, 1, True), "warming_samples")
        self.assertEqual(classify(10, -150, 3, False), "blocked_min_size")


if __name__ == "__main__":
    unittest.main()
