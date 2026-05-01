import unittest

from build_kraken_spot_5000_experiment_queue import build_experiments, windows_for


class KrakenSpot5000ExperimentQueueTests(unittest.TestCase):
    def test_builds_requested_count(self) -> None:
        rows = build_experiments(5000)
        self.assertEqual(len(rows), 5000)
        self.assertTrue(rows[0]["experiment_id"].startswith("kraken_spot_exp_"))

    def test_windows_for(self) -> None:
        self.assertEqual(windows_for("last_30"), ["last", "30s"])
        self.assertIn("5m", windows_for("last_30_60_5m"))


if __name__ == "__main__":
    unittest.main()
