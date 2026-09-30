"""Competition lane priority policy tests (unittest, CI-discovered).

Reconciled from the former ad-hoc runner ``bot/test_competition_lane_priority.py``
(a manual ``_run``-style script that ``unittest discover`` never picked up) into the
discoverable ``tests/`` suite. Coverage is unchanged: lane priority, floor bumps,
loser-lane defend guards, and experimental candidate sort keys.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.competition import (
    get_competition_lane_priority,
    get_experimental_lane_floor_bump,
    get_competition_lane_recent_stats,
    loser_lane_defend_guard_active,
    loser_lane_nonflat_hard_block_active,
    get_experimental_candidate_sort_key,
    _lane_is_losing,
    _lane_is_winning,
)


def _make_record(pnl, early_fail=False, first_green=False, minutes_ago=0):
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {
        "realized_pnl": float(pnl),
        "first_green_before_fail": bool(first_green),
        "early_fail": bool(early_fail),
        "recorded_at_utc": ts.isoformat(),
    }


def _populate(alleyway, lane, records):
    alleyway.setdefault("competition_lane_records", {})[lane] = records


def _make_item(symbol, confidence, regime):
    return (symbol, f"signal_{symbol}", confidence, "SNIPER", 0.01, regime, "candle_direction", "normal")


def _sort_kwargs(alleyway):
    def fake_stress(sym):
        return {"score": 0.0, "drawdown_share": 0.0, "position_ratio": 0.0, "all_losing": False}

    def fake_anchor():
        return {"active": False}

    def fake_symbol_stats(sym):
        return {"trade_count": 0, "wins": 0, "losses": 0, "realized_pnl": 0.0}

    return dict(
        alleyway_state=alleyway,
        book_stress={"adopted_positions": 0, "direct_positions": 0},
        get_symbol_stress=fake_stress,
        get_anchor_drag_state=fake_anchor,
        get_competition_symbol_recent_stats=fake_symbol_stats,
        cluster_window=6,
        max_age_seconds=1200,
        anchor_drag_sort_penalty=0.05,
        symbol_recent_drag_sort_penalty=0.03,
    )


class CompetitionLanePriorityTests(unittest.TestCase):
    def test_no_data_returns_neutral(self) -> None:
        r = get_competition_lane_priority(
            alleyway_state={}, lane="RAW", cluster_window=6, max_age_seconds=1200
        )
        self.assertEqual(r, (0.5, 0, 0.0))

    def test_winning_lane_high_priority(self) -> None:
        aw = {}
        _populate(aw, "RAW", [_make_record(1.0, minutes_ago=i) for i in range(5)])
        r = get_competition_lane_priority(
            alleyway_state=aw, lane="RAW", cluster_window=6, max_age_seconds=1200
        )
        self.assertEqual(r[0], 1.0)
        self.assertEqual(r[1], 5)
        self.assertEqual(r[2], 5.0)

    def test_losing_lane_low_priority(self) -> None:
        aw = {}
        _populate(aw, "GEMINI", [_make_record(-1.0, minutes_ago=i) for i in range(4)])
        r = get_competition_lane_priority(
            alleyway_state=aw, lane="GEMINI", cluster_window=6, max_age_seconds=1200
        )
        self.assertEqual(r[0], 0.0)
        self.assertEqual(r[2], -4.0)

    def test_lane_is_losing_not_enough_trades(self) -> None:
        aw = {}
        _populate(aw, "GEMINI", [_make_record(-1.0)])
        self.assertFalse(
            _lane_is_losing(
                alleyway_state=aw, lane="GEMINI", cluster_window=6,
                max_age_seconds=1200, min_trades=3, max_win_rate=0.40,
            )
        )

    def test_lane_is_losing_detected(self) -> None:
        aw = {}
        _populate(aw, "GEMINI", [_make_record(-1.0, minutes_ago=i) for i in range(4)])
        self.assertTrue(
            _lane_is_losing(
                alleyway_state=aw, lane="GEMINI", cluster_window=6,
                max_age_seconds=1200, min_trades=4, max_win_rate=0.35,
            )
        )

    def test_lane_is_winning_strong(self) -> None:
        aw = {}
        _populate(aw, "RAW", [_make_record(1.0, minutes_ago=i) for i in range(4)])
        self.assertTrue(
            _lane_is_winning(
                alleyway_state=aw, lane="RAW", cluster_window=6,
                max_age_seconds=1200, min_trades=3, min_win_rate=0.55,
            )
        )

    def test_lane_is_winning_mixed(self) -> None:
        aw = {}
        _populate(
            aw, "PRICE",
            [_make_record(1.0), _make_record(-1.0), _make_record(0.5)],
        )
        self.assertTrue(
            _lane_is_winning(
                alleyway_state=aw, lane="PRICE", cluster_window=6,
                max_age_seconds=1200, min_trades=3, min_win_rate=0.55,
            )
        )

    def test_floor_bump_no_data(self) -> None:
        self.assertEqual(
            get_experimental_lane_floor_bump(
                alleyway_state={}, regime="RAW", cluster_window=6, max_age_seconds=1200
            ),
            0.0,
        )

    def test_floor_bump_non_experimental(self) -> None:
        aw = {}
        _populate(aw, "SNIPER", [_make_record(1.0, minutes_ago=i) for i in range(4)])
        self.assertEqual(
            get_experimental_lane_floor_bump(
                alleyway_state=aw, regime="SNIPER", cluster_window=6, max_age_seconds=1200
            ),
            0.0,
        )

    def test_floor_bump_winning_regime(self) -> None:
        aw = {}
        _populate(aw, "RAW", [_make_record(1.0, minutes_ago=i) for i in range(4)])
        bump = get_experimental_lane_floor_bump(
            alleyway_state=aw, regime="RAW", cluster_window=6, max_age_seconds=1200
        )
        self.assertGreater(bump, 0.0)
        self.assertGreaterEqual(bump, 0.02)
        self.assertLessEqual(bump, 0.08)

    def test_floor_bump_strong_winner_max(self) -> None:
        aw = {}
        _populate(aw, "PRICE", [_make_record(1.0, minutes_ago=i) for i in range(5)])
        bump = get_experimental_lane_floor_bump(
            alleyway_state=aw, regime="PRICE", cluster_window=6, max_age_seconds=1200
        )
        self.assertEqual(bump, 0.08)

    def test_floor_bump_loser_no_bump(self) -> None:
        aw = {}
        _populate(aw, "GEMINI", [_make_record(-1.0, minutes_ago=i) for i in range(4)])
        self.assertEqual(
            get_experimental_lane_floor_bump(
                alleyway_state=aw, regime="GEMINI", cluster_window=6, max_age_seconds=1200
            ),
            0.0,
        )

    def test_defend_guard_not_active_new_lane(self) -> None:
        self.assertFalse(
            loser_lane_defend_guard_active(
                alleyway_state={}, regime="GEMINI", cluster_window=6, max_age_seconds=1200
            )
        )

    def test_defend_guard_active_loser(self) -> None:
        aw = {}
        _populate(aw, "GEMINI", [_make_record(-1.0, minutes_ago=i) for i in range(5)])
        self.assertTrue(
            loser_lane_defend_guard_active(
                alleyway_state=aw, regime="GEMINI", cluster_window=6, max_age_seconds=1200
            )
        )

    def test_nonflat_block_not_active_new_lane(self) -> None:
        self.assertFalse(
            loser_lane_nonflat_hard_block_active(
                alleyway_state={}, regime="PRICE", cluster_window=6, max_age_seconds=1200
            )
        )

    def test_nonflat_block_active_severe_loser(self) -> None:
        aw = {}
        _populate(aw, "PRICE", [_make_record(-1.0, minutes_ago=i) for i in range(4)])
        self.assertTrue(
            loser_lane_nonflat_hard_block_active(
                alleyway_state=aw, regime="PRICE", cluster_window=6, max_age_seconds=1200
            )
        )

    def test_sort_key_winning_lane_higher(self) -> None:
        aw = {}
        _populate(aw, "RAW", [_make_record(1.0, minutes_ago=i) for i in range(4)])
        _populate(aw, "GEMINI", [_make_record(-1.0, minutes_ago=i) for i in range(4)])
        kwargs = _sort_kwargs(aw)
        raw_key = get_experimental_candidate_sort_key(_make_item("EURUSD", 0.70, "RAW"), **kwargs)
        gemini_key = get_experimental_candidate_sort_key(_make_item("GBPUSD", 0.70, "GEMINI"), **kwargs)
        # RAW lane_priority = (1.0, 4, 4.0), GEMINI = (0.0, 4, -4.0):
        # RAW keeps full confidence while GEMINI takes the loser penalty.
        self.assertGreater(raw_key[1], gemini_key[1])

    def test_sort_key_loser_penalty_applied(self) -> None:
        aw = {}
        _populate(aw, "PRICE", [_make_record(-1.0, minutes_ago=i) for i in range(5)])
        losing_key = get_experimental_candidate_sort_key(
            _make_item("EURUSD", 0.75, "PRICE"), **_sort_kwargs(aw)
        )
        self.assertLess(losing_key[1], 0.75)


if __name__ == "__main__":
    unittest.main()
