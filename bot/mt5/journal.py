"""Trade/behavior records, snapshots and runtime state files."""
from __future__ import annotations
from .config import *  # noqa: F401,F403
from .state import active_positions, alleyway_state  # noqa: F401
from collections import deque
from datetime import datetime
from datetime import timezone
import MetaTrader5 as mt5
import json
import os
import time
from .log import append_jsonl_record, log
from .market_data import is_tick_stale
from .positions import get_position_lane
from .risk import get_alleyway_mapping
from .sessions import is_crypto, is_exotic, is_good_session
from .signals import get_price_edge_signal
from .strategy_lab import advance_strategy_lab_lane, get_current_strategy_lab_lane_config, get_current_strategy_lab_lane_id, get_post_cleanup_raw_shotgun_min_confidence, get_strategy_lab_symbol_variants, is_strategy_lab_lane, is_strategy_lab_symbol


def emit_strategy_lab_event(
    event_type,
    symbol,
    signal_type,
    mode,
    regime,
    confidence=None,
    **extra,
):
    if not is_strategy_lab_lane(symbol, signal_type, mode, regime):
        return

    record = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "event_type": str(event_type or "unknown"),
        "symbol": str(symbol or "").upper(),
        "signal_type": str(signal_type or ""),
        "mode": str(mode or "").upper(),
        "regime": str(regime or "").upper(),
        "confidence": float(confidence or 0.0),
    }
    for key, value in extra.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            record[str(key)] = value
        else:
            record[str(key)] = str(value)
    append_jsonl_record(STRATEGY_LAB_LOG_FILE, record)

def emit_blocked_quality_candidate_record(
    symbol,
    regime,
    signal,
    mode,
    confidence,
    reason,
    trigger="",
    entry_posture="",
):
    tick = None
    try:
        tick = mt5.symbol_info_tick(symbol)
    except Exception:
        tick = None

    bid = float(getattr(tick, "bid", 0.0) or 0.0) if tick else 0.0
    ask = float(getattr(tick, "ask", 0.0) or 0.0) if tick else 0.0
    last = float(getattr(tick, "last", 0.0) or 0.0) if tick else 0.0
    if bid > 0.0 and ask > 0.0:
        ref_price = (bid + ask) / 2.0
    else:
        ref_price = last if last > 0.0 else 0.0

    record = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": str(symbol),
        "regime": str(regime),
        "signal": str(signal or ""),
        "mode": str(mode or ""),
        "confidence": float(confidence or 0.0),
        "reason": str(reason or "unknown"),
        "trigger": str(trigger or ""),
        "entry_posture": str(entry_posture or ""),
        "reference_price": float(ref_price or 0.0),
        "bid": bid,
        "ask": ask,
        "last": last,
        "guard_threshold": (
            float(get_post_cleanup_raw_shotgun_min_confidence(symbol))
            if str(regime) == "RAW" and str(mode) == "SHOTGUN"
            else None
        ),
    }
    append_jsonl_record(BLOCKED_QUALITY_CANDIDATE_LOG_FILE, record)
    emit_strategy_lab_event(
        event_type="blocked_quality",
        symbol=symbol,
        signal_type=signal,
        mode=mode,
        regime=regime,
        confidence=confidence,
        reason=str(reason or "unknown"),
        trigger=str(trigger or ""),
        entry_posture=str(entry_posture or ""),
        guard_threshold=record.get("guard_threshold"),
        reference_price=record.get("reference_price", 0.0),
    )

def update_trade_behavior_metrics(pdata, pnl, hold_sec, atr_dollar_value):
    pnl = float(pnl or 0.0)
    hold_sec = max(0.0, float(hold_sec or 0.0))
    atr_dollar_value = max(0.0, float(atr_dollar_value or 0.0))

    pdata['max_favorable_excursion_pnl'] = max(
        float(pdata.get('max_favorable_excursion_pnl', 0.0) or 0.0),
        max(0.0, pnl),
    )
    pdata['max_adverse_excursion_pnl'] = max(
        float(pdata.get('max_adverse_excursion_pnl', 0.0) or 0.0),
        max(0.0, -pnl),
    )

    if pnl > 0 and pdata.get('time_to_first_green_seconds') is None:
        pdata['time_to_first_green_seconds'] = hold_sec

    if atr_dollar_value > 0:
        milestones = (
            (0.25, 'time_to_0_25_atr_seconds'),
            (0.50, 'time_to_0_5_atr_seconds'),
            (1.00, 'time_to_1_0_atr_seconds'),
        )
        for mult, key in milestones:
            if pdata.get(key) is None and pnl >= atr_dollar_value * mult:
                pdata[key] = hold_sec
        if pdata.get('time_to_minus_0_35_atr_seconds') is None and pnl <= -(atr_dollar_value * 0.35):
            pdata['time_to_minus_0_35_atr_seconds'] = hold_sec

def emit_trade_behavior_record(ticket, pdata, exit_reason, exit_type, realized_pnl=None, hold_sec=None):
    if not pdata or pdata.get('behavior_recorded'):
        return

    entry_time = float(pdata.get('entry_time', time.time()) or time.time())
    if hold_sec is None:
        hold_sec = max(0.0, time.time() - entry_time)
    hold_sec = max(0.0, float(hold_sec or 0.0))
    realized = float(realized_pnl if realized_pnl is not None else pdata.get('last_pnl', 0.0) or 0.0)
    max_favorable = float(pdata.get('max_favorable_excursion_pnl', 0.0) or 0.0)
    max_adverse = float(pdata.get('max_adverse_excursion_pnl', 0.0) or 0.0)
    atr = max(0.0, float(pdata.get('atr', 0.0) or 0.0))
    volume = max(0.0, float(pdata.get('volume', 0.0) or 0.0))
    atr_dollar_value = 0.0

    try:
        sym_info = mt5.symbol_info(str(pdata.get('symbol', '') or ''))
        if sym_info and sym_info.trade_tick_value > 0 and sym_info.trade_tick_size > 0 and atr > 0 and volume > 0:
            atr_ticks = atr / sym_info.trade_tick_size
            atr_dollar_value = atr_ticks * sym_info.trade_tick_value * volume
    except Exception:
        atr_dollar_value = 0.0

    def atr_units(value):
        if atr_dollar_value <= 0:
            return None
        return float(value) / atr_dollar_value

    first_green_seconds = pdata.get('time_to_first_green_seconds')
    minus_035_seconds = pdata.get('time_to_minus_0_35_atr_seconds')
    strategy_lab_variant = str(pdata.get('strategy_lab_variant', '') or '')
    strategy_lab_lane_id = str(pdata.get('strategy_lab_lane_id', '') or '')
    strategy_lab_role = str(pdata.get('strategy_lab_role', '') or '')
    strategy_lab_hypothesis = str(pdata.get('strategy_lab_hypothesis', '') or '')
    mfe_capture_pct = None
    peak_before_exit = float(pdata.get('peak_pnl', 0.0) or 0.0)
    if peak_before_exit > 0:
        mfe_capture_pct = float(realized) / peak_before_exit * 100.0
    first_green_before_fail = (
        first_green_seconds is not None
        and (minus_035_seconds is None or float(first_green_seconds) <= float(minus_035_seconds))
    )

    record = {
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'ticket': int(ticket),
        'symbol': str(pdata.get('symbol', '') or ''),
        'direction': str(pdata.get('direction', '') or ''),
        'entry_mode': str(pdata.get('mode', '') or ''),
        'entry_signal_type': str(pdata.get('entry_signal_type', 'unlabeled') or 'unlabeled'),
        'entry_context': str(pdata.get('entry_context', 'unknown') or 'unknown'),
        'regime_at_entry': str(pdata.get('entry_regime', 'unknown') or 'unknown'),
        'entry_confidence_raw': float(pdata.get('confidence', 0.0) or 0.0),
        'strategy_lab_variant': strategy_lab_variant,
        'strategy_lab_lane_id': strategy_lab_lane_id,
        'strategy_lab_role': strategy_lab_role,
        'strategy_lab_hypothesis': strategy_lab_hypothesis,
        'entry_price': float(pdata.get('entry_price', 0.0) or 0.0),
        'atr_at_entry': atr,
        'spread_at_entry': float(pdata.get('spread_at_entry', 0.0) or 0.0),
        'entry_time_utc': datetime.fromtimestamp(entry_time, timezone.utc).isoformat(),
        'exit_time_utc': datetime.now(timezone.utc).isoformat(),
        'hold_seconds': hold_sec,
        'time_to_first_green_seconds': first_green_seconds,
        'time_to_0_25_atr_seconds': pdata.get('time_to_0_25_atr_seconds'),
        'time_to_0_5_atr_seconds': pdata.get('time_to_0_5_atr_seconds'),
        'time_to_1_0_atr_seconds': pdata.get('time_to_1_0_atr_seconds'),
        'time_to_minus_0_35_atr_seconds': minus_035_seconds,
        'max_favorable_excursion_pnl': max_favorable,
        'max_adverse_excursion_pnl': max_adverse,
        'max_favorable_excursion_atr': atr_units(max_favorable),
        'max_adverse_excursion_atr': atr_units(max_adverse),
        'first_green_before_fail': first_green_before_fail,
        'hit_0_25_atr_before_minus_0_35_atr': (
            pdata.get('time_to_0_25_atr_seconds') is not None
            and (minus_035_seconds is None or float(pdata.get('time_to_0_25_atr_seconds')) <= float(minus_035_seconds))
        ),
        'hit_0_5_atr_before_minus_0_35_atr': (
            pdata.get('time_to_0_5_atr_seconds') is not None
            and (minus_035_seconds is None or float(pdata.get('time_to_0_5_atr_seconds')) <= float(minus_035_seconds))
        ),
        'peak_pnl_before_exit': peak_before_exit,
        'mfe_capture_pct': mfe_capture_pct,
        'realized_pnl': realized,
        'exit_reason': str(exit_reason or 'UNKNOWN_EXIT'),
        'exit_type': str(exit_type or 'managed'),
        'adopted': bool(pdata.get('adopted', False)),
        'mean_reversion': bool(pdata.get('mean_reversion', False)),
    }
    append_jsonl_record(TRADE_BEHAVIOR_LOG_FILE, record)
    emit_strategy_lab_event(
        event_type="exit",
        symbol=record['symbol'],
        signal_type=record['entry_signal_type'],
        mode=record['entry_mode'],
        regime=record['regime_at_entry'],
        confidence=record['entry_confidence_raw'],
        ticket=record['ticket'],
        realized_pnl=record['realized_pnl'],
        exit_reason=record['exit_reason'],
        exit_type=record['exit_type'],
        hold_seconds=record['hold_seconds'],
        first_green_before_fail=record['first_green_before_fail'],
        time_to_first_green_seconds=record['time_to_first_green_seconds'],
        max_favorable_excursion_pnl=record['max_favorable_excursion_pnl'],
        max_adverse_excursion_pnl=record['max_adverse_excursion_pnl'],
        peak_pnl_before_exit=record['peak_pnl_before_exit'],
        entry_context=record['entry_context'],
        experiment_variant=record['strategy_lab_variant'],
        lane_id=record['strategy_lab_lane_id'],
        role=record['strategy_lab_role'],
        hypothesis=record['strategy_lab_hypothesis'],
        mfe_capture_pct=record['mfe_capture_pct'],
    )
    record_competition_lane_outcome(record)
    if strategy_lab_lane_id:
        advance_strategy_lab_lane(strategy_lab_lane_id)
    pdata['behavior_recorded'] = True

def format_competition_lane_trigger(prefix, pdata_or_lane, symbol, *parts):
    lane = (
        str(pdata_or_lane or '').upper()
        if isinstance(pdata_or_lane, str)
        else get_position_lane(pdata_or_lane)
    )
    normalized_symbol = str(symbol or '?')
    extra_parts = [str(part) for part in parts if str(part or '')]
    return ":".join([str(prefix), lane, normalized_symbol, *extra_parts])

def record_competition_lane_outcome(record):
    lane = str((record or {}).get('regime_at_entry', 'UNKNOWN') or 'UNKNOWN').upper()
    scorecards = alleyway_state.setdefault('competition_lane_records', {})
    lane_records = scorecards.setdefault(lane, [])
    lane_records.append(
        {
            'realized_pnl': float((record or {}).get('realized_pnl', 0.0) or 0.0),
            'first_green_before_fail': bool((record or {}).get('first_green_before_fail', False)),
            'early_fail': str((record or {}).get('exit_reason', '') or '').startswith('EARLY_FAIL'),
            'recorded_at_utc': str((record or {}).get('recorded_at_utc', '') or ''),
        }
    )
    if len(lane_records) > COMPETITION_LANE_SCORECARD_LIMIT:
        del lane_records[:-COMPETITION_LANE_SCORECARD_LIMIT]

def hydrate_competition_lane_records_from_log(path=TRADE_BEHAVIOR_LOG_FILE):
    recent_lines = deque(
        maxlen=max(COMPETITION_LANE_SCORECARD_LIMIT * max(len(COMPETITION_LANE_NAMES) + 1, 4), 120)
    )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    recent_lines.append(line)
    except FileNotFoundError:
        alleyway_state['competition_lane_records'] = {}
        return {'lanes': {}, 'records_loaded': 0, 'source': 'missing'}
    except Exception as exc:
        log(f"  LANE_SCORE_RESTORE_FAIL reason=read error={exc}")
        alleyway_state['competition_lane_records'] = {}
        return {'lanes': {}, 'records_loaded': 0, 'source': 'error'}

    scorecards = {}
    restored_count = 0
    malformed_count = 0
    for line in recent_lines:
        try:
            record = json.loads(line)
        except Exception:
            malformed_count += 1
            continue

        lane = str((record or {}).get('regime_at_entry', 'UNKNOWN') or 'UNKNOWN').upper()
        lane_records = scorecards.setdefault(lane, [])
        lane_records.append(
            {
                'realized_pnl': float((record or {}).get('realized_pnl', 0.0) or 0.0),
                'first_green_before_fail': bool((record or {}).get('first_green_before_fail', False)),
                'early_fail': str((record or {}).get('exit_reason', '') or '').startswith('EARLY_FAIL'),
                'recorded_at_utc': str((record or {}).get('recorded_at_utc', '') or ''),
            }
        )
        if len(lane_records) > COMPETITION_LANE_SCORECARD_LIMIT:
            del lane_records[:-COMPETITION_LANE_SCORECARD_LIMIT]
        restored_count += 1

    alleyway_state['competition_lane_records'] = scorecards
    return {
        'lanes': {lane: len(records) for lane, records in scorecards.items()},
        'records_loaded': restored_count,
        'malformed': malformed_count,
        'source': 'tail',
    }

def build_competition_lane_scorecard(active_positions):
    active_counts = {}
    for pdata in active_positions.values():
        lane = get_position_lane(pdata)
        active_counts[lane] = int(active_counts.get(lane, 0) or 0) + 1

    scorecards = alleyway_state.get('competition_lane_records', {}) or {}
    lanes = list(COMPETITION_LANE_NAMES)
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
        realized_pnl = sum(float(r.get('realized_pnl', 0.0) or 0.0) for r in lane_records)
        wins = sum(1 for r in lane_records if float(r.get('realized_pnl', 0.0) or 0.0) > 0.0)
        first_green = sum(1 for r in lane_records if r.get('first_green_before_fail'))
        early_fail = sum(1 for r in lane_records if r.get('early_fail'))
        lane_fragments.append(
            f"{lane}[a={int(active_counts.get(lane, 0) or 0)} "
            f"t={trade_count} pnl={realized_pnl:+.2f} "
            f"w={wins} fg={first_green} ef={early_fail}]"
        )
    return " ".join(lane_fragments)

def get_competition_lane_recent_stats(lane):
    normalized_lane = str(lane or 'UNKNOWN').upper()
    scorecards = alleyway_state.get('competition_lane_records', {}) or {}
    lane_records = list(scorecards.get(normalized_lane, []) or [])
    if not lane_records:
        return {
            'lane': normalized_lane,
            'records': [],
            'trade_count': 0,
            'wins': 0,
            'early_fails': 0,
            'first_green': 0,
            'realized_pnl': 0.0,
            'fresh': False,
        }

    recent = lane_records[-COMPETITION_LANE_CLUSTER_WINDOW:]
    fresh_records = []
    now = datetime.now(timezone.utc)
    for record in recent:
        recorded_at = str(record.get('recorded_at_utc', '') or '')
        try:
            recorded_dt = datetime.fromisoformat(recorded_at.replace('Z', '+00:00'))
        except Exception:
            recorded_dt = None
        if recorded_dt is None or (now - recorded_dt).total_seconds() <= COMPETITION_LANE_CLUSTER_MAX_AGE_SECONDS:
            fresh_records.append(record)

    window = fresh_records if fresh_records else recent
    return {
        'lane': normalized_lane,
        'records': window,
        'trade_count': len(window),
        'wins': sum(1 for r in window if float(r.get('realized_pnl', 0.0) or 0.0) > 0.0),
        'early_fails': sum(1 for r in window if r.get('early_fail')),
        'first_green': sum(1 for r in window if r.get('first_green_before_fail')),
        'realized_pnl': sum(float(r.get('realized_pnl', 0.0) or 0.0) for r in window),
        'fresh': bool(fresh_records),
    }

def emit_price_candidate_records(cycle, opportunities, entry_posture, rearm_reason, free_margin_ratio, book_stress):
    if not opportunities:
        return

    price_rows = []
    for symbol, signal, confidence, mode, atr, regime, signal_type, entry_context in opportunities:
        if regime != 'PRICE':
            continue
        price_rows.append((symbol, signal, confidence, mode, atr, signal_type, entry_context))

    if not price_rows:
        return

    for rank, row in enumerate(price_rows[:3], start=1):
        symbol, signal, confidence, mode, atr, signal_type, entry_context = row
        record = {
            'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
            'cycle': int(cycle),
            'rank': int(rank),
            'symbol': str(symbol),
            'signal': str(signal),
            'confidence': float(confidence or 0.0),
            'mode': str(mode),
            'atr': float(atr or 0.0),
            'signal_type': str(signal_type or 'price_unlabeled'),
            'entry_context': str(entry_context or 'price_unlabeled'),
            'entry_posture': str(entry_posture or 'UNKNOWN'),
            'rearm_reason': str(rearm_reason or 'none'),
            'free_margin_ratio': float(free_margin_ratio or 0.0),
            'managed_positions': int(book_stress.get('managed_positions', 0) or 0),
            'direct_positions': int(book_stress.get('direct_positions', 0) or 0),
            'managed_drawdown_pct': float(book_stress.get('managed_drawdown_pct', 0.0) or 0.0),
            'top_symbol_drawdown_pct': float(book_stress.get('top_symbol_drawdown_pct', 0.0) or 0.0),
        }
        append_jsonl_record(PRICE_CANDIDATE_LOG_FILE, record)

def log_symbol_filter_snapshot(diagnostics, context="active-symbols"):
    """Log current shared symbol-filter outcomes without changing behavior."""
    try:
        if not diagnostics:
            return
        watch_spread = diagnostics.get('watchlist_spread_blocked') or []
        watch_stale = diagnostics.get('watchlist_stale') or []
        watch_spread_text = ','.join(watch_spread[:6]) if watch_spread else '-'
        watch_stale_text = ','.join(watch_stale[:6]) if watch_stale else '-'
        log(
            "  SYMBOL_FILTER "
            f"[{context}] total={int(diagnostics.get('total_symbols', 0) or 0)} "
            f"active={int(diagnostics.get('active', 0) or 0)} "
            f"hidden={int(diagnostics.get('disabled_or_hidden', 0) or 0)} "
            f"session={int(diagnostics.get('session_blocked', 0) or 0)} "
            f"no_tick={int(diagnostics.get('no_tick', 0) or 0)} "
            f"stale={int(diagnostics.get('stale_tick', 0) or 0)} "
            f"spread={int(diagnostics.get('spread_blocked', 0) or 0)} "
            f"watch_spread={watch_spread_text} "
            f"watch_stale={watch_stale_text}"
        )
    except Exception as exc:
        log(f"  SYMBOL_FILTER [{context}] error={str(exc)[:80]}")

def log_price_watchlist_snapshot(active_symbols, context="active-symbols"):
    """
    PRICE-lane-only diagnostic: show why historically useful PRICE symbols are
    on or off the current tradeable board without changing any behavior.
    """
    try:
        active_set = {str(sym or "").upper() for sym in active_symbols}
        now = time.time()
        parts = []
        for symbol in PRICE_UNIVERSE_WATCHLIST:
            info = mt5.symbol_info(symbol)
            if not info:
                parts.append(f"{symbol}:missing")
                continue
            if info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
                parts.append(f"{symbol}:disabled")
                continue
            if not info.visible:
                parts.append(f"{symbol}:hidden")
                continue

            tick = mt5.symbol_info_tick(symbol)
            if not tick or tick.ask <= 0:
                parts.append(f"{symbol}:no_tick")
                continue

            diag = {}
            signal, confidence, _atr, _thesis, signal_type = get_price_edge_signal(symbol, diagnostics=diag)
            best_score = float(diag.get('price_best_score', 0.0) or 0.0)
            best_conf = float(diag.get('price_best_confidence', 0.0) or 0.0)
            best_type = diag.get('price_best_signal_type') or diag.get('price_best_score_signal_type') or '-'
            score_text = f"score={best_score:.1f}:conf={best_conf:.2f}:{best_type}"

            tick_stale, tick_age = is_tick_stale(tick, now=now)
            if tick_stale:
                age_text = "?" if tick_age is None else str(int(tick_age))
                parts.append(f"{symbol}:stale({age_text}s):{score_text}")
                continue

            spread_pct = abs(tick.ask - tick.bid) / tick.ask * 100
            if is_crypto(symbol):
                max_spread = MAX_SPREAD_PCT_CRYPTO
            elif is_exotic(symbol):
                max_spread = MAX_SPREAD_PCT_EXOTIC
            else:
                max_spread = MAX_SPREAD_PCT_FOREX
            if spread_pct > max_spread:
                parts.append(f"{symbol}:spread({spread_pct:.3f}>{max_spread:.3f}):{score_text}")
                continue

            status = "active" if symbol in active_set else "offboard"
            if signal:
                parts.append(f"{symbol}:{status}:sig={signal_type or '-'}:{confidence:.2f}")
            elif best_score > 0 or best_conf > 0:
                parts.append(f"{symbol}:{status}:{score_text}")
            else:
                parts.append(f"{symbol}:{status}:score=0.0")

        if parts:
            log(f"  PRICE_WATCH [{context}] {' | '.join(parts)}")
    except Exception as exc:
        log(f"  PRICE_WATCH [{context}] error={str(exc)[:80]}")

def log_price_shadow_board_snapshot(context="active-symbols", max_items_per_reason=3):
    """
    PRICE-only diagnostic: inspect offboard visible non-exotic symbols and surface
    the strongest latent PRICE theses by exclusion reason without changing runtime
    symbol eligibility.
    """
    try:
        all_symbols = mt5.symbols_get()
        if not all_symbols:
            return

        now = time.time()
        shadow = {'session': [], 'spread': [], 'stale': []}
        for info in all_symbols:
            if info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED or not info.visible:
                continue

            symbol = str(info.name or "").upper()
            if not symbol or is_exotic(symbol) or is_crypto(symbol):
                continue

            tick = mt5.symbol_info_tick(symbol)
            if not tick or tick.ask <= 0:
                continue

            reason = None
            if not is_good_session(symbol):
                reason = 'session'
            else:
                tick_stale, _tick_age = is_tick_stale(tick, now=now)
                if tick_stale:
                    reason = 'stale'
                else:
                    spread_pct = abs(tick.ask - tick.bid) / tick.ask * 100
                    if spread_pct > MAX_SPREAD_PCT_FOREX:
                        reason = 'spread'
            if not reason:
                continue

            diag = {}
            signal, confidence, _atr, _thesis, signal_type = get_price_edge_signal(symbol, diagnostics=diag)
            best_score = float(diag.get('price_best_score', 0.0) or 0.0)
            best_conf = float(diag.get('price_best_confidence', 0.0) or 0.0)
            if not signal and best_score <= 0 and best_conf <= 0:
                continue

            best_type = signal_type or diag.get('price_best_signal_type') or diag.get('price_best_score_signal_type') or '-'
            shadow[reason].append((best_score, best_conf, symbol, best_type))

        parts = []
        for reason in ('session', 'spread', 'stale'):
            ranked = sorted(shadow[reason], key=lambda item: (item[0], item[1], item[2]), reverse=True)
            if not ranked:
                parts.append(f"{reason}=-")
                continue
            reason_items = [
                f"{symbol}:{score:.1f}:{conf:.2f}:{signal_type}"
                for score, conf, symbol, signal_type in ranked[:max_items_per_reason]
            ]
            parts.append(f"{reason}={','.join(reason_items)}")

        log(f"  PRICE_SHADOW [{context}] {' '.join(parts)}")
    except Exception as exc:
        log(f"  PRICE_SHADOW [{context}] error={str(exc)[:80]}")

def maybe_log_price_watch_alert(active_symbols, context="cycle", cooldown_seconds=30):
    """
    Emit a compact alert only when a watched PRICE symbol develops nonzero
    structure. This keeps the lane quiet during dead tape but surfaces the
    first meaningful board change automatically.
    """
    try:
        now = time.time()
        next_allowed = float(alleyway_state.get('price_watch_alert_until', 0.0) or 0.0)
        if now < next_allowed:
            return

        active_set = {str(sym or "").upper() for sym in active_symbols}
        alerts = []
        for symbol in PRICE_UNIVERSE_WATCHLIST:
            info = mt5.symbol_info(symbol)
            if not info or info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED or not info.visible:
                continue
            tick = mt5.symbol_info_tick(symbol)
            if not tick or tick.ask <= 0:
                continue

            spread_pct = abs(tick.ask - tick.bid) / tick.ask * 100
            if is_crypto(symbol):
                max_spread = MAX_SPREAD_PCT_CRYPTO
            elif is_exotic(symbol):
                max_spread = MAX_SPREAD_PCT_EXOTIC
            else:
                max_spread = MAX_SPREAD_PCT_FOREX

            diag = {}
            signal, confidence, _atr, _thesis, signal_type = get_price_edge_signal(symbol, diagnostics=diag)
            best_score = float(diag.get('price_best_score', 0.0) or 0.0)
            best_conf = float(diag.get('price_best_confidence', 0.0) or 0.0)
            if not signal and best_score <= 0 and best_conf <= 0:
                continue

            state = "active" if symbol in active_set else "offboard"
            if spread_pct > max_spread:
                state = f"spread({spread_pct:.3f}>{max_spread:.3f})"
            if signal:
                alerts.append(f"{symbol}:{state}:sig={signal_type or '-'}:{confidence:.2f}")
            else:
                best_type = diag.get('price_best_signal_type') or diag.get('price_best_score_signal_type') or '-'
                alerts.append(f"{symbol}:{state}:score={best_score:.1f}:conf={best_conf:.2f}:{best_type}")

        if alerts:
            alleyway_state['price_watch_alert_until'] = now + cooldown_seconds
            log(f"  PRICE_WATCH_ALERT [{context}] {' | '.join(alerts)}")
    except Exception as exc:
        log(f"  PRICE_WATCH_ALERT [{context}] error={str(exc)[:80]}")

def maybe_log_price_blocker_alert(
    reversion_diag,
    active_positions_count,
    direct_positions_count,
    post_cleanup_hold_remaining=0,
    post_cleanup_hold_trigger="",
    post_cleanup_quality_gate_active=False,
    post_cleanup_quality_gate_trigger="",
    context="cycle",
    cooldown_seconds=30,
):
    """
    Emit a compact blocker line only when PRICE has an honest pass-class thesis
    but still never reaches pre-open/open. This keeps diagnosis inside the
    PRICE lane without changing shared behavior.
    """
    try:
        price_opp = int(reversion_diag.get('price_opportunities', 0) or 0)
        price_opened = int(reversion_diag.get('price_opened', 0) or 0)
        exp_ready = int(reversion_diag.get('experimental_preopen_ready', 0) or 0)
        price_blk_conf = int(reversion_diag.get('price_blocked_late_confidence', 0) or 0)
        best_conf = float(reversion_diag.get('price_best_confidence', 0.0) or 0.0)
        best_score = float(reversion_diag.get('price_best_score', 0.0) or 0.0)
        best_symbol = str(reversion_diag.get('price_best_symbol', '-') or '-').upper()
        best_signal_type = str(reversion_diag.get('price_best_signal_type', '-') or '-')
        if (
            price_opp <= 0
            or price_opened > 0
            or exp_ready > 0
            or price_blk_conf > 0
            or best_conf < PRICE_PASS_CONFIDENCE
            or best_symbol in {'', '-'}
        ):
            return

        now = time.time()
        next_allowed = float(alleyway_state.get('price_blocker_alert_until', 0.0) or 0.0)
        if now < next_allowed:
            return

        sync_freeze_until = float(
            get_alleyway_mapping('sync_close_reentry_symbol_freeze_until').get(best_symbol, 0.0) or 0.0
        )
        market_closed_until = float(
            get_alleyway_mapping('market_closed_symbol_until').get(best_symbol, 0.0) or 0.0
        )
        sync_freeze_left = max(0, int(sync_freeze_until - now))
        market_closed_left = max(0, int(market_closed_until - now))
        reasons = [
            f"symbol={best_symbol}",
            f"sig={best_signal_type}",
            f"conf={best_conf:.2f}",
            f"score={best_score:.1f}",
            f"posture={alleyway_state.get('entry_posture', '?')}",
            f"managed={int(active_positions_count)}",
            f"direct={int(direct_positions_count)}",
            f"raw_opp={int(reversion_diag.get('raw_opportunities', 0) or 0)}",
            f"exp_pair={int(reversion_diag.get('experimental_pair_slots', 0) or 0)}",
            f"exp_ready={exp_ready}",
        ]
        if post_cleanup_hold_remaining > 0:
            reasons.append(
                f"holdoff={int(post_cleanup_hold_remaining)}s:{post_cleanup_hold_trigger or 'unknown'}"
            )
        if post_cleanup_quality_gate_active:
            reasons.append(f"quality_gate={post_cleanup_quality_gate_trigger or 'unknown'}")
        if sync_freeze_left > 0:
            reasons.append(f"sync_freeze={sync_freeze_left}s")
        if market_closed_left > 0:
            reasons.append(f"venue_cooldown={market_closed_left}s")
        last_sync = str(alleyway_state.get('last_sync_close_holdoff_event', '') or '')
        if last_sync:
            reasons.append(f"last_sync={last_sync}")

        alleyway_state['price_blocker_alert_until'] = now + cooldown_seconds
        log(f"  PRICE_BLOCKER [{context}] {' '.join(reasons)}")
    except Exception as exc:
        log(f"  PRICE_BLOCKER [{context}] error={str(exc)[:80]}")

def note_strategy_lab_near_miss(
    reversion_diag,
    *,
    symbol,
    stage,
    reason,
    best_signal_type="",
    best_confidence=0.0,
    best_score=0.0,
    emitted_signal="",
    emitted_confidence=0.0,
    emitted_signal_type="",
    emitted_mode="",
    emitted_regime="",
):
    if reversion_diag is None or not is_strategy_lab_symbol(symbol):
        return
    strength = max(float(emitted_confidence or 0.0), float(best_confidence or 0.0))
    current_strength = float(reversion_diag.get("strategy_lab_near_miss_strength", 0.0) or 0.0)
    if strength < current_strength:
        return
    normalized_symbol = str(symbol or "").upper()
    candidate_signal_type = str(emitted_signal_type or best_signal_type or "")
    target_lane = False
    if candidate_signal_type:
        target_lane = is_strategy_lab_lane(normalized_symbol, candidate_signal_type, "SNIPER", "PRICE")
    reversion_diag["strategy_lab_near_miss_symbol"] = normalized_symbol
    reversion_diag["strategy_lab_near_miss_stage"] = str(stage or "")
    reversion_diag["strategy_lab_near_miss_reason"] = str(reason or "")
    reversion_diag["strategy_lab_near_miss_best_signal_type"] = str(best_signal_type or "")
    reversion_diag["strategy_lab_near_miss_best_confidence"] = float(best_confidence or 0.0)
    reversion_diag["strategy_lab_near_miss_best_score"] = float(best_score or 0.0)
    reversion_diag["strategy_lab_near_miss_emitted_signal"] = str(emitted_signal or "")
    reversion_diag["strategy_lab_near_miss_emitted_confidence"] = float(emitted_confidence or 0.0)
    reversion_diag["strategy_lab_near_miss_emitted_signal_type"] = str(emitted_signal_type or "")
    reversion_diag["strategy_lab_near_miss_emitted_mode"] = str(emitted_mode or "")
    reversion_diag["strategy_lab_near_miss_emitted_regime"] = str(emitted_regime or "")
    reversion_diag["strategy_lab_near_miss_target_lane"] = bool(target_lane)
    reversion_diag["strategy_lab_near_miss_strength"] = strength

def maybe_log_strategy_lab_near_miss_alert(
    reversion_diag,
    context="cycle",
    cooldown_seconds=30,
):
    try:
        symbol = str(reversion_diag.get("strategy_lab_near_miss_symbol", "") or "").upper()
        if not symbol:
            return
        now = time.time()
        next_allowed = float(alleyway_state.get("strategy_lab_near_miss_alert_until", 0.0) or 0.0)
        if now < next_allowed:
            return

        stage = str(reversion_diag.get("strategy_lab_near_miss_stage", "") or "")
        reason = str(reversion_diag.get("strategy_lab_near_miss_reason", "") or "")
        best_signal_type = str(reversion_diag.get("strategy_lab_near_miss_best_signal_type", "") or "")
        best_confidence = float(reversion_diag.get("strategy_lab_near_miss_best_confidence", 0.0) or 0.0)
        best_score = float(reversion_diag.get("strategy_lab_near_miss_best_score", 0.0) or 0.0)
        emitted_signal = str(reversion_diag.get("strategy_lab_near_miss_emitted_signal", "") or "")
        emitted_confidence = float(reversion_diag.get("strategy_lab_near_miss_emitted_confidence", 0.0) or 0.0)
        emitted_signal_type = str(reversion_diag.get("strategy_lab_near_miss_emitted_signal_type", "") or "")
        emitted_mode = str(reversion_diag.get("strategy_lab_near_miss_emitted_mode", "") or "").upper()
        emitted_regime = str(reversion_diag.get("strategy_lab_near_miss_emitted_regime", "") or "").upper()
        target_lane = bool(reversion_diag.get("strategy_lab_near_miss_target_lane", False))

        parts = [
            f"symbol={symbol}",
            f"stage={stage or '-'}",
            f"reason={reason or '-'}",
            f"best={best_signal_type or '-'}:{best_confidence:.2f}",
            f"score={best_score:.1f}",
            f"target_lane={'yes' if target_lane else 'no'}",
        ]
        if emitted_signal or emitted_signal_type or emitted_mode or emitted_regime:
            parts.append(
                f"emitted={emitted_regime or '-'}:{emitted_mode or '-'}:{emitted_signal_type or '-'}:{emitted_signal or '-'}:{emitted_confidence:.2f}"
            )

        alleyway_state["strategy_lab_near_miss_alert_until"] = now + cooldown_seconds
        log(f"  STRATEGY_LAB_NEAR_MISS [{context}] {' '.join(parts)}")

        if target_lane and best_signal_type:
            emit_strategy_lab_event(
                event_type="near_miss",
                symbol=symbol,
                signal_type=best_signal_type,
                mode="SNIPER",
                regime="PRICE",
                confidence=best_confidence,
                stage=stage or "",
                reason=reason or "",
                best_score=round(best_score, 2),
                emitted_signal=emitted_signal or "",
                emitted_confidence=round(emitted_confidence, 4),
                emitted_signal_type=emitted_signal_type or "",
                emitted_mode=emitted_mode or "",
                emitted_regime=emitted_regime or "",
            )
    except Exception as exc:
        log(f"  STRATEGY_LAB_NEAR_MISS [{context}] error={str(exc)[:80]}")

def log_stale_symbol(symbol, context, age_seconds=None):
    now = time.time()
    cooldowns = alleyway_state.setdefault("stale_symbol_log_until", {})
    next_allowed = float(cooldowns.get(symbol, 0.0) or 0.0)
    if now < next_allowed:
        return
    cooldowns[symbol] = now + STALE_SYMBOL_LOG_COOLDOWN_SECONDS
    age_text = "unknown" if age_seconds is None else f"{int(age_seconds)}s"
    log(f"  STALE_SYMBOL {symbol} context={context} tick_age={age_text}")

def log_rearm_transition(previous_posture, previous_reason):
    current_posture = alleyway_state.get("entry_posture", "DEFEND")
    current_reason = alleyway_state.get("rearm_reason", "")
    if current_posture == previous_posture and current_reason == previous_reason:
        return
    event = "REARM_ENTER" if current_posture == "REARM" else "REARM_EXIT"
    from_reason = previous_reason or "n/a"
    to_reason = current_reason or "n/a"
    detail = alleyway_state.get("rearm_debug", "")
    log(
        f"  {event} from={previous_posture} to={current_posture} "
        f"reason={to_reason} prev={from_reason}"
        f"{(' detail=' + detail) if detail else ''}"
    )

def format_position_observability(pdata):
    """Compact position metadata for close/disappearance attribution logs."""
    if not pdata:
        return "symbol=? mode=? adopted=?"
    symbol = pdata.get('symbol', '?')
    mode = pdata.get('mode', '?')
    adopted = "yes" if pdata.get('adopted') else "no"
    last_pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
    peak_pnl = float(pdata.get('peak_pnl', 0.0) or 0.0)
    return (
        f"symbol={symbol} mode={mode} adopted={adopted} "
        f"last_pnl=${last_pnl:+.2f} peak_pnl=${peak_pnl:+.2f}"
    )

def write_worker_state(status, event, reason="", detail="", exit_code=None, state_file=WORKER_STATE_FILE):
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "status": status,
        "event": event,
        "reason": reason,
        "detail": detail,
        "exit_code": exit_code,
    }
    try:
        with open(state_file, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    except Exception:
        pass

def write_runtime_state(balance=None, equity=None, margin_free=None):
    managed_items = list(active_positions.items())
    managed_positions = [pdata for _, pdata in managed_items]
    adopted_positions = [pdata for pdata in managed_positions if pdata.get("adopted")]
    direct_positions = [pdata for pdata in managed_positions if not pdata.get("adopted")]

    symbol_summary = {}
    for pdata in managed_positions:
        symbol = pdata.get("symbol", "UNKNOWN")
        bucket = symbol_summary.setdefault(
            symbol,
            {"count": 0, "volume": 0.0, "pnl": 0.0, "adopted_count": 0},
        )
        bucket["count"] += 1
        bucket["volume"] += float(pdata.get("volume", 0.0) or 0.0)
        bucket["pnl"] += float(pdata.get("last_pnl", 0.0) or 0.0)
        if pdata.get("adopted"):
            bucket["adopted_count"] += 1

    ordered_symbols = sorted(
        symbol_summary.items(),
        key=lambda item: (item[1]["count"], abs(item[1]["pnl"])),
        reverse=True,
    )

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "managed_positions": len(managed_positions),
        "adopted_positions": len(adopted_positions),
        "direct_positions": len(direct_positions),
        "strategy_lab_active_lane_id": get_current_strategy_lab_lane_id(),
        "strategy_lab_active_lane_variant": get_current_strategy_lab_lane_config().get("variant_label", ""),
        "strategy_lab_symbol_variants": get_strategy_lab_symbol_variants(),
        "strategy_lab_last_completed_lane_id": alleyway_state.get("strategy_lab_last_completed_lane_id", ""),
        "strategy_lab_lane_rotated_at": alleyway_state.get("strategy_lab_lane_rotated_at", ""),
        "balance": balance,
        "equity": equity,
        "entry_posture": alleyway_state.get("entry_posture", "DEFEND"),
        "rearm_active": bool(alleyway_state.get("rearm_active", False)),
        "rearm_reason": alleyway_state.get("rearm_reason", ""),
        "managed_drawdown_pct": round(float(alleyway_state.get("managed_drawdown_pct", 0.0) or 0.0), 4),
        "top_symbol_drawdown_pct": round(float(alleyway_state.get("top_symbol_drawdown_pct", 0.0) or 0.0), 4),
        "free_margin_ratio": round(
            float(
                alleyway_state.get(
                    "free_margin_ratio",
                    (margin_free / equity) if (margin_free is not None and equity) else 0.0,
                ) or 0.0
            ),
            4,
        ),
        "post_cleanup_hold_remaining_s": max(
            0,
            int(float(alleyway_state.get("post_cleanup_flat_rearm_hold_until", 0.0) or 0.0) - time.time()),
        ),
        "post_cleanup_hold_until_ts": float(
            alleyway_state.get("post_cleanup_flat_rearm_hold_until", 0.0) or 0.0
        ),
        "post_cleanup_hold_trigger": alleyway_state.get("post_cleanup_flat_rearm_trigger", ""),
        "post_cleanup_hold_armed_at": alleyway_state.get("post_cleanup_flat_rearm_armed_at", ""),
        "post_cleanup_hold_last_pnl": round(
            float(alleyway_state.get("post_cleanup_flat_rearm_last_pnl", 0.0) or 0.0),
            2,
        ),
        "post_cleanup_quality_gate_pending": bool(
            alleyway_state.get("post_cleanup_quality_gate_pending", False)
        ),
        "post_cleanup_quality_gate_trigger": alleyway_state.get("post_cleanup_quality_gate_trigger", ""),
        "post_cleanup_quality_gate_armed_at": alleyway_state.get("post_cleanup_quality_gate_armed_at", ""),
        "post_cleanup_first_leg_hold_remaining_s": max(
            0,
            int(float(alleyway_state.get("post_cleanup_first_leg_rearm_hold_until", 0.0) or 0.0) - time.time()),
        ),
        "post_cleanup_first_leg_hold_until_ts": float(
            alleyway_state.get("post_cleanup_first_leg_rearm_hold_until", 0.0) or 0.0
        ),
        "post_cleanup_first_leg_hold_trigger": alleyway_state.get("post_cleanup_first_leg_rearm_trigger", ""),
        "post_cleanup_first_leg_hold_armed_at": alleyway_state.get("post_cleanup_first_leg_rearm_armed_at", ""),
        "last_sync_close_holdoff_event": alleyway_state.get("last_sync_close_holdoff_event", ""),
        "last_sync_close_holdoff_checked_at": alleyway_state.get("last_sync_close_holdoff_checked_at", ""),
        "positions": [
            {
                "ticket": ticket,
                "symbol": pdata.get("symbol"),
                "volume": round(float(pdata.get("volume", 0.0) or 0.0), 2),
                "adopted": bool(pdata.get("adopted")),
                "mode": pdata.get("mode", "UNKNOWN"),
            }
            for ticket, pdata in managed_items
        ],
        "symbols": [
            {
                "symbol": symbol,
                "count": data["count"],
                "volume": round(data["volume"], 2),
                "pnl": round(data["pnl"], 2),
                "adopted_count": data["adopted_count"],
            }
            for symbol, data in ordered_symbols
        ],
    }

    try:
        with open(RUNTIME_STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    except Exception:
        pass

def flush_runtime_state_snapshot():
    acct = None
    try:
        acct = mt5.account_info()
    except Exception:
        acct = None
    write_runtime_state(
        balance=getattr(acct, "balance", None),
        equity=getattr(acct, "equity", None),
        margin_free=getattr(acct, "margin_free", None),
    )
