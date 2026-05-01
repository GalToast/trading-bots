"""Tests for the competition lane priority implementation."""

from datetime import datetime, timedelta, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


_passed = 0
_failed = 0


def _run(name, fn):
    global _passed, _failed
    try:
        fn()
        _passed += 1
        print(f"  PASS  {name}")
    except AssertionError as e:
        _failed += 1
        print(f"  FAIL  {name}: {e}")
    except Exception as e:
        _failed += 1
        print(f"  ERR   {name}: {type(e).__name__}: {e}")


def test_no_data_returns_neutral():
    aw = {}
    r = get_competition_lane_priority(alleyway_state=aw, lane="RAW", cluster_window=6, max_age_seconds=1200)
    assert r == (0.5, 0, 0.0), f"got {r}"


def test_winning_lane_high_priority():
    aw = {}
    _populate(aw, "RAW", [_make_record(1.0, minutes_ago=i) for i in range(5)])
    r = get_competition_lane_priority(alleyway_state=aw, lane="RAW", cluster_window=6, max_age_seconds=1200)
    assert r[0] == 1.0, f"win_rate={r[0]}"
    assert r[1] == 5, f"count={r[1]}"
    assert r[2] == 5.0, f"pnl={r[2]}"


def test_losing_lane_low_priority():
    aw = {}
    _populate(aw, "GEMINI", [_make_record(-1.0, minutes_ago=i) for i in range(4)])
    r = get_competition_lane_priority(alleyway_state=aw, lane="GEMINI", cluster_window=6, max_age_seconds=1200)
    assert r[0] == 0.0
    assert r[2] == -4.0


def test_lane_is_losing_not_enough_trades():
    aw = {}
    _populate(aw, "GEMINI", [_make_record(-1.0)])
    assert not _lane_is_losing(alleyway_state=aw, lane="GEMINI", cluster_window=6, max_age_seconds=1200, min_trades=3, max_win_rate=0.40)


def test_lane_is_losing_detected():
    aw = {}
    _populate(aw, "GEMINI", [_make_record(-1.0, minutes_ago=i) for i in range(4)])
    assert _lane_is_losing(alleyway_state=aw, lane="GEMINI", cluster_window=6, max_age_seconds=1200, min_trades=4, max_win_rate=0.35)


def test_lane_is_winning_strong():
    aw = {}
    _populate(aw, "RAW", [_make_record(1.0, minutes_ago=i) for i in range(4)])
    assert _lane_is_winning(alleyway_state=aw, lane="RAW", cluster_window=6, max_age_seconds=1200, min_trades=3, min_win_rate=0.55)


def test_lane_is_winning_mixed():
    aw = {}
    _populate(aw, "PRICE", [
        _make_record(1.0), _make_record(-1.0), _make_record(0.5),
    ])
    assert _lane_is_winning(alleyway_state=aw, lane="PRICE", cluster_window=6, max_age_seconds=1200, min_trades=3, min_win_rate=0.55)


def test_floor_bump_no_data():
    aw = {}
    assert get_experimental_lane_floor_bump(alleyway_state=aw, regime="RAW", cluster_window=6, max_age_seconds=1200) == 0.0


def test_floor_bump_non_experimental():
    aw = {}
    _populate(aw, "SNIPER", [_make_record(1.0, minutes_ago=i) for i in range(4)])
    assert get_experimental_lane_floor_bump(alleyway_state=aw, regime="SNIPER", cluster_window=6, max_age_seconds=1200) == 0.0


def test_floor_bump_winning_regime():
    aw = {}
    _populate(aw, "RAW", [_make_record(1.0, minutes_ago=i) for i in range(4)])
    bump = get_experimental_lane_floor_bump(alleyway_state=aw, regime="RAW", cluster_window=6, max_age_seconds=1200)
    assert bump >= 0.02, f"bump={bump}"
    assert bump <= 0.08, f"bump={bump}"


def test_floor_bump_strong_winner_max():
    aw = {}
    _populate(aw, "PRICE", [_make_record(1.0, minutes_ago=i) for i in range(5)])
    bump = get_experimental_lane_floor_bump(alleyway_state=aw, regime="PRICE", cluster_window=6, max_age_seconds=1200)
    assert bump == 0.08, f"bump={bump}"


def test_floor_bump_loser_no_bump():
    aw = {}
    _populate(aw, "GEMINI", [_make_record(-1.0, minutes_ago=i) for i in range(4)])
    assert get_experimental_lane_floor_bump(alleyway_state=aw, regime="GEMINI", cluster_window=6, max_age_seconds=1200) == 0.0


def test_defend_guard_not_active_new_lane():
    aw = {}
    assert not loser_lane_defend_guard_active(alleyway_state=aw, regime="GEMINI", cluster_window=6, max_age_seconds=1200)


def test_defend_guard_active_loser():
    aw = {}
    _populate(aw, "GEMINI", [_make_record(-1.0, minutes_ago=i) for i in range(5)])
    assert loser_lane_defend_guard_active(alleyway_state=aw, regime="GEMINI", cluster_window=6, max_age_seconds=1200)


def test_nonflat_block_not_active_new_lane():
    aw = {}
    assert not loser_lane_nonflat_hard_block_active(alleyway_state=aw, regime="PRICE", cluster_window=6, max_age_seconds=1200)


def test_nonflat_block_active_severe_loser():
    aw = {}
    _populate(aw, "PRICE", [_make_record(-1.0, minutes_ago=i) for i in range(4)])
    assert loser_lane_nonflat_hard_block_active(alleyway_state=aw, regime="PRICE", cluster_window=6, max_age_seconds=1200)


def _make_item(symbol, confidence, regime):
    return (symbol, f"signal_{symbol}", confidence, "SNIPER", 0.01, regime, "candle_direction", "normal")


def test_sort_key_winning_lane_higher():
    aw = {}
    _populate(aw, "RAW", [_make_record(1.0, minutes_ago=i) for i in range(4)])
    _populate(aw, "GEMINI", [_make_record(-1.0, minutes_ago=i) for i in range(4)])

    def fake_stress(sym):
        return {"score": 0.0, "drawdown_share": 0.0, "position_ratio": 0.0, "all_losing": False}

    def fake_anchor():
        return {"active": False}

    def fake_symbol_stats(sym):
        return {"trade_count": 0, "wins": 0, "losses": 0, "realized_pnl": 0.0}

    kwargs = dict(
        alleyway_state=aw,
        book_stress={"adopted_positions": 0, "direct_positions": 0},
        get_symbol_stress=fake_stress,
        get_anchor_drag_state=fake_anchor,
        get_competition_symbol_recent_stats=fake_symbol_stats,
        cluster_window=6,
        max_age_seconds=1200,
        anchor_drag_sort_penalty=0.05,
        symbol_recent_drag_sort_penalty=0.03,
    )

    raw_key = get_experimental_candidate_sort_key(_make_item("EURUSD", 0.70, "RAW"), **kwargs)
    gemini_key = get_experimental_candidate_sort_key(_make_item("GBPUSD", 0.70, "GEMINI"), **kwargs)

    # RAW lane_priority = (1.0, 4, 4.0), GEMINI = (0.0, 4, -4.0)
    # RAW should have higher adjusted confidence (no penalty) vs GEMINI (penalty for losing)
    assert raw_key[1] > gemini_key[1], f"RAW adj_conf={raw_key[1]} <= GEMINI adj_conf={gemini_key[1]}"


def test_sort_key_loser_penalty_applied():
    """Verify that a losing regime gets a confidence penalty in the sort key."""
    aw = {}
    _populate(aw, "PRICE", [_make_record(-1.0, minutes_ago=i) for i in range(5)])  # 0% WR, -$5

    def fake_stress(sym):
        return {"score": 0.0, "drawdown_share": 0.0, "position_ratio": 0.0, "all_losing": False}

    def fake_anchor():
        return {"active": False}

    def fake_symbol_stats(sym):
        return {"trade_count": 0, "wins": 0, "losses": 0, "realized_pnl": 0.0}

    kwargs = dict(
        alleyway_state=aw,
        book_stress={"adopted_positions": 0, "direct_positions": 0},
        get_symbol_stress=fake_stress,
        get_anchor_drag_state=fake_anchor,
        get_competition_symbol_recent_stats=fake_symbol_stats,
        cluster_window=6,
        max_age_seconds=1200,
        anchor_drag_sort_penalty=0.05,
        symbol_recent_drag_sort_penalty=0.03,
    )

    losing_key = get_experimental_candidate_sort_key(_make_item("EURUSD", 0.75, "PRICE"), **kwargs)
    # The adjusted confidence should be reduced by up to 0.15 for a losing regime
    assert losing_key[1] < 0.75, f"losing regime should have reduced confidence, got {losing_key[1]}"


if __name__ == "__main__":
    tests = [
        ("no_data_neutral", test_no_data_returns_neutral),
        ("winning_lane_priority", test_winning_lane_high_priority),
        ("losing_lane_priority", test_losing_lane_low_priority),
        ("lane_losing_not_enough_trades", test_lane_is_losing_not_enough_trades),
        ("lane_losing_detected", test_lane_is_losing_detected),
        ("lane_winning_strong", test_lane_is_winning_strong),
        ("lane_winning_mixed", test_lane_is_winning_mixed),
        ("floor_bump_no_data", test_floor_bump_no_data),
        ("floor_bump_non_experimental", test_floor_bump_non_experimental),
        ("floor_bump_winning", test_floor_bump_winning_regime),
        ("floor_bump_strong_max", test_floor_bump_strong_winner_max),
        ("floor_bump_loser", test_floor_bump_loser_no_bump),
        ("defend_guard_not_active", test_defend_guard_not_active_new_lane),
        ("defend_guard_active", test_defend_guard_active_loser),
        ("nonflat_block_not_active", test_nonflat_block_not_active_new_lane),
        ("nonflat_block_active", test_nonflat_block_active_severe_loser),
        ("sort_key_winning_higher", test_sort_key_winning_lane_higher),
        ("sort_key_loser_penalty", test_sort_key_loser_penalty_applied),
    ]

    print("=== Competition Lane Priority Tests ===")
    for name, fn in tests:
        _run(name, fn)
    print(f"\n{_passed} passed, {_failed} failed, {len(tests)} total")
    sys.exit(0 if _failed == 0 else 1)
