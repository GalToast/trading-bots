from __future__ import annotations

from datetime import datetime, timezone


def get_effective_rearm_limits(
    *,
    rearm_max_direct_positions,
    rearm_max_non_reversion_direct,
    rearm_max_losing_direct_positions,
):
    canonical_direct_cap = 3
    canonical_non_reversion_cap = 1
    canonical_losing_cap = 1
    return (
        min(rearm_max_direct_positions, canonical_direct_cap),
        min(rearm_max_non_reversion_direct, canonical_non_reversion_cap),
        min(rearm_max_losing_direct_positions, canonical_losing_cap),
    )


def get_post_cleanup_quality_gate(
    *,
    alleyway_state,
    now,
    direct_position_count,
    get_active_post_cleanup_holdoff,
):
    if direct_position_count != 0:
        alleyway_state["post_cleanup_quality_gate_pending"] = False
        alleyway_state["post_cleanup_quality_gate_trigger"] = ""
        return False, ""
    remaining, _ = get_active_post_cleanup_holdoff(now)
    if remaining > 0:
        return False, ""
    if not alleyway_state.get("post_cleanup_quality_gate_pending", False):
        return False, ""
    trigger = alleyway_state.get("post_cleanup_quality_gate_trigger", "unknown")
    return True, trigger


def is_one_pos_exotic_mercy_trigger(trigger):
    return str(trigger or "").startswith("ONE_POS_EXOTIC_MERCY_EXIT:")


def consume_post_cleanup_quality_gate(*, alleyway_state):
    alleyway_state["post_cleanup_quality_gate_pending"] = False
    alleyway_state["post_cleanup_quality_gate_trigger"] = ""


def arm_post_cleanup_flat_rearm_holdoff(
    *,
    alleyway_state,
    now,
    trigger,
    pnl,
    direct_position_count,
    holdoff_seconds,
    log,
    flush_runtime_state_snapshot,
):
    if direct_position_count != 0:
        return False

    hold_until = now + holdoff_seconds
    current = float(alleyway_state.get("post_cleanup_flat_rearm_hold_until", 0.0) or 0.0)
    if hold_until <= current:
        return False

    alleyway_state["post_cleanup_flat_rearm_hold_until"] = hold_until
    alleyway_state["post_cleanup_flat_rearm_trigger"] = trigger
    alleyway_state["post_cleanup_flat_rearm_armed_at"] = datetime.now(timezone.utc).isoformat()
    alleyway_state["post_cleanup_flat_rearm_last_pnl"] = float(pnl or 0.0)
    alleyway_state["post_cleanup_quality_gate_pending"] = True
    alleyway_state["post_cleanup_quality_gate_trigger"] = trigger
    alleyway_state["post_cleanup_quality_gate_armed_at"] = datetime.now(timezone.utc).isoformat()
    alleyway_state["rearm_cycles_remaining"] = 0
    alleyway_state["rearm_active"] = False
    alleyway_state["entry_posture"] = "DEFEND"
    log(
        f"  POST_CLEANUP_HOLDOFF {holdoff_seconds}s "
        f"trigger={trigger} pnl=${pnl:+.2f}"
    )
    flush_runtime_state_snapshot()
    return True


def arm_post_cleanup_first_leg_rearm_holdoff(
    *,
    alleyway_state,
    now,
    trigger,
    symbol,
    mode,
    direct_position_count,
    holdoff_seconds,
    flush_runtime_state_snapshot,
    log,
):
    if direct_position_count != 1:
        return False

    hold_until = now + holdoff_seconds
    current = float(alleyway_state.get("post_cleanup_first_leg_rearm_hold_until", 0.0) or 0.0)
    if hold_until <= current:
        return False

    armed_trigger = str(trigger or "unknown")
    if symbol:
        armed_trigger = f"{armed_trigger}:{symbol}"
    if mode:
        armed_trigger = f"{armed_trigger}:{mode}"
    alleyway_state["post_cleanup_first_leg_rearm_hold_until"] = hold_until
    alleyway_state["post_cleanup_first_leg_rearm_trigger"] = armed_trigger
    alleyway_state["post_cleanup_first_leg_rearm_armed_at"] = datetime.now(timezone.utc).isoformat()
    alleyway_state["rearm_cycles_remaining"] = 0
    alleyway_state["rearm_active"] = False
    alleyway_state["entry_posture"] = "DEFEND"
    log(
        f"  POST_CLEANUP_FIRST_LEG_HOLDOFF {holdoff_seconds}s "
        f"trigger={armed_trigger}"
    )
    flush_runtime_state_snapshot()
    return True


def arm_one_position_quiet_rearm_holdoff(
    *,
    alleyway_state,
    now,
    trigger,
    pnl,
    direct_position_count,
    holdoff_seconds,
    log,
):
    if direct_position_count != 1:
        return False

    hold_until = now + holdoff_seconds
    current = float(alleyway_state.get("one_position_quiet_rearm_hold_until", 0.0) or 0.0)
    if hold_until <= current:
        return False

    alleyway_state["one_position_quiet_rearm_hold_until"] = hold_until
    alleyway_state["one_position_quiet_rearm_trigger"] = trigger
    alleyway_state["rearm_cycles_remaining"] = 0
    alleyway_state["rearm_active"] = False
    alleyway_state["entry_posture"] = "DEFEND"
    log(
        f"  ONE_POSITION_REARM_HOLDOFF {holdoff_seconds}s "
        f"trigger={trigger} pnl=${pnl:+.2f}"
    )
    return True


def log_rearm_transition(*, alleyway_state, log, previous_posture, previous_reason):
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


def update_entry_posture(
    *,
    alleyway_state,
    active_positions,
    book_stress,
    free_margin_ratio,
    now,
    get_effective_rearm_limits,
    get_active_post_cleanup_holdoff,
    log_rearm_transition,
    rearm_min_free_margin_ratio,
    rearm_max_managed_drawdown_pct,
    rearm_max_top_symbol_drawdown_pct,
    rearm_hysteresis_max_direct_positions,
    rearm_hysteresis_min_free_margin_ratio,
    rearm_hysteresis_max_managed_drawdown_pct,
    rearm_hysteresis_max_top_symbol_drawdown_pct,
    rearm_hysteresis_max_losing_direct_positions,
    one_position_rearm_min_green_pnl_usd,
    rearm_hold_cycles,
    rearm_quiet_cooldown_cycles,
    rearm_hysteresis_hold_cycles,
    adopted_book_defend_guard_max_free_margin_ratio,
    adopted_book_defend_guard_max_managed_drawdown_pct,
    adopted_book_defend_guard_max_top_symbol_drawdown_pct,
    adopted_book_defend_guard_min_adopted_positions,
):
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
        free_margin_ratio >= rearm_min_free_margin_ratio
        and book_stress["managed_drawdown_pct"] <= rearm_max_managed_drawdown_pct
        and book_stress["top_symbol_drawdown_pct"] <= rearm_max_top_symbol_drawdown_pct
        and book_stress["direct_positions"] <= effective_rearm_max_direct_positions
        and direct_non_reversion <= effective_rearm_max_non_reversion_direct
        and direct_losing_positions <= effective_rearm_max_losing_direct_positions
    )
    if nonflat_rearm_sanity_block:
        quiet_book = False
    rearm_hysteresis_eligible = (
        previous_posture == "REARM"
        and not flat_book
        and book_stress["direct_positions"] <= rearm_hysteresis_max_direct_positions
        and free_margin_ratio >= rearm_hysteresis_min_free_margin_ratio
        and book_stress["managed_drawdown_pct"] <= rearm_hysteresis_max_managed_drawdown_pct
        and book_stress["top_symbol_drawdown_pct"] <= rearm_hysteresis_max_top_symbol_drawdown_pct
        and direct_non_reversion <= effective_rearm_max_non_reversion_direct
        and direct_losing_positions <= rearm_hysteresis_max_losing_direct_positions
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

    one_position_guard_reason = ""
    alleyway_state["one_position_profit_ticket"] = 0
    alleyway_state["one_position_profit_hold_cycles"] = 0

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
        and lone_direct_pnl < one_position_rearm_min_green_pnl_usd
    ):
        one_position_guard_reason = (
            f"one-pos-contained symbol={lone_direct_symbol or 'UNKNOWN'} "
            f"pnl=${lone_direct_pnl:+.2f} "
            f"release=${one_position_rearm_min_green_pnl_usd:.2f}"
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

    adopted_book_guard_active = (
        not flat_book
        and book_stress["direct_positions"] == 0
        and book_stress["adopted_positions"] >= adopted_book_defend_guard_min_adopted_positions
        and (
            free_margin_ratio <= adopted_book_defend_guard_max_free_margin_ratio
            or book_stress["managed_drawdown_pct"] >= adopted_book_defend_guard_max_managed_drawdown_pct
            or book_stress["top_symbol_drawdown_pct"] >= adopted_book_defend_guard_max_top_symbol_drawdown_pct
        )
    )
    if adopted_book_guard_active:
        reason = (
            f"adopted-book-guard fm={free_margin_ratio:.2f} "
            f"dd={book_stress['managed_drawdown_pct']:.3f} "
            f"top={book_stress['top_symbol_drawdown_pct']:.3f} "
            f"adopted={book_stress['adopted_positions']}"
        )
        alleyway_state["rearm_cycles_remaining"] = 0
        alleyway_state["rearm_active"] = False
        alleyway_state["entry_posture"] = "DEFEND"
        alleyway_state["rearm_reason"] = reason
        alleyway_state["managed_drawdown_pct"] = book_stress["managed_drawdown_pct"]
        alleyway_state["top_symbol_drawdown_pct"] = book_stress["top_symbol_drawdown_pct"]
        alleyway_state["free_margin_ratio"] = free_margin_ratio
        alleyway_state["rearm_used_this_quiet"] = False
        log_rearm_transition(previous_posture, previous_reason)
        return False, reason

    if quiet_book:
        current = alleyway_state.get("rearm_cycles_remaining", 0)
        rearm_used = alleyway_state.get("rearm_used_this_quiet", False)
        if flat_book:
            alleyway_state["rearm_used_this_quiet"] = False
            if current <= 0:
                alleyway_state["rearm_cycles_remaining"] = rearm_hold_cycles
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
            current = alleyway_state.get("rearm_cycles_remaining", 0)
            rearm_used = alleyway_state.get("rearm_used_this_quiet", False)
            if rearm_used:
                competition_bypass = free_margin_ratio > 0.80
                cooldown = alleyway_state.get("rearm_quiet_cooldown", 0) + 1
                if competition_bypass or cooldown >= rearm_quiet_cooldown_cycles:
                    alleyway_state["rearm_used_this_quiet"] = False
                    alleyway_state["rearm_quiet_cooldown"] = 0
                    rearm_used = False
                else:
                    alleyway_state["rearm_quiet_cooldown"] = cooldown
            else:
                alleyway_state["rearm_quiet_cooldown"] = 0

            if current <= 0 and not rearm_used:
                alleyway_state["rearm_cycles_remaining"] = rearm_hold_cycles
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
            remaining = max(current, rearm_hysteresis_hold_cycles)
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
            alleyway_state["rearm_used_this_quiet"] = False
            reason = (
                one_position_guard_reason
                if one_position_guard_reason
                else (
                    f"guarded fm={free_margin_ratio:.2f} "
                    f"dd={book_stress['managed_drawdown_pct']:.3f} "
                    f"top={book_stress['top_symbol_drawdown_pct']:.3f}"
                )
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
