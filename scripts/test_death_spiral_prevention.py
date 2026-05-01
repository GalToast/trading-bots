#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from death_spiral_prevention import LossTracker


class DeathSpiralPreventionTests(unittest.TestCase):
    def test_persists_active_cooldown_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "tracker.json"
            tracker = LossTracker(max_consecutive_losses=2, cooldown_seconds=3600, state_path=state_path)
            tracker.record_close("BERT-USD", won=False)
            result = tracker.record_close("BERT-USD", won=False)
            tracker.save()

            self.assertEqual(result["action"], "blocked")

            restarted = LossTracker(max_consecutive_losses=2, cooldown_seconds=3600, state_path=state_path)

        self.assertTrue(restarted.is_blocked("BERT-USD"))

    def test_expired_cooldown_does_not_reload_as_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "tracker.json"
            state_path.write_text(
                (
                    '{"consecutive_losses":{"BMB-USD":3},'
                    '"total_losses":{"BMB-USD":3},'
                    '"total_wins":{},'
                    f'"blocked_until":{{"BMB-USD":{time.time() - 1}}}}}'
                ),
                encoding="utf-8",
            )

            restarted = LossTracker(max_consecutive_losses=2, cooldown_seconds=3600, state_path=state_path)

        self.assertFalse(restarted.is_blocked("BMB-USD"))


if __name__ == "__main__":
    unittest.main()
