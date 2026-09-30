"""Position book manager: entries, exits, defense and rearm logic."""
from __future__ import annotations
from .config import *  # noqa: F401,F403
from .state import active_positions, alleyway_state, cooldown_until, recently_trimmed_symbols, total_pnl  # noqa: F401
from datetime import datetime
from datetime import timezone
from mt5_config import BOT_COMMENT_PREFIX
from mt5_config import BOT_MAGIC
import MetaTrader5 as mt5
import json
import os
import time
from .journal import emit_strategy_lab_event, emit_trade_behavior_record, flush_runtime_state_snapshot, format_competition_lane_trigger, format_position_observability, log_rearm_transition, log_stale_symbol, update_trade_behavior_metrics
from .log import log
from .market_data import get_learner, is_tick_stale, mark_broker_connection_backoff, mark_symbol_insufficient_margin, mark_symbol_market_closed
from .positions import get_position_hold_seconds, get_position_lane
from .risk import calc_sl_tp_prices, get_alleyway_mapping, get_effective_rearm_limits, get_symbol_family_bucket, get_symbol_stress, register_risk_event
from .sessions import is_exotic
from .signals import get_htf_bias
from .strategy_lab import get_active_strategy_lab_lane_config, get_strategy_lab_stall_exit_reason, get_strategy_lab_trail_floor, get_strategy_lab_variant_label, refresh_strategy_lab_owner_lane_on_startup


def arm_sync_close_reentry_freeze(symbol, now=None):
    """
    After a loser sync-close in a non-flat direct book, block immediate
    replacement on the same symbol and, for indices, close substitutes.
    """
    now = float(now if now is not None else time.time())
    symbol = str(symbol or "").upper()
    if not symbol:
        return "", 0, 0

    symbol_freeze_until = get_alleyway_mapping('sync_close_reentry_symbol_freeze_until')
    symbol_freeze_until[symbol] = max(
        float(symbol_freeze_until.get(symbol, 0.0) or 0.0),
        now + SYNC_CLOSE_REENTRY_SYMBOL_FREEZE_SECONDS,
    )
    alleyway_state['sync_close_reentry_symbol_freeze_until'] = symbol_freeze_until

    family = get_symbol_family_bucket(symbol)
    family_seconds = 0
    if family:
        family_freeze_until = get_alleyway_mapping('sync_close_reentry_family_freeze_until')
        family_seconds = SYNC_CLOSE_REENTRY_INDEX_FAMILY_FREEZE_SECONDS
        family_freeze_until[family] = max(
            float(family_freeze_until.get(family, 0.0) or 0.0),
            now + family_seconds,
        )
        alleyway_state['sync_close_reentry_family_freeze_until'] = family_freeze_until

    return family, SYNC_CLOSE_REENTRY_SYMBOL_FREEZE_SECONDS, family_seconds

def arm_profit_capture_freeze(now):
    """After banking a winner, stay defensive and block fresh adds briefly."""
    alleyway_state['profit_capture_cooldown_until'] = (
        now + PROFIT_CAPTURE_COOLDOWN_SECONDS
    )
    alleyway_state['profit_capture_entry_freeze_until'] = (
        now + PROFIT_CAPTURE_ENTRY_FREEZE_SECONDS
    )
    alleyway_state['rearm_cycles_remaining'] = 0
    alleyway_state['rearm_active'] = False
    alleyway_state['rearm_used_this_quiet'] = True
    alleyway_state['entry_posture'] = 'DEFEND'

def get_active_post_cleanup_holdoff(now=None):
    """Return remaining seconds and trigger for an active post-cleanup rebuild holdoff."""
    if now is None:
        now = time.time()
    hold_until = float(alleyway_state.get('post_cleanup_flat_rearm_hold_until', 0.0) or 0.0)
    if now >= hold_until:
        return 0, ''
    remaining = max(0, int(hold_until - now))
    trigger = alleyway_state.get('post_cleanup_flat_rearm_trigger', 'unknown')
    return remaining, trigger

def get_post_cleanup_quality_gate(now=None):
    """Return whether the next flat-book rebuild should use the stricter quality gate."""
    if now is None:
        now = time.time()
    if count_direct_positions() != 0:
        alleyway_state['post_cleanup_quality_gate_pending'] = False
        alleyway_state['post_cleanup_quality_gate_trigger'] = ''
        return False, ''
    remaining, _ = get_active_post_cleanup_holdoff(now)
    if remaining > 0:
        return False, ''
    if not alleyway_state.get('post_cleanup_quality_gate_pending', False):
        return False, ''
    trigger = alleyway_state.get('post_cleanup_quality_gate_trigger', 'unknown')
    return True, trigger

def is_one_pos_exotic_mercy_trigger(trigger):
    return str(trigger or '').startswith("ONE_POS_EXOTIC_MERCY_EXIT:")

def consume_post_cleanup_quality_gate():
    alleyway_state['post_cleanup_quality_gate_pending'] = False
    alleyway_state['post_cleanup_quality_gate_trigger'] = ''

def arm_post_cleanup_flat_rearm_holdoff(now, trigger, pnl):
    """Pause flat-book REARM after forced loser cleanup so rebuilds do not instantly churn."""
    if count_direct_positions() != 0:
        return False

    hold_until = now + POST_CLEANUP_FLAT_REARM_HOLDOFF_SECONDS
    current = float(alleyway_state.get('post_cleanup_flat_rearm_hold_until', 0.0) or 0.0)
    if hold_until <= current:
        return False

    alleyway_state['post_cleanup_flat_rearm_hold_until'] = hold_until
    alleyway_state['post_cleanup_flat_rearm_trigger'] = trigger
    alleyway_state['post_cleanup_flat_rearm_armed_at'] = datetime.now(timezone.utc).isoformat()
    alleyway_state['post_cleanup_flat_rearm_last_pnl'] = float(pnl or 0.0)
    alleyway_state['post_cleanup_quality_gate_pending'] = True
    alleyway_state['post_cleanup_quality_gate_trigger'] = trigger
    alleyway_state['post_cleanup_quality_gate_armed_at'] = datetime.now(timezone.utc).isoformat()
    alleyway_state['rearm_cycles_remaining'] = 0
    alleyway_state['rearm_active'] = False
    alleyway_state['entry_posture'] = 'DEFEND'
    log(
        f"  POST_CLEANUP_HOLDOFF {POST_CLEANUP_FLAT_REARM_HOLDOFF_SECONDS}s "
        f"trigger={trigger} pnl=${pnl:+.2f}"
    )
    flush_runtime_state_snapshot()
    return True

def arm_post_cleanup_first_leg_rearm_holdoff(now, trigger, symbol, mode):
    """After the first flat-book rebuild leg, pause follow-on rebuild pressure."""
    if count_direct_positions() != 1:
        return False

    hold_until = now + POST_CLEANUP_FIRST_LEG_REARM_HOLDOFF_SECONDS
    current = float(alleyway_state.get('post_cleanup_first_leg_rearm_hold_until', 0.0) or 0.0)
    if hold_until <= current:
        return False

    armed_trigger = f"{trigger}:{symbol}:{mode}"
    alleyway_state['post_cleanup_first_leg_rearm_hold_until'] = hold_until
    alleyway_state['post_cleanup_first_leg_rearm_trigger'] = armed_trigger
    alleyway_state['post_cleanup_first_leg_rearm_armed_at'] = datetime.now(timezone.utc).isoformat()
    alleyway_state['rearm_cycles_remaining'] = 0
    alleyway_state['rearm_active'] = False
    alleyway_state['entry_posture'] = 'DEFEND'
    log(
        f"  POST_CLEANUP_FIRST_LEG_HOLDOFF {POST_CLEANUP_FIRST_LEG_REARM_HOLDOFF_SECONDS}s "
        f"trigger={armed_trigger}"
    )
    flush_runtime_state_snapshot()
    return True

def arm_one_position_quiet_rearm_holdoff(now, trigger, pnl):
    """Pause quiet-book REARM when forced loser cleanup leaves one direct position."""
    if count_direct_positions() != 1:
        return

    hold_until = now + ONE_POSITION_QUIET_REARM_HOLDOFF_SECONDS
    current = float(alleyway_state.get('one_position_quiet_rearm_hold_until', 0.0) or 0.0)
    if hold_until <= current:
        return

    alleyway_state['one_position_quiet_rearm_hold_until'] = hold_until
    alleyway_state['one_position_quiet_rearm_trigger'] = trigger
    alleyway_state['rearm_cycles_remaining'] = 0
    alleyway_state['rearm_active'] = False
    alleyway_state['entry_posture'] = 'DEFEND'
    log(
        f"  ONE_POSITION_REARM_HOLDOFF {ONE_POSITION_QUIET_REARM_HOLDOFF_SECONDS}s "
        f"trigger={trigger} pnl=${pnl:+.2f}"
    )

def defend_bag_winner_positions(brain, free_margin_ratio):
    """Bank one winner in a stressed DEFEND book so recovery becomes realized, not just floating."""
    active_count = len(active_positions)
    if alleyway_state.get('entry_posture') != 'DEFEND':
        return 0
    idle_cycles = int(alleyway_state.get('cycles_without_trade', 0) or 0)
    # Live 2026-04-07 proof: once the repaired large-book posture floor stopped
    # fresh rebuilds, a 17-position DEFEND book could sit heavily net-green with
    # repeated open=0 / quiet=no while no existing harvest lane would bank a
    # winner. Keep a helper-owned contained-book harvest branch here so profit
    # realization does not depend on drift-prone top-of-file loser caps.
    contained_loaded_harvest_min_positions = 12
    contained_loaded_harvest_min_free_margin_ratio = 0.35
    contained_loaded_harvest_min_net_pnl = 100.0
    contained_loaded_harvest_min_win_pnl = 10.0
    contained_loaded_harvest_max_losers = 6
    contained_loaded_harvest_min_idle_cycles = 20
    # Live 2026-04-07 proof: after the large-book harvest lane worked, the book
    # compressed into a calm 7-position DEFEND hold with very high free margin
    # and repeated open=0 / quiet=no, but financed unwind still stalled on
    # blk_defend_loaded=0. Give that smaller contained shape its own helper-
    # owned harvest lane so profit realization does not depend on freeze counters.
    contained_mid_harvest_min_positions = 6
    contained_mid_harvest_max_positions = 10
    contained_mid_harvest_min_free_margin_ratio = 0.90
    contained_mid_harvest_min_net_pnl = 0.25
    contained_mid_harvest_min_win_pnl = 0.12
    contained_mid_harvest_max_losers = 4
    contained_mid_harvest_min_idle_cycles = 25

    winners = []
    losing_count = 0
    total_pnl = 0.0
    worst_loser_abs = 0.0
    now = time.time()

    for ticket, pdata in active_positions.items():
        if pdata.get('adopted'):
            continue
        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        total_pnl += pnl
        if pnl < 0:
            losing_count += 1
            worst_loser_abs = max(worst_loser_abs, abs(pnl))
            continue

        hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
        confidence = float(pdata.get('confidence', 0.0) or 0.0)
        winners.append((ticket, pdata, pnl, volume, hold_sec, confidence))

    stressed_winners = [
        item for item in winners
        if item[2] >= DEFEND_WIN_BAG_MIN_WIN_PNL
    ]
    harvest_winners = [
        item for item in winners
        if item[2] >= DEFEND_PROFIT_HARVEST_MIN_WIN_PNL
    ]

    if (
        active_count >= contained_loaded_harvest_min_positions
        and free_margin_ratio >= contained_loaded_harvest_min_free_margin_ratio
        and total_pnl >= contained_loaded_harvest_min_net_pnl
        and losing_count <= contained_loaded_harvest_max_losers
        and idle_cycles >= contained_loaded_harvest_min_idle_cycles
        and now >= float(alleyway_state.get('defend_profit_harvest_cooldown_until', 0.0) or 0.0)
    ):
        bag_reason = 'loaded_profit_harvest'
        candidate_winners = [
            item for item in winners
            if item[2] >= contained_loaded_harvest_min_win_pnl
        ]
    elif (
        contained_mid_harvest_min_positions <= active_count <= contained_mid_harvest_max_positions
        and free_margin_ratio >= contained_mid_harvest_min_free_margin_ratio
        and total_pnl >= contained_mid_harvest_min_net_pnl
        and losing_count <= contained_mid_harvest_max_losers
        and idle_cycles >= contained_mid_harvest_min_idle_cycles
        and now >= float(alleyway_state.get('defend_profit_harvest_cooldown_until', 0.0) or 0.0)
    ):
        bag_reason = 'mid_profit_harvest'
        candidate_winners = [
            item for item in winners
            if item[2] >= contained_mid_harvest_min_win_pnl
        ]
    elif active_count >= DEFEND_WIN_BAG_MIN_POSITIONS and (
        free_margin_ratio <= DEFEND_WIN_BAG_MAX_FREE_MARGIN_RATIO
    ):
        bag_reason = 'stress_recovery'
        candidate_winners = stressed_winners
    elif (
        active_count >= DEFEND_PROFIT_HARVEST_MIN_POSITIONS
        and free_margin_ratio >= DEFEND_PROFIT_HARVEST_MIN_FREE_MARGIN_RATIO
        and total_pnl >= DEFEND_PROFIT_HARVEST_MIN_NET_PNL
        and losing_count <= DEFEND_PROFIT_HARVEST_MAX_LOSERS
        and now >= float(alleyway_state.get('defend_profit_harvest_cooldown_until', 0.0) or 0.0)
    ):
        bag_reason = 'profit_harvest'
        candidate_winners = harvest_winners
    else:
        return 0

    if bag_reason == 'stress_recovery' and total_pnl < DEFEND_WIN_BAG_MIN_NET_PNL:
        return 0
    if bag_reason == 'stress_recovery' and losing_count == 0:
        return 0
    if not candidate_winners:
        return 0

    if bag_reason in {'loaded_profit_harvest', 'mid_profit_harvest'}:
        candidate_winners.sort(
            key=lambda item: (
                0 if item[1].get('mode') != 'REVERSION' else 1,
                -item[2],    # bank the strongest real winner first
                -item[4],    # prefer older held winners
                item[5],     # lower confidence first
            )
        )
    else:
        candidate_winners.sort(
            key=lambda item: (
                -item[2],     # bigger realized gain first
                item[4],      # older positions first
                0 if item[1].get('mode') == 'REVERSION' else 1,
                item[5],      # lower confidence first
            )
        )

    ticket, pdata, pnl, volume, hold_sec, confidence = candidate_winners[0]
    if close_position(ticket, exit_reason="WIN_BAG", exit_type="harvest"):
        mode = pdata.get('mode', 'REVERSION')
        symbol = pdata.get('symbol', '?')
        brain.record_exit(symbol, pnl, mode, hold_sec, failure_reason="PROFIT_HARVEST")
        brain.save()
        active_positions.pop(ticket, None)
        if bag_reason in {'profit_harvest', 'loaded_profit_harvest', 'mid_profit_harvest'}:
            alleyway_state['defend_profit_harvest_cooldown_until'] = (
                now + DEFEND_PROFIT_HARVEST_COOLDOWN_SECONDS
            )
        arm_profit_capture_freeze(now)
        log(
            f"  WIN_BAG {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} reason={bag_reason} defend_fm={free_margin_ratio:.2f} "
            f"net=${total_pnl:+.2f} losers={losing_count} idle={idle_cycles}"
        )
        return 1

    return 0

def defend_profit_capture_positions(brain, free_margin_ratio, mode_counts):
    """
    General profit capture for defended books.

    When the book is already net positive and calm, bank one strong winner
    before the bot gives back float or defaults to peeling losers first.
    """
    now = time.time()
    if now < float(alleyway_state.get('profit_capture_cooldown_until', 0.0) or 0.0):
        return 0

    active_count = len(active_positions)
    idle_cycles = int(alleyway_state.get('cycles_without_trade', 0) or 0)
    reversion_count = int(mode_counts.get('REVERSION', 0) or 0)
    if not (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and PROFIT_CAPTURE_MIN_POSITIONS <= active_count <= PROFIT_CAPTURE_MAX_POSITIONS
        and free_margin_ratio >= PROFIT_CAPTURE_MIN_FREE_MARGIN_RATIO
        and idle_cycles >= PROFIT_CAPTURE_MIN_IDLE_CYCLES
        and reversion_count >= max(1, active_count - PROFIT_CAPTURE_MAX_LOSERS)
    ):
        return 0

    total_pnl = 0.0
    losing_count = 0
    losing_pnl_abs = 0.0
    winners = []

    for ticket, pdata in active_positions.items():
        if pdata.get('adopted'):
            continue

        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        total_pnl += pnl
        if pnl <= 0:
            if pnl < 0:
                losing_count += 1
                losing_pnl_abs += abs(pnl)
            continue

        hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
        confidence = float(pdata.get('confidence', 0.0) or 0.0)
        winners.append((ticket, pdata, pnl, volume, hold_sec, confidence))

    if losing_count > PROFIT_CAPTURE_MAX_LOSERS:
        return 0
    if total_pnl < PROFIT_CAPTURE_MIN_NET_PNL:
        return 0
    if losing_count == 0 and total_pnl < PROFIT_CAPTURE_ALL_GREEN_MIN_NET_PNL:
        return 0

    loss_cover_threshold = max(
        PROFIT_CAPTURE_MIN_WIN_PNL,
        losing_pnl_abs if losing_count > 0 else PROFIT_CAPTURE_MIN_WIN_PNL * 1.5,
    )
    candidate_winners = [
        item for item in winners
        if item[2] >= PROFIT_CAPTURE_MIN_WIN_PNL
        and item[2] >= loss_cover_threshold
    ]
    if not candidate_winners:
        return 0

    candidate_winners.sort(
        key=lambda item: (
            -item[2],
            -item[4],
            0 if item[1].get('mode') == 'REVERSION' else 1,
            item[5],
        )
    )

    ticket, pdata, pnl, volume, hold_sec, confidence = candidate_winners[0]
    if close_position(ticket, exit_reason="LOADED_WIN_BAG", exit_type="harvest"):
        mode = pdata.get('mode', 'REVERSION')
        symbol = pdata.get('symbol', '?')
        brain.record_exit(symbol, pnl, mode, hold_sec, failure_reason="PROFIT_HARVEST")
        brain.save()
        active_positions.pop(ticket, None)
        arm_profit_capture_freeze(now)
        all_green = losing_count == 0
        log(
            f"  WIN_BAG {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} reason=profit_capture defend_fm={free_margin_ratio:.2f} "
            f"net=${total_pnl:+.2f} losers={losing_count} all_green={'yes' if all_green else 'no'} "
            f"idle={idle_cycles}"
        )
        return 1

    return 0

def rearm_financed_unwind_positions(brain, free_margin_ratio, mode_counts):
    """
    Peel one funded loser from a quiet net-green REARM book before it stalls.

    This is intentionally narrow: no REVERSION carriers, one loser max, strong
    free margin, real carry, and enough idle time that the book is proving it is
    calm rather than still trying to rebuild.
    """
    now = time.time()
    if now < float(alleyway_state.get('rearm_financed_unwind_cooldown_until', 0.0) or 0.0):
        return 0

    active_count = len(active_positions)
    idle_cycles = int(alleyway_state.get('cycles_without_trade', 0) or 0)
    direct_count = sum(1 for pdata in active_positions.values() if not pdata.get('adopted'))
    if not (
        alleyway_state.get('entry_posture') == 'REARM'
        and REARM_FINANCED_UNWIND_MIN_POSITIONS <= active_count <= REARM_FINANCED_UNWIND_MAX_POSITIONS
        and direct_count == active_count
        and free_margin_ratio >= REARM_FINANCED_UNWIND_MIN_FREE_MARGIN_RATIO
        and idle_cycles >= REARM_FINANCED_UNWIND_MIN_IDLE_CYCLES
        and int(mode_counts.get('REVERSION', 0) or 0) == 0
    ):
        return 0

    total_pnl = 0.0
    positive_carry = 0.0
    losers = []

    for ticket, pdata in active_positions.items():
        if pdata.get('adopted'):
            continue

        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        total_pnl += pnl
        if pnl > 0:
            positive_carry += pnl
            continue
        if pnl >= 0:
            continue

        hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
        confidence = float(pdata.get('confidence', 0.0) or 0.0)
        losers.append((ticket, pdata, pnl, volume, hold_sec, confidence))

    if len(losers) == 0 or len(losers) > REARM_FINANCED_UNWIND_MAX_LOSERS:
        return 0
    if total_pnl < REARM_FINANCED_UNWIND_MIN_NET_PNL:
        return 0
    if positive_carry < REARM_FINANCED_UNWIND_MIN_POSITIVE_CARRY:
        return 0

    eligible_losers = [
        item for item in losers
        if abs(item[2]) <= REARM_FINANCED_UNWIND_MAX_LOSS
        and positive_carry >= abs(item[2]) * REARM_FINANCED_UNWIND_CARRY_COVER_RATIO
        and (total_pnl - item[2]) >= REARM_FINANCED_UNWIND_MIN_REMAINING_NET
    ]
    if not eligible_losers:
        return 0

    eligible_losers.sort(
        key=lambda item: (
            item[2],
            -item[4],
            item[5],
        )
    )

    ticket, pdata, pnl, volume, hold_sec, confidence = eligible_losers[0]
    if close_position(ticket, exit_reason="REARM_FINANCED_UNWIND", exit_type="unwind"):
        mode = pdata.get('mode', 'MACHINE_GUN')
        symbol = pdata.get('symbol', '?')
        brain.record_exit(symbol, pnl, mode, hold_sec)
        brain.save()
        active_positions.pop(ticket, None)
        alleyway_state['rearm_financed_unwind_cooldown_until'] = (
            now + REARM_FINANCED_UNWIND_COOLDOWN_SECONDS
        )
        family, symbol_freeze_seconds, family_freeze_seconds = arm_sync_close_reentry_freeze(symbol, now)
        arm_profit_capture_freeze(now)
        family_suffix = (
            f" family_freeze={family}:{family_freeze_seconds}s" if family and family_freeze_seconds > 0 else ""
        )
        log(
            f"  REARM_FINANCED_UNWIND {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} rearm_fm={free_margin_ratio:.2f} net=${total_pnl:+.2f} "
            f"remaining_net=${(total_pnl - pnl):+.2f} carry=${positive_carry:+.2f} "
            f"idle={idle_cycles} symbol_freeze={symbol_freeze_seconds}s{family_suffix}"
        )
        return 1

    return 0

def defend_three_book_win_bag_positions(brain, free_margin_ratio, mode_counts):
    """
    In a 3-position DEFEND book, bank one repeat winner only after the same
    ticket stays positive across multiple idle cycles. The goal is to realize
    repetitive carry without immediately handing it back to fresh re-entry.

    Also allow a narrow net-green lane for the live 3-book failure mode:
    two solid winners carrying one tiny drag (or all-green), where leaving
    the whole book floating has repeatedly failed to realize recovery.
    """
    active_count = len(active_positions)
    idle_cycles = int(alleyway_state.get('cycles_without_trade', 0) or 0)
    direct_count = sum(1 for pdata in active_positions.values() if not pdata.get('adopted'))
    three_book_shape = (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and DEFEND_THREE_BOOK_WIN_BAG_MIN_POSITIONS <= active_count <= DEFEND_THREE_BOOK_WIN_BAG_MAX_POSITIONS
        and direct_count == active_count
        and free_margin_ratio >= DEFEND_THREE_BOOK_WIN_BAG_MIN_FREE_MARGIN_RATIO
        and idle_cycles >= max(2, DEFEND_THREE_BOOK_WIN_BAG_TRIGGER_CYCLES // 2)
        and int(mode_counts.get('REVERSION', 0) or 0) == 0
    )

    if not three_book_shape:
        alleyway_state['defend_three_book_win_bag_ticket'] = 0
        alleyway_state['defend_three_book_win_bag_cycles'] = 0
        return 0

    now = time.time()
    if now < float(alleyway_state.get('defend_three_book_win_bag_cooldown_until', 0.0) or 0.0):
        return 0

    positive_positions = []
    losers = []
    total_pnl = 0.0

    for ticket, pdata in active_positions.items():
        if pdata.get('adopted'):
            continue

        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        total_pnl += pnl
        if pnl > 0:
            hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
            confidence = float(pdata.get('confidence', 0.0) or 0.0)
            positive_positions.append((ticket, pdata, pnl, volume, hold_sec, confidence))
        else:
            hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
            confidence = float(pdata.get('confidence', 0.0) or 0.0)
            losers.append((ticket, pdata, pnl, volume, hold_sec, confidence))

    negative_count = len(losers)
    worst_loser_abs = max((abs(item[2]) for item in losers if item[2] < 0), default=0.0)
    positive_positions.sort(
        key=lambda item: (
            -item[2],
            -item[4],
            item[5],
        )
    )

    bag_reason = ""
    candidate = None

    if len(positive_positions) == 1 and negative_count == 2:
        candidate = positive_positions[0]
        if candidate[2] < DEFEND_THREE_BOOK_WIN_BAG_MIN_WIN_PNL:
            alleyway_state['defend_three_book_win_bag_ticket'] = 0
            alleyway_state['defend_three_book_win_bag_cycles'] = 0
            return 0
        bag_reason = "three_book_repeat"
    elif (
        len(positive_positions) >= DEFEND_THREE_BOOK_NET_GREEN_MIN_WINNERS
        and negative_count <= DEFEND_THREE_BOOK_NET_GREEN_MAX_LOSERS
        and total_pnl >= DEFEND_THREE_BOOK_NET_GREEN_MIN_TOTAL_PNL
        and worst_loser_abs <= DEFEND_THREE_BOOK_NET_GREEN_MAX_LOSER_ABS
    ):
        candidate = positive_positions[0]
        if candidate[2] < DEFEND_THREE_BOOK_NET_GREEN_MIN_PRIMARY_WIN_PNL:
            alleyway_state['defend_three_book_win_bag_ticket'] = 0
            alleyway_state['defend_three_book_win_bag_cycles'] = 0
            return 0
        bag_reason = "three_book_net_green"

    if candidate is None:
        alleyway_state['defend_three_book_win_bag_ticket'] = 0
        alleyway_state['defend_three_book_win_bag_cycles'] = 0
        return 0

    ticket, pdata, pnl, volume, hold_sec, confidence = candidate

    tracked_ticket = int(alleyway_state.get('defend_three_book_win_bag_ticket', 0) or 0)
    if tracked_ticket == ticket:
        three_book_cycles = int(alleyway_state.get('defend_three_book_win_bag_cycles', 0) or 0) + 1
    else:
        alleyway_state['defend_three_book_win_bag_ticket'] = ticket
        three_book_cycles = 1
    alleyway_state['defend_three_book_win_bag_cycles'] = three_book_cycles

    if three_book_cycles < DEFEND_THREE_BOOK_WIN_BAG_TRIGGER_CYCLES:
        return 0

    symbol = pdata.get('symbol', '?')
    if close_position(ticket, exit_reason="DEFEND_FINANCED_UNWIND", exit_type="unwind"):
        mode = pdata.get('mode', 'SHOTGUN')
        brain.record_exit(symbol, pnl, mode, hold_sec, failure_reason="PROFIT_HARVEST")
        brain.save()
        active_positions.pop(ticket, None)
        alleyway_state['defend_three_book_win_bag_ticket'] = 0
        alleyway_state['defend_three_book_win_bag_cycles'] = 0
        alleyway_state['defend_three_book_win_bag_cooldown_until'] = (
            now + DEFEND_THREE_BOOK_WIN_BAG_COOLDOWN_SECONDS
        )
        symbol_freeze_until = get_alleyway_mapping('defend_three_book_win_bag_symbol_freeze_until')
        symbol_freeze_until[symbol] = now + DEFEND_THREE_BOOK_WIN_BAG_SYMBOL_FREEZE_SECONDS
        alleyway_state['defend_three_book_win_bag_symbol_freeze_until'] = symbol_freeze_until
        arm_profit_capture_freeze(now)
        log(
            f"  THREE_BOOK_WIN_BAG {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} reason={bag_reason} defend_fm={free_margin_ratio:.2f} "
            f"net=${total_pnl:+.2f} winners={len(positive_positions)} losers={negative_count} "
            f"worst_loser=${worst_loser_abs:+.2f} idle={idle_cycles} cycles={three_book_cycles} "
            f"freeze={DEFEND_THREE_BOOK_WIN_BAG_SYMBOL_FREEZE_SECONDS}s"
        )
        return 1

    return 0

def defend_two_book_win_bag_positions(brain, free_margin_ratio, mode_counts):
    """
    In a 2-position DEFEND book, bank one repeating winner only after the same
    ticket has stayed green across multiple idle cycles. This is a deliberate
    harvest lane for near-clean books, not a rearm trigger.
    """
    active_count = len(active_positions)
    idle_cycles = int(alleyway_state.get('cycles_without_trade', 0) or 0)
    direct_count = sum(1 for pdata in active_positions.values() if not pdata.get('adopted'))
    entry_posture = str(alleyway_state.get('entry_posture') or '')
    now = time.time()
    two_book_base_shape = (
        True
        and DEFEND_TWO_BOOK_WIN_BAG_MIN_POSITIONS <= active_count <= DEFEND_TWO_BOOK_WIN_BAG_MAX_POSITIONS
        and direct_count == active_count
        and free_margin_ratio >= DEFEND_TWO_BOOK_WIN_BAG_MIN_FREE_MARGIN_RATIO
        and int(mode_counts.get('REVERSION', 0) or 0) == 0
    )
    two_book_repeat_shape = (
        two_book_base_shape
        and entry_posture == 'DEFEND'
        and idle_cycles >= max(3, DEFEND_TWO_BOOK_WIN_BAG_TRIGGER_CYCLES // 2)
    )
    two_book_green_shape = (
        two_book_base_shape
        and entry_posture in {'DEFEND', 'REARM'}
    )

    def log_two_book_diag(reason, cycles=0, ticket=0, symbol='?', pnl=0.0, extra=''):
        reason = str(reason or 'unknown')
        cycles = int(cycles or 0)
        last_reason = str(alleyway_state.get('defend_two_book_win_bag_last_reason', '') or '')
        last_logged_cycle = int(alleyway_state.get('defend_two_book_win_bag_last_logged_cycle', 0) or 0)
        should_log = False

        if reason != last_reason:
            should_log = True
        elif cycles > 0 and cycles != last_logged_cycle:
            milestone_cycles = {
                1,
                max(1, DEFEND_TWO_BOOK_WIN_BAG_TRIGGER_CYCLES // 2),
                max(1, DEFEND_TWO_BOOK_WIN_BAG_TRIGGER_CYCLES - 1),
                DEFEND_TWO_BOOK_WIN_BAG_TRIGGER_CYCLES,
            }
            if cycles in milestone_cycles or cycles % 3 == 0:
                should_log = True

        alleyway_state['defend_two_book_win_bag_last_reason'] = reason
        alleyway_state['defend_two_book_win_bag_last_logged_cycle'] = cycles

        if not should_log:
            return

        suffix = f" {extra}" if extra else ""
        log(
            f"  TWO_BOOK_WIN_BAG_DIAG reason={reason} ticket={int(ticket or 0)} "
            f"symbol={symbol or '?'} pnl=${float(pnl or 0.0):+.2f} cycles={cycles} "
            f"idle={idle_cycles} defend_fm={free_margin_ratio:.2f}{suffix}"
        )

    alleyway_state['defend_two_book_pending_entry_freeze_until'] = 0.0

    if not (two_book_repeat_shape or two_book_green_shape):
        alleyway_state['defend_two_book_win_bag_ticket'] = 0
        alleyway_state['defend_two_book_win_bag_cycles'] = 0
        shape_reasons = []
        if entry_posture not in {'DEFEND', 'REARM'}:
            shape_reasons.append(f"posture={entry_posture}")
        if not (DEFEND_TWO_BOOK_WIN_BAG_MIN_POSITIONS <= active_count <= DEFEND_TWO_BOOK_WIN_BAG_MAX_POSITIONS):
            shape_reasons.append(f"active={active_count}")
        if direct_count != active_count:
            shape_reasons.append(f"direct={direct_count}/{active_count}")
        if free_margin_ratio < DEFEND_TWO_BOOK_WIN_BAG_MIN_FREE_MARGIN_RATIO:
            shape_reasons.append(f"fm={free_margin_ratio:.2f}")
        if entry_posture == 'DEFEND' and idle_cycles < max(3, DEFEND_TWO_BOOK_WIN_BAG_TRIGGER_CYCLES // 2):
            shape_reasons.append(f"idle={idle_cycles}")
        if int(mode_counts.get('REVERSION', 0) or 0) != 0:
            shape_reasons.append(f"reversion={int(mode_counts.get('REVERSION', 0) or 0)}")
        log_two_book_diag('shape_blocked', extra=' '.join(shape_reasons[:4]))
        return 0

    if now < float(alleyway_state.get('defend_two_book_win_bag_cooldown_until', 0.0) or 0.0):
        cooldown_left = float(alleyway_state.get('defend_two_book_win_bag_cooldown_until', 0.0) or 0.0) - now
        log_two_book_diag('cooldown', extra=f"cooldown_left={max(0, int(cooldown_left))}s")
        return 0

    positive_positions = []
    negative_count = 0
    total_pnl = 0.0

    for ticket, pdata in active_positions.items():
        if pdata.get('adopted'):
            continue

        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        total_pnl += pnl
        if pnl > 0:
            hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
            confidence = float(pdata.get('confidence', 0.0) or 0.0)
            positive_positions.append((ticket, pdata, pnl, volume, hold_sec, confidence))
        else:
            negative_count += 1

    if len(positive_positions) == 2 and negative_count == 0 and two_book_green_shape:
        candidate = max(
            positive_positions,
            key=lambda item: (item[2], item[3], item[5], item[4]),
        )
        ticket, pdata, pnl, volume, hold_sec, confidence = candidate
        symbol = pdata.get('symbol', '?')
        if total_pnl < DEFEND_TWO_BOOK_GREEN_MIN_NET_PNL:
            alleyway_state['defend_two_book_win_bag_ticket'] = 0
            alleyway_state['defend_two_book_win_bag_cycles'] = 0
            log_two_book_diag(
                'all_green_net_below_min',
                ticket=ticket,
                symbol=symbol,
                pnl=pnl,
                extra=f"net=${total_pnl:+.2f} min_net=${DEFEND_TWO_BOOK_GREEN_MIN_NET_PNL:.2f}",
            )
            return 0

        log_two_book_diag(
            'all_green_armed',
            ticket=ticket,
            symbol=symbol,
            pnl=pnl,
            extra=f"net=${total_pnl:+.2f} posture={entry_posture}",
        )
        if close_position(ticket, exit_reason="DEFEND_TWO_BOOK_WIN_BAG", exit_type="harvest"):
            mode = pdata.get('mode', 'SHOTGUN')
            brain.record_exit(symbol, pnl, mode, hold_sec)
            brain.save()
            active_positions.pop(ticket, None)
            alleyway_state['defend_two_book_win_bag_ticket'] = 0
            alleyway_state['defend_two_book_win_bag_cycles'] = 0
            alleyway_state['defend_two_book_win_bag_cooldown_until'] = (
                now + DEFEND_TWO_BOOK_WIN_BAG_COOLDOWN_SECONDS
            )
            symbol_freeze_until = get_alleyway_mapping('defend_two_book_win_bag_symbol_freeze_until')
            symbol_freeze_until[symbol] = now + DEFEND_TWO_BOOK_WIN_BAG_SYMBOL_FREEZE_SECONDS
            alleyway_state['defend_two_book_win_bag_symbol_freeze_until'] = symbol_freeze_until
            arm_profit_capture_freeze(now)
            log(
                f"  TWO_BOOK_WIN_BAG {symbol} #{ticket} P/L=${pnl:+.2f} "
                f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
                f"mode={mode} reason=two_book_all_green posture={entry_posture} "
                f"defend_fm={free_margin_ratio:.2f} net=${total_pnl:+.2f} "
                f"idle={idle_cycles} freeze={DEFEND_TWO_BOOK_WIN_BAG_SYMBOL_FREEZE_SECONDS}s"
            )
            return 1

        log_two_book_diag(
            'all_green_close_failed',
            ticket=ticket,
            symbol=symbol,
            pnl=pnl,
            extra=f"net=${total_pnl:+.2f} posture={entry_posture}",
        )
        return 0

    if len(positive_positions) != 1 or negative_count != 1:
        alleyway_state['defend_two_book_win_bag_ticket'] = 0
        alleyway_state['defend_two_book_win_bag_cycles'] = 0
        log_two_book_diag(
            'winner_shape_reset',
            extra=f"positive={len(positive_positions)} negative={negative_count}",
        )
        return 0

    ticket, pdata, pnl, volume, hold_sec, confidence = positive_positions[0]
    if pnl < DEFEND_TWO_BOOK_WIN_BAG_MIN_WIN_PNL:
        alleyway_state['defend_two_book_win_bag_ticket'] = 0
        alleyway_state['defend_two_book_win_bag_cycles'] = 0
        log_two_book_diag(
            'pnl_below_min',
            ticket=ticket,
            symbol=pdata.get('symbol', '?'),
            pnl=pnl,
            extra=f"min_pnl={DEFEND_TWO_BOOK_WIN_BAG_MIN_WIN_PNL:.2f}",
        )
        return 0

    tracked_ticket = int(alleyway_state.get('defend_two_book_win_bag_ticket', 0) or 0)
    if tracked_ticket == ticket:
        two_book_cycles = int(alleyway_state.get('defend_two_book_win_bag_cycles', 0) or 0) + 1
    else:
        alleyway_state['defend_two_book_win_bag_ticket'] = ticket
        two_book_cycles = 1
    alleyway_state['defend_two_book_win_bag_cycles'] = two_book_cycles
    alleyway_state['defend_two_book_pending_entry_freeze_until'] = (
        now + DEFEND_TWO_BOOK_PENDING_ENTRY_FREEZE_SECONDS
    )
    log_two_book_diag(
        'tracking',
        cycles=two_book_cycles,
        ticket=ticket,
        symbol=pdata.get('symbol', '?'),
        pnl=pnl,
    )

    if two_book_cycles < DEFEND_TWO_BOOK_WIN_BAG_TRIGGER_CYCLES:
        return 0

    symbol = pdata.get('symbol', '?')
    log_two_book_diag(
        'armed',
        cycles=two_book_cycles,
        ticket=ticket,
        symbol=symbol,
        pnl=pnl,
        extra=f"net=${total_pnl:+.2f}",
    )
    if close_position(ticket, exit_reason="THREE_BOOK_WIN_BAG", exit_type="harvest"):
        mode = pdata.get('mode', 'SHOTGUN')
        brain.record_exit(symbol, pnl, mode, hold_sec)
        brain.save()
        active_positions.pop(ticket, None)
        alleyway_state['defend_two_book_win_bag_ticket'] = 0
        alleyway_state['defend_two_book_win_bag_cycles'] = 0
        alleyway_state['defend_two_book_win_bag_cooldown_until'] = (
            now + DEFEND_TWO_BOOK_WIN_BAG_COOLDOWN_SECONDS
        )
        symbol_freeze_until = get_alleyway_mapping('defend_two_book_win_bag_symbol_freeze_until')
        symbol_freeze_until[symbol] = now + DEFEND_TWO_BOOK_WIN_BAG_SYMBOL_FREEZE_SECONDS
        alleyway_state['defend_two_book_win_bag_symbol_freeze_until'] = symbol_freeze_until
        arm_profit_capture_freeze(now)
        log(
            f"  TWO_BOOK_WIN_BAG {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} reason=two_book_repeat defend_fm={free_margin_ratio:.2f} "
            f"net=${total_pnl:+.2f} idle={idle_cycles} cycles={two_book_cycles} "
            f"freeze={DEFEND_TWO_BOOK_WIN_BAG_SYMBOL_FREEZE_SECONDS}s"
        )
        return 1

    log_two_book_diag(
        'close_failed',
        cycles=two_book_cycles,
        ticket=ticket,
        symbol=symbol,
        pnl=pnl,
        extra=f"net=${total_pnl:+.2f}",
    )
    return 0

def defend_mixed_win_bag_positions(brain, free_margin_ratio, mode_counts):
    """
    In a mixed DEFEND book, realize one strong REVERSION winner before the bot
    defaults to another loser peel. This targets the live shape where a couple
    of non-REVERSION losers linger while the REVERSION side is already carrying
    the book net positive.

    It also handles the all-green contained shape: if a loaded mixed DEFEND
    book is already frozen by the no-add governor and sitting meaningfully net
    positive, bank one real winner rather than waiting for a loser to reappear.
    """
    active_count = len(active_positions)
    reversion_count = int(mode_counts.get('REVERSION', 0) or 0)
    non_reversion_count = max(0, active_count - reversion_count)
    idle_cycles = int(alleyway_state.get('cycles_without_trade', 0) or 0)
    blocked_defend_loaded = int(alleyway_state.get('last_blocked_defend_loaded', 0) or 0)
    mixed_shape = (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and DEFEND_MIXED_WIN_BAG_MIN_POSITIONS <= active_count <= DEFEND_MIXED_WIN_BAG_MAX_POSITIONS
        and free_margin_ratio >= DEFEND_MIXED_WIN_BAG_MIN_FREE_MARGIN_RATIO
        and non_reversion_count >= DEFEND_MIXED_WIN_BAG_MIN_NON_REVERSION
        and idle_cycles >= DEFEND_MIXED_WIN_BAG_MIN_IDLE_CYCLES
    )

    if mixed_shape:
        alleyway_state['defend_mixed_win_bag_cycles'] = int(
            alleyway_state.get('defend_mixed_win_bag_cycles', 0) or 0
        ) + 1
    else:
        alleyway_state['defend_mixed_win_bag_cycles'] = 0
        return 0

    now = time.time()
    if now < float(alleyway_state.get('defend_mixed_win_bag_cooldown_until', 0.0) or 0.0):
        return 0

    mixed_cycles = int(alleyway_state.get('defend_mixed_win_bag_cycles', 0) or 0)
    if mixed_cycles < DEFEND_MIXED_WIN_BAG_TRIGGER_CYCLES:
        return 0

    total_pnl = 0.0
    losing_count = 0
    worst_loser_abs = 0.0
    non_reversion_losers = 0
    reversion_winners = []
    positive_winners = []

    for ticket, pdata in active_positions.items():
        if pdata.get('adopted'):
            continue

        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        total_pnl += pnl
        mode = pdata.get('mode', 'MACHINE_GUN')
        if pnl < 0:
            losing_count += 1
            worst_loser_abs = max(worst_loser_abs, abs(pnl))
            if mode != 'REVERSION':
                non_reversion_losers += 1
            continue

        hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
        confidence = float(pdata.get('confidence', 0.0) or 0.0)
        winner = (ticket, pdata, pnl, volume, hold_sec, confidence)
        positive_winners.append(winner)
        if mode == 'REVERSION':
            reversion_winners.append(winner)

    if total_pnl < DEFEND_MIXED_WIN_BAG_MIN_NET_PNL:
        return 0
    all_green_contained_shape = (
        losing_count == 0
        and blocked_defend_loaded >= DEFEND_MIXED_GREEN_HARVEST_MIN_BLOCKED_DEFEND_LOADED
        and total_pnl >= DEFEND_MIXED_GREEN_HARVEST_MIN_NET_PNL
    )

    harvest_reason = None
    if all_green_contained_shape:
        candidate_winners = [
            item for item in positive_winners
            if item[2] >= DEFEND_MIXED_GREEN_HARVEST_MIN_WIN_PNL
        ]
        candidate_winners.sort(
            key=lambda item: (
                item[1].get('mode') == 'REVERSION',  # Prefer de-risking heavier non-REVERSION legs first.
                -item[2],
                -item[4],
                item[5],
            )
        )
        harvest_reason = 'contained_green'
    else:
        if losing_count == 0 or losing_count > DEFEND_MIXED_WIN_BAG_MAX_LOSERS:
            return 0
        if non_reversion_losers == 0:
            return 0
        candidate_winners = [
            item for item in reversion_winners
            if item[2] >= DEFEND_MIXED_WIN_BAG_MIN_WIN_PNL
            and item[2] >= max(DEFEND_MIXED_WIN_BAG_MIN_WIN_PNL, worst_loser_abs * 1.5)
        ]
        harvest_reason = 'mixed_defend'
    if not candidate_winners:
        return 0

    if harvest_reason != 'contained_green':
        candidate_winners.sort(
            key=lambda item: (
                -item[2],
                -item[4],
                item[5],
            )
        )

    ticket, pdata, pnl, volume, hold_sec, confidence = candidate_winners[0]
    if close_position(ticket, exit_reason="ANCHOR_UNWIND", exit_type="unwind"):
        mode = pdata.get('mode', 'REVERSION')
        symbol = pdata.get('symbol', '?')
        brain.record_exit(symbol, pnl, mode, hold_sec)
        brain.save()
        active_positions.pop(ticket, None)
        alleyway_state['defend_mixed_win_bag_cycles'] = 0
        alleyway_state['defend_mixed_win_bag_cooldown_until'] = (
            now + DEFEND_MIXED_WIN_BAG_COOLDOWN_SECONDS
        )
        arm_profit_capture_freeze(now)
        log(
            f"  WIN_BAG {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} reason={harvest_reason} defend_fm={free_margin_ratio:.2f} "
            f"net=${total_pnl:+.2f} losers={losing_count} nonrev_losers={non_reversion_losers} "
            f"idle={idle_cycles} cycles={mixed_cycles} blk_defend_loaded={blocked_defend_loaded}"
        )
        return 1

    return 0

def defend_crowd_win_bag_positions(brain, free_margin_ratio, reversion_diag, mode_counts):
    """
    Bank one meaningful REVERSION winner when a crowded DEFEND book is clearly
    stuck behind crowding vetoes but still net positive. This is narrower than
    the failed light-harvest path: it only acts in the 6-7 position crowded
    defend shape, requires sustained blk_crowd pressure, and avoids books with
    multiple active losers.
    """
    active_count = len(active_positions)
    reversion_count = int(mode_counts.get('REVERSION', 0) or 0)
    crowd_shape = (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and DEFEND_CROWD_WIN_BAG_MIN_POSITIONS <= active_count <= DEFEND_CROWD_WIN_BAG_MAX_POSITIONS
        and reversion_count >= active_count
        and free_margin_ratio >= DEFEND_CROWD_WIN_BAG_MIN_FREE_MARGIN_RATIO
        and int(reversion_diag.get('opened', 0) or 0) == 0
        and int(reversion_diag.get('blocked_crowding', 0) or 0) >= DEFEND_CROWD_WIN_BAG_MIN_BLOCKED_CROWD
    )

    if crowd_shape:
        alleyway_state['defend_crowd_win_bag_cycles'] = int(
            alleyway_state.get('defend_crowd_win_bag_cycles', 0) or 0
        ) + 1
    else:
        alleyway_state['defend_crowd_win_bag_cycles'] = 0
        return 0

    now = time.time()
    if now < float(alleyway_state.get('defend_crowd_win_bag_cooldown_until', 0.0) or 0.0):
        return 0

    crowd_cycles = int(alleyway_state.get('defend_crowd_win_bag_cycles', 0) or 0)
    if crowd_cycles < DEFEND_CROWD_WIN_BAG_TRIGGER_CYCLES:
        return 0

    total_pnl = 0.0
    losing_count = 0
    worst_loser_abs = 0.0
    winners = []

    for ticket, pdata in active_positions.items():
        if pdata.get('adopted') or pdata.get('mode') != 'REVERSION':
            continue

        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        total_pnl += pnl
        if pnl < 0:
            losing_count += 1
            worst_loser_abs = max(worst_loser_abs, abs(pnl))
            continue

        hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
        confidence = float(pdata.get('confidence', 0.0) or 0.0)
        winners.append((ticket, pdata, pnl, volume, hold_sec, confidence))

    if total_pnl < DEFEND_CROWD_WIN_BAG_MIN_NET_PNL:
        return 0
    if losing_count > DEFEND_CROWD_WIN_BAG_MAX_LOSERS:
        return 0

    candidate_winners = [
        item for item in winners
        if item[2] >= DEFEND_CROWD_WIN_BAG_MIN_WIN_PNL
        and item[2] >= max(DEFEND_CROWD_WIN_BAG_MIN_WIN_PNL, worst_loser_abs * 2.0)
    ]
    if not candidate_winners:
        return 0

    candidate_winners.sort(
        key=lambda item: (
            -item[2],    # strongest realized win first
            -item[4],    # older winners first
            item[5],     # lower confidence first
        )
    )

    ticket, pdata, pnl, volume, hold_sec, confidence = candidate_winners[0]
    if close_position(ticket, exit_reason="DEFEND_CROWD_WIN_BAG", exit_type="harvest"):
        mode = pdata.get('mode', 'REVERSION')
        symbol = pdata.get('symbol', '?')
        brain.record_exit(symbol, pnl, mode, hold_sec)
        brain.save()
        active_positions.pop(ticket, None)
        alleyway_state['defend_crowd_win_bag_cycles'] = 0
        alleyway_state['defend_crowd_win_bag_cooldown_until'] = (
            now + DEFEND_CROWD_WIN_BAG_COOLDOWN_SECONDS
        )
        arm_profit_capture_freeze(now)
        log(
            f"  WIN_BAG {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} reason=crowd_defend defend_fm={free_margin_ratio:.2f} "
            f"net=${total_pnl:+.2f} losers={losing_count} "
            f"blk_crowd={reversion_diag.get('blocked_crowding', 0)} cycles={crowd_cycles}"
        )
        return 1

    return 0

def defend_pinned_unwind_positions(brain, free_margin_ratio, reversion_diag):
    """
    Peel one small loser only after a small DEFEND book has been visibly pinned
    for a while. This is intentionally narrower than the failed light-harvest
    path: it acts only after sustained cleanup blocking and only on a near-flat
    loser the book can afford to shed.
    """
    active_count = len(active_positions)
    pinned_shape = (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and DEFEND_PINNED_UNWIND_MIN_POSITIONS <= active_count <= DEFEND_PINNED_UNWIND_MAX_POSITIONS
        and free_margin_ratio >= DEFEND_PINNED_UNWIND_MIN_FREE_MARGIN_RATIO
        and int(reversion_diag.get('opened', 0) or 0) == 0
        and int(reversion_diag.get('blocked_defend_cleanup', 0) or 0) >= DEFEND_PINNED_UNWIND_MIN_BLOCKED_CLEANUP
    )

    if pinned_shape:
        alleyway_state['defend_pinned_cycles'] = int(alleyway_state.get('defend_pinned_cycles', 0) or 0) + 1
    else:
        alleyway_state['defend_pinned_cycles'] = 0
        return 0

    now = time.time()
    if now < float(alleyway_state.get('defend_pinned_unwind_cooldown_until', 0.0) or 0.0):
        return 0

    pinned_cycles = int(alleyway_state.get('defend_pinned_cycles', 0) or 0)
    if pinned_cycles < DEFEND_PINNED_UNWIND_TRIGGER_CYCLES:
        return 0

    total_pnl = 0.0
    losers = []
    for ticket, pdata in active_positions.items():
        if pdata.get('adopted'):
            continue
        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        total_pnl += pnl
        if pnl >= 0:
            continue

        hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
        confidence = float(pdata.get('confidence', 0.0) or 0.0)
        losers.append((ticket, pdata, pnl, volume, hold_sec, confidence))

    if total_pnl < DEFEND_PINNED_UNWIND_MIN_NET_PNL:
        return 0

    eligible_losers = [
        item for item in losers
        if abs(item[2]) <= DEFEND_PINNED_UNWIND_MAX_LOSS
    ]
    if not eligible_losers:
        return 0

    eligible_losers.sort(
        key=lambda item: (
            abs(item[2]),  # peel the closest-to-flat loser first
            -item[4],      # older first
            item[5],       # lower confidence first
        )
    )

    ticket, pdata, pnl, volume, hold_sec, confidence = eligible_losers[0]
    if close_position(ticket, exit_reason="PINNED_UNWIND", exit_type="unwind"):
        mode = pdata.get('mode', 'REVERSION')
        symbol = pdata.get('symbol', '?')
        brain.record_exit(symbol, pnl, mode, hold_sec)
        brain.save()
        active_positions.pop(ticket, None)
        alleyway_state['defend_pinned_cycles'] = 0
        alleyway_state['defend_pinned_unwind_cooldown_until'] = (
            now + DEFEND_PINNED_UNWIND_COOLDOWN_SECONDS
        )
        log(
            f"  PINNED_UNWIND {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} defend_fm={free_margin_ratio:.2f} net=${total_pnl:+.2f} "
            f"blk_cleanup={reversion_diag.get('blocked_defend_cleanup', 0)} cycles={pinned_cycles}"
        )
        return 1

    return 0

def defend_crowd_unwind_positions(brain, free_margin_ratio, reversion_diag, mode_counts):
    """
    Peel one weak REVERSION loser when a small-to-mid DEFEND book stays crowded
    for a sustained period. This is deliberately narrower than a broad derisk:
    it only acts after repeated crowding vetoes with no fresh opens.
    """
    active_count = len(active_positions)
    reversion_count = int(mode_counts.get('REVERSION', 0) or 0)
    crowd_shape = (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and DEFEND_CROWD_UNWIND_MIN_POSITIONS <= active_count <= DEFEND_CROWD_UNWIND_MAX_POSITIONS
        and reversion_count >= DEFEND_CROWD_UNWIND_MIN_POSITIONS
        and free_margin_ratio >= DEFEND_CROWD_UNWIND_MIN_FREE_MARGIN_RATIO
        and int(reversion_diag.get('opened', 0) or 0) == 0
        and int(reversion_diag.get('blocked_crowding', 0) or 0) >= DEFEND_CROWD_UNWIND_MIN_BLOCKED_CROWD
    )

    if crowd_shape:
        alleyway_state['defend_crowd_unwind_cycles'] = int(alleyway_state.get('defend_crowd_unwind_cycles', 0) or 0) + 1
    else:
        alleyway_state['defend_crowd_unwind_cycles'] = 0
        return 0

    now = time.time()
    if now < float(alleyway_state.get('defend_crowd_unwind_cooldown_until', 0.0) or 0.0):
        return 0

    crowd_cycles = int(alleyway_state.get('defend_crowd_unwind_cycles', 0) or 0)
    if crowd_cycles < DEFEND_CROWD_UNWIND_TRIGGER_CYCLES:
        return 0

    total_pnl = 0.0
    losers = []
    for ticket, pdata in active_positions.items():
        if pdata.get('adopted') or pdata.get('mode') != 'REVERSION':
            continue
        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        total_pnl += pnl
        if pnl >= 0:
            continue

        hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
        confidence = float(pdata.get('confidence', 0.0) or 0.0)
        losers.append((ticket, pdata, pnl, volume, hold_sec, confidence))

    if total_pnl > DEFEND_CROWD_UNWIND_MAX_NET_PNL:
        return 0

    eligible_losers = [
        item for item in losers
        if abs(item[2]) <= DEFEND_CROWD_UNWIND_MAX_LOSS
    ]
    if not eligible_losers:
        return 0

    eligible_losers.sort(
        key=lambda item: (
            item[2],   # weakest loser first
            -item[4],  # older first
            item[5],   # lower confidence first
        )
    )

    ticket, pdata, pnl, volume, hold_sec, confidence = eligible_losers[0]
    if close_position(ticket, exit_reason="CROWD_UNWIND", exit_type="unwind"):
        mode = pdata.get('mode', 'REVERSION')
        symbol = pdata.get('symbol', '?')
        brain.record_exit(symbol, pnl, mode, hold_sec)
        brain.save()
        active_positions.pop(ticket, None)
        alleyway_state['defend_crowd_unwind_cycles'] = 0
        alleyway_state['defend_crowd_unwind_cooldown_until'] = (
            now + DEFEND_CROWD_UNWIND_COOLDOWN_SECONDS
        )
        log(
            f"  CROWD_UNWIND {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} defend_fm={free_margin_ratio:.2f} net=${total_pnl:+.2f} "
            f"blk_crowd={reversion_diag.get('blocked_crowding', 0)} cycles={crowd_cycles}"
        )
        return 1

    return 0

def defend_anchor_unwind_positions(brain, free_margin_ratio, reversion_diag, mode_counts):
    """
    Peel one non-REVERSION anchor loser when a loaded DEFEND book is already
    frozen and still weak. This is for the live shape where containment is
    working (`open=0`) but the book is not getting lighter on its own.
    """
    active_count = len(active_positions)
    non_reversion_count = sum(
        1
        for pdata in active_positions.values()
        if not pdata.get('adopted') and pdata.get('mode') != 'REVERSION'
    )
    anchor_shape = (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and active_count >= DEFEND_ANCHOR_UNWIND_MIN_POSITIONS
        and non_reversion_count >= DEFEND_ANCHOR_UNWIND_MIN_NON_REVERSION
        and DEFEND_ANCHOR_UNWIND_MIN_FREE_MARGIN_RATIO <= free_margin_ratio <= DEFEND_ANCHOR_UNWIND_MAX_FREE_MARGIN_RATIO
        and int(reversion_diag.get('opened', 0) or 0) == 0
        and int(reversion_diag.get('blocked_defend_mg', 0) or 0) >= DEFEND_ANCHOR_UNWIND_MIN_BLOCKED_DEFEND_MG
    )

    if anchor_shape:
        alleyway_state['defend_anchor_unwind_cycles'] = int(alleyway_state.get('defend_anchor_unwind_cycles', 0) or 0) + 1
    else:
        alleyway_state['defend_anchor_unwind_cycles'] = 0
        return 0

    now = time.time()
    if now < float(alleyway_state.get('defend_anchor_unwind_cooldown_until', 0.0) or 0.0):
        return 0

    anchor_cycles = int(alleyway_state.get('defend_anchor_unwind_cycles', 0) or 0)
    if anchor_cycles < DEFEND_ANCHOR_UNWIND_TRIGGER_CYCLES:
        return 0

    total_pnl = 0.0
    positive_carry = 0.0
    losers = []
    for ticket, pdata in active_positions.items():
        if pdata.get('adopted'):
            continue
        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        total_pnl += pnl
        if pnl > 0:
            positive_carry += pnl
            continue
        if pnl >= 0 or pdata.get('mode') == 'REVERSION':
            continue

        hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
        confidence = float(pdata.get('confidence', 0.0) or 0.0)
        losers.append((ticket, pdata, pnl, volume, hold_sec, confidence))

    if total_pnl > DEFEND_ANCHOR_UNWIND_MAX_NET_PNL:
        return 0
    if positive_carry < DEFEND_ANCHOR_UNWIND_MIN_POSITIVE_CARRY:
        return 0

    eligible_losers = [
        item for item in losers
        if abs(item[2]) <= DEFEND_ANCHOR_UNWIND_MAX_LOSS
    ]
    if not eligible_losers:
        return 0

    eligible_losers.sort(
        key=lambda item: (
            item[2],   # worst anchor first
            -item[4],  # older first
            item[5],   # lower confidence first
        )
    )

    ticket, pdata, pnl, volume, hold_sec, confidence = eligible_losers[0]
    if close_position(ticket, exit_reason="ANCHOR_UNWIND", exit_type="unwind"):
        mode = pdata.get('mode', 'MACHINE_GUN')
        symbol = pdata.get('symbol', '?')
        brain.record_exit(symbol, pnl, mode, hold_sec)
        brain.save()
        active_positions.pop(ticket, None)
        alleyway_state['defend_anchor_unwind_cycles'] = 0
        alleyway_state['defend_anchor_unwind_cooldown_until'] = (
            now + DEFEND_ANCHOR_UNWIND_COOLDOWN_SECONDS
        )
        log(
            f"  ANCHOR_UNWIND {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} defend_fm={free_margin_ratio:.2f} net=${total_pnl:+.2f} "
            f"carry=${positive_carry:+.2f} blk_defend_mg={reversion_diag.get('blocked_defend_mg', 0)} "
            f"cycles={anchor_cycles}"
        )
        return 1

    return 0

def defend_financed_unwind_positions(brain, free_margin_ratio, reversion_diag, mode_counts):
    """
    Peel one financed non-REVERSION loser from a loaded DEFEND book only after
    the no-add governor has visibly frozen the book and the remaining winners
    already leave the book net positive after the cleanup.
    """
    now = time.time()
    active_count = len(active_positions)
    non_reversion_count = sum(
        1
        for pdata in active_positions.values()
        if not pdata.get('adopted') and pdata.get('mode') != 'REVERSION'
    )
    blocked_defend_loaded = max(
        int(reversion_diag.get('blocked_defend_loaded', 0) or 0),
        int(alleyway_state.get('last_blocked_defend_loaded', 0) or 0),
    )
    if active_count >= DEFEND_LOADED_FINANCED_UNWIND_MIN_POSITIONS:
        financed_min_positions = DEFEND_LOADED_FINANCED_UNWIND_MIN_POSITIONS
        financed_max_positions = DEFEND_LOADED_FINANCED_UNWIND_MAX_POSITIONS
        financed_min_non_reversion = DEFEND_LOADED_FINANCED_UNWIND_MIN_NON_REVERSION
        financed_min_free_margin = DEFEND_LOADED_FINANCED_UNWIND_MIN_FREE_MARGIN_RATIO
        financed_min_net_pnl = DEFEND_LOADED_FINANCED_UNWIND_MIN_NET_PNL
        financed_min_positive_carry = DEFEND_LOADED_FINANCED_UNWIND_MIN_POSITIVE_CARRY
        financed_min_blocked_defend_loaded = DEFEND_LOADED_FINANCED_UNWIND_MIN_BLOCKED_DEFEND_LOADED
        financed_trigger_cycles = DEFEND_LOADED_FINANCED_UNWIND_TRIGGER_CYCLES
        financed_cooldown_seconds = DEFEND_LOADED_FINANCED_UNWIND_COOLDOWN_SECONDS
        financed_max_loss = DEFEND_LOADED_FINANCED_UNWIND_MAX_LOSS
        financed_min_remaining_net = DEFEND_LOADED_FINANCED_UNWIND_MIN_REMAINING_NET
        financed_carry_cover_ratio = DEFEND_LOADED_FINANCED_UNWIND_CARRY_COVER_RATIO
    else:
        financed_min_positions = DEFEND_FINANCED_UNWIND_MIN_POSITIONS
        financed_max_positions = DEFEND_FINANCED_UNWIND_MAX_POSITIONS
        financed_min_non_reversion = DEFEND_FINANCED_UNWIND_MIN_NON_REVERSION
        financed_min_free_margin = DEFEND_FINANCED_UNWIND_MIN_FREE_MARGIN_RATIO
        financed_min_net_pnl = DEFEND_FINANCED_UNWIND_MIN_NET_PNL
        financed_min_positive_carry = DEFEND_FINANCED_UNWIND_MIN_POSITIVE_CARRY
        financed_min_blocked_defend_loaded = DEFEND_FINANCED_UNWIND_MIN_BLOCKED_DEFEND_LOADED
        financed_trigger_cycles = DEFEND_FINANCED_UNWIND_TRIGGER_CYCLES
        financed_cooldown_seconds = DEFEND_FINANCED_UNWIND_COOLDOWN_SECONDS
        financed_max_loss = DEFEND_FINANCED_UNWIND_MAX_LOSS
        financed_min_remaining_net = DEFEND_FINANCED_UNWIND_MIN_REMAINING_NET
        financed_carry_cover_ratio = DEFEND_FINANCED_UNWIND_CARRY_COVER_RATIO
    shape_diag_active = (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and active_count >= max(4, financed_min_positions - 2)
    )

    def log_financed_shape_diag(reason, extra=""):
        last_reason = alleyway_state.get('defend_financed_unwind_last_shape_reason')
        last_logged_at = float(alleyway_state.get('defend_financed_unwind_last_shape_logged_at', 0.0) or 0.0)
        if (
            reason == last_reason
            and (now - last_logged_at) < 10.0
        ):
            return
        alleyway_state['defend_financed_unwind_last_shape_reason'] = reason
        alleyway_state['defend_financed_unwind_last_shape_logged_at'] = now
        log(
            f"  FINANCED_UNWIND_SHAPE reason={reason} "
            f"active={active_count} nonrev={non_reversion_count} "
            f"blk_defend_loaded={blocked_defend_loaded} "
            f"defend_fm={free_margin_ratio:.2f} {extra}".rstrip()
        )

    financed_shape = (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and financed_min_positions <= active_count <= financed_max_positions
        and non_reversion_count >= financed_min_non_reversion
        and free_margin_ratio >= financed_min_free_margin
        and int(reversion_diag.get('opened', 0) or 0) == 0
        and blocked_defend_loaded >= financed_min_blocked_defend_loaded
    )

    if financed_shape:
        alleyway_state['defend_financed_unwind_cycles'] = int(
            alleyway_state.get('defend_financed_unwind_cycles', 0) or 0
        ) + 1
    else:
        if shape_diag_active:
            if active_count < financed_min_positions:
                log_financed_shape_diag(
                    'too_few_positions',
                    f"need_active={financed_min_positions}",
                )
            elif active_count > financed_max_positions:
                log_financed_shape_diag(
                    'too_many_positions',
                    f"max_active={financed_max_positions}",
                )
            elif non_reversion_count < financed_min_non_reversion:
                log_financed_shape_diag(
                    'too_few_nonreversion',
                    f"need_nonrev={financed_min_non_reversion}",
                )
            elif free_margin_ratio < financed_min_free_margin:
                log_financed_shape_diag(
                    'free_margin_too_low',
                    f"min_fm={financed_min_free_margin:.2f}",
                )
            elif int(reversion_diag.get('opened', 0) or 0) > 0:
                log_financed_shape_diag(
                    'opened_this_cycle',
                    f"opened={int(reversion_diag.get('opened', 0) or 0)}",
                )
            elif blocked_defend_loaded < financed_min_blocked_defend_loaded:
                log_financed_shape_diag(
                    'not_frozen_long_enough',
                    f"need_blk={financed_min_blocked_defend_loaded}",
                )
        alleyway_state['defend_financed_unwind_cycles'] = 0
        return 0

    financed_cycles = int(alleyway_state.get('defend_financed_unwind_cycles', 0) or 0)

    def log_financed_diag(reason, extra=""):
        last_reason = alleyway_state.get('defend_financed_unwind_last_diag_reason')
        if (
            reason == last_reason
            and financed_cycles % DEFEND_FINANCED_UNWIND_DIAG_EVERY_CYCLES != 0
        ):
            return
        alleyway_state['defend_financed_unwind_last_diag_reason'] = reason
        log(
            f"  FINANCED_UNWIND_DIAG reason={reason} "
            f"active={active_count} nonrev={non_reversion_count} "
            f"blk_defend_loaded={blocked_defend_loaded} "
            f"cycles={financed_cycles} defend_fm={free_margin_ratio:.2f} "
            f"{extra}".rstrip()
        )

    if now < float(alleyway_state.get('defend_financed_unwind_cooldown_until', 0.0) or 0.0):
        cooldown_until = float(alleyway_state.get('defend_financed_unwind_cooldown_until', 0.0) or 0.0)
        log_financed_diag(
            'cooldown',
            f"remain={max(0, int(cooldown_until - now))}s",
        )
        return 0

    if financed_cycles < financed_trigger_cycles:
        log_financed_diag(
            'arming',
            f"need={financed_trigger_cycles} net=pending carry=pending",
        )
        return 0

    total_pnl = 0.0
    positive_carry = 0.0
    losers = []
    for ticket, pdata in active_positions.items():
        if pdata.get('adopted'):
            continue

        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        total_pnl += pnl
        if pnl > 0:
            positive_carry += pnl
            continue
        if pnl >= 0 or pdata.get('mode') == 'REVERSION':
            continue

        hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
        confidence = float(pdata.get('confidence', 0.0) or 0.0)
        losers.append((ticket, pdata, pnl, volume, hold_sec, confidence))

    if total_pnl < financed_min_net_pnl:
        log_financed_diag(
            'net_below_min',
            f"net=${total_pnl:+.2f} min_net=${financed_min_net_pnl:.2f} "
            f"carry=${positive_carry:+.2f} losers={len(losers)}",
        )
        return 0
    if positive_carry < financed_min_positive_carry:
        log_financed_diag(
            'carry_below_min',
            f"net=${total_pnl:+.2f} carry=${positive_carry:+.2f} "
            f"min_carry=${financed_min_positive_carry:.2f} losers={len(losers)}",
        )
        return 0

    eligible_losers = [
        item for item in losers
        if abs(item[2]) <= financed_max_loss
        and positive_carry >= abs(item[2]) * financed_carry_cover_ratio
        and (total_pnl - item[2]) >= financed_min_remaining_net
    ]
    if not eligible_losers:
        worst_loser = min((item[2] for item in losers), default=0.0)
        projected_remaining = (
            total_pnl - worst_loser if losers else total_pnl
        )
        log_financed_diag(
            'no_eligible_loser',
            f"net=${total_pnl:+.2f} carry=${positive_carry:+.2f} "
            f"worst=${worst_loser:+.2f} remain_if_closed=${projected_remaining:+.2f} "
            f"max_loss=${financed_max_loss:.2f} cover={financed_carry_cover_ratio:.2f}",
        )
        return 0

    eligible_losers.sort(
        key=lambda item: (
            item[2],   # peel the biggest remaining loser first
            -item[4],  # older first
            item[5],   # lower confidence first
        )
    )

    ticket, pdata, pnl, volume, hold_sec, confidence = eligible_losers[0]
    if close_position(ticket, exit_reason="DEFEND_FINANCED_UNWIND", exit_type="unwind"):
        mode = pdata.get('mode', 'MACHINE_GUN')
        symbol = pdata.get('symbol', '?')
        brain.record_exit(symbol, pnl, mode, hold_sec)
        brain.save()
        active_positions.pop(ticket, None)
        alleyway_state['defend_financed_unwind_cycles'] = 0
        alleyway_state['defend_financed_unwind_last_diag_reason'] = ''
        alleyway_state['defend_financed_unwind_cooldown_until'] = (
            now + financed_cooldown_seconds
        )
        log(
            f"  FINANCED_UNWIND {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} defend_fm={free_margin_ratio:.2f} net=${total_pnl:+.2f} "
            f"remaining_net=${(total_pnl - pnl):+.2f} carry=${positive_carry:+.2f} "
            f"blk_defend_loaded={blocked_defend_loaded} cycles={financed_cycles}"
        )
        return 1

    return 0

def defend_small_book_unwind_positions(brain, free_margin_ratio, reversion_diag, mode_counts):
    """
    Peel one smallest non-REVERSION loser in a frozen 4-position DEFEND book,
    including the live mixed shape with a couple of REVERSION carriers around
    one heavy anchor. It still needs real carry and a real anchor loss; this
    only fixes the cleanup lane so it sees the book that containment froze.
    """
    active_count = len(active_positions)
    non_reversion_count = sum(
        1
        for pdata in active_positions.values()
        if not pdata.get('adopted') and pdata.get('mode') != 'REVERSION'
    )
    reversion_count = sum(
        1
        for pdata in active_positions.values()
        if not pdata.get('adopted') and pdata.get('mode') == 'REVERSION'
    )
    frozen_cleanup_cycles = defend_frozen_cleanup_cycles(reversion_diag)
    small_book_shape = (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and DEFEND_SMALL_BOOK_UNWIND_MIN_POSITIONS <= active_count <= DEFEND_SMALL_BOOK_UNWIND_MAX_POSITIONS
        and non_reversion_count >= DEFEND_SMALL_BOOK_UNWIND_MIN_NON_REVERSION
        and reversion_count >= DEFEND_SMALL_BOOK_UNWIND_MIN_REVERSION
        and free_margin_ratio >= DEFEND_SMALL_BOOK_UNWIND_MIN_FREE_MARGIN_RATIO
        and int(reversion_diag.get('opened', 0) or 0) == 0
        and (
            frozen_cleanup_cycles >= DEFEND_SMALL_BOOK_UNWIND_MIN_BLOCKED_DEFEND_LOADED
            or int(reversion_diag.get('blocked_defend_mg', 0) or 0) >= DEFEND_SMALL_BOOK_UNWIND_MIN_BLOCKED_DEFEND_MG
        )
    )

    if small_book_shape:
        alleyway_state['defend_small_book_unwind_cycles'] = int(
            alleyway_state.get('defend_small_book_unwind_cycles', 0) or 0
        ) + 1
    else:
        alleyway_state['defend_small_book_unwind_cycles'] = 0
        return 0

    now = time.time()
    if now < float(alleyway_state.get('defend_small_book_unwind_cooldown_until', 0.0) or 0.0):
        return 0

    small_book_cycles = int(alleyway_state.get('defend_small_book_unwind_cycles', 0) or 0)
    if small_book_cycles < DEFEND_SMALL_BOOK_UNWIND_TRIGGER_CYCLES:
        return 0

    total_pnl = 0.0
    positive_carry = 0.0
    largest_anchor_loss = 0.0
    losers = []
    for ticket, pdata in active_positions.items():
        if pdata.get('adopted'):
            continue

        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        total_pnl += pnl
        if pnl > 0:
            positive_carry += pnl
            continue
        if pnl >= 0 or pdata.get('mode') == 'REVERSION':
            continue

        largest_anchor_loss = max(largest_anchor_loss, abs(pnl))
        hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
        confidence = float(pdata.get('confidence', 0.0) or 0.0)
        losers.append((ticket, pdata, pnl, volume, hold_sec, confidence))

    if positive_carry < DEFEND_SMALL_BOOK_UNWIND_MIN_POSITIVE_CARRY:
        return 0
    if largest_anchor_loss < DEFEND_SMALL_BOOK_UNWIND_MIN_ANCHOR_LOSS:
        return 0

    eligible_losers = [
        item for item in losers
        if abs(item[2]) <= DEFEND_SMALL_BOOK_UNWIND_MAX_LOSS
        and positive_carry >= abs(item[2]) * DEFEND_SMALL_BOOK_UNWIND_CARRY_COVER_RATIO
    ]
    if not eligible_losers:
        return 0

    eligible_losers.sort(
        key=lambda item: (
            abs(item[2]),  # peel the smallest drag first
            -item[4],      # older first
            item[5],       # lower confidence first
        )
    )

    ticket, pdata, pnl, volume, hold_sec, confidence = eligible_losers[0]
    if close_position(ticket, exit_reason="DEFEND_SMALL_BOOK_UNWIND", exit_type="unwind"):
        mode = pdata.get('mode', 'MACHINE_GUN')
        symbol = pdata.get('symbol', '?')
        brain.record_exit(symbol, pnl, mode, hold_sec)
        brain.save()
        active_positions.pop(ticket, None)
        alleyway_state['defend_small_book_unwind_cycles'] = 0
        alleyway_state['defend_small_book_unwind_cooldown_until'] = (
            now + DEFEND_SMALL_BOOK_UNWIND_COOLDOWN_SECONDS
        )
        log(
            f"  SMALL_BOOK_UNWIND {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} defend_fm={free_margin_ratio:.2f} net=${total_pnl:+.2f} "
            f"carry=${positive_carry:+.2f} anchor=${largest_anchor_loss:.2f} "
            f"freeze_cycles={frozen_cleanup_cycles} blk_defend_loaded={reversion_diag.get('blocked_defend_loaded', 0)} "
            f"blk_defend_mg={reversion_diag.get('blocked_defend_mg', 0)} cycles={small_book_cycles}"
        )
        return 1

    return 0

def defend_same_symbol_cluster_cleanup_positions(brain, free_margin_ratio, reversion_diag, mode_counts):
    """
    Peel one tiny loser from a frozen same-symbol 4-book DEFEND cluster.

    This is intentionally narrower than the mixed small-book unwind. It exists
    for the live endgame where a single-symbol SHOTGUN cluster sits safely in
    DEFEND with strong margin, no winners, and no qualifying mixed-book carry.
    """
    active_count = len(active_positions)
    blocked_defend_loaded = defend_frozen_cleanup_cycles(reversion_diag)
    same_symbol_shape = (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and DEFEND_SAME_SYMBOL_CLEANUP_MIN_POSITIONS <= active_count <= DEFEND_SAME_SYMBOL_CLEANUP_MAX_POSITIONS
        and free_margin_ratio >= DEFEND_SAME_SYMBOL_CLEANUP_MIN_FREE_MARGIN_RATIO
        and int(reversion_diag.get('opened', 0) or 0) == 0
        and blocked_defend_loaded >= DEFEND_SAME_SYMBOL_CLEANUP_MIN_BLOCKED_DEFEND_LOADED
    )

    if same_symbol_shape:
        alleyway_state['defend_same_symbol_cleanup_cycles'] = int(
            alleyway_state.get('defend_same_symbol_cleanup_cycles', 0) or 0
        ) + 1
    else:
        alleyway_state['defend_same_symbol_cleanup_cycles'] = 0
        return 0

    now = time.time()
    if now < float(alleyway_state.get('defend_same_symbol_cleanup_cooldown_until', 0.0) or 0.0):
        return 0

    cleanup_cycles = int(alleyway_state.get('defend_same_symbol_cleanup_cycles', 0) or 0)
    if cleanup_cycles in {1, 6, DEFEND_SAME_SYMBOL_CLEANUP_TRIGGER_CYCLES}:
        log(
            f"  SAME_SYMBOL_CLEANUP_ARM cycles={cleanup_cycles} active={active_count} "
            f"blk_defend_loaded={blocked_defend_loaded} defend_fm={free_margin_ratio:.2f}"
        )
    if cleanup_cycles < DEFEND_SAME_SYMBOL_CLEANUP_TRIGGER_CYCLES:
        return 0

    total_pnl = 0.0
    cluster_symbol = None
    eligible_losers = []
    diag_reason = None

    for ticket, pdata in active_positions.items():
        if pdata.get('adopted') or pdata.get('mode') == 'REVERSION':
            diag_reason = "shape_drifted"
            return 0

        symbol = str(pdata.get('symbol', '') or '').upper()
        if not symbol:
            diag_reason = "missing_symbol"
            return 0
        if cluster_symbol is None:
            cluster_symbol = symbol
        elif symbol != cluster_symbol:
            diag_reason = "mixed_symbol_cluster"
            return 0

        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        total_pnl += pnl
        if pnl > 0:
            diag_reason = "has_green_leg"
            break
        if pnl < 0 and abs(pnl) <= DEFEND_SAME_SYMBOL_CLEANUP_MAX_SINGLE_LOSS:
            hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
            confidence = float(pdata.get('confidence', 0.0) or 0.0)
            eligible_losers.append((ticket, pdata, pnl, volume, hold_sec, confidence))

    if cluster_symbol is None:
        diag_reason = diag_reason or "no_cluster_symbol"
        return 0
    if diag_reason is None and total_pnl < -DEFEND_SAME_SYMBOL_CLEANUP_MAX_TOTAL_LOSS:
        diag_reason = "net_too_red"
    if diag_reason is None and len(eligible_losers) != active_count:
        diag_reason = "ineligible_leg_present"
    if diag_reason is not None:
        log(
            f"  SAME_SYMBOL_CLEANUP_DIAG reason={diag_reason} "
            f"symbol={cluster_symbol} cycles={cleanup_cycles} active={active_count} "
            f"eligible={len(eligible_losers)} net=${total_pnl:+.2f} "
            f"blk_defend_loaded={blocked_defend_loaded} defend_fm={free_margin_ratio:.2f}"
        )
        return 0

    eligible_losers.sort(
        key=lambda item: (
            abs(item[2]),  # realize the smallest drag first
            -item[4],      # older first
            item[5],       # lower confidence first
        )
    )

    ticket, pdata, pnl, volume, hold_sec, confidence = eligible_losers[0]
    if close_position(ticket, exit_reason="DEFEND_SAME_SYMBOL_CLEANUP", exit_type="cleanup"):
        mode = pdata.get('mode', 'SHOTGUN')
        symbol = pdata.get('symbol', '?')
        brain.record_exit(symbol, pnl, mode, hold_sec, failure_reason="SAME_SYMBOL_CLUSTER_CLEANUP")
        brain.save()
        active_positions.pop(ticket, None)
        alleyway_state['defend_same_symbol_cleanup_cycles'] = 0
        alleyway_state['defend_same_symbol_cleanup_cooldown_until'] = (
            now + DEFEND_SAME_SYMBOL_CLEANUP_COOLDOWN_SECONDS
        )
        arm_profit_capture_freeze(now)
        freeze_family, freeze_symbol_seconds, freeze_family_seconds = arm_sync_close_reentry_freeze(symbol, now)
        freeze_bits = [f"freeze={freeze_symbol_seconds}s"]
        if freeze_family and freeze_family_seconds > 0:
            freeze_bits.append(f"family={freeze_family}:{freeze_family_seconds}s")
        log(
            f"  SAME_SYMBOL_CLEANUP {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} defend_fm={free_margin_ratio:.2f} net=${total_pnl:+.2f} "
            f"blk_defend_loaded={blocked_defend_loaded} cycles={cleanup_cycles} "
            f"{' '.join(freeze_bits)}"
        )
        return 1

    return 0

def defend_three_book_same_symbol_cleanup_positions(brain, free_margin_ratio, reversion_diag, mode_counts):
    """
    Peel one tiny loser from a frozen same-symbol 3-book DEFEND cluster.

    This is the follow-on endgame after SAME_SYMBOL_CLEANUP proves out on a
    4-book. Keep it narrow so it only handles the dead-end case of a tiny
    same-symbol SHOTGUN cluster with strong margin and bounded losses.
    """
    active_count = len(active_positions)
    blocked_defend_loaded = defend_frozen_cleanup_cycles(reversion_diag)
    same_symbol_shape = (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and DEFEND_THREE_BOOK_SAME_SYMBOL_CLEANUP_MIN_POSITIONS <= active_count <= DEFEND_THREE_BOOK_SAME_SYMBOL_CLEANUP_MAX_POSITIONS
        and free_margin_ratio >= DEFEND_THREE_BOOK_SAME_SYMBOL_CLEANUP_MIN_FREE_MARGIN_RATIO
        and int(reversion_diag.get('opened', 0) or 0) == 0
        and blocked_defend_loaded >= DEFEND_THREE_BOOK_SAME_SYMBOL_CLEANUP_MIN_BLOCKED_DEFEND_LOADED
    )

    if same_symbol_shape:
        alleyway_state['defend_three_book_same_symbol_cleanup_cycles'] = int(
            alleyway_state.get('defend_three_book_same_symbol_cleanup_cycles', 0) or 0
        ) + 1
    else:
        alleyway_state['defend_three_book_same_symbol_cleanup_cycles'] = 0
        return 0

    now = time.time()
    if now < float(alleyway_state.get('defend_three_book_same_symbol_cleanup_cooldown_until', 0.0) or 0.0):
        return 0

    cleanup_cycles = int(alleyway_state.get('defend_three_book_same_symbol_cleanup_cycles', 0) or 0)
    if cleanup_cycles < DEFEND_THREE_BOOK_SAME_SYMBOL_CLEANUP_TRIGGER_CYCLES:
        return 0

    total_pnl = 0.0
    cluster_symbol = None
    eligible_losers = []

    for ticket, pdata in active_positions.items():
        if pdata.get('adopted') or pdata.get('mode') == 'REVERSION':
            return 0

        symbol = str(pdata.get('symbol', '') or '').upper()
        if not symbol:
            return 0
        if cluster_symbol is None:
            cluster_symbol = symbol
        elif symbol != cluster_symbol:
            return 0

        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        total_pnl += pnl
        if pnl > 0:
            return 0
        if pnl < 0 and abs(pnl) <= DEFEND_THREE_BOOK_SAME_SYMBOL_CLEANUP_MAX_SINGLE_LOSS:
            hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
            confidence = float(pdata.get('confidence', 0.0) or 0.0)
            eligible_losers.append((ticket, pdata, pnl, volume, hold_sec, confidence))

    if cluster_symbol is None:
        return 0
    if total_pnl < -DEFEND_THREE_BOOK_SAME_SYMBOL_CLEANUP_MAX_TOTAL_LOSS:
        return 0
    if len(eligible_losers) != active_count:
        return 0

    eligible_losers.sort(
        key=lambda item: (
            abs(item[2]),
            -item[4],
            item[5],
        )
    )

    ticket, pdata, pnl, volume, hold_sec, confidence = eligible_losers[0]
    if close_position(ticket, exit_reason="DEFEND_THREE_BOOK_SAME_SYMBOL_CLEANUP", exit_type="cleanup"):
        mode = pdata.get('mode', 'SHOTGUN')
        symbol = pdata.get('symbol', '?')
        brain.record_exit(symbol, pnl, mode, hold_sec, failure_reason="THREE_BOOK_SAME_SYMBOL_CLEANUP")
        brain.save()
        active_positions.pop(ticket, None)
        alleyway_state['defend_three_book_same_symbol_cleanup_cycles'] = 0
        alleyway_state['defend_three_book_same_symbol_cleanup_cooldown_until'] = (
            now + DEFEND_THREE_BOOK_SAME_SYMBOL_CLEANUP_COOLDOWN_SECONDS
        )
        arm_profit_capture_freeze(now)
        freeze_family, freeze_symbol_seconds, freeze_family_seconds = arm_sync_close_reentry_freeze(symbol, now)
        freeze_bits = [f"freeze={freeze_symbol_seconds}s"]
        if freeze_family and freeze_family_seconds > 0:
            freeze_bits.append(f"family={freeze_family}:{freeze_family_seconds}s")
        log(
            f"  THREE_BOOK_SAME_SYMBOL_CLEANUP {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} defend_fm={free_margin_ratio:.2f} net=${total_pnl:+.2f} "
            f"blk_defend_loaded={blocked_defend_loaded} cycles={cleanup_cycles} "
            f"{' '.join(freeze_bits)}"
        )
        return 1

    return 0

def defend_two_book_same_symbol_cleanup_positions(brain, free_margin_ratio, mode_counts):
    """
    Peel one tiny loser from a near-flat same-symbol 2-book DEFEND cluster.

    This covers the final dead-end after the 3-book cleanup succeeds: both
    remaining legs are same-symbol, non-REVERSION, very small, and too flat
    to qualify for the winner-based two-book harvest lane.
    """
    active_count = len(active_positions)
    idle_cycles = int(alleyway_state.get('cycles_without_trade', 0) or 0)
    same_symbol_shape = (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and DEFEND_TWO_BOOK_SAME_SYMBOL_CLEANUP_MIN_POSITIONS <= active_count <= DEFEND_TWO_BOOK_SAME_SYMBOL_CLEANUP_MAX_POSITIONS
        and free_margin_ratio >= DEFEND_TWO_BOOK_SAME_SYMBOL_CLEANUP_MIN_FREE_MARGIN_RATIO
        and idle_cycles >= DEFEND_TWO_BOOK_SAME_SYMBOL_CLEANUP_MIN_IDLE_CYCLES
        and int(mode_counts.get('REVERSION', 0) or 0) == 0
    )

    if same_symbol_shape:
        alleyway_state['defend_two_book_same_symbol_cleanup_cycles'] = int(
            alleyway_state.get('defend_two_book_same_symbol_cleanup_cycles', 0) or 0
        ) + 1
    else:
        alleyway_state['defend_two_book_same_symbol_cleanup_cycles'] = 0
        return 0

    now = time.time()
    if now < float(alleyway_state.get('defend_two_book_same_symbol_cleanup_cooldown_until', 0.0) or 0.0):
        return 0

    cleanup_cycles = int(alleyway_state.get('defend_two_book_same_symbol_cleanup_cycles', 0) or 0)
    if cleanup_cycles < DEFEND_TWO_BOOK_SAME_SYMBOL_CLEANUP_TRIGGER_CYCLES:
        return 0

    total_pnl = 0.0
    cluster_symbol = None
    eligible_losers = []

    for ticket, pdata in active_positions.items():
        if pdata.get('adopted') or pdata.get('mode') == 'REVERSION':
            alleyway_state['defend_two_book_same_symbol_cleanup_cycles'] = 0
            return 0

        symbol = pdata.get('symbol', '?')
        if cluster_symbol is None:
            cluster_symbol = symbol
        elif symbol != cluster_symbol:
            alleyway_state['defend_two_book_same_symbol_cleanup_cycles'] = 0
            return 0

        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        total_pnl += pnl
        if pnl < 0:
            hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
            confidence = float(pdata.get('confidence', 0.0) or 0.0)
            eligible_losers.append((ticket, pdata, pnl, volume, hold_sec, confidence))

    if cluster_symbol is None or len(eligible_losers) == 0:
        alleyway_state['defend_two_book_same_symbol_cleanup_cycles'] = 0
        return 0
    if abs(total_pnl) > DEFEND_TWO_BOOK_SAME_SYMBOL_CLEANUP_MAX_TOTAL_LOSS:
        return 0

    eligible_losers = [
        item for item in eligible_losers
        if abs(item[2]) <= DEFEND_TWO_BOOK_SAME_SYMBOL_CLEANUP_MAX_SINGLE_LOSS
    ]
    if not eligible_losers:
        return 0

    eligible_losers.sort(
        key=lambda item: (
            abs(item[2]),
            -item[4],
            item[5],
        )
    )

    ticket, pdata, pnl, volume, hold_sec, confidence = eligible_losers[0]
    if close_position(ticket, exit_reason="DEFEND_TWO_BOOK_SAME_SYMBOL_CLEANUP", exit_type="cleanup"):
        mode = pdata.get('mode', 'SHOTGUN')
        symbol = pdata.get('symbol', '?')
        brain.record_exit(symbol, pnl, mode, hold_sec, failure_reason="TWO_BOOK_SAME_SYMBOL_CLEANUP")
        brain.save()
        active_positions.pop(ticket, None)
        alleyway_state['defend_two_book_same_symbol_cleanup_cycles'] = 0
        alleyway_state['defend_two_book_same_symbol_cleanup_cooldown_until'] = (
            now + DEFEND_TWO_BOOK_SAME_SYMBOL_CLEANUP_COOLDOWN_SECONDS
        )
        arm_profit_capture_freeze(now)
        freeze_family, freeze_symbol_seconds, freeze_family_seconds = arm_sync_close_reentry_freeze(symbol, now)
        freeze_bits = [f"freeze={freeze_symbol_seconds}s"]
        if freeze_family and freeze_family_seconds > 0:
            freeze_bits.append(f"family={freeze_family}:{freeze_family_seconds}s")
        log(
            f"  TWO_BOOK_SAME_SYMBOL_CLEANUP {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} defend_fm={free_margin_ratio:.2f} net=${total_pnl:+.2f} "
            f"idle={idle_cycles} cycles={cleanup_cycles} {' '.join(freeze_bits)}"
        )
        return 1

    return 0

def defend_two_book_mixed_cleanup_positions(brain, free_margin_ratio, reversion_diag, mode_counts):
    """
    Peel the smaller loser from a frozen mixed-symbol 2-book DEFEND endgame.

    This is intentionally narrower than the two-book win-bag helper. It exists
    only for the live dead-end where both remaining non-REVERSION legs are red,
    margin is healthy, containment has already frozen the pair, and there is no
    winner leg for the harvest path to track.
    """
    active_count = len(active_positions)
    idle_cycles = int(alleyway_state.get('cycles_without_trade', 0) or 0)
    blocked_defend_loaded = defend_frozen_cleanup_cycles(reversion_diag)
    mixed_shape = (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and DEFEND_TWO_BOOK_MIXED_CLEANUP_MIN_POSITIONS <= active_count <= DEFEND_TWO_BOOK_MIXED_CLEANUP_MAX_POSITIONS
        and free_margin_ratio >= DEFEND_TWO_BOOK_MIXED_CLEANUP_MIN_FREE_MARGIN_RATIO
        and idle_cycles >= DEFEND_TWO_BOOK_MIXED_CLEANUP_MIN_IDLE_CYCLES
        and blocked_defend_loaded >= DEFEND_TWO_BOOK_MIXED_CLEANUP_MIN_BLOCKED_DEFEND_LOADED
        and int(reversion_diag.get('opened', 0) or 0) == 0
        and int(mode_counts.get('REVERSION', 0) or 0) == 0
    )

    if mixed_shape:
        alleyway_state['defend_two_book_mixed_cleanup_cycles'] = int(
            alleyway_state.get('defend_two_book_mixed_cleanup_cycles', 0) or 0
        ) + 1
    else:
        alleyway_state['defend_two_book_mixed_cleanup_cycles'] = 0
        return 0

    now = time.time()
    if now < float(alleyway_state.get('defend_two_book_mixed_cleanup_cooldown_until', 0.0) or 0.0):
        return 0

    cleanup_cycles = int(alleyway_state.get('defend_two_book_mixed_cleanup_cycles', 0) or 0)
    if cleanup_cycles < DEFEND_TWO_BOOK_MIXED_CLEANUP_TRIGGER_CYCLES:
        return 0

    total_pnl = 0.0
    seen_symbols = set()
    eligible_losers = []

    for ticket, pdata in active_positions.items():
        if pdata.get('adopted') or pdata.get('mode') == 'REVERSION':
            alleyway_state['defend_two_book_mixed_cleanup_cycles'] = 0
            return 0

        symbol = str(pdata.get('symbol', '') or '').upper()
        if not symbol:
            alleyway_state['defend_two_book_mixed_cleanup_cycles'] = 0
            return 0
        seen_symbols.add(symbol)

        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        total_pnl += pnl
        if pnl < 0 and abs(pnl) <= DEFEND_TWO_BOOK_MIXED_CLEANUP_MAX_SINGLE_LOSS:
            hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
            confidence = float(pdata.get('confidence', 0.0) or 0.0)
            eligible_losers.append((ticket, pdata, pnl, volume, hold_sec, confidence))

    if len(seen_symbols) != active_count:
        alleyway_state['defend_two_book_mixed_cleanup_cycles'] = 0
        return 0
    if abs(total_pnl) > DEFEND_TWO_BOOK_MIXED_CLEANUP_MAX_TOTAL_LOSS:
        return 0
    if len(eligible_losers) != active_count:
        alleyway_state['defend_two_book_mixed_cleanup_cycles'] = 0
        return 0

    eligible_losers.sort(
        key=lambda item: (
            abs(item[2]),  # realize the smaller drag first
            -item[4],
            item[5],
        )
    )

    ticket, pdata, pnl, volume, hold_sec, confidence = eligible_losers[0]
    if close_position(ticket, exit_reason="DEFEND_TWO_BOOK_MIXED_CLEANUP", exit_type="cleanup"):
        mode = pdata.get('mode', 'SHOTGUN')
        symbol = pdata.get('symbol', '?')
        brain.record_exit(symbol, pnl, mode, hold_sec, failure_reason="TWO_BOOK_MIXED_CLEANUP")
        brain.save()
        active_positions.pop(ticket, None)
        alleyway_state['defend_two_book_mixed_cleanup_cycles'] = 0
        alleyway_state['defend_two_book_mixed_cleanup_cooldown_until'] = (
            now + DEFEND_TWO_BOOK_MIXED_CLEANUP_COOLDOWN_SECONDS
        )
        arm_profit_capture_freeze(now)
        freeze_family, freeze_symbol_seconds, freeze_family_seconds = arm_sync_close_reentry_freeze(symbol, now)
        freeze_bits = [f"freeze={freeze_symbol_seconds}s"]
        if freeze_family and freeze_family_seconds > 0:
            freeze_bits.append(f"family={freeze_family}:{freeze_family_seconds}s")
        log(
            f"  TWO_BOOK_MIXED_CLEANUP {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} defend_fm={free_margin_ratio:.2f} net=${total_pnl:+.2f} "
            f"idle={idle_cycles} blk_defend_loaded={blocked_defend_loaded} cycles={cleanup_cycles} "
            f"{' '.join(freeze_bits)}"
        )
        return 1

    return 0

def defend_one_pos_exotic_mercy_exit_positions(brain, free_margin_ratio, reversion_diag):
    """
    Retire a stranded lone exotic loser after cleanup has clearly finished.

    This is intentionally not a generic one-position stop-out. It exists only
    for the late competition endgame where containment has already reduced the
    book to one exotic non-REVERSION survivor and the bot is repeatedly proving
    it is blocked from rebuilding around it.
    """
    active_count = len(active_positions)
    idle_cycles = int(alleyway_state.get('cycles_without_trade', 0) or 0)
    rearm_reason = str(alleyway_state.get('rearm_reason', '') or '')
    if active_count != 1:
        alleyway_state['defend_one_pos_exotic_mercy_cycles'] = 0
        return 0

    ticket, pdata = next(iter(active_positions.items()))
    if pdata.get('adopted') or pdata.get('mode') == 'REVERSION':
        alleyway_state['defend_one_pos_exotic_mercy_cycles'] = 0
        return 0

    symbol = str(pdata.get('symbol', '') or '').upper()
    mercy_shape = (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and rearm_reason.startswith('one-pos-contained')
        and count_direct_positions() == 1
        and bool(symbol)
        and is_exotic(symbol)
        and free_margin_ratio >= DEFEND_ONE_POS_EXOTIC_MERCY_MIN_FREE_MARGIN_RATIO
        and idle_cycles >= DEFEND_ONE_POS_EXOTIC_MERCY_MIN_IDLE_CYCLES
        and int(reversion_diag.get('opened', 0) or 0) == 0
    )

    if mercy_shape:
        alleyway_state['defend_one_pos_exotic_mercy_cycles'] = int(
            alleyway_state.get('defend_one_pos_exotic_mercy_cycles', 0) or 0
        ) + 1
    else:
        alleyway_state['defend_one_pos_exotic_mercy_cycles'] = 0
        return 0

    now = time.time()
    if now < float(alleyway_state.get('defend_one_pos_exotic_mercy_cooldown_until', 0.0) or 0.0):
        return 0

    mercy_cycles = int(alleyway_state.get('defend_one_pos_exotic_mercy_cycles', 0) or 0)
    if mercy_cycles in {1, DEFEND_ONE_POS_EXOTIC_MERCY_TRIGGER_CYCLES}:
        log(
            f"  ONE_POS_EXOTIC_MERCY_ARM cycles={mercy_cycles} active={active_count} "
            f"idle={idle_cycles} reason={rearm_reason or 'n/a'} defend_fm={free_margin_ratio:.2f}"
        )
    if mercy_cycles < DEFEND_ONE_POS_EXOTIC_MERCY_TRIGGER_CYCLES:
        return 0

    pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
    volume = float(pdata.get('volume', 0.0) or 0.0)
    hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
    try:
        positions = mt5.positions_get(ticket=ticket)
        if positions:
            pos = positions[0]
            pnl = float(pos.profit)
            volume = float(pos.volume)
            pos_time = float(getattr(pos, 'time', 0) or 0.0)
            if pos_time > 0:
                tick = mt5.symbol_info_tick(symbol)
                broker_now = float(getattr(tick, 'time', 0) or 0.0) if tick else 0.0
                if broker_now > 0:
                    hold_sec = max(0.0, broker_now - pos_time)
                else:
                    hold_sec = max(0.0, now - min(pos_time, now))
    except Exception:
        pass

    if pnl >= 0 or abs(pnl) > DEFEND_ONE_POS_EXOTIC_MERCY_MAX_LOSS:
        alleyway_state['defend_one_pos_exotic_mercy_cycles'] = 0
        return 0

    if hold_sec < DEFEND_ONE_POS_EXOTIC_MERCY_MIN_HOLD_SECONDS:
        log(
            f"  ONE_POS_EXOTIC_MERCY_DIAG reason=hold_blocked symbol={symbol} "
            f"hold={int(hold_sec)}s min_hold={DEFEND_ONE_POS_EXOTIC_MERCY_MIN_HOLD_SECONDS}s "
            f"pnl=${pnl:+.2f}"
        )
        alleyway_state['defend_one_pos_exotic_mercy_cycles'] = 0
        return 0

    confidence = float(pdata.get('confidence', 0.0) or 0.0)
    if close_position(ticket, exit_reason="ONE_POS_EXOTIC_MERCY_EXIT", exit_type="mercy"):
        mode = pdata.get('mode', 'SHOTGUN')
        brain.record_exit(symbol, pnl, mode, hold_sec, failure_reason="ONE_POS_EXOTIC_MERCY_EXIT")
        brain.save()
        active_positions.pop(ticket, None)
        alleyway_state['defend_one_pos_exotic_mercy_cycles'] = 0
        alleyway_state['defend_one_pos_exotic_mercy_cooldown_until'] = (
            now + DEFEND_ONE_POS_EXOTIC_MERCY_COOLDOWN_SECONDS
        )
        arm_profit_capture_freeze(now)
        arm_post_cleanup_flat_rearm_holdoff(now, f"ONE_POS_EXOTIC_MERCY_EXIT:{symbol}", pnl)
        log(
            f"  ONE_POS_EXOTIC_MERCY_EXIT {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} defend_fm={free_margin_ratio:.2f} "
            f"idle={idle_cycles} reason={rearm_reason or 'n/a'} cycles={mercy_cycles}"
        )
        return 1

    log(
        f"  ONE_POS_EXOTIC_MERCY_DIAG reason=close_failed symbol={symbol} "
        f"ticket={ticket} pnl=${pnl:+.2f} hold={int(hold_sec)}s defend_fm={free_margin_ratio:.2f}"
    )
    return 0

def defend_one_pos_index_mercy_exit_positions(brain, free_margin_ratio, reversion_diag):
    """
    Retire a stranded lone index loser once a cleanup book has obviously
    finished compressing and the remaining leg is just wasting benchmark time.
    """
    active_count = len(active_positions)
    idle_cycles = int(alleyway_state.get('cycles_without_trade', 0) or 0)
    rearm_reason = str(alleyway_state.get('rearm_reason', '') or '')
    if active_count != 1:
        alleyway_state['defend_one_pos_index_mercy_cycles'] = 0
        return 0

    ticket, pdata = next(iter(active_positions.items()))
    if pdata.get('adopted') or pdata.get('mode') == 'REVERSION':
        alleyway_state['defend_one_pos_index_mercy_cycles'] = 0
        return 0

    symbol = str(pdata.get('symbol', '') or '').upper()
    mercy_shape = (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and rearm_reason.startswith('one-pos-contained')
        and count_direct_positions() == 1
        and bool(symbol)
        and get_symbol_family_bucket(symbol) == "INDEX"
        and free_margin_ratio >= DEFEND_ONE_POS_INDEX_MERCY_MIN_FREE_MARGIN_RATIO
        and idle_cycles >= DEFEND_ONE_POS_INDEX_MERCY_MIN_IDLE_CYCLES
        and int(reversion_diag.get('opened', 0) or 0) == 0
    )

    if mercy_shape:
        alleyway_state['defend_one_pos_index_mercy_cycles'] = int(
            alleyway_state.get('defend_one_pos_index_mercy_cycles', 0) or 0
        ) + 1
    else:
        alleyway_state['defend_one_pos_index_mercy_cycles'] = 0
        return 0

    now = time.time()
    if now < float(alleyway_state.get('defend_one_pos_index_mercy_cooldown_until', 0.0) or 0.0):
        return 0

    mercy_cycles = int(alleyway_state.get('defend_one_pos_index_mercy_cycles', 0) or 0)
    if mercy_cycles in {1, DEFEND_ONE_POS_INDEX_MERCY_TRIGGER_CYCLES}:
        log(
            f"  ONE_POS_INDEX_MERCY_ARM cycles={mercy_cycles} active={active_count} "
            f"idle={idle_cycles} reason={rearm_reason or 'n/a'} defend_fm={free_margin_ratio:.2f}"
        )
    if mercy_cycles < DEFEND_ONE_POS_INDEX_MERCY_TRIGGER_CYCLES:
        return 0

    pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
    volume = float(pdata.get('volume', 0.0) or 0.0)
    hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
    try:
        positions = mt5.positions_get(ticket=ticket)
        if positions:
            pos = positions[0]
            pnl = float(pos.profit)
            volume = float(pos.volume)
            pos_time = float(getattr(pos, 'time', 0) or 0.0)
            if pos_time > 0:
                tick = mt5.symbol_info_tick(symbol)
                broker_now = float(getattr(tick, 'time', 0) or 0.0) if tick else 0.0
                if broker_now > 0:
                    hold_sec = max(0.0, broker_now - pos_time)
                else:
                    hold_sec = max(0.0, now - min(pos_time, now))
    except Exception:
        pass

    if pnl >= 0 or abs(pnl) > DEFEND_ONE_POS_INDEX_MERCY_MAX_LOSS:
        alleyway_state['defend_one_pos_index_mercy_cycles'] = 0
        return 0

    if hold_sec < DEFEND_ONE_POS_INDEX_MERCY_MIN_HOLD_SECONDS:
        log(
            f"  ONE_POS_INDEX_MERCY_DIAG reason=hold_blocked symbol={symbol} "
            f"hold={int(hold_sec)}s min_hold={DEFEND_ONE_POS_INDEX_MERCY_MIN_HOLD_SECONDS}s "
            f"pnl=${pnl:+.2f}"
        )
        alleyway_state['defend_one_pos_index_mercy_cycles'] = 0
        return 0

    confidence = float(pdata.get('confidence', 0.0) or 0.0)
    if close_position(ticket, exit_reason="ONE_POS_INDEX_MERCY_EXIT", exit_type="mercy"):
        mode = pdata.get('mode', 'SNIPER')
        brain.record_exit(symbol, pnl, mode, hold_sec, failure_reason="ONE_POS_INDEX_MERCY_EXIT")
        brain.save()
        active_positions.pop(ticket, None)
        alleyway_state['defend_one_pos_index_mercy_cycles'] = 0
        alleyway_state['defend_one_pos_index_mercy_cooldown_until'] = (
            now + DEFEND_ONE_POS_INDEX_MERCY_COOLDOWN_SECONDS
        )
        arm_profit_capture_freeze(now)
        arm_post_cleanup_flat_rearm_holdoff(now, f"ONE_POS_INDEX_MERCY_EXIT:{symbol}", pnl)
        arm_sync_close_reentry_freeze(symbol, now)
        log(
            f"  ONE_POS_INDEX_MERCY_EXIT {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} defend_fm={free_margin_ratio:.2f} "
            f"idle={idle_cycles} reason={rearm_reason or 'n/a'} cycles={mercy_cycles}"
        )
        return 1

    log(
        f"  ONE_POS_INDEX_MERCY_DIAG reason=close_failed symbol={symbol} "
        f"ticket={ticket} pnl=${pnl:+.2f} hold={int(hold_sec)}s defend_fm={free_margin_ratio:.2f}"
    )
    return 0

def defend_four_book_mixed_cleanup_positions(brain, free_margin_ratio, reversion_diag, mode_counts):
    """
    Peel the smallest loser from a stalled mixed-symbol 4-book DEFEND endgame.

    This lane is intentionally narrow. It only covers the live 4-book state
    that forms after the 5-book financed lane compresses, but before any
    3-book helper can take over. Keep it helper-owned, mixed-symbol only, and
    conservative enough that it merely nudges a stranded cleanup book forward.
    """
    active_count = len(active_positions)
    idle_cycles = int(alleyway_state.get('cycles_without_trade', 0) or 0)
    blocked_defend_loaded = defend_frozen_cleanup_cycles(reversion_diag)
    mixed_shape = (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and DEFEND_FOUR_BOOK_MIXED_CLEANUP_MIN_POSITIONS <= active_count <= DEFEND_FOUR_BOOK_MIXED_CLEANUP_MAX_POSITIONS
        and free_margin_ratio >= DEFEND_FOUR_BOOK_MIXED_CLEANUP_MIN_FREE_MARGIN_RATIO
        and idle_cycles >= DEFEND_FOUR_BOOK_MIXED_CLEANUP_MIN_IDLE_CYCLES
        and blocked_defend_loaded >= DEFEND_FOUR_BOOK_MIXED_CLEANUP_MIN_BLOCKED_DEFEND_LOADED
        and int(reversion_diag.get('opened', 0) or 0) == 0
        and int(mode_counts.get('REVERSION', 0) or 0) == 0
    )

    if mixed_shape:
        alleyway_state['defend_four_book_mixed_cleanup_cycles'] = int(
            alleyway_state.get('defend_four_book_mixed_cleanup_cycles', 0) or 0
        ) + 1
    else:
        alleyway_state['defend_four_book_mixed_cleanup_cycles'] = 0
        return 0

    now = time.time()
    if now < float(alleyway_state.get('defend_four_book_mixed_cleanup_cooldown_until', 0.0) or 0.0):
        return 0

    cleanup_cycles = int(alleyway_state.get('defend_four_book_mixed_cleanup_cycles', 0) or 0)
    if cleanup_cycles < DEFEND_FOUR_BOOK_MIXED_CLEANUP_TRIGGER_CYCLES:
        return 0

    total_pnl = 0.0
    positive_carry = 0.0
    positive_legs = 0
    seen_symbols = set()
    eligible_losers = []

    for ticket, pdata in active_positions.items():
        if pdata.get('adopted') or pdata.get('mode') == 'REVERSION':
            alleyway_state['defend_four_book_mixed_cleanup_cycles'] = 0
            return 0

        symbol = str(pdata.get('symbol', '') or '').upper()
        if not symbol:
            alleyway_state['defend_four_book_mixed_cleanup_cycles'] = 0
            return 0
        seen_symbols.add(symbol)

        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        total_pnl += pnl
        if pnl > 0:
            positive_legs += 1
            positive_carry += pnl
            continue
        if pnl < 0 and abs(pnl) <= DEFEND_FOUR_BOOK_MIXED_CLEANUP_MAX_SINGLE_LOSS:
            hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
            confidence = float(pdata.get('confidence', 0.0) or 0.0)
            eligible_losers.append((ticket, pdata, pnl, volume, hold_sec, confidence))

    if len(seen_symbols) != active_count:
        alleyway_state['defend_four_book_mixed_cleanup_cycles'] = 0
        return 0
    if total_pnl < -DEFEND_FOUR_BOOK_MIXED_CLEANUP_MAX_TOTAL_LOSS:
        return 0
    if positive_legs > DEFEND_FOUR_BOOK_MIXED_CLEANUP_MAX_GREEN_LEGS:
        return 0
    if positive_carry > DEFEND_FOUR_BOOK_MIXED_CLEANUP_MAX_POSITIVE_CARRY:
        return 0
    if not eligible_losers:
        alleyway_state['defend_four_book_mixed_cleanup_cycles'] = 0
        return 0

    eligible_losers.sort(
        key=lambda item: (
            abs(item[2]),  # peel the lightest drag first
            -item[4],
            item[5],
        )
    )

    ticket, pdata, pnl, volume, hold_sec, confidence = eligible_losers[0]
    remaining_net_if_closed = total_pnl - pnl
    if remaining_net_if_closed < DEFEND_FOUR_BOOK_MIXED_CLEANUP_MIN_REMAINING_NET:
        return 0

    if close_position(ticket, exit_reason="DEFEND_FOUR_BOOK_MIXED_CLEANUP", exit_type="cleanup"):
        mode = pdata.get('mode', 'MACHINE_GUN')
        symbol = pdata.get('symbol', '?')
        brain.record_exit(symbol, pnl, mode, hold_sec, failure_reason="FOUR_BOOK_MIXED_CLEANUP")
        brain.save()
        active_positions.pop(ticket, None)
        alleyway_state['defend_four_book_mixed_cleanup_cycles'] = 0
        alleyway_state['defend_four_book_mixed_cleanup_cooldown_until'] = (
            now + DEFEND_FOUR_BOOK_MIXED_CLEANUP_COOLDOWN_SECONDS
        )
        arm_profit_capture_freeze(now)
        freeze_family, freeze_symbol_seconds, freeze_family_seconds = arm_sync_close_reentry_freeze(symbol, now)
        freeze_bits = [f"freeze={freeze_symbol_seconds}s"]
        if freeze_family and freeze_family_seconds > 0:
            freeze_bits.append(f"family={freeze_family}:{freeze_family_seconds}s")
        log(
            f"  FOUR_BOOK_MIXED_CLEANUP {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} defend_fm={free_margin_ratio:.2f} net=${total_pnl:+.2f} "
            f"remain_if_closed=${remaining_net_if_closed:+.2f} "
            f"idle={idle_cycles} positive_legs={positive_legs} carry=${positive_carry:+.2f} "
            f"blk_defend_loaded={blocked_defend_loaded} cycles={cleanup_cycles} "
            f"{' '.join(freeze_bits)}"
        )
        return 1

    return 0

def defend_no_expansion_active(free_margin_ratio, active_count=None):
    """When DEFEND is trying to unwind a loaded book, do not let new adds re-inflate it."""
    if active_count is None:
        active_count = len(active_positions)
    return (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and (
            (
                active_count >= DEFEND_NO_EXPANSION_MIN_POSITIONS
                and free_margin_ratio <= DEFEND_NO_EXPANSION_MAX_FREE_MARGIN_RATIO
            )
            or (
                active_count >= DEFEND_NO_EXPANSION_STRESS_MIN_POSITIONS
                and free_margin_ratio <= DEFEND_NO_EXPANSION_STRESS_MAX_FREE_MARGIN_RATIO
            )
            or free_margin_ratio <= CRITICAL_MARGIN_DERISK_RELEASE_RATIO
        )
    )

def defend_loaded_no_add_active(
    *,
    current_flat_book_rebuild,
    entry_posture,
    current_active_count,
    effective_active_count=None,
    projected_active_count,
    free_margin_ratio,
    managed_drawdown_pct,
    top_symbol_drawdown_pct,
    candidate_regime=None,
    current_price_positions=0,
    current_raw_positions=0,
    current_gemini_positions=0,
):
    """
    Single source of truth for DEFEND book expansion control.

    Live proof repeatedly showed that once a non-flat DEFEND book reaches the
    3+/4+ projected shape, allowing fresh adds creates rebuild churn instead of
    useful compounding. Keep the helper-owned floor here so threshold drift in
    the visible constants cannot silently reopen that leak.
    """
    canonical_loaded_floor = 4
    canonical_midload_floor = 3

    defend_load_count = (
        float(effective_active_count)
        if effective_active_count is not None
        else float(current_active_count)
    )
    projected_load_count = max(defend_load_count, float(projected_active_count))

    if current_flat_book_rebuild or entry_posture != "DEFEND" or defend_load_count <= 0:
        return False

    if candidate_regime in {'PRICE', 'RAW', 'GEMINI'}:
        if candidate_regime == 'PRICE':
            current_regime_positions = current_price_positions
        elif candidate_regime == 'RAW':
            current_regime_positions = current_raw_positions
        else:
            current_regime_positions = current_gemini_positions
        if (
            free_margin_ratio >= DEFEND_EXPERIMENTAL_CONTINUATION_MIN_FREE_MARGIN_RATIO
            and defend_load_count <= DEFEND_EXPERIMENTAL_CONTINUATION_MAX_ACTIVE_POSITIONS
            and current_regime_positions < DEFEND_EXPERIMENTAL_CONTINUATION_MAX_PER_REGIME
        ):
            return False
        if (
            free_margin_ratio >= DEFEND_COMPETITION_EXPERIMENTAL_MIN_FREE_MARGIN_RATIO
            and defend_load_count <= DEFEND_COMPETITION_EXPERIMENTAL_MAX_ACTIVE_POSITIONS
            and current_regime_positions < DEFEND_EXPERIMENTAL_CONTINUATION_MAX_PER_REGIME
            and top_symbol_drawdown_pct <= REARM_MAX_TOP_SYMBOL_DRAWDOWN_PCT
        ):
            return False

    loaded_threshold = min(
        DEFEND_LOADED_NO_ADD_MIN_POSITIONS,
        DEFEND_BENCHMARK_LOADED_NO_ADD_MIN_POSITIONS,
        canonical_loaded_floor,
    )
    midload_threshold = min(
        DEFEND_MIDLOAD_NO_ADD_MIN_POSITIONS,
        DEFEND_BENCHMARK_MIDLOAD_NO_ADD_MIN_POSITIONS,
        canonical_midload_floor,
    )

    if projected_load_count >= loaded_threshold:
        return True

    if projected_load_count >= midload_threshold:
        return True

    return False

def rearm_inherited_book_no_add_active(
    *,
    current_flat_book_rebuild,
    entry_posture,
    adopted_positions,
):
    """
    Freeze fresh REARM entries while the inherited book is still crowded.

    Keep the pre-open helper aligned with the posture gate so a future reload
    does not load contradictory REARM thresholds.
    """

    if current_flat_book_rebuild or entry_posture != "REARM":
        return False

    return adopted_positions >= ADOPTED_BOOK_REARM_FREEZE_THRESHOLD

def defend_frozen_cleanup_cycles(reversion_diag):
    """
    Treat the current no-add lanes as one cleanup-freeze signal.

    The live 4-book DEFEND bottleneck now freezes mostly via the loaded-book
    helper, while older cleanup experiments counted MACHINE_GUN vetoes.
    Use the strongest observed freeze count so cleanup lanes do not go blind
    when the active containment source shifts. The same-symbol cleanup lane is
    evaluated before the current cycle's REV_DIAG summary is fully populated, so
    it must also inherit the last persisted loaded-book freeze count.
    """
    return max(
        int(reversion_diag.get('blocked_defend_loaded', 0) or 0),
        int(alleyway_state.get('last_blocked_defend_loaded', 0) or 0),
        int(reversion_diag.get('blocked_defend_mg', 0) or 0),
        int(reversion_diag.get('blocked_defend_cleanup', 0) or 0),
    )

def count_direct_positions():
    return sum(1 for pdata in active_positions.values() if not pdata.get('adopted'))

def restore_post_cleanup_runtime_state(now=None, state_file=RUNTIME_STATE_FILE):
    if now is None:
        now = time.time()
    if not os.path.exists(state_file):
        return []

    try:
        with open(state_file, "r", encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except Exception:
        return []

    restored = []
    restored_lane_id = str(snapshot.get("strategy_lab_active_lane_id", "") or "")
    if restored_lane_id in STRATEGY_LAB_LANES:
        refreshed_lane_id, refreshed_reason = refresh_strategy_lab_owner_lane_on_startup(restored_lane_id)
        alleyway_state["strategy_lab_last_completed_lane_id"] = str(
            snapshot.get("strategy_lab_last_completed_lane_id", "") or ""
        )
        alleyway_state["strategy_lab_lane_rotated_at"] = str(
            snapshot.get("strategy_lab_lane_rotated_at", "") or ""
        )
        restored.append(f"strategy_lab_lane={refreshed_lane_id}")
        restored.append(f"strategy_lab_owner={refreshed_reason}")
    direct_count = count_direct_positions()

    flat_hold_until = float(
        snapshot.get("post_cleanup_hold_until_ts", 0.0) or 0.0
    )
    if flat_hold_until <= 0:
        flat_hold_until = now + max(0, int(snapshot.get("post_cleanup_hold_remaining_s", 0) or 0))
    if direct_count == 0 and flat_hold_until > now:
        alleyway_state["post_cleanup_flat_rearm_hold_until"] = flat_hold_until
        alleyway_state["post_cleanup_flat_rearm_trigger"] = str(
            snapshot.get("post_cleanup_hold_trigger", "") or ""
        )
        alleyway_state["post_cleanup_flat_rearm_armed_at"] = str(
            snapshot.get("post_cleanup_hold_armed_at", "") or ""
        )
        alleyway_state["post_cleanup_flat_rearm_last_pnl"] = float(
            snapshot.get("post_cleanup_hold_last_pnl", 0.0) or 0.0
        )
        restored.append(
            f"flat_hold={max(0, int(flat_hold_until - now))}s"
        )

    if direct_count == 0 and bool(snapshot.get("post_cleanup_quality_gate_pending", False)):
        alleyway_state["post_cleanup_quality_gate_pending"] = True
        alleyway_state["post_cleanup_quality_gate_trigger"] = str(
            snapshot.get("post_cleanup_quality_gate_trigger", "") or ""
        )
        alleyway_state["post_cleanup_quality_gate_armed_at"] = str(
            snapshot.get("post_cleanup_quality_gate_armed_at", "") or ""
        )
        restored.append(
            f"quality_gate={alleyway_state['post_cleanup_quality_gate_trigger'] or 'pending'}"
        )

    first_leg_hold_until = float(
        snapshot.get("post_cleanup_first_leg_hold_until_ts", 0.0) or 0.0
    )
    if first_leg_hold_until <= 0:
        first_leg_hold_until = now + max(
            0,
            int(snapshot.get("post_cleanup_first_leg_hold_remaining_s", 0) or 0),
        )
    if direct_count == 1 and first_leg_hold_until > now:
        alleyway_state["post_cleanup_first_leg_rearm_hold_until"] = first_leg_hold_until
        alleyway_state["post_cleanup_first_leg_rearm_trigger"] = str(
            snapshot.get("post_cleanup_first_leg_hold_trigger", "") or ""
        )
        alleyway_state["post_cleanup_first_leg_rearm_armed_at"] = str(
            snapshot.get("post_cleanup_first_leg_hold_armed_at", "") or ""
        )
        restored.append(
            f"first_leg_hold={max(0, int(first_leg_hold_until - now))}s"
        )

    return restored

def update_entry_posture(book_stress, free_margin_ratio):
    """Hold a brief re-arm window once the managed book calms down enough."""
    now = time.time()
    previous_posture = alleyway_state.get("entry_posture", "DEFEND")
    previous_reason = alleyway_state.get("rearm_reason", "")
    flat_book = book_stress["managed_positions"] == 0
    direct_losing_positions = 0
    direct_non_reversion = 0
    lone_direct_pnl = None
    lone_direct_symbol = None
    for pdata in active_positions.values():
        if pdata.get("adopted"):
            continue
        lone_direct_pnl = float(pdata.get("last_pnl", 0.0) or 0.0)
        lone_direct_symbol = pdata.get("symbol", "UNKNOWN")
        if pdata.get("mode") != "REVERSION":
            direct_non_reversion += 1
        if lone_direct_pnl < 0:
            direct_losing_positions += 1

    # Keep posture eligibility pinned to the helper-owned live floor.
    # Do not reintroduce direct overrides with REARM_MAX_* here. That exact
    # regression was already live-proven to be wrong:
    # - 2026-04-07 large-book drift reopened 14+ position DEFEND books
    # - 2026-04-07 small-book drift reclassified a 4-position USDHKD loser
    #   cluster as quiet-book REARM on restart
    # If someone wants to argue for aggression, the experiment belongs in the
    # helper with fresh live evidence, not as a local override here.
    (
        effective_rearm_max_direct_positions,
        effective_rearm_max_non_reversion_direct,
        effective_rearm_max_losing_direct_positions,
    ) = get_effective_rearm_limits()

    nonflat_rearm_sanity_block = (
        not flat_book
        and (
            book_stress["direct_positions"] > effective_rearm_max_direct_positions
            or direct_non_reversion > effective_rearm_max_non_reversion_direct
            or direct_losing_positions > effective_rearm_max_losing_direct_positions
        )
    )

    quiet_book = (
        free_margin_ratio >= REARM_MIN_FREE_MARGIN_RATIO
        and book_stress["managed_drawdown_pct"] <= REARM_MAX_MANAGED_DRAWDOWN_PCT
        and book_stress["top_symbol_drawdown_pct"] <= REARM_MAX_TOP_SYMBOL_DRAWDOWN_PCT
        and book_stress["direct_positions"] <= effective_rearm_max_direct_positions
        and direct_non_reversion <= effective_rearm_max_non_reversion_direct
        and direct_losing_positions <= effective_rearm_max_losing_direct_positions
    )
    if nonflat_rearm_sanity_block:
        quiet_book = False
    rearm_hysteresis_eligible = (
        previous_posture == "REARM"
        and not flat_book
        and book_stress["direct_positions"] <= REARM_HYSTERESIS_MAX_DIRECT_POSITIONS
        and free_margin_ratio >= REARM_HYSTERESIS_MIN_FREE_MARGIN_RATIO
        and book_stress["managed_drawdown_pct"] <= REARM_HYSTERESIS_MAX_MANAGED_DRAWDOWN_PCT
        and book_stress["top_symbol_drawdown_pct"] <= REARM_HYSTERESIS_MAX_TOP_SYMBOL_DRAWDOWN_PCT
        and direct_non_reversion <= effective_rearm_max_non_reversion_direct
        and direct_losing_positions <= REARM_HYSTERESIS_MAX_LOSING_DIRECT_POSITIONS
    )
    if nonflat_rearm_sanity_block:
        rearm_hysteresis_eligible = False
    alleyway_state["rearm_debug"] = (
        f"fm={free_margin_ratio:.2f}"
        f"|dd={book_stress['managed_drawdown_pct']:.3f}"
        f"|top={book_stress['top_symbol_drawdown_pct']:.3f}"
        f"|direct={book_stress['direct_positions']}"
        f"|nonrev={direct_non_reversion}"
        f"|losing={direct_losing_positions}"
        f"|quiet={'yes' if quiet_book else 'no'}"
        f"|hyst={'yes' if rearm_hysteresis_eligible else 'no'}"
        f"|remain={int(alleyway_state.get('rearm_cycles_remaining', 0) or 0)}"
    )
    # Live proof on 2026-04-07 showed two final restart drifts:
    # 1) a lone red survivor could reclassify into REARM on restart
    # 2) the first flat-book rebuild leg could immediately snowball into a 3-leg basket
    # Keep these guards in the live posture path so safety does not depend on
    # top-of-file aggression constants or a separate entry-loop cap.
    one_position_guard_reason = ""
    alleyway_state["one_position_profit_ticket"] = 0
    alleyway_state["one_position_profit_hold_cycles"] = 0

    inherited_book_guard_active = rearm_inherited_book_no_add_active(
        current_flat_book_rebuild=False,
        entry_posture=previous_posture,
        adopted_positions=book_stress["adopted_positions"],
    )
    if (
        not flat_book
        and book_stress["direct_positions"] == 0
        and book_stress["adopted_positions"] >= ADOPTED_BOOK_REARM_FREEZE_THRESHOLD
        and inherited_book_guard_active
    ):
        reason = (
            f"adopted-book-freeze adopted={book_stress['adopted_positions']} "
            f"thresh={ADOPTED_BOOK_REARM_FREEZE_THRESHOLD}"
        )
        alleyway_state["rearm_cycles_remaining"] = 0
        alleyway_state["rearm_active"] = False
        alleyway_state["entry_posture"] = "DEFEND"
        alleyway_state["rearm_reason"] = reason
        alleyway_state["managed_drawdown_pct"] = book_stress["managed_drawdown_pct"]
        alleyway_state["top_symbol_drawdown_pct"] = book_stress["top_symbol_drawdown_pct"]
        alleyway_state["free_margin_ratio"] = free_margin_ratio
        log_rearm_transition(previous_posture, previous_reason)
        return False, reason

    post_cleanup_hold_remaining, post_cleanup_hold_trigger = get_active_post_cleanup_holdoff(now)
    if post_cleanup_hold_remaining > 0:
        reason = (
            f"post-cleanup-holdoff {post_cleanup_hold_remaining}s "
            f"trigger={post_cleanup_hold_trigger or 'unknown'}"
        )
        alleyway_state["rearm_cycles_remaining"] = 0
        alleyway_state["rearm_active"] = False
        alleyway_state["entry_posture"] = "DEFEND"
        alleyway_state["rearm_reason"] = reason
        alleyway_state["managed_drawdown_pct"] = book_stress["managed_drawdown_pct"]
        alleyway_state["top_symbol_drawdown_pct"] = book_stress["top_symbol_drawdown_pct"]
        alleyway_state["free_margin_ratio"] = free_margin_ratio
        log_rearm_transition(previous_posture, previous_reason)
        return False, reason

    one_position_hold_until = float(alleyway_state.get("one_position_quiet_rearm_hold_until", 0.0) or 0.0)
    if (
        not flat_book
        and book_stress["direct_positions"] == 1
        and now < one_position_hold_until
    ):
        remaining = max(0, int(one_position_hold_until - now))
        reason = (
            f"one-pos-holdoff {remaining}s "
            f"trigger={alleyway_state.get('one_position_quiet_rearm_trigger', 'unknown')}"
        )
        alleyway_state["rearm_cycles_remaining"] = 0
        alleyway_state["rearm_active"] = False
        alleyway_state["entry_posture"] = "DEFEND"
        alleyway_state["rearm_reason"] = reason
        alleyway_state["managed_drawdown_pct"] = book_stress["managed_drawdown_pct"]
        alleyway_state["top_symbol_drawdown_pct"] = book_stress["top_symbol_drawdown_pct"]
        alleyway_state["free_margin_ratio"] = free_margin_ratio
        log_rearm_transition(previous_posture, previous_reason)
        return False, reason

    post_cleanup_first_leg_hold_until = float(
        alleyway_state.get("post_cleanup_first_leg_rearm_hold_until", 0.0) or 0.0
    )
    if (
        not flat_book
        and book_stress["direct_positions"] == 1
        and now < post_cleanup_first_leg_hold_until
    ):
        remaining = max(0, int(post_cleanup_first_leg_hold_until - now))
        reason = (
            f"post-cleanup-first-leg-holdoff {remaining}s "
            f"trigger={alleyway_state.get('post_cleanup_first_leg_rearm_trigger', 'unknown')}"
        )
        alleyway_state["rearm_cycles_remaining"] = 0
        alleyway_state["rearm_active"] = False
        alleyway_state["entry_posture"] = "DEFEND"
        alleyway_state["rearm_reason"] = reason
        alleyway_state["managed_drawdown_pct"] = book_stress["managed_drawdown_pct"]
        alleyway_state["top_symbol_drawdown_pct"] = book_stress["top_symbol_drawdown_pct"]
        alleyway_state["free_margin_ratio"] = free_margin_ratio
        log_rearm_transition(previous_posture, previous_reason)
        return False, reason

    if (
        not flat_book
        and book_stress["direct_positions"] == 1
        and lone_direct_pnl is not None
        and lone_direct_pnl < ONE_POSITION_REARM_MIN_GREEN_PNL_USD
    ):
        one_position_guard_reason = (
            f"one-pos-contained symbol={lone_direct_symbol or 'UNKNOWN'} "
            f"pnl=${lone_direct_pnl:+.2f} "
            f"release=${ONE_POSITION_REARM_MIN_GREEN_PNL_USD:.2f}"
        )

    if one_position_guard_reason:
        alleyway_state["rearm_cycles_remaining"] = 0
        alleyway_state["rearm_active"] = False
        alleyway_state["entry_posture"] = "DEFEND"
        alleyway_state["rearm_reason"] = one_position_guard_reason
        alleyway_state["managed_drawdown_pct"] = book_stress["managed_drawdown_pct"]
        alleyway_state["top_symbol_drawdown_pct"] = book_stress["top_symbol_drawdown_pct"]
        alleyway_state["free_margin_ratio"] = free_margin_ratio
        alleyway_state["rearm_used_this_quiet"] = False
        log_rearm_transition(previous_posture, previous_reason)
        return False, one_position_guard_reason

    if quiet_book:
        current = alleyway_state.get("rearm_cycles_remaining", 0)
        rearm_used = alleyway_state.get("rearm_used_this_quiet", False)
        if flat_book:
            # Flat book: clear the used flag so REARM can fire, but only set counter if expired
            alleyway_state["rearm_used_this_quiet"] = False
            if current <= 0:
                alleyway_state["rearm_cycles_remaining"] = REARM_HOLD_CYCLES
            # Decrement so it expires
            remaining = max(0, alleyway_state["rearm_cycles_remaining"] - 1)
            alleyway_state["rearm_cycles_remaining"] = remaining
            if remaining == 0:
                alleyway_state["rearm_used_this_quiet"] = True
            reason = (
                f"flat-book fm={free_margin_ratio:.2f} "
                f"dd={book_stress['managed_drawdown_pct']:.3f} "
                f"top={book_stress['top_symbol_drawdown_pct']:.3f}"
            )
        else:
            # Quiet but not flat: apply cooldown reset to unlock growth
            current = alleyway_state.get("rearm_cycles_remaining", 0)
            rearm_used = alleyway_state.get("rearm_used_this_quiet", False)
            
            # Cooldown: after N quiet cycles, reset rearm_used so we can fire again
            # COMPETITION FIX: When margin is healthy (>80%), bypass cooldown for compounding
            if rearm_used:
                competition_bypass = free_margin_ratio > 0.80  # COMPETITION FIX: Allow faster re-entry
                cooldown = alleyway_state.get("rearm_quiet_cooldown", 0) + 1
                if competition_bypass or cooldown >= REARM_QUIET_COOLDOWN_CYCLES:
                    alleyway_state["rearm_used_this_quiet"] = False
                    alleyway_state["rearm_quiet_cooldown"] = 0
                    rearm_used = False  # Update local var for logic below
                else:
                    alleyway_state["rearm_quiet_cooldown"] = cooldown
            else:
                # Not used yet, clear any stale cooldown
                alleyway_state["rearm_quiet_cooldown"] = 0
            
            if current <= 0 and not rearm_used:
                alleyway_state["rearm_cycles_remaining"] = REARM_HOLD_CYCLES
            # Always decrement even during quiet managed books so REARM eventually expires
            remaining = max(0, alleyway_state["rearm_cycles_remaining"] - 1)
            alleyway_state["rearm_cycles_remaining"] = remaining
            if remaining == 0:
                alleyway_state["rearm_used_this_quiet"] = True
            reason = (
                f"quiet-book fm={free_margin_ratio:.2f} "
                f"dd={book_stress['managed_drawdown_pct']:.3f} "
                f"top={book_stress['top_symbol_drawdown_pct']:.3f}"
            )
    else:
        if rearm_hysteresis_eligible:
            current = int(alleyway_state.get("rearm_cycles_remaining", 0) or 0)
            remaining = max(current, REARM_HYSTERESIS_HOLD_CYCLES)
            remaining = max(1, remaining - 1)
            alleyway_state["rearm_cycles_remaining"] = remaining
            reason = (
                f"rearm-hold fm={free_margin_ratio:.2f} "
                f"dd={book_stress['managed_drawdown_pct']:.3f} "
                f"top={book_stress['top_symbol_drawdown_pct']:.3f} "
                f"direct={book_stress['direct_positions']} "
                f"losing={direct_losing_positions}"
            )
        else:
            remaining = max(0, alleyway_state.get("rearm_cycles_remaining", 0) - 1)
            alleyway_state["rearm_cycles_remaining"] = remaining
            # Reset flag when book is not quiet, so next quiet period can trigger REARM
            alleyway_state["rearm_used_this_quiet"] = False
            if one_position_guard_reason:
                reason = one_position_guard_reason
            else:
                reason = (
                    f"guarded fm={free_margin_ratio:.2f} "
                    f"dd={book_stress['managed_drawdown_pct']:.3f} "
                    f"top={book_stress['top_symbol_drawdown_pct']:.3f}"
                )

    rearm_active = alleyway_state.get("rearm_cycles_remaining", 0) > 0
    alleyway_state["rearm_active"] = rearm_active
    alleyway_state["entry_posture"] = "REARM" if rearm_active else "DEFEND"
    alleyway_state["rearm_reason"] = reason
    alleyway_state["managed_drawdown_pct"] = book_stress["managed_drawdown_pct"]
    alleyway_state["top_symbol_drawdown_pct"] = book_stress["top_symbol_drawdown_pct"]
    alleyway_state["free_margin_ratio"] = free_margin_ratio
    alleyway_state["rearm_debug"] = (
        f"fm={free_margin_ratio:.2f}"
        f"|dd={book_stress['managed_drawdown_pct']:.3f}"
        f"|top={book_stress['top_symbol_drawdown_pct']:.3f}"
        f"|direct={book_stress['direct_positions']}"
        f"|nonrev={direct_non_reversion}"
        f"|losing={direct_losing_positions}"
        f"|quiet={'yes' if quiet_book else 'no'}"
        f"|hyst={'yes' if rearm_hysteresis_eligible else 'no'}"
        f"|remain={int(alleyway_state.get('rearm_cycles_remaining', 0) or 0)}"
    )
    log_rearm_transition(previous_posture, previous_reason)
    return rearm_active, reason

def cleanup_stale_adopted_positions(brain):
    """Close adopted positions that are old, losing, and tiny — dead weight blocking new entries."""
    cleaned = 0
    now = time.time()
    candidates = []
    # Compute MT5 server clock offset using a live tick as the server clock.
    # MT5 server time is ahead of local clock — tick.time gives us the
    # server's current timestamp which we compare to time.time().
    mt5_server_offset = 0
    try:
        tick = mt5.symbol_info_tick('EURUSD')
        if tick and tick.time:
            mt5_server_offset = float(tick.time) - time.time()
    except:
        pass
    for ticket, pdata in list(active_positions.items()):
        if not pdata.get('adopted'):
            continue
        # Get REAL open time from MT5 (not the fake adoption time)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if not positions:
                continue
            pos = positions[0]
            mt5_open_time = getattr(pos, 'time', None)
            if mt5_open_time:
                # Adjust for MT5 server clock being ahead of local clock
                adjusted_open_time = float(mt5_open_time) - mt5_server_offset
                hold_sec = now - adjusted_open_time
            else:
                hold_sec = now - pdata.get('entry_time', now)
        except:
            hold_sec = now - pdata.get('entry_time', now)
        pnl = pdata.get('last_pnl', 0.0) or 0.0
        vol = pdata.get('volume', 0) or 0
        # Fetch REAL PnL from MT5 (pdata last_pnl may be stale)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                vol = float(positions[0].volume)
        except:
            pass
        # Clean up if: older than 20 min AND losing, OR older than 45 min regardless, OR volume <= 0.01 and losing
        age_min = hold_sec / 60
        if (hold_sec > 1200 and pnl < 0) or (hold_sec > 2700) or (vol <= 0.01 and pnl < -0.50):
            candidates.append((ticket, pdata, pnl, hold_sec))
    # Close worst losers first, max 4 per cycle
    candidates.sort(key=lambda x: x[2])
    for ticket, pdata, pnl, hold_sec in candidates[:4]:
        if close_position(ticket, exit_reason="ADOPTED_CLEANUP", exit_type="cleanup"):
            active_positions.pop(ticket, None)
            cleaned += 1
            log(
                f"  ADOPTED_CLEANUP lane={get_position_lane(pdata)} "
                f"#{ticket} {pdata['symbol']} P/L=${pnl:+.2f} age={int(hold_sec)}s "
                f"vol={pdata.get('volume',0)}"
            )
    return cleaned

def trim_stressed_symbol_positions(brain):
    """De-risk the newest direct adds when one symbol dominates book pain."""
    acct = mt5.account_info()
    free_margin_ratio = 1.0
    if acct and getattr(acct, 'equity', 0):
        try:
            free_margin_ratio = max(0.0, float(acct.margin_free) / float(acct.equity))
        except Exception:
            free_margin_ratio = 1.0

    stressed_symbols = []
    for symbol in {pdata['symbol'] for pdata in active_positions.values()}:
        stress = get_symbol_stress(symbol)
        # Don't trim single-position symbols unless the absolute loss is meaningful
        symbol_positions = [p for p in active_positions.values() if p['symbol'] == symbol and not p.get('adopted')]
        if len(symbol_positions) == 1:
            max_loss = abs(min(float(p.get('last_pnl', 0.0) or 0.0) for p in symbol_positions))
            if max_loss < 5.0:  # Don't trim single positions under $5 loss
                continue
        if (
            stress["drawdown_share"] >= SYMBOL_STRESS_TRIM_DRAWDOWN_SHARE
            or stress["score"] >= SYMBOL_STRESS_TRIM_SCORE
        ):
            stressed_symbols.append((symbol, stress))

    stressed_symbols.sort(key=lambda item: (item[1]["drawdown_share"], item[1]["score"]), reverse=True)

    trims = 0
    for symbol, stress in stressed_symbols:
        if trims >= MAX_STRESS_TRIMS_PER_CYCLE:
            break

        candidates = []
        for ticket, pdata in active_positions.items():
            if pdata['symbol'] != symbol:
                continue
            if pdata.get('adopted'):
                continue
            hold_sec = time.time() - pdata.get('entry_time', time.time())
            grace_seconds = (
                REVERSION_STRESS_TRIM_GRACE_SECONDS
                if pdata.get('mean_reversion')
                else FRESH_TRADE_STRESS_TRIM_GRACE_SECONDS
            )
            emergency_trim = (
                free_margin_ratio <= EMERGENCY_STRESS_TRIM_MARGIN_RATIO
                or stress["score"] >= EMERGENCY_STRESS_TRIM_SCORE
            )
            if hold_sec < grace_seconds and not emergency_trim:
                continue
            candidates.append((ticket, pdata))

        if not candidates:
            continue

        # Trim newest direct adds first, preferring pyramids and currently losing tickets.
        candidates.sort(
            key=lambda item: (
                0 if item[1].get('is_pyramid') else 1,
                -(float(item[1].get('entry_time', 0.0) or 0.0)),
                float(item[1].get('last_pnl', 0.0) or 0.0),
            )
        )

        ticket, pdata = candidates[0]
        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        hold_sec = max(0.0, time.time() - float(pdata.get('entry_time', time.time()) or time.time()))
        mode = pdata.get('mode', 'MACHINE_GUN')

        if close_position(ticket, exit_reason="STRESS_TRIM", exit_type="risk"):
            brain.record_exit(symbol, pnl, mode, hold_sec, failure_reason="STRESS_TRIM")
            brain.save()
            active_positions.pop(ticket, None)
            recently_trimmed_symbols[symbol] = time.time()
            triggered_cluster = register_risk_event()
            arm_post_cleanup_flat_rearm_holdoff(
                time.time(),
                format_competition_lane_trigger("STRESS_TRIM", pdata, symbol),
                pnl,
            )
            arm_one_position_quiet_rearm_holdoff(
                time.time(),
                format_competition_lane_trigger("STRESS_TRIM", pdata, symbol),
                pnl,
            )
            trims += 1
            log(
                f"  STRESS_TRIM lane={get_position_lane(pdata)} {symbol} #{ticket} P/L=${pnl:+.2f} "
                f"(share={stress['drawdown_share']:.2f}, score={stress['score']:.2f})"
            )
            if triggered_cluster:
                remaining = int(max(0, alleyway_state.get('cluster_cooldown_until', 0) - time.time()))
                log(f"  CLUSTER_COOLDOWN armed for {remaining}s after repeated trims/reversals")

    return trims

def critical_margin_derisk_positions(brain):
    """When margin is critically compressed, actively shed the weakest direct positions."""
    acct = mt5.account_info()
    if not acct or not getattr(acct, 'equity', 0):
        return 0

    try:
        free_margin_ratio = float(acct.margin_free) / float(acct.equity)
    except Exception:
        free_margin_ratio = 1.0

    if free_margin_ratio > CRITICAL_MARGIN_DERISK_TRIGGER_RATIO:
        return 0

    candidates = []
    now = time.time()
    for ticket, pdata in active_positions.items():
        if pdata.get('adopted'):
            continue

        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
        confidence = float(pdata.get('confidence', 0.0) or 0.0)
        mode = pdata.get('mode', 'REVERSION')
        
        # Protect fresh GEMINI breakouts from getting immediately chopped as fodder
        if mode == 'GEMINI' and hold_sec < 300:
            continue

        candidates.append((ticket, pdata, pnl, volume, hold_sec, confidence))

    if not candidates:
        return 0

    # Worst losers first, preferring later/non-core adds before older conviction positions.
    candidates.sort(
        key=lambda item: (
            item[2],                              # lower P/L first
            0 if item[1].get('is_pyramid') else 1,
            -item[4],                            # newer first
            -item[3],                            # larger volume first
            item[5],                             # lower confidence first
        )
    )

    derisked = 0
    for ticket, pdata, pnl, volume, hold_sec, confidence in candidates:
        if derisked >= MAX_CRITICAL_MARGIN_DERISKS_PER_CYCLE:
            break

        if close_position(ticket, exit_reason="CRITICAL_DERISK", exit_type="risk"):
            mode = pdata.get('mode', 'MACHINE_GUN')
            symbol = pdata.get('symbol', '?')
            brain.record_exit(symbol, pnl, mode, hold_sec, failure_reason="MARGIN_PRESSURE")
            brain.save()
            active_positions.pop(ticket, None)
            arm_post_cleanup_flat_rearm_holdoff(
                time.time(),
                format_competition_lane_trigger("CRITICAL_DERISK", pdata, symbol),
                pnl,
            )
            arm_one_position_quiet_rearm_holdoff(
                time.time(),
                format_competition_lane_trigger("CRITICAL_DERISK", pdata, symbol),
                pnl,
            )
            derisked += 1
            log(
                f"  CRITICAL_DERISK lane={get_position_lane(pdata)} {symbol} #{ticket} P/L=${pnl:+.2f} "
                f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f}"
            )

            time.sleep(0.2)
            acct = mt5.account_info()
            if acct and getattr(acct, 'equity', 0):
                try:
                    free_margin_ratio = float(acct.margin_free) / float(acct.equity)
                except Exception:
                    free_margin_ratio = free_margin_ratio
                if free_margin_ratio >= CRITICAL_MARGIN_DERISK_RELEASE_RATIO:
                    break

    return derisked

def defend_crowding_derisk_positions(brain, mode_counts, free_margin_ratio):
    """Slowly unwind a sticky DEFEND book when crowding/overload persists."""
    active_count = len(active_positions)
    reversion_count = int(mode_counts.get('REVERSION', 0) or 0)
    reversion_share = (reversion_count / active_count) if active_count > 0 else 0.0
    defend_reversion_crowded = (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and active_count > 0
        and free_margin_ratio <= DEFEND_CROWDING_DERISK_MAX_FREE_MARGIN_RATIO
        and reversion_count >= DEFEND_CROWDING_DERISK_MIN_REVERSION_POSITIONS
        and reversion_share >= DEFEND_CROWDING_DERISK_MIN_SHARE
        and not alleyway_state.get('rearm_used_this_quiet', False)  # Skip derisk if we recently used REARM
    )
    defend_book_overloaded = (
        alleyway_state.get('entry_posture') == 'DEFEND'
        and active_count >= DEFEND_OVERLOAD_DERISK_MIN_POSITIONS
        and free_margin_ratio <= DEFEND_OVERLOAD_DERISK_MAX_FREE_MARGIN_RATIO
    )
    defend_crowded = defend_reversion_crowded or defend_book_overloaded

    if defend_crowded:
        alleyway_state['defend_crowding_cycles'] = int(alleyway_state.get('defend_crowding_cycles', 0) or 0) + 1
    else:
        alleyway_state['defend_crowding_cycles'] = 0
        return 0

    if alleyway_state['defend_crowding_cycles'] < DEFEND_CROWDING_DERISK_TRIGGER_CYCLES:
        return 0

    candidates = []
    now = time.time()
    for ticket, pdata in active_positions.items():
        if pdata.get('adopted'):
            continue
        pnl = float(pdata.get('last_pnl', 0.0) or 0.0)
        volume = float(pdata.get('volume', 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pnl = float(positions[0].profit)
                volume = float(positions[0].volume)
        except Exception:
            pass

        hold_sec = max(0.0, now - float(pdata.get('entry_time', now) or now))
        confidence = float(pdata.get('confidence', 0.0) or 0.0)
        mode = pdata.get('mode', 'REVERSION')
        
        # Protect fresh GEMINI breakouts from getting immediately chopped as fodder
        if mode == 'GEMINI' and hold_sec < 300:
            continue

        candidates.append((ticket, pdata, pnl, volume, hold_sec, confidence))

    if not candidates:
        return 0

    candidates.sort(
        key=lambda item: (
            item[2],      # biggest loser first
            0 if item[1].get('mode') == 'REVERSION' else 1,
            -item[4],     # newer first
            -item[3],     # larger volume first
            item[5],      # lower confidence first
        )
    )

    ticket, pdata, pnl, volume, hold_sec, confidence = candidates[0]
    if pnl >= 0:
        return 0

    if close_position(ticket, exit_reason="DEFEND_DERISK", exit_type="risk"):
        mode = pdata.get('mode', 'REVERSION')
        symbol = pdata.get('symbol', '?')
        brain.record_exit(symbol, pnl, mode, hold_sec, failure_reason="STRESS_TRIM")
        brain.save()
        active_positions.pop(ticket, None)
        arm_post_cleanup_flat_rearm_holdoff(
            time.time(),
            format_competition_lane_trigger("DEFEND_DERISK", pdata, symbol),
            pnl,
        )
        arm_one_position_quiet_rearm_holdoff(
            time.time(),
            format_competition_lane_trigger("DEFEND_DERISK", pdata, symbol),
            pnl,
        )
        log(
            f"  DEFEND_DERISK lane={get_position_lane(pdata)} {symbol} #{ticket} P/L=${pnl:+.2f} "
            f"vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
            f"mode={mode} crowd_cycles={alleyway_state.get('defend_crowding_cycles', 0)}"
        )
        alleyway_state['defend_crowding_cycles'] = 0
        return 1

    return 0

def set_broker_sl_tp(ticket, direction, entry_price, atr=0, mode='MACHINE_GUN'):
    """Set broker-side SL/TP so positions survive bot crashes
    
    Uses ATR-based stops instead of hardcoded pips.
    Checks actual order_send retcode, not mt5.last_error().
    """
    try:
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False
        pos = positions[0]
        symbol = pos.symbol
        sym_info = mt5.symbol_info(symbol)
        if not sym_info:
            return False
        point = float(getattr(sym_info, 'point', 0.0) or 0.00001)
        digits = int(getattr(sym_info, 'digits', 5) or 5)
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return False

        sl_price, tp_price = calc_sl_tp_prices(symbol, direction, entry_price, atr, mode)
        if not sl_price or not tp_price:
            return False

        stops_points = max(
            int(getattr(sym_info, 'trade_stops_level', 0) or 0),
            int(getattr(sym_info, 'trade_freeze_level', 0) or 0),
            10,
        )
        min_stop_distance = (stops_points + 5) * point

        def build_prices(extra_distance=0.0):
            total_min_distance = min_stop_distance + extra_distance
            if direction == 'BUY':
                safe_sl = min(sl_price, round(float(tick.bid) - total_min_distance, digits))
                safe_tp = max(tp_price, round(float(tick.ask) + total_min_distance, digits))
            else:
                safe_sl = max(sl_price, round(float(tick.ask) + total_min_distance, digits))
                safe_tp = min(tp_price, round(float(tick.bid) - total_min_distance, digits))
            return safe_sl, safe_tp

        for attempt_idx, extra_distance in enumerate((0.0, min_stop_distance), start=1):
            safe_sl, safe_tp = build_prices(extra_distance)
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": ticket,
                "sl": safe_sl,
                "tp": safe_tp,
            }

            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"  [SL_TP] Set SL={safe_sl} TP={safe_tp} on #{ticket} ({mode})")
                return True

            retcode = result.retcode if result else 'None'
            comment = result.comment if result else 'No result'
            if int(retcode or 0) == 10016 and attempt_idx == 1:
                log(
                    f"  [SL_TP_RETRY] #{ticket} invalid stops with SL={safe_sl} TP={safe_tp} "
                    f"retrying wider buffer={min_stop_distance:.5f}"
                )
                continue
            log(f"  [SL_TP] Failed on #{ticket}: retcode={retcode} comment={comment}")
            break
        return False
    except Exception as e:
        log(f"  [SL_TP] Exception on #{ticket}: {e}")
        return False

def try_open_position(symbol, signal, lot, mode, confidence, atr):
    """Open position with ATR-based SL/TP"""
    try:
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return None
        tick_stale, tick_age = is_tick_stale(tick)
        if tick_stale:
            log_stale_symbol(symbol, "try_open_position", tick_age)
            return None

        sym_info = mt5.symbol_info(symbol)
        if not sym_info:
            return None

        # Clamp lot to symbol limits
        min_lot = sym_info.volume_min
        max_lot = sym_info.volume_max
        lot_step = sym_info.volume_step
        lot = max(min_lot, min(max_lot, round(lot / lot_step) * lot_step))
        lot = round(lot, 2)

        price = tick.ask if signal == 'BUY' else tick.bid
        order_type = mt5.ORDER_TYPE_BUY if signal == 'BUY' else mt5.ORDER_TYPE_SELL

        sl_price, tp_price = calc_sl_tp_prices(symbol, signal, price, atr, mode)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "sl": sl_price,
            "tp": tp_price,
            "deviation": 50,
            "magic": 888888,
            "comment": f"{mode}-{signal}",
            "type_time": mt5.ORDER_TIME_GTC,
            # Don't force filling mode - let broker use default
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            return result.order
        # Try with filling mode from symbol info if available
        if result and result.retcode == 10030:  # Unsupported filling mode
            sym_fill = getattr(sym_info, 'filling_mode', None)
            if sym_fill:
                request["type_filling"] = sym_fill
                result = mt5.order_send(request)
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    return result.order
        # Log failure details
        if result and result.retcode != mt5.TRADE_RETCODE_DONE:
            if (
                int(getattr(result, "retcode", 0) or 0) == 10018
                or "market closed" in str(getattr(result, "comment", "") or "").lower()
            ):
                mark_symbol_market_closed(
                    symbol,
                    retcode=getattr(result, "retcode", None),
                    comment=getattr(result, "comment", ""),
                )
            if (
                int(getattr(result, "retcode", 0) or 0) == 10019
                or "no money" in str(getattr(result, "comment", "") or "").lower()
            ):
                mark_symbol_insufficient_margin(
                    symbol,
                    retcode=getattr(result, "retcode", None),
                    comment=getattr(result, "comment", ""),
                )
            if (
                int(getattr(result, "retcode", 0) or 0) == 10031
                or "no connection" in str(getattr(result, "comment", "") or "").lower()
                or "absence of network connection" in str(getattr(result, "comment", "") or "").lower()
            ):
                mark_broker_connection_backoff(
                    retcode=getattr(result, "retcode", None),
                    comment=getattr(result, "comment", ""),
                )
            log(f"  [ORDER_FAIL] {symbol} {mode} {signal} retcode={result.retcode} comment={result.comment} volume={lot} price={price}")
        return None
    except Exception as e:
        log(f"  [ORDER_EXCEPTION] {symbol} {mode} {signal} error={e}")
        return None

def close_position(ticket, exit_reason=None, exit_type="managed"):
    try:
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            log(f"  CLOSE_FAIL ticket={ticket} reason=position_missing")
            return False

        pos = positions[0]
        tick = mt5.symbol_info_tick(pos.symbol)
        if not tick:
            log(f"  CLOSE_FAIL ticket={ticket} symbol={pos.symbol} reason=no_tick")
            return False
        tick_stale, tick_age = is_tick_stale(tick)
        if tick_stale:
            log_stale_symbol(pos.symbol, f"close_position#{ticket}", tick_age)
            return False

        if pos.type == 0:
            price = tick.bid
            order_type = mt5.ORDER_TYPE_SELL
        else:
            price = tick.ask
            order_type = mt5.ORDER_TYPE_BUY

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": order_type,
            "price": price,
            "position": ticket,
            "deviation": 50,
            "magic": BOT_MAGIC,
            "comment": f"{BOT_COMMENT_PREFIX} Exit",
            "type_time": mt5.ORDER_TIME_GTC,
        }

        last_retcode = None
        last_comment = ""
        for filling_mode in (mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN):
            request["type_filling"] = filling_mode
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                pdata = active_positions.get(ticket)
                if pdata:
                    hold_sec = get_position_hold_seconds(pdata, pos)
                    emit_trade_behavior_record(
                        ticket,
                        pdata,
                        exit_reason or "CLOSE_POSITION",
                        exit_type,
                        realized_pnl=float(getattr(pos, "profit", 0.0) or 0.0),
                        hold_sec=hold_sec,
                    )
                return True
            if result:
                last_retcode = getattr(result, "retcode", None)
                last_comment = getattr(result, "comment", "") or ""
            else:
                last_retcode = "none"
                last_comment = "order_send returned None"
        log(
            f"  CLOSE_FAIL ticket={ticket} symbol={pos.symbol} "
            f"retcode={last_retcode} comment={last_comment or 'n/a'}"
        )
        return False
    except Exception as exc:
        log(f"  CLOSE_FAIL ticket={ticket} reason=exception error={exc}")
        return False

def close_position_partial(ticket, close_volume):
    """Close a portion of a position, leaving the rest open."""
    try:
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False

        pos = positions[0]
        if close_volume >= pos.volume:
            return close_position(ticket, exit_reason="PARTIAL_CLOSE_FULL", exit_type="managed")

        tick = mt5.symbol_info_tick(pos.symbol)
        if not tick:
            return False
        tick_stale, tick_age = is_tick_stale(tick)
        if tick_stale:
            log_stale_symbol(pos.symbol, f"close_position_partial#{ticket}", tick_age)
            return False

        if pos.type == 0:
            price = tick.bid
            order_type = mt5.ORDER_TYPE_SELL
        else:
            price = tick.ask
            order_type = mt5.ORDER_TYPE_BUY

        sym_info = mt5.symbol_info(pos.symbol)
        if sym_info:
            close_volume = max(sym_info.volume_min, min(close_volume, pos.volume - sym_info.volume_min))
            close_volume = round(close_volume / sym_info.volume_step) * sym_info.volume_step

        if close_volume <= 0:
            return False

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": close_volume,
            "type": order_type,
            "price": price,
            "position": ticket,
            "deviation": 50,
            "magic": BOT_MAGIC,
            "comment": f"{BOT_COMMENT_PREFIX} Partial",
            "type_time": mt5.ORDER_TIME_GTC,
        }

        for filling_mode in (mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN):
            request["type_filling"] = filling_mode
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"  PARTIAL CLOSE #{ticket}: closed {close_volume} lot, {pos.volume - close_volume:.2f} remaining")
                return True
        return False
    except Exception as e:
        log(f"  PARTIAL CLOSE error #{ticket}: {e}")
        return False

def manage_position(ticket, pdata, brain, live_position=None):
    """Manage a single position.
    
    Args:
        live_position: Optional pre-fetched position object from cycle-scoped snapshot.
            If provided, skips the individual positions_get(ticket=ticket) call.
    """
    try:
        if live_position is not None:
            pos = live_position
        else:
            positions = mt5.positions_get(ticket=ticket)
            if positions is None:
                return False  # API error, keep in memory and try again next loop
            if len(positions) == 0:
                log(f"  POSITION_MISSING #{ticket} {format_position_observability(pdata)} source=manage_position")
                active_positions.pop(ticket, None)
                return False
            pos = positions[0]
        mode = pdata['mode']
        mode_config = FIRE_MODES.get(mode, FIRE_MODES['MACHINE_GUN'])

        pnl = pos.profit
        pdata['last_pnl'] = pnl

        if pnl > pdata.get('peak_pnl', 0):
            pdata['peak_pnl'] = pnl
            pdata['peak_volume'] = pos.volume  # Track volume at peak

        hold_sec = get_position_hold_seconds(pdata, pos)

        exit_triggered = False
        exit_reason = ""

        # ATR-based exits (use stored ATR from entry)
        entry_atr = pdata.get('atr', 0)
        lot = pos.volume

        # Estimate dollar value of 1 ATR move for this position
        try:
            sym_info = mt5.symbol_info(pdata['symbol'])
            if sym_info and sym_info.trade_tick_value > 0 and sym_info.trade_tick_size > 0:
                atr_ticks = entry_atr / sym_info.trade_tick_size
                atr_dollar_value = atr_ticks * sym_info.trade_tick_value * lot
            else:
                atr_dollar_value = entry_atr * lot * 100000  # fallback
        except:
            atr_dollar_value = entry_atr * lot * 100000

        update_trade_behavior_metrics(pdata, pnl, hold_sec, atr_dollar_value)

        # === MEAN-REVERSION EXITS (tighter, faster) ===
        is_mr = pdata.get('mean_reversion', False)
        if is_mr:
            # MR trades: take profit at 1.5 ATR (bounces don't run far)
            if pnl >= atr_dollar_value * 1.5:
                exit_triggered = True
                exit_reason = f"MR_TP (pnl=${pnl:+.2f}, 1.5 ATR bounce captured)"
            # Fast time exit: MR trades should resolve quickly
            if not exit_triggered and hold_sec > 300 and pnl < 0:
                exit_triggered = True
                exit_reason = f"MR_TIMEOUT ({int(hold_sec)}s, losing bounce)"
            # Trail very tight on MR winners
            if not exit_triggered and pdata['peak_pnl'] > atr_dollar_value * 0.8:
                peak_volume = pdata.get('peak_volume', lot)
                volume_ratio = lot / peak_volume if peak_volume > 0 else 1.0
                scaled_peak = pdata['peak_pnl'] * volume_ratio
                if pnl < scaled_peak * 0.50:
                    exit_triggered = True
                    exit_reason = f"MR_TRAIL (peak ${pdata['peak_pnl']:+.2f}, vol_ratio={volume_ratio:.2f}, now ${pnl:+.2f})"

        # Fresh-entry fail-fast: keep throughput available, but reclaim capital
        # quickly when a new trade never behaves like a winner.
        is_adopted = pdata.get('adopted', False)
        entry_regime = str(pdata.get('entry_regime', '') or '').upper()
        entry_signal_type = str(pdata.get('entry_signal_type', '') or '').lower()
        entry_confidence = float(pdata.get('confidence', 0.0) or 0.0)
        early_fail_loss = max(EARLY_FAIL_DOLLAR_FLOOR, atr_dollar_value * EARLY_FAIL_ATR_LOSS_MULT)
        early_fail_hold_sec = EARLY_FAIL_MIN_HOLD_SECONDS
        hard_stop_sec = EARLY_FAIL_HARD_STOP_SECONDS
        strategy_lab_lane_config = get_active_strategy_lab_lane_config(
            pdata.get('symbol'),
            pdata.get('entry_signal_type'),
            mode,
            pdata.get('entry_regime'),
        ) or {}
        early_fail_override = strategy_lab_lane_config.get('early_fail_dollar_floor_override')
        if early_fail_override is not None:
            early_fail_loss = max(early_fail_loss, float(early_fail_override))
        if entry_regime == 'RAW':
            if entry_signal_type == 'candle_direction':
                early_fail_loss = min(
                    early_fail_loss,
                    max(EARLY_FAIL_DOLLAR_FLOOR, atr_dollar_value * RAW_CANDLE_DIRECTION_EARLY_FAIL_ATR_LOSS_MULT),
                )
                early_fail_hold_sec = min(early_fail_hold_sec, RAW_CANDLE_DIRECTION_EARLY_FAIL_MIN_HOLD_SECONDS)
                hard_stop_sec = min(hard_stop_sec, RAW_CANDLE_DIRECTION_EARLY_FAIL_HARD_STOP_SECONDS)
            elif entry_signal_type == 'trend_continuation' and entry_confidence <= 0.70:
                early_fail_loss = min(
                    early_fail_loss,
                    max(EARLY_FAIL_DOLLAR_FLOOR, atr_dollar_value * RAW_WEAK_TREND_EARLY_FAIL_ATR_LOSS_MULT),
                )
                early_fail_hold_sec = min(early_fail_hold_sec, RAW_WEAK_TREND_EARLY_FAIL_MIN_HOLD_SECONDS)
                hard_stop_sec = min(hard_stop_sec, RAW_WEAK_TREND_EARLY_FAIL_HARD_STOP_SECONDS)
        if mode == 'GEMINI':
            early_fail_loss *= 2.5
            early_fail_hold_sec *= 3.0
            
        if not exit_triggered and not is_adopted and hold_sec >= early_fail_hold_sec:
            if pdata.get('peak_pnl', 0.0) <= 0 and pnl <= -early_fail_loss:
                exit_triggered = True
                exit_reason = (
                    f"EARLY_FAIL ({int(hold_sec)}s, pnl=${pnl:+.2f}, "
                    f"peak=${pdata.get('peak_pnl', 0.0):+.2f}, {mode}"
                    f"{':' + entry_signal_type if entry_signal_type else ''})"
                )
        peak_gate_hold_seconds = strategy_lab_lane_config.get('peak_gate_hold_seconds')
        peak_gate_min_peak_usd = strategy_lab_lane_config.get('peak_gate_min_peak_usd')
        if (
            not exit_triggered
            and not is_adopted
            and peak_gate_hold_seconds is not None
            and peak_gate_min_peak_usd is not None
            and hold_sec >= float(peak_gate_hold_seconds)
            and float(pdata.get('peak_pnl', 0.0) or 0.0) < float(peak_gate_min_peak_usd)
            and pnl <= 0
        ):
            exit_triggered = True
            exit_reason = (
                f"PEAK_GATE ({int(hold_sec)}s, peak=${pdata.get('peak_pnl', 0.0):+.2f}, "
                f"min_peak=${float(peak_gate_min_peak_usd):+.2f}, pnl=${pnl:+.2f}, {mode})"
            )
        if mode == 'GEMINI':
            hard_stop_sec *= 3.0
            
        if not exit_triggered and not is_adopted and hold_sec >= hard_stop_sec and pnl < 0:
            htf_bias, _ = get_htf_bias(pdata['symbol'])
            if htf_bias != pdata['direction']:
                exit_triggered = True
                exit_reason = (
                    f"EARLY_FAIL_HTF ({int(hold_sec)}s, pnl=${pnl:+.2f}, "
                    f"bias={htf_bias or 'NONE'}, {mode})"
                )

        # === COMPETITION MODE: LET WINNERS RUN ===

        # Use the lane-specific cap when present; otherwise fall back to the
        # global disaster-stop.
        mode_loss_cap_map = MODE_ADVERSE_DOLLAR_CAP.get(mode, {})
        single_trade_loss_cap = float(
            mode_loss_cap_map.get(
                pdata['symbol'],
                mode_loss_cap_map.get('DEFAULT', MAX_SINGLE_TRADE_LOSS_USD),
            )
        )

        # === HARD LOSS CAP — force-close any position exceeding its dollar cap ===
        if pnl <= -single_trade_loss_cap:
            exit_triggered = True
            exit_reason = f"HARD_LOSS_CAP (pnl=${pnl:+.2f}, cap=${single_trade_loss_cap:.2f}, {mode})"

        if not exit_triggered:
            stall_reason = get_strategy_lab_stall_exit_reason(pdata, mode, hold_sec)
            if stall_reason:
                exit_triggered = True
                exit_reason = stall_reason

        # 0. Partial close: lock in gains when profit > 5.0 ATR
        #    Another agent: Delayed partials to 5.0 ATR and reduced to 20% to keep size on for 10x compounding.
        if pnl > atr_dollar_value * 5.0 and not pdata.get('partial_closed', False):
            partial_closed = close_position_partial(ticket, 0.20)
            if partial_closed:
                pdata['partial_closed'] = True
                log(f"  PARTIAL [{mode}] {pdata['symbol']} #{ticket} closed 20% at ${pnl:+.2f} (>5 ATR)")

        # 1. Trailing stop: keep higher-ATR behavior stable, add only micro-ATR protection.
        #    Audit note: current telemetry shows no realized SNIPER trades that reached
        #    >=1 ATR MFE and still finished red, so broad loosening above 1 ATR is not
        #    supported yet. The useful gap is sub-0.5 ATR green-to-red leakage.
        if pdata['peak_pnl'] > 0:
            peak_volume = pdata.get('peak_volume', lot)
            volume_ratio = lot / peak_volume if peak_volume > 0 else 1.0
            scaled_peak = pdata['peak_pnl'] * volume_ratio  # Peak PNL scaled to current volume
            trail_variant = "baseline"

            if scaled_peak > atr_dollar_value * 4.0:
                trail_threshold = scaled_peak * 0.20  # Trail at 20% of peak for massive runners
            elif scaled_peak > atr_dollar_value * 2.0:
                trail_threshold = scaled_peak * 0.40  # Trail at 40% of peak
            elif scaled_peak > atr_dollar_value * 1.0:
                trail_threshold = scaled_peak * 0.50  # Trail at 50% of peak for small winners
            elif scaled_peak > atr_dollar_value * 0.5:
                trail_threshold = scaled_peak * 0.65  # Loosened from 80% to allow breathing (MEETING-20260409)
            elif scaled_peak > atr_dollar_value * 0.2:
                trail_threshold = scaled_peak * 0.75  # Loosened from 90%
            elif scaled_peak > atr_dollar_value * 0.05:
                trail_threshold = scaled_peak * 0.85  # Loosened from 95% to avoid spread-exit on noise
            else:
                trail_threshold = None  # Don't trail absolute noise (sub-0.05 ATR = spread-level)

            strategy_lab_floor = get_strategy_lab_trail_floor(pdata, mode, scaled_peak, hold_sec)
            if strategy_lab_floor is not None and (
                trail_threshold is None or strategy_lab_floor > trail_threshold
            ):
                trail_threshold = strategy_lab_floor
                trail_variant = get_strategy_lab_variant_label(
                    pdata.get('symbol'),
                    pdata.get('entry_signal_type'),
                    mode,
                    pdata.get('entry_regime'),
                ) or "strategy_lab_trail"

            if trail_threshold is not None and pnl < trail_threshold:
                exit_triggered = True
                if trail_variant == "baseline":
                    exit_reason = (
                        f"TRAIL (peak ${pdata['peak_pnl']:+.2f}, "
                        f"vol_ratio={volume_ratio:.2f}, now ${pnl:+.2f}, {mode})"
                    )
                else:
                    emit_strategy_lab_event(
                        event_type="exit_challenger_triggered",
                        symbol=pdata.get('symbol'),
                        signal_type=pdata.get('entry_signal_type'),
                        mode=mode,
                        regime=pdata.get('entry_regime'),
                        confidence=pdata.get('confidence'),
                        trail_variant=trail_variant,
                        peak_pnl=round(float(pdata.get('peak_pnl', 0.0) or 0.0), 4),
                        scaled_peak=round(float(scaled_peak or 0.0), 4),
                        trail_threshold=round(float(trail_threshold or 0.0), 4),
                        pnl=round(float(pnl or 0.0), 4),
                    )
                    exit_reason = (
                        f"TRAIL_LAB[{trail_variant}] (peak ${pdata['peak_pnl']:+.2f}, "
                        f"threshold ${trail_threshold:+.2f}, vol_ratio={volume_ratio:.2f}, "
                        f"now ${pnl:+.2f}, {mode})"
                    )

        # 1b. Broker-side trailing SL — move SL to lock in profits
        #     This protects against bot crashes by moving the broker SL
        #     When peak > 0.5 ATR: move SL to breakeven
        #     When peak > 1.0 ATR: trail SL at 0.5 ATR behind best price
        if not exit_triggered and pdata['peak_pnl'] > atr_dollar_value * 0.5 and not pdata.get('adopted', False):
            last_trail = pdata.get('last_trail_pnl', 0)
            # Only move SL if peak improved since last trail (avoid spamming broker)
            if pdata['peak_pnl'] > last_trail + atr_dollar_value * 0.15:
                sym_info = mt5.symbol_info(pdata['symbol'])
                if sym_info and sym_info.trade_stops_level > 0:
                    tick = mt5.symbol_info_tick(pdata['symbol'])
                    if tick:
                        stops_level_price = sym_info.trade_stops_level * sym_info.trade_tick_size
                        entry = pdata.get('entry_price', 0)
                        if pdata['direction'] == 'BUY':
                            new_sl = tick.bid - max(stops_level_price, atr_dollar_value * 0.3 / (lot * sym_info.trade_tick_value / sym_info.trade_tick_size))
                            new_sl = max(new_sl, entry)  # At least breakeven
                            new_sl = round(new_sl, sym_info.digits)
                            if new_sl > pdata.get('current_sl', 0) + sym_info.point:
                                req = {"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": new_sl, "tp": pdata.get('current_tp', 0)}
                                res = mt5.order_send(req)
                                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                    pdata['current_sl'] = new_sl
                                    pdata['last_trail_pnl'] = pdata['peak_pnl']
                                    log(f"  [TRAIL_SL] {pdata['symbol']} #{ticket} SL moved to {new_sl} (peak ${pdata['peak_pnl']:+.2f})")
                        else:  # SELL
                            new_sl = tick.ask + max(stops_level_price, atr_dollar_value * 0.3 / (lot * sym_info.trade_tick_value / sym_info.trade_tick_size))
                            new_sl = min(new_sl, entry)  # At least breakeven
                            new_sl = round(new_sl, sym_info.digits)
                            if pdata.get('current_sl', 0) == 0 or new_sl < pdata['current_sl'] - sym_info.point:
                                req = {"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": new_sl, "tp": pdata.get('current_tp', 0)}
                                res = mt5.order_send(req)
                                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                    pdata['current_sl'] = new_sl
                                    pdata['last_trail_pnl'] = pdata['peak_pnl']
                                    log(f"  [TRAIL_SL] {pdata['symbol']} #{ticket} SL moved to {new_sl} (peak ${pdata['peak_pnl']:+.2f})")

        # 2. Time exit: LONG holds for competition
        #    SNIPER: 30 min, SHOTGUN: 20 min, MACHINE_GUN: 12 min
        #    But NEVER time-exit a profitable position
        max_hold = {'SNIPER': 1800, 'SHOTGUN': 1200, 'MACHINE_GUN': 720}
        if not exit_triggered and not is_adopted and hold_sec > max_hold.get(mode, 900):
            # Dynamic Time Exit: only if meaningfully negative (loss > 0.5 ATR)
            # Prevent instantly killing a trade that is just down pennies.
            loss_threshold = max(0.50, atr_dollar_value * 0.5)
            if pnl <= -loss_threshold:
                # ONLY EXIT if the HTF trend is no longer actively supporting us
                htf_bias, _ = get_htf_bias(pdata['symbol'])
                if htf_bias != pdata['direction']:
                    exit_triggered = True
                    exit_reason = f"TIME_FLAT ({int(hold_sec)}s, pnl=${pnl:+.2f}, >0.5 ATR loss, {mode})"

        # 3. Signal reversal: only exit if losing
        #    If profitable and M15 flips, just tighten the trail instead
        if not exit_triggered and hold_sec > 120:
            htf_bias, _ = get_htf_bias(pdata['symbol'])
            if htf_bias and htf_bias != pdata['direction']:
                if pnl <= 0:
                    exit_triggered = True
                    exit_reason = f"REVERSAL (M15 flipped to {htf_bias}, losing, {mode})"
                elif pdata['peak_pnl'] > 0:
                    # Tighten trail to 60% of peak if M15 reverses while profitable
                    peak_volume = pdata.get('peak_volume', lot)
                    volume_ratio = lot / peak_volume if peak_volume > 0 else 1.0
                    scaled_peak = pdata['peak_pnl'] * volume_ratio
                    tight_trail = scaled_peak * 0.60
                    if pnl < tight_trail:
                        exit_triggered = True
                        exit_reason = f"TIGHT_TRAIL (M15 reversed, peak ${pdata['peak_pnl']:+.2f}, vol_ratio={volume_ratio:.2f}, {mode})"

        if exit_triggered:
            if close_position(ticket, exit_reason=exit_reason, exit_type="managed"):
                # Classify failure reason for brain learning
                failure_reason = None
                if pnl <= 0:
                    er_upper = exit_reason.upper()
                    if "REVERSAL" in er_upper or "TIGHT_TRAIL" in er_upper:
                        failure_reason = "WRONG_DIRECTION"
                    elif "EARLY_FAIL" in er_upper:
                        failure_reason = "SPREAD_KILL"
                    elif "TIME_FLAT" in er_upper or "TIMEOUT" in er_upper or "MR_TIMEOUT" in er_upper:
                        failure_reason = "WHIPSAW"
                    elif "TRAIL" in er_upper or "MR_TRAIL" in er_upper:
                        failure_reason = "STOP_TOO_TIGHT"
                    elif "SPREAD" in er_upper:
                        failure_reason = "SPREAD_KILL"
                    else:
                        failure_reason = "WRONG_DIRECTION"

                brain.record_exit(pdata['symbol'], pnl, mode, hold_sec, failure_reason=failure_reason)
                # Record outcome with symbol learner for adaptive parameter tuning
                learner = get_learner()
                learner.record_outcome(pdata['symbol'], pnl, mode, {"failure_reason": failure_reason} if failure_reason else {})
                brain.save()
                if exit_reason.startswith("REVERSAL"):
                    triggered_cluster = register_risk_event()
                    if triggered_cluster:
                        remaining = int(max(0, alleyway_state.get('cluster_cooldown_until', 0) - time.time()))
                        log(f"  CLUSTER_COOLDOWN armed for {remaining}s after repeated trims/reversals")
                log(f"  EXIT [{exit_reason}] {pdata['symbol']} #{ticket} P/L=${pnl:+.2f}")
                active_positions.pop(ticket, None)
                exit_tag = exit_reason.split(' ', 1)[0]
                if not is_adopted and count_direct_positions() == 0:
                    flat_trigger_prefix = "MANAGED_FLAT_WIN_EXIT" if pnl > 0 else "MANAGED_FLAT_EXIT"
                    arm_post_cleanup_flat_rearm_holdoff(
                        time.time(),
                        format_competition_lane_trigger(flat_trigger_prefix, pdata, pdata['symbol'], exit_tag),
                        pnl,
                    )
                if pnl <= 0:
                    arm_one_position_quiet_rearm_holdoff(
                        time.time(),
                        format_competition_lane_trigger("MANAGED_EXIT", pdata, pdata['symbol'], exit_tag),
                        pnl,
                    )
                return True
            log(
                f"  EXIT_CLOSE_FAILED reason={exit_reason} "
                f"{format_position_observability(pdata)} hold={int(hold_sec)}s"
            )

        return False
    except:
        return False

def check_pyramid_opportunities(brain, equity):
    """Add to winning positions that are moving in our favor"""
    if not PYRAMID_ENABLED:
        return

    if alleyway_state.get('entry_posture') == 'DEFEND':
        return

    acct = mt5.account_info()
    if acct and getattr(acct, 'equity', 0):
        try:
            free_margin_ratio = float(acct.margin_free) / float(acct.equity)
        except Exception:
            free_margin_ratio = 0.0
    else:
        free_margin_ratio = 0.0

    if defend_no_expansion_active(free_margin_ratio):
        return

    for ticket, pdata in list(active_positions.items()):
        try:
            pnl = pdata.get('last_pnl', 0)
            atr = pdata.get('atr', 0)
            if atr <= 0 or pnl <= 0:
                continue

            # Recovery rule: inherited exposure can be managed out, but it should not expand.
            if pdata.get('adopted'):
                continue

            # Check how many pyramid adds this position has
            pyramid_count = pdata.get('pyramid_count', 0)
            if pyramid_count >= PYRAMID_MAX_ADDS:
                continue

            # Check absolute position limits
            if len(active_positions) >= MAX_CONCURRENT_POSITIONS:
                continue
            symbol_positions = [t for t, p in active_positions.items() if p['symbol'] == pdata['symbol']]
            if len(symbol_positions) >= MAX_POSITIONS_PER_SYMBOL:
                continue

            # Calculate ATR dollar value
            symbol = pdata['symbol']
            mode = pdata['mode']
            lot = pdata.get('volume', 0.01)
            stress = get_symbol_stress(symbol)

            if SYMBOL_ALLOWLIST and symbol not in SYMBOL_ALLOWLIST:
                continue

            # Do not keep pyramiding a symbol that already dominates the book.
            if stress["position_ratio"] >= 0.80 or stress["volume_share"] >= 0.35:
                continue

            try:
                sym_info = mt5.symbol_info(symbol)
                if sym_info and sym_info.trade_tick_value > 0 and sym_info.trade_tick_size > 0:
                    atr_ticks = atr / sym_info.trade_tick_size
                    atr_dollar = atr_ticks * sym_info.trade_tick_value * lot
                else:
                    continue
            except:
                continue

            # Only pyramid when profit exceeds threshold (ATR + USD double gate)
            if pnl < atr_dollar * PYRAMID_MIN_PROFIT_ATR:
                continue
            
            # NEW: Also require minimum USD profit
            if pnl < PYRAMID_MIN_PROFIT_USD:
                continue

            # Check if price has moved enough since last pyramid
            last_pyramid_pnl = pdata.get('last_pyramid_pnl', 0)
            if pnl < last_pyramid_pnl + atr_dollar * 0.3:
                continue

            # Calculate pyramid lot (decaying)
            pyramid_lot = lot * (PYRAMID_LOT_DECAY ** (pyramid_count + 1))
            pyramid_lot = max(0.01, round(pyramid_lot, 2))

            # Open pyramid position
            new_ticket = try_open_position(symbol, pdata['direction'], pyramid_lot, mode, 0.99, atr)

            if new_ticket:
                active_positions[new_ticket] = {
                    'ticket': int(new_ticket),
                    'symbol': symbol,
                    'direction': pdata['direction'],
                    'entry_price': 0,  # will be filled by MT5
                    'entry_time': time.time(),
                    'peak_pnl': 0.0,
                    'peak_volume': 0.0,  # Track volume at peak
                    'mode': mode,
                    'confidence': 0.99,
                    'last_pnl': 0.0,
                    'atr': atr,
                    'volume': pyramid_lot,
                    'adopted': False,
                    'is_pyramid': True,
                    'parent_ticket': ticket,
                    'pyramid_count': 0  # pyramids don't pyramid
                }
                # Update parent
                pdata['pyramid_count'] = pyramid_count + 1
                pdata['last_pyramid_pnl'] = pnl
                log(f"  PYRAMID [{mode}] {pdata['direction']} {symbol} +{pyramid_lot}lot (add #{pyramid_count+1}, parent P/L=${pnl:+.2f})")

        except:
            pass
