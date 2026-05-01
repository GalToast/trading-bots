import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_adaptive_transfer_board import build_payload


class AdaptiveTransferBoardTests(unittest.TestCase):
    def test_expected_verdicts_present(self) -> None:
        payload = build_payload()
        rows = {row["symbol"]: row for row in payload["rows"]}

        self.assertEqual(rows["GBPUSD"]["verdict"], "donor_reference")
        self.assertEqual(rows["NZDUSD"]["verdict"], "adapt_first")
        self.assertEqual(rows["EURUSD"]["verdict"], "reject_for_now")
        self.assertEqual(rows["USDJPY"]["verdict"], "adapt_first")

    def test_usdjpy_no_longer_carries_stale_runtime_blocker(self) -> None:
        payload = build_payload()
        rows = {row["symbol"]: row for row in payload["rows"]}

        self.assertEqual(rows["USDJPY"]["blockers"], [])
        self.assertIn("no longer active", rows["USDJPY"]["rationale"])
        self.assertEqual(rows["USDJPY"]["stage"], "bounded_proof_pending")
        self.assertEqual(rows["USDJPY"]["source_stage"], "blocked_runtime")


if __name__ == "__main__":
    unittest.main()
