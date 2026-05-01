from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.competition import get_competition_lane_priority, get_experimental_lane_floor_bump


def _record(pnl: float, *, minutes_ago: int = 0) -> dict:
    return {
        "realized_pnl": pnl,
        "first_green_before_fail": pnl > 0,
        "early_fail": False,
        "recorded_at_utc": (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(),
    }


class CompetitionLanePriorityTests(unittest.TestCase):
    def test_no_data_returns_neutral_priority(self) -> None:
        priority = get_competition_lane_priority(
            alleyway_state={},
            lane="RAW",
            cluster_window=6,
            max_age_seconds=1200,
        )
        self.assertEqual(priority, (0.5, 0, 0.0))

    def test_winning_lane_receives_floor_bump(self) -> None:
        state = {
            "competition_lane_records": {
                "RAW": [_record(1.0, minutes_ago=i) for i in range(4)],
            }
        }
        bump = get_experimental_lane_floor_bump(
            alleyway_state=state,
            regime="RAW",
            cluster_window=6,
            max_age_seconds=1200,
        )
        self.assertGreater(bump, 0.0)


if __name__ == "__main__":
    unittest.main()
