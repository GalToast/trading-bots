import unittest

from build_kraken_spot_prebreak_experiment_queue import build_experiments, windows_for


class KrakenSpotPrebreakExperimentQueueTests(unittest.TestCase):
    def test_builds_requested_count(self) -> None:
        rows = build_experiments(5000)
        self.assertEqual(len(rows), 5000)
        self.assertTrue(rows[0]["experiment_id"].startswith("kraken_spot_prebreak_"))
        self.assertIn(rows[0]["mode"], {"prebreak_compression", "first_lift_after_flat", "compression_pop"})

    def test_windows_for(self) -> None:
        self.assertEqual(windows_for("last_30"), ["last", "30s"])
        self.assertEqual(windows_for("30_60"), ["30s", "60s"])


if __name__ == "__main__":
    unittest.main()
