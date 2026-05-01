from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone


def record_competition_lane_outcome(*, alleyway_state, record, scorecard_limit):
    lane = str((record or {}).get("regime_at_entry", "UNKNOWN") or "UNKNOWN").upper()
    scorecards = alleyway_state.setdefault("competition_lane_records", {})
    lane_records = scorecards.setdefault(lane, [])
    lane_records.append(
        {
            "realized_pnl": float((record or {}).get("realized_pnl", 0.0) or 0.0),
            "first_green_before_fail": bool((record or {}).get("first_green_before_fail", False)),
            "early_fail": str((record or {}).get("exit_reason", "") or "").startswith("EARLY_FAIL"),
            "recorded_at_utc": str((record or {}).get("recorded_at_utc", "") or ""),
        }
    )
    if len(lane_records) > scorecard_limit:
        del lane_records[:-scorecard_limit]


def record_competition_symbol_outcome(*, alleyway_state, record, scorecard_limit):
    symbol = str((record or {}).get("symbol", "") or "").upper()
    if not symbol:
        return
    scorecards = alleyway_state.setdefault("competition_symbol_records", {})
    symbol_records = scorecards.setdefault(symbol, [])
    symbol_records.append(
        {
            "realized_pnl": float((record or {}).get("realized_pnl", 0.0) or 0.0),
            "first_green_before_fail": bool((record or {}).get("first_green_before_fail", False)),
            "early_fail": str((record or {}).get("exit_reason", "") or "").startswith("EARLY_FAIL"),
            "recorded_at_utc": str((record or {}).get("recorded_at_utc", "") or ""),
        }
    )
    if len(symbol_records) > scorecard_limit:
        del symbol_records[:-scorecard_limit]


def hydrate_competition_lane_records_from_log(
    *,
    alleyway_state,
    path,
    scorecard_limit,
    lane_names,
    log,
):
    recent_lines = deque(
        maxlen=max(scorecard_limit * max(len(lane_names) + 1, 4), 120)
    )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    recent_lines.append(line)
    except FileNotFoundError:
        alleyway_state["competition_lane_records"] = {}
        return {"lanes": {}, "records_loaded": 0, "source": "missing"}
    except Exception as exc:
        log(f"  LANE_SCORE_RESTORE_FAIL reason=read error={exc}")
        alleyway_state["competition_lane_records"] = {}
        return {"lanes": {}, "records_loaded": 0, "source": "error"}

    scorecards = {}
    restored_count = 0
    malformed_count = 0
    for line in recent_lines:
        try:
            record = json.loads(line)
        except Exception:
            malformed_count += 1
            continue

        lane = str((record or {}).get("regime_at_entry", "UNKNOWN") or "UNKNOWN").upper()
        lane_records = scorecards.setdefault(lane, [])
        lane_records.append(
            {
                "realized_pnl": float((record or {}).get("realized_pnl", 0.0) or 0.0),
                "first_green_before_fail": bool((record or {}).get("first_green_before_fail", False)),
                "early_fail": str((record or {}).get("exit_reason", "") or "").startswith("EARLY_FAIL"),
                "recorded_at_utc": str((record or {}).get("recorded_at_utc", "") or ""),
            }
        )
        if len(lane_records) > scorecard_limit:
            del lane_records[:-scorecard_limit]
        restored_count += 1

    alleyway_state["competition_lane_records"] = scorecards
    return {
        "lanes": {lane: len(records) for lane, records in scorecards.items()},
        "records_loaded": restored_count,
        "malformed": malformed_count,
        "source": "tail",
    }


def hydrate_competition_symbol_records_from_log(
    *,
    alleyway_state,
    path,
    scorecard_limit,
    log,
):
    recent_lines = deque(maxlen=max(scorecard_limit * 8, 160))
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    recent_lines.append(line)
    except FileNotFoundError:
        alleyway_state["competition_symbol_records"] = {}
        return {"symbols": {}, "records_loaded": 0, "source": "missing"}
    except Exception as exc:
        log(f"  SYMBOL_SCORE_RESTORE_FAIL reason=read error={exc}")
        alleyway_state["competition_symbol_records"] = {}
        return {"symbols": {}, "records_loaded": 0, "source": "error"}

    scorecards = {}
    restored_count = 0
    malformed_count = 0
    for line in recent_lines:
        try:
            record = json.loads(line)
        except Exception:
            malformed_count += 1
            continue

        symbol = str((record or {}).get("symbol", "") or "").upper()
        if not symbol:
            continue
        symbol_records = scorecards.setdefault(symbol, [])
        symbol_records.append(
            {
                "realized_pnl": float((record or {}).get("realized_pnl", 0.0) or 0.0),
                "first_green_before_fail": bool((record or {}).get("first_green_before_fail", False)),
                "early_fail": str((record or {}).get("exit_reason", "") or "").startswith("EARLY_FAIL"),
                "recorded_at_utc": str((record or {}).get("recorded_at_utc", "") or ""),
            }
        )
        if len(symbol_records) > scorecard_limit:
            del symbol_records[:-scorecard_limit]
        restored_count += 1

    alleyway_state["competition_symbol_records"] = scorecards
    return {
        "symbols": {symbol: len(records) for symbol, records in scorecards.items()},
        "records_loaded": restored_count,
        "malformed": malformed_count,
        "source": "tail",
    }


def build_competition_lane_scorecard(*, active_positions, alleyway_state, lane_names, get_position_lane):
    active_counts = {}
    for pdata in active_positions.values():
        lane = get_position_lane(pdata)
        active_counts[lane] = int(active_counts.get(lane, 0) or 0) + 1

    scorecards = alleyway_state.get("competition_lane_records", {}) or {}
    lanes = list(lane_names)
    for lane in active_counts:
        if lane not in lanes:
            lanes.append(lane)
    for lane in scorecards:
        if lane not in lanes:
            lanes.append(lane)

    lane_fragments = []
    for lane in lanes:
        lane_records = list(scorecards.get(lane, []) or [])
        trade_count = len(lane_records)
        realized_pnl = sum(float(r.get("realized_pnl", 0.0) or 0.0) for r in lane_records)
        wins = sum(1 for r in lane_records if float(r.get("realized_pnl", 0.0) or 0.0) > 0.0)
        first_green = sum(1 for r in lane_records if r.get("first_green_before_fail"))
        early_fail = sum(1 for r in lane_records if r.get("early_fail"))
        lane_fragments.append(
            f"{lane}[a={int(active_counts.get(lane, 0) or 0)} "
            f"t={trade_count} pnl={realized_pnl:+.2f} "
            f"w={wins} fg={first_green} ef={early_fail}]"
        )
    return " ".join(lane_fragments)


def get_competition_lane_recent_stats(*, alleyway_state, lane, cluster_window, max_age_seconds):
    normalized_lane = str(lane or "UNKNOWN").upper()
    scorecards = alleyway_state.get("competition_lane_records", {}) or {}
    lane_records = list(scorecards.get(normalized_lane, []) or [])
    if not lane_records:
        return {
            "lane": normalized_lane,
            "records": [],
            "trade_count": 0,
            "wins": 0,
            "early_fails": 0,
            "first_green": 0,
            "realized_pnl": 0.0,
            "fresh": False,
        }

    recent = lane_records[-cluster_window:]
    fresh_records = []
    now = datetime.now(timezone.utc)
    for record in recent:
        recorded_at = str(record.get("recorded_at_utc", "") or "")
        try:
            recorded_dt = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
        except Exception:
            recorded_dt = None
        if recorded_dt is None or (now - recorded_dt).total_seconds() <= max_age_seconds:
            fresh_records.append(record)

    window = fresh_records if fresh_records else recent
    return {
        "lane": normalized_lane,
        "records": window,
        "trade_count": len(window),
        "wins": sum(1 for r in window if float(r.get("realized_pnl", 0.0) or 0.0) > 0.0),
        "early_fails": sum(1 for r in window if r.get("early_fail")),
        "first_green": sum(1 for r in window if r.get("first_green_before_fail")),
        "realized_pnl": sum(float(r.get("realized_pnl", 0.0) or 0.0) for r in window),
        "fresh": bool(fresh_records),
    }


def get_competition_symbol_recent_stats(*, alleyway_state, symbol, cluster_window, max_age_seconds):
    normalized_symbol = str(symbol or "").upper()
    scorecards = alleyway_state.get("competition_symbol_records", {}) or {}
    symbol_records = list(scorecards.get(normalized_symbol, []) or [])
    if not symbol_records:
        return {
            "symbol": normalized_symbol,
            "records": [],
            "trade_count": 0,
            "wins": 0,
            "losses": 0,
            "early_fails": 0,
            "first_green": 0,
            "realized_pnl": 0.0,
            "fresh": False,
        }

    recent = symbol_records[-cluster_window:]
    fresh_records = []
    now = datetime.now(timezone.utc)
    for record in recent:
        recorded_at = str(record.get("recorded_at_utc", "") or "")
        try:
            recorded_dt = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
        except Exception:
            recorded_dt = None
        if recorded_dt is None or (now - recorded_dt).total_seconds() <= max_age_seconds:
            fresh_records.append(record)

    window = fresh_records if fresh_records else recent
    wins = sum(1 for r in window if float(r.get("realized_pnl", 0.0) or 0.0) > 0.0)
    losses = sum(1 for r in window if float(r.get("realized_pnl", 0.0) or 0.0) < 0.0)
    return {
        "symbol": normalized_symbol,
        "records": window,
        "trade_count": len(window),
        "wins": wins,
        "losses": losses,
        "early_fails": sum(1 for r in window if r.get("early_fail")),
        "first_green": sum(1 for r in window if r.get("first_green_before_fail")),
        "realized_pnl": sum(float(r.get("realized_pnl", 0.0) or 0.0) for r in window),
        "fresh": bool(fresh_records),
    }


def get_competition_lane_priority(*, alleyway_state, lane, cluster_window, max_age_seconds):
    """Return a priority tuple (win_rate, trade_count, realized_pnl) for the lane's recent performance.

    Higher win_rate is better. trade_count breaks ties (more data = more reliable).
    realized_pnl breaks further ties. Used for sorting experimental candidates.
    """
    stats = get_competition_lane_recent_stats(
        alleyway_state=alleyway_state,
        lane=lane,
        cluster_window=cluster_window,
        max_age_seconds=max_age_seconds,
    )
    trade_count = stats["trade_count"]
    if trade_count == 0:
        return (0.5, 0, 0.0)  # No data: neutral priority
    wins = stats["wins"]
    win_rate = wins / trade_count
    realized_pnl = stats["realized_pnl"]
    return (win_rate, trade_count, realized_pnl)


def _lane_is_losing(*, alleyway_state, lane, cluster_window, max_age_seconds,
                    min_trades=3, max_win_rate=0.40):
    """Check if a lane is consistently losing based on recent scorecard."""
    stats = get_competition_lane_recent_stats(
        alleyway_state=alleyway_state,
        lane=lane,
        cluster_window=cluster_window,
        max_age_seconds=max_age_seconds,
    )
    trade_count = stats["trade_count"]
    if trade_count < min_trades:
        return False
    win_rate = stats["wins"] / trade_count if trade_count > 0 else 0.0
    return win_rate <= max_win_rate and stats["realized_pnl"] < 0.0


def _lane_is_winning(*, alleyway_state, lane, cluster_window, max_age_seconds,
                     min_trades=3, min_win_rate=0.55):
    """Check if a lane is performing well based on recent scorecard."""
    stats = get_competition_lane_recent_stats(
        alleyway_state=alleyway_state,
        lane=lane,
        cluster_window=cluster_window,
        max_age_seconds=max_age_seconds,
    )
    trade_count = stats["trade_count"]
    if trade_count < min_trades:
        return False
    win_rate = stats["wins"] / trade_count if trade_count > 0 else 0.0
    return win_rate >= min_win_rate and stats["realized_pnl"] > 0.0


def get_experimental_lane_floor_bump(
    *,
    alleyway_state,
    regime,
    book_stress=None,
    free_margin_ratio=1.0,
    cluster_window=6,
    max_age_seconds=1200,
):
    """Return a confidence floor bump for winning regimes.

    When a regime (PRICE/RAW/GEMINI) has a recent win rate >= 55% over 3+ trades
    with positive realized PnL, we raise the confidence floor by 0.02-0.08
    depending on performance. This means only high-confidence signals from winning
    regimes can enter — good regimes get a higher bar (quality signals), and we
    don't waste good regimes on weak signals.

    For strongly winning regimes (WR >= 65%, 4+ trades), the bump is 0.08.
    For moderately winning regimes (WR >= 55%, 3+ trades), the bump is 0.02-0.05.
    """
    regime = str(regime or "").upper()
    if regime not in {"PRICE", "RAW", "GEMINI"}:
        return 0.0

    stats = get_competition_lane_recent_stats(
        alleyway_state=alleyway_state,
        lane=regime,
        cluster_window=cluster_window,
        max_age_seconds=max_age_seconds,
    )
    trade_count = stats["trade_count"]
    if trade_count < 3:
        return 0.0

    win_rate = stats["wins"] / trade_count if trade_count > 0 else 0.0
    realized_pnl = stats["realized_pnl"]

    if realized_pnl <= 0.0 or win_rate < 0.55:
        return 0.0

    # Strong performers: WR >= 65%, 4+ trades, positive PnL
    if win_rate >= 0.65 and trade_count >= 4:
        return 0.08
    # Moderate performers: WR >= 60%, 3+ trades
    if win_rate >= 0.60:
        return 0.05
    # Baseline winning: WR >= 55%, 3+ trades
    return 0.02


def loser_lane_defend_guard_active(
    *,
    alleyway_state,
    regime,
    book_stress=None,
    free_margin_ratio=1.0,
    cluster_window=6,
    max_age_seconds=1200,
):
    """Activate defend guard for consistently losing regimes.

    When a regime (PRICE/RAW/GEMINI) has win rate <= 35% over 4+ trades
    with negative realized PnL, ALL experimental relief modes are disabled.
    This prevents bad regimes from getting any special entry treatment.
    """
    regime = str(regime or "").upper()
    if regime not in {"PRICE", "RAW", "GEMINI"}:
        return False

    return _lane_is_losing(
        alleyway_state=alleyway_state,
        lane=regime,
        cluster_window=cluster_window,
        max_age_seconds=max_age_seconds,
        min_trades=4,
        max_win_rate=0.35,
    )


def loser_lane_nonflat_hard_block_active(
    *,
    alleyway_state,
    regime,
    book_stress=None,
    free_margin_ratio=1.0,
    cluster_window=6,
    max_age_seconds=1200,
):
    """Hard block entries from severely underperforming regimes when book is not flat.

    When a regime (PRICE/RAW/GEMINI) has win rate <= 30% over 3+ trades
    with negative realized PnL, entries require confidence >= max(min_confidence,
    mode_floor + buffer). This forces losing regimes to only enter on extremely
    high-confidence signals.
    """
    regime = str(regime or "").upper()
    if regime not in {"PRICE", "RAW", "GEMINI"}:
        return False

    return _lane_is_losing(
        alleyway_state=alleyway_state,
        lane=regime,
        cluster_window=cluster_window,
        max_age_seconds=max_age_seconds,
        min_trades=3,
        max_win_rate=0.30,
    )


def get_experimental_candidate_sort_key(
    item,
    *,
    alleyway_state,
    book_stress,
    get_symbol_stress,
    get_anchor_drag_state,
    get_competition_symbol_recent_stats,
    cluster_window,
    max_age_seconds,
    anchor_drag_sort_penalty,
    symbol_recent_drag_sort_penalty,
):
    symbol, _signal, confidence, _mode, _atr, regime, _signal_type, _entry_context = item
    adjusted_confidence = float(confidence or 0.0)

    # Compute actual lane priority from recent scorecard (win_rate, trade_count, pnl)
    lane_priority = get_competition_lane_priority(
        alleyway_state=alleyway_state,
        lane=regime,
        cluster_window=cluster_window,
        max_age_seconds=max_age_seconds,
    )

    # Penalize confidence for candidates from losing regimes
    lane_wr, lane_count, lane_pnl = lane_priority
    if lane_count >= 3 and lane_wr < 0.40 and lane_pnl < 0.0:
        adjusted_confidence -= min(0.15, 0.10 * (0.40 - lane_wr) / 0.40)

    if regime in {"PRICE", "RAW", "GEMINI"}:
        stress = get_symbol_stress(symbol)
        adopted_heavy_rearm = (
            alleyway_state.get("entry_posture") == "REARM"
            and int((book_stress or {}).get("adopted_positions", 0) or 0) > 0
            and int((book_stress or {}).get("direct_positions", 0) or 0) == 0
        )
        if adopted_heavy_rearm:
            adjusted_confidence -= min(
                0.30,
                float(stress.get("score", 0.0) or 0.0) * 0.10
                + float(stress.get("drawdown_share", 0.0) or 0.0) * 0.80
                + float(stress.get("position_ratio", 0.0) or 0.0) * 0.08
                + (0.05 if stress.get("all_losing") else 0.0),
            )
        elif alleyway_state.get("entry_posture") == "REARM":
            adjusted_confidence -= min(
                0.15,
                float(stress.get("drawdown_share", 0.0) or 0.0) * 0.40,
            )

    anchor_drag = get_anchor_drag_state()
    if anchor_drag.get("active"):
        if symbol == anchor_drag.get("symbol"):
            adjusted_confidence -= min(
                anchor_drag_sort_penalty,
                0.05 + float(anchor_drag.get("drawdown_share", 0.0) or 0.0) * 0.15,
            )
        else:
            adjusted_confidence += min(
                0.06,
                float(anchor_drag.get("drawdown_share", 0.0) or 0.0) * 0.10,
            )

    symbol_recent_stats = get_competition_symbol_recent_stats(symbol)
    if (
        regime in {"PRICE", "RAW", "GEMINI"}
        and int(symbol_recent_stats.get("trade_count", 0) or 0) >= 2
        and float(symbol_recent_stats.get("realized_pnl", 0.0) or 0.0) < 0.0
    ):
        losses = int(symbol_recent_stats.get("losses", 0) or 0)
        wins = int(symbol_recent_stats.get("wins", 0) or 0)
        if losses > wins:
            adjusted_confidence -= min(
                symbol_recent_drag_sort_penalty,
                0.02 * losses + (0.03 if wins == 0 else 0.0),
            )
    return (lane_priority, adjusted_confidence, float(confidence or 0.0))


def prioritize_experimental_opportunities(
    opportunities,
    *,
    alleyway_state,
    book_stress,
    get_symbol_stress,
    get_anchor_drag_state,
    get_competition_symbol_recent_stats,
    cluster_window,
    max_age_seconds,
    anchor_drag_sort_penalty,
    symbol_recent_drag_sort_penalty,
):
    if not opportunities:
        return opportunities

    experimental_regimes = ("GEMINI", "PRICE", "RAW")
    buckets = {regime: [] for regime in experimental_regimes}
    remainder = []

    for item in opportunities:
        regime = item[5]
        if regime in buckets:
            buckets[regime].append(item)
        else:
            remainder.append(item)

    if not any(buckets.values()):
        return opportunities

    for regime, bucket in buckets.items():
        if bucket:
            bucket.sort(
                key=lambda item: get_experimental_candidate_sort_key(
                    item,
                    alleyway_state=alleyway_state,
                    book_stress=book_stress,
                    get_symbol_stress=get_symbol_stress,
                    get_anchor_drag_state=get_anchor_drag_state,
                    get_competition_symbol_recent_stats=get_competition_symbol_recent_stats,
                    cluster_window=cluster_window,
                    max_age_seconds=max_age_seconds,
                    anchor_drag_sort_penalty=anchor_drag_sort_penalty,
                    symbol_recent_drag_sort_penalty=symbol_recent_drag_sort_penalty,
                ),
                reverse=True,
            )

    promoted = []
    while True:
        available = [
            (
                regime,
                get_experimental_candidate_sort_key(
                    bucket[0],
                    alleyway_state=alleyway_state,
                    book_stress=book_stress,
                    get_symbol_stress=get_symbol_stress,
                    get_anchor_drag_state=get_anchor_drag_state,
                    get_competition_symbol_recent_stats=get_competition_symbol_recent_stats,
                    cluster_window=cluster_window,
                    max_age_seconds=max_age_seconds,
                    anchor_drag_sort_penalty=anchor_drag_sort_penalty,
                    symbol_recent_drag_sort_penalty=symbol_recent_drag_sort_penalty,
                ),
            )
            for regime, bucket in buckets.items()
            if bucket
        ]
        if not available:
            break
        available.sort(key=lambda item: item[1], reverse=True)
        for regime, _sort_key in available:
            if buckets[regime]:
                promoted.append(buckets[regime].pop(0))

    return promoted + remainder
