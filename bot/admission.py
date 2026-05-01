from __future__ import annotations

import time


def get_experimental_direct_counts(*, active_positions):
    experimental_direct_counts = {"PRICE": 0, "RAW": 0, "GEMINI": 0}
    for pdata in active_positions.values():
        regime_name = str(pdata.get("entry_regime") or "").upper()
        if not pdata.get("adopted") and regime_name in experimental_direct_counts:
            experimental_direct_counts[regime_name] += 1
    return experimental_direct_counts


def compute_cycle_entry_state(
    *,
    alleyway_state,
    active_positions,
    opportunities,
    rearm_active,
    rearm_profile,
    overlap_active,
    in_cooldown,
    free_margin_ratio,
    book_stress,
    now,
    get_active_post_cleanup_holdoff,
    get_post_cleanup_quality_gate,
    rearm_fair_seat_min_active,
    rearm_fair_seat_min_direct,
    rearm_fair_seat_dominant_direct,
    rearm_nonflat_entry_cycle_cap,
    flat_book_rebuild_max_entries,
    post_cleanup_quality_max_entries,
    rearm_max_managed_drawdown_pct,
    rearm_max_top_symbol_drawdown_pct,
    defend_experimental_continuation_max_active_positions,
    price_pass_confidence,
    fire_modes,
    get_experimental_candidate_sort_key,
):
    flat_book_rebuild = (
        rearm_active
        and book_stress["managed_positions"] == 0
        and book_stress["direct_positions"] == 0
    )

    max_entries_per_cycle = 10 if overlap_active else 8
    if rearm_active:
        max_entries_per_cycle += rearm_profile["extra_entry_slots"]
    if flat_book_rebuild:
        max_entries_per_cycle = min(max_entries_per_cycle, flat_book_rebuild_max_entries)
    if rearm_active and not flat_book_rebuild and book_stress["managed_positions"] > 0:
        max_entries_per_cycle = min(max_entries_per_cycle, rearm_nonflat_entry_cycle_cap)
    if in_cooldown:
        max_entries_per_cycle = 1

    post_cleanup_hold_remaining, post_cleanup_hold_trigger = get_active_post_cleanup_holdoff()
    post_cleanup_quality_gate_active, post_cleanup_quality_gate_trigger = get_post_cleanup_quality_gate()
    post_cleanup_entry_freeze_active = post_cleanup_hold_remaining > 0
    post_cleanup_first_leg_hold_active = (
        len(active_positions) <= 1
        and now < float(alleyway_state.get("post_cleanup_first_leg_rearm_hold_until", 0.0) or 0.0)
    )
    profit_capture_freeze_active = (
        len(active_positions) > 0
        and now < float(alleyway_state.get("profit_capture_entry_freeze_until", 0.0) or 0.0)
    )
    two_book_pending_freeze_active = (
        len(active_positions) <= 2
        and now < float(alleyway_state.get("defend_two_book_pending_entry_freeze_until", 0.0) or 0.0)
    )
    post_cleanup_experimental_relief_window = (
        free_margin_ratio >= 0.95
        and len(active_positions) <= 1
        and book_stress["managed_drawdown_pct"] <= rearm_max_managed_drawdown_pct
        and any(regime in {"PRICE", "RAW", "GEMINI"} for *_head, regime, _signal_type, _entry_context in opportunities)
    )
    if post_cleanup_entry_freeze_active and not post_cleanup_experimental_relief_window:
        max_entries_per_cycle = 0
    elif post_cleanup_quality_gate_active:
        max_entries_per_cycle = min(max_entries_per_cycle, post_cleanup_quality_max_entries)
    if post_cleanup_first_leg_hold_active and not post_cleanup_experimental_relief_window:
        max_entries_per_cycle = 0
    if profit_capture_freeze_active:
        max_entries_per_cycle = 0
    if two_book_pending_freeze_active:
        max_entries_per_cycle = 0

    experimental_direct_counts = get_experimental_direct_counts(active_positions=active_positions)
    reserved_rearm_experimental_regime = None
    if (
        rearm_active
        and alleyway_state.get("entry_posture") == "REARM"
        and book_stress["managed_positions"] > 0
        and len(active_positions) >= rearm_fair_seat_min_active
        and book_stress["direct_positions"] >= rearm_fair_seat_min_direct
    ):
        dominant_experimental_regimes = {
            regime_name
            for regime_name, count in experimental_direct_counts.items()
            if count >= rearm_fair_seat_dominant_direct
        }
        if dominant_experimental_regimes:
            reserve_candidates = []
            for item in opportunities:
                _symbol, _signal, reserve_confidence, reserve_mode, _atr, reserve_regime, _signal_type, _entry_context = item
                if reserve_regime not in experimental_direct_counts:
                    continue
                if experimental_direct_counts.get(reserve_regime, 0) > 0:
                    continue
                reserve_floor = (
                    price_pass_confidence
                    if reserve_regime == "PRICE"
                    else fire_modes[reserve_mode]["min_confidence"]
                )
                if reserve_confidence < reserve_floor:
                    continue
                reserve_candidates.append(
                    (
                        get_experimental_candidate_sort_key(item, book_stress),
                        reserve_regime,
                    )
                )
            if reserve_candidates:
                reserve_candidates.sort(key=lambda item: item[0], reverse=True)
                reserved_rearm_experimental_regime = reserve_candidates[0][1]

    return {
        "flat_book_rebuild": flat_book_rebuild,
        "max_entries_per_cycle": max_entries_per_cycle,
        "post_cleanup_hold_remaining": post_cleanup_hold_remaining,
        "post_cleanup_hold_trigger": post_cleanup_hold_trigger,
        "post_cleanup_quality_gate_active": post_cleanup_quality_gate_active,
        "post_cleanup_quality_gate_trigger": post_cleanup_quality_gate_trigger,
        "post_cleanup_entry_freeze_active": post_cleanup_entry_freeze_active,
        "post_cleanup_first_leg_hold_active": post_cleanup_first_leg_hold_active,
        "profit_capture_freeze_active": profit_capture_freeze_active,
        "two_book_pending_freeze_active": two_book_pending_freeze_active,
        "post_cleanup_experimental_relief_window": post_cleanup_experimental_relief_window,
        "experimental_direct_counts": experimental_direct_counts,
        "reserved_rearm_experimental_regime": reserved_rearm_experimental_regime,
    }


def compute_candidate_entry_state(
    *,
    alleyway_state,
    active_positions,
    regime_counts,
    mode_counts,
    symbol,
    signal,
    confidence,
    mode,
    regime,
    book_stress,
    free_margin_ratio,
    rearm_active,
    consecutive_wins,
    direct_losing_positions,
    direct_non_reversion,
    effective_rearm_max_non_reversion_direct,
    cycle_opened_symbols,
    cycle_has_actionable_experimental_pressure,
    reserved_rearm_experimental_regime,
    experimental_direct_counts,
    current_flat_book_rebuild,
    post_cleanup_entry_freeze_active,
    post_cleanup_first_leg_hold_active,
    post_cleanup_experimental_relief_window,
    two_book_pending_freeze_active,
    post_cleanup_quality_gate_active,
    post_cleanup_quality_gate_trigger,
    get_competition_lane_recent_stats,
    recovery_mode_dd_allowed,
    get_experimental_lane_floor_bump,
    loser_lane_nonflat_hard_block_active,
    loser_lane_defend_guard_active,
    defend_loaded_no_add_active,
    defend_no_expansion_active,
    is_one_pos_exotic_mercy_trigger,
    is_exotic,
    gemini_max_positions,
    defend_experimental_continuation_max_per_regime,
    defend_experimental_continuation_max_active_positions,
    rearm_experimental_continuation_max_per_regime,
    rearm_adopted_experimental_relief_max_active_positions,
    rearm_adopted_experimental_relief_min_free_margin_ratio,
    rearm_recovery_experimental_relief_min_confidence,
    rearm_max_top_symbol_drawdown_pct,
    defend_competition_experimental_max_active_positions,
    defend_competition_experimental_min_free_margin_ratio,
    defend_experimental_continuation_min_free_margin_ratio,
    defend_experimental_shape_relief_reasons,
    defend_experimental_shape_relief_max_age_seconds,
    rearm_max_managed_drawdown_pct,
    price_pass_confidence,
    fire_modes,
    loser_lane_nonflat_hard_block_min_confidence,
    loser_lane_nonflat_hard_block_confidence_buffer,
    defend_no_expansion_stress_min_positions,
    defend_cleanup_freeze_min_positions,
    defend_cleanup_freeze_max_free_margin_ratio,
    adpoted_position_cap_weight,
    max_concurrent_positions,
    defend_nonflat_block_non_reversion,
    defend_machine_gun_min_confidence,
    defend_nonflat_min_confidence,
    rearm_machine_gun_min_confidence,
    rearm_nonflat_min_confidence,
    rearm_nonflat_block_non_reversion,
    defend_reversion_rebuild_max_positions,
    defend_reversion_rebuild_min_free_margin_ratio,
    defend_reversion_rebuild_max_managed_drawdown_pct,
    defend_reversion_rebuild_max_top_symbol_drawdown_pct,
    defend_reversion_rebuild_max_losing_direct_positions,
    rearm_rebuild_cap_min_positions,
    rearm_rebuild_cap_mixed_book_block,
    rearm_rebuild_cap_min_free_margin_ratio,
    rearm_rebuild_cap_max_managed_drawdown_pct,
    rearm_rebuild_cap_max_top_symbol_drawdown_pct,
    defend_competition_experimental_total_cap,
    rearm_fair_seat_dominant_direct,
    raw_candle_direction_min_confidence,
    competition_lane_cluster_min_early_fails,
    competition_lane_cluster_brake_min_confidence,
    post_cleanup_quality_first_wave_sniper_only,
    post_cleanup_quality_blocked_symbols,
    post_cleanup_quality_block_exotics,
    post_cleanup_mercy_first_wave_block_exotics,
):
    current_active_count = len(active_positions)
    projected_active_count = current_active_count + 1
    current_direct_green_positions = sum(
        1
        for pdata in active_positions.values()
        if not pdata.get("adopted") and float(pdata.get("last_pnl", 0.0) or 0.0) > 0.0
    )
    current_raw_positions = regime_counts.get("RAW", 0)
    current_price_positions = regime_counts.get("PRICE", 0)
    current_gemini_positions = regime_counts.get("GEMINI", 0)
    current_experimental_regime_positions = (
        current_price_positions
        if regime == "PRICE"
        else (
            current_raw_positions
            if regime == "RAW"
            else (current_gemini_positions if regime == "GEMINI" else 0)
        )
    ) if regime in {"PRICE", "RAW", "GEMINI"} else 0
    current_experimental_mode_open = (
        (regime == "RAW" and current_raw_positions > 0)
        or (regime == "PRICE" and current_price_positions > 0)
        or (regime == "GEMINI" and current_gemini_positions > 0)
    )
    current_experimental_continuation_cap = defend_experimental_continuation_max_per_regime
    if (
        regime in {"PRICE", "RAW", "GEMINI"}
        and rearm_active
        and alleyway_state.get("entry_posture") == "REARM"
        and free_margin_ratio >= 0.70
        and current_active_count <= defend_experimental_continuation_max_active_positions
    ):
        current_experimental_continuation_cap = max(
            current_experimental_continuation_cap,
            rearm_experimental_continuation_max_per_regime,
        )

    current_post_cleanup_experimental_relief = (
        regime in {"PRICE", "RAW", "GEMINI"}
        and (post_cleanup_entry_freeze_active or post_cleanup_first_leg_hold_active)
        and post_cleanup_experimental_relief_window
        and current_active_count <= 1
        and not current_experimental_mode_open
        and (current_raw_positions + current_price_positions + current_gemini_positions) == 0
        and recovery_mode_dd_allowed(regime, book_stress["managed_drawdown_pct"])
    )
    current_rearm_fair_seat_block = (
        reserved_rearm_experimental_regime in {"PRICE", "RAW", "GEMINI"}
        and regime in {"PRICE", "RAW", "GEMINI"}
        and regime != reserved_rearm_experimental_regime
        and experimental_direct_counts.get(regime, 0) >= rearm_fair_seat_dominant_direct
    )
    current_legacy_experiment_priority_block = (
        cycle_has_actionable_experimental_pressure
        and regime not in {"PRICE", "RAW", "GEMINI"}
    )
    lane_recent_stats = (
        get_competition_lane_recent_stats(regime)
        if regime in {"PRICE", "RAW", "GEMINI"}
        else None
    )
    lane_cluster_brake_active = False
    current_first_direct_flat_shot = (
        book_stress["managed_positions"] == 0
        and current_active_count == 0
    )
    current_flat_book_rebuild = (
        rearm_active
        and current_first_direct_flat_shot
    )
    current_effective_active_count = (
        book_stress["direct_positions"]
        + book_stress["adopted_positions"] * adpoted_position_cap_weight
    )
    current_effective_active_count += max(
        0,
        current_active_count - book_stress["managed_positions"],
    )

    current_financed_shape_reason = str(
        alleyway_state.get("defend_financed_unwind_last_shape_reason") or ""
    )
    current_financed_shape_logged_at = float(
        alleyway_state.get("defend_financed_unwind_last_shape_logged_at", 0.0) or 0.0
    )
    defend_relief_direct_cap = 2
    defend_three_plus_direct_book = int(book_stress.get("direct_positions", 0) or 0) > defend_relief_direct_cap
    defend_relief_min_confidence = max(
        float(rearm_recovery_experimental_relief_min_confidence or 0.0),
        0.75,
    )
    defend_relief_quality_ok = (
        regime in {"PRICE", "RAW", "GEMINI"}
        and confidence >= defend_relief_min_confidence
    )
    current_small_defend_experimental_relief = (
        regime in {"PRICE", "RAW", "GEMINI"}
        and alleyway_state.get("entry_posture") == "DEFEND"
        and current_effective_active_count > 0
        and current_effective_active_count <= 4
        and not defend_three_plus_direct_book
        and defend_relief_quality_ok
        and free_margin_ratio >= defend_experimental_continuation_min_free_margin_ratio
        and recovery_mode_dd_allowed(regime, book_stress["managed_drawdown_pct"])
        and current_experimental_regime_positions < defend_experimental_continuation_max_per_regime
    )
    current_loaded_defend_experimental_relief = (
        regime in {"PRICE", "RAW", "GEMINI"}
        and alleyway_state.get("entry_posture") == "DEFEND"
        and current_effective_active_count > 0
        and current_effective_active_count <= defend_competition_experimental_max_active_positions
        and not defend_three_plus_direct_book
        and defend_relief_quality_ok
        and free_margin_ratio >= defend_competition_experimental_min_free_margin_ratio
        and book_stress["top_symbol_drawdown_pct"] <= rearm_max_top_symbol_drawdown_pct
        and recovery_mode_dd_allowed(regime, book_stress["managed_drawdown_pct"])
        and current_experimental_regime_positions < defend_experimental_continuation_max_per_regime
    )
    current_adopted_defend_experimental_relief = (
        regime in {"PRICE", "RAW", "GEMINI"}
        and alleyway_state.get("entry_posture") == "DEFEND"
        and current_effective_active_count > 0
        and current_effective_active_count <= defend_competition_experimental_max_active_positions
        and free_margin_ratio >= 0.30
        and book_stress["direct_positions"] == 0
        and book_stress["adopted_positions"] > 0
        and book_stress["top_symbol_drawdown_pct"] <= rearm_max_top_symbol_drawdown_pct
        and recovery_mode_dd_allowed(regime, book_stress["managed_drawdown_pct"])
        and current_experimental_regime_positions < defend_experimental_continuation_max_per_regime
    )
    current_experimental_shape_relief = (
        regime in {"PRICE", "RAW", "GEMINI"}
        and alleyway_state.get("entry_posture") == "DEFEND"
        and current_active_count > 0
        and not defend_three_plus_direct_book
        and defend_relief_quality_ok
        and free_margin_ratio >= defend_experimental_continuation_min_free_margin_ratio
        and recovery_mode_dd_allowed(regime, book_stress["managed_drawdown_pct"])
        and current_active_count <= defend_experimental_continuation_max_active_positions
        and current_experimental_regime_positions < defend_experimental_continuation_max_per_regime
        and current_financed_shape_reason in defend_experimental_shape_relief_reasons
        and (time.time() - current_financed_shape_logged_at) <= defend_experimental_shape_relief_max_age_seconds
    )
    current_defend_experimental_relief = (
        current_experimental_shape_relief
        or current_small_defend_experimental_relief
        or current_loaded_defend_experimental_relief
        or current_adopted_defend_experimental_relief
    )
    current_adopted_rearm_experimental_relief = (
        regime in {"PRICE", "RAW", "GEMINI"}
        and alleyway_state.get("entry_posture") == "REARM"
        and not current_flat_book_rebuild
        and current_active_count > 0
        and current_effective_active_count <= rearm_adopted_experimental_relief_max_active_positions
        and free_margin_ratio >= rearm_adopted_experimental_relief_min_free_margin_ratio
        and book_stress["direct_positions"] == 0
        and book_stress["adopted_positions"] > 0
        and confidence >= rearm_recovery_experimental_relief_min_confidence
        and book_stress["top_symbol_drawdown_pct"] <= rearm_max_top_symbol_drawdown_pct
        and recovery_mode_dd_allowed(regime, book_stress["managed_drawdown_pct"])
        and current_experimental_regime_positions < defend_experimental_continuation_max_per_regime
    )

    mode_config = fire_modes[mode]
    experimental_mode_floor = (
        price_pass_confidence if regime == "PRICE" else mode_config["min_confidence"]
    )
    lane_health_floor_bump = get_experimental_lane_floor_bump(
        regime,
        book_stress=book_stress,
        free_margin_ratio=free_margin_ratio,
    )
    if regime in {"PRICE", "RAW", "GEMINI"} and lane_health_floor_bump > 0.0:
        experimental_mode_floor = min(0.95, experimental_mode_floor + lane_health_floor_bump)
    current_loser_lane_nonflat_hard_block = (
        not current_flat_book_rebuild
        and loser_lane_nonflat_hard_block_active(
            regime,
            book_stress=book_stress,
            free_margin_ratio=free_margin_ratio,
        )
        and confidence < max(
            loser_lane_nonflat_hard_block_min_confidence,
            experimental_mode_floor + loser_lane_nonflat_hard_block_confidence_buffer,
        )
    )
    current_loser_lane_defend_guard = (
        not current_flat_book_rebuild
        and loser_lane_defend_guard_active(
            regime,
            book_stress=book_stress,
            free_margin_ratio=free_margin_ratio,
        )
    )
    if current_loser_lane_defend_guard:
        current_small_defend_experimental_relief = False
        current_loaded_defend_experimental_relief = False
        current_adopted_defend_experimental_relief = False
        current_experimental_shape_relief = False
        current_defend_experimental_relief = False
        current_onepos_experimental_relief = False
    if current_loser_lane_nonflat_hard_block:
        current_small_defend_experimental_relief = False
        current_loaded_defend_experimental_relief = False
        current_adopted_defend_experimental_relief = False
        current_experimental_shape_relief = False
        current_defend_experimental_relief = False
        current_onepos_experimental_relief = False
    current_defend_hard_freeze = (
        alleyway_state.get("entry_posture") == "DEFEND"
        and book_stress["managed_positions"] > 0
        and current_active_count >= defend_no_expansion_stress_min_positions
        and not current_defend_experimental_relief
    )
    current_defend_cleanup_freeze = (
        not current_flat_book_rebuild
        and alleyway_state.get("entry_posture") == "DEFEND"
        and book_stress["managed_positions"] > 0
        and current_active_count >= defend_cleanup_freeze_min_positions
        and free_margin_ratio <= defend_cleanup_freeze_max_free_margin_ratio
    )
    current_defend_loaded_no_add_active = defend_loaded_no_add_active(
        current_flat_book_rebuild=current_flat_book_rebuild,
        entry_posture=alleyway_state.get("entry_posture"),
        current_active_count=current_active_count,
        effective_active_count=current_effective_active_count,
        projected_active_count=projected_active_count,
        free_margin_ratio=free_margin_ratio,
        managed_drawdown_pct=book_stress["managed_drawdown_pct"],
        top_symbol_drawdown_pct=book_stress["top_symbol_drawdown_pct"],
        direct_positions=book_stress["direct_positions"],
        adopted_positions=book_stress["adopted_positions"],
        candidate_regime=regime,
        current_price_positions=current_price_positions,
        current_raw_positions=current_raw_positions,
        current_gemini_positions=current_gemini_positions,
    )
    current_onepos_experimental_relief = (
        regime in {"PRICE", "RAW", "GEMINI"}
        and alleyway_state.get("entry_posture") == "DEFEND"
        and not current_flat_book_rebuild
        and book_stress["managed_positions"] == 1
        and book_stress["direct_positions"] == 1
        and current_active_count == 1
        and free_margin_ratio >= 0.85
        and book_stress["top_symbol_drawdown_pct"] <= rearm_max_top_symbol_drawdown_pct
    )
    current_defend_onepos_no_add_active = (
        not current_flat_book_rebuild
        and alleyway_state.get("entry_posture") == "DEFEND"
        and book_stress["managed_positions"] == 1
        and current_active_count >= 1
        and book_stress["direct_positions"] == 1
        and direct_losing_positions >= 1
        and not current_onepos_experimental_relief
    )
    current_defend_noexp_active = (
        not current_flat_book_rebuild
        and defend_no_expansion_active(free_margin_ratio, current_active_count)
        and not current_defend_experimental_relief
    )
    current_defend_non_reversion_freeze = (
        defend_nonflat_block_non_reversion
        and alleyway_state.get("entry_posture") == "DEFEND"
        and book_stress["managed_positions"] > 0
        and mode != "REVERSION"
        and not current_defend_experimental_relief
        and (
            free_margin_ratio < 0.30
            or book_stress["managed_drawdown_pct"] > 0.08
            or book_stress["managed_positions"] >= 7
        )
    )
    current_rearm_non_reversion_freeze = (
        rearm_nonflat_block_non_reversion
        and not current_flat_book_rebuild
        and rearm_active
        and book_stress["managed_positions"] > 0
        and mode != "REVERSION"
        and direct_non_reversion >= effective_rearm_max_non_reversion_direct
    )
    current_flat_rebuild_non_reversion_freeze = (
        current_first_direct_flat_shot
        and mode not in {"SNIPER", "REVERSION", "PRICE", "MACHINE_GUN", "GEMINI"}
    )
    current_post_cleanup_quality_mode_block = (
        post_cleanup_quality_gate_active
        and current_flat_book_rebuild
        and post_cleanup_quality_first_wave_sniper_only
        and mode not in {"SNIPER", "PRICE", "MACHINE_GUN", "GEMINI"}
    )
    current_post_cleanup_mercy_rebuild = (
        post_cleanup_quality_gate_active
        and current_flat_book_rebuild
        and is_one_pos_exotic_mercy_trigger(post_cleanup_quality_gate_trigger)
    )
    current_post_cleanup_quality_symbol_block = (
        post_cleanup_quality_gate_active
        and current_flat_book_rebuild
        and symbol in post_cleanup_quality_blocked_symbols
    )
    current_post_cleanup_quality_exotic_block = (
        post_cleanup_quality_gate_active
        and current_flat_book_rebuild
        and post_cleanup_quality_block_exotics
        and is_exotic(symbol)
    )
    current_post_cleanup_mercy_symbol_block = (
        current_post_cleanup_mercy_rebuild
        and post_cleanup_mercy_first_wave_block_exotics
        and is_exotic(symbol)
    )
    current_offense_quality_floor = 0.0
    if not current_flat_book_rebuild:
        if alleyway_state.get("entry_posture") == "DEFEND" and book_stress["managed_positions"] > 0:
            if current_defend_experimental_relief and regime in {"PRICE", "RAW", "GEMINI"}:
                current_offense_quality_floor = experimental_mode_floor
            elif mode == "MACHINE_GUN":
                current_offense_quality_floor = defend_machine_gun_min_confidence
            elif mode != "REVERSION":
                current_offense_quality_floor = defend_nonflat_min_confidence
        elif rearm_active and book_stress["managed_positions"] > 0:
            if mode == "MACHINE_GUN":
                current_offense_quality_floor = rearm_machine_gun_min_confidence
            else:
                current_offense_quality_floor = rearm_nonflat_min_confidence
    current_defend_machine_gun_freeze = (
        current_defend_non_reversion_freeze
        and mode == "MACHINE_GUN"
    )
    current_defend_reversion_rebuild_block = (
        not current_flat_book_rebuild
        and mode == "REVERSION"
        and book_stress["managed_positions"] > 0
        and (
            alleyway_state.get("entry_posture") == "DEFEND"
            or rearm_active
        )
        and (
            current_active_count >= defend_reversion_rebuild_max_positions
            or free_margin_ratio < defend_reversion_rebuild_min_free_margin_ratio
            or book_stress["managed_drawdown_pct"] > defend_reversion_rebuild_max_managed_drawdown_pct
            or book_stress["top_symbol_drawdown_pct"] > defend_reversion_rebuild_max_top_symbol_drawdown_pct
            or direct_non_reversion >= 7
            or direct_losing_positions > defend_reversion_rebuild_max_losing_direct_positions
        )
    )
    current_rearm_rebuild_cap = (
        not current_flat_book_rebuild
        and rearm_active
        and book_stress["managed_positions"] > 0
        and current_active_count >= rearm_rebuild_cap_min_positions
        and (
            (
                rearm_rebuild_cap_mixed_book_block
                and (
                    mode_counts.get("MACHINE_GUN", 0) > 0
                    or mode_counts.get("SHOTGUN", 0) > 0
                    or mode_counts.get("SNIPER", 0) > 0
                )
            )
            or free_margin_ratio < rearm_rebuild_cap_min_free_margin_ratio
            or book_stress["managed_drawdown_pct"] > rearm_rebuild_cap_max_managed_drawdown_pct
            or book_stress["top_symbol_drawdown_pct"] > rearm_rebuild_cap_max_top_symbol_drawdown_pct
        )
    )
    current_rearm_unfinanced_no_add_active = (
        not current_flat_book_rebuild
        and rearm_active
        and alleyway_state.get("entry_posture") == "REARM"
        and book_stress["managed_positions"] > 0
        and book_stress["direct_positions"] > 0
        and current_direct_green_positions == 0
        and direct_losing_positions > 0
    )
    current_experimental_pair_slot = (
        regime in {"RAW", "PRICE", "GEMINI"}
        and (
            (
                rearm_active
                and alleyway_state.get("entry_posture") == "REARM"
                and free_margin_ratio >= 0.30
                and book_stress["managed_positions"] <= 30
            )
            or current_defend_experimental_relief
            or (
                alleyway_state.get("entry_posture") == "DEFEND"
                and not defend_three_plus_direct_book
                and defend_relief_quality_ok
                and not current_loser_lane_defend_guard
                and (
                    free_margin_ratio >= defend_competition_experimental_min_free_margin_ratio
                    or (
                        free_margin_ratio >= 0.30
                        and book_stress["direct_positions"] == 0
                        and book_stress["adopted_positions"] > 0
                    )
                )
                and current_effective_active_count <= defend_competition_experimental_max_active_positions
                and recovery_mode_dd_allowed(regime, book_stress["managed_drawdown_pct"])
            )
            or current_adopted_rearm_experimental_relief
        )
        and current_experimental_regime_positions < current_experimental_continuation_cap
        and (
            current_raw_positions + current_price_positions + current_gemini_positions
            < defend_competition_experimental_total_cap
        )
    )

    return {
        "current_active_count": current_active_count,
        "projected_active_count": projected_active_count,
        "current_direct_green_positions": current_direct_green_positions,
        "current_raw_positions": current_raw_positions,
        "current_price_positions": current_price_positions,
        "current_gemini_positions": current_gemini_positions,
        "current_experimental_regime_positions": current_experimental_regime_positions,
        "current_experimental_mode_open": current_experimental_mode_open,
        "current_experimental_continuation_cap": current_experimental_continuation_cap,
        "current_post_cleanup_experimental_relief": current_post_cleanup_experimental_relief,
        "current_rearm_fair_seat_block": current_rearm_fair_seat_block,
        "current_legacy_experiment_priority_block": current_legacy_experiment_priority_block,
        "lane_recent_stats": lane_recent_stats,
        "lane_cluster_brake_active": lane_cluster_brake_active,
        "raw_candle_direction_floor_block": regime == "RAW" and signal == signal and mode == mode and False,
        "current_first_direct_flat_shot": current_first_direct_flat_shot,
        "current_flat_book_rebuild": current_flat_book_rebuild,
        "current_effective_active_count": current_effective_active_count,
        "current_financed_shape_reason": current_financed_shape_reason,
        "current_financed_shape_logged_at": current_financed_shape_logged_at,
        "current_small_defend_experimental_relief": current_small_defend_experimental_relief,
        "current_loaded_defend_experimental_relief": current_loaded_defend_experimental_relief,
        "current_adopted_defend_experimental_relief": current_adopted_defend_experimental_relief,
        "current_experimental_shape_relief": current_experimental_shape_relief,
        "current_defend_experimental_relief": current_defend_experimental_relief,
        "current_adopted_rearm_experimental_relief": current_adopted_rearm_experimental_relief,
        "mode_config": mode_config,
        "experimental_mode_floor": experimental_mode_floor,
        "lane_health_floor_bump": lane_health_floor_bump,
        "current_loser_lane_nonflat_hard_block": current_loser_lane_nonflat_hard_block,
        "current_loser_lane_defend_guard": current_loser_lane_defend_guard,
        "current_defend_hard_freeze": current_defend_hard_freeze,
        "current_defend_cleanup_freeze": current_defend_cleanup_freeze,
        "current_defend_loaded_no_add_active": current_defend_loaded_no_add_active,
        "current_onepos_experimental_relief": current_onepos_experimental_relief,
        "current_defend_onepos_no_add_active": current_defend_onepos_no_add_active,
        "current_defend_noexp_active": current_defend_noexp_active,
        "current_defend_non_reversion_freeze": current_defend_non_reversion_freeze,
        "current_rearm_non_reversion_freeze": current_rearm_non_reversion_freeze,
        "current_flat_rebuild_non_reversion_freeze": current_flat_rebuild_non_reversion_freeze,
        "current_post_cleanup_quality_mode_block": current_post_cleanup_quality_mode_block,
        "current_post_cleanup_mercy_rebuild": current_post_cleanup_mercy_rebuild,
        "current_post_cleanup_quality_symbol_block": current_post_cleanup_quality_symbol_block,
        "current_post_cleanup_quality_exotic_block": current_post_cleanup_quality_exotic_block,
        "current_post_cleanup_mercy_symbol_block": current_post_cleanup_mercy_symbol_block,
        "current_offense_quality_floor": current_offense_quality_floor,
        "current_defend_machine_gun_freeze": current_defend_machine_gun_freeze,
        "current_defend_reversion_rebuild_block": current_defend_reversion_rebuild_block,
        "current_rearm_rebuild_cap": current_rearm_rebuild_cap,
        "current_rearm_unfinanced_no_add_active": current_rearm_unfinanced_no_add_active,
        "current_experimental_pair_slot": current_experimental_pair_slot,
        "gemini_position_cap_block": regime == "GEMINI" and current_gemini_positions >= gemini_max_positions,
        "gemini_margin_gate_block": regime == "GEMINI" and free_margin_ratio < 0.20,
        "machine_gun_entry_brake_block": mode == "MACHINE_GUN" and mode_counts.get("MACHINE_GUN", 0) >= 6 and consecutive_wins == 0,
        "post_cleanup_freeze_block": post_cleanup_entry_freeze_active and not current_post_cleanup_experimental_relief,
        "post_cleanup_first_leg_freeze_block": post_cleanup_first_leg_hold_active and not current_post_cleanup_experimental_relief,
        "two_book_pending_freeze_block": two_book_pending_freeze_active,
        "cycle_symbol_rearm_block": rearm_active and not current_flat_book_rebuild and symbol in cycle_opened_symbols,
        "cycle_symbol_experimental_block": regime in {"PRICE", "RAW", "GEMINI"} and symbol in cycle_opened_symbols,
        "max_concurrent_positions_block": current_effective_active_count >= max_concurrent_positions,
        "raw_candle_direction_floor_block": regime == "RAW" and confidence < raw_candle_direction_min_confidence,
    }


def compute_symbol_entry_state(
    *,
    active_positions,
    symbol,
    regime,
    confidence,
    mode,
    book_stress,
    free_margin_ratio,
    rearm_active,
    current_flat_book_rebuild,
    current_active_count,
    current_raw_positions,
    current_price_positions,
    current_gemini_positions,
    current_experimental_pair_slot,
    current_experimental_regime_positions,
    experimental_mode_floor,
    profit_buffer_available,
    get_symbol_stress,
    get_anchor_drag_state,
    get_experimental_lane_floor_bump,
    allow_experimental_cluster_improvement_relief,
    rearm_second_wave_experimental_cap_min_active,
    rearm_second_wave_experimental_cap_min_direct,
    anchor_drag_max_free_margin_ratio,
    anchor_drag_max_buffer_usd,
    anchor_drag_symbol_confidence_buffer,
    anchor_drag_global_confidence_buffer,
    rearm_recovery_experimental_relief_min_confidence,
    financed_crowd_block_min_active_positions,
    financed_crowd_block_max_free_margin_ratio,
    financed_crowd_block_max_buffer_usd,
    financed_crowd_block_min_symbol_positions,
    financed_crowd_block_min_stress_score,
    financed_crowd_block_min_drawdown_share,
    experimental_same_symbol_reentry_cooldown_seconds,
    price_same_symbol_continuation_cap,
    raw_trend_followon_book_min_raw_positions,
    raw_trend_followon_min_confidence,
):
    symbol_positions = [p for p in active_positions.values() if p["symbol"] == symbol]
    now = time.time()
    direct_regime_positions = sum(
        1
        for p in active_positions.values()
        if not p.get("adopted") and (p.get("entry_regime") or "").upper() == str(regime or "").upper()
    )
    current_rearm_second_wave_experimental_cap = (
        regime in {"RAW", "PRICE", "GEMINI"}
        and not current_flat_book_rebuild
        and rearm_active
        and current_active_count >= rearm_second_wave_experimental_cap_min_active
        and book_stress["direct_positions"] >= rearm_second_wave_experimental_cap_min_direct
        and direct_regime_positions >= 1
    )

    symbol_stress = get_symbol_stress(symbol)
    same_symbol_direct_positions = 0
    youngest_same_symbol_direct_age_seconds = None
    for pdata in symbol_positions:
        if pdata.get("adopted", False):
            continue
        same_symbol_direct_positions += 1
        entry_time = float(pdata.get("entry_time", now) or now)
        age_seconds = max(0.0, now - entry_time)
        if (
            youngest_same_symbol_direct_age_seconds is None
            or age_seconds < youngest_same_symbol_direct_age_seconds
        ):
            youngest_same_symbol_direct_age_seconds = age_seconds
    anchor_drag = get_anchor_drag_state()
    anchor_drag_active = (
        bool(anchor_drag.get("active"))
        and not current_flat_book_rebuild
        and free_margin_ratio <= anchor_drag_max_free_margin_ratio
        and profit_buffer_available <= anchor_drag_max_buffer_usd
    )
    anchor_symbol = str(anchor_drag.get("symbol") or "")
    anchor_lane_penalty = (
        get_experimental_lane_floor_bump(regime, book_stress, free_margin_ratio)
        if regime in {"PRICE", "RAW", "GEMINI"}
        else 0.0
    )
    anchor_symbol_relief = (
        anchor_drag_active
        and symbol == anchor_symbol
        and regime in {"PRICE", "RAW", "GEMINI"}
        and current_experimental_pair_slot
        and current_experimental_regime_positions == 0
        and same_symbol_direct_positions == 0
        and book_stress["direct_positions"] == 0
        and anchor_lane_penalty <= 0.0
        and confidence >= max(
            experimental_mode_floor + anchor_drag_symbol_confidence_buffer,
            rearm_recovery_experimental_relief_min_confidence,
        )
    )
    financed_crowd_relief = allow_experimental_cluster_improvement_relief(
        symbol_positions=symbol_positions,
        symbol_stress=symbol_stress,
        regime=regime,
        confidence=confidence,
        experimental_mode_floor=experimental_mode_floor,
        current_experimental_pair_slot=current_experimental_pair_slot,
        current_experimental_regime_positions=current_experimental_regime_positions,
        book_stress=book_stress,
        free_margin_ratio=free_margin_ratio,
        profit_buffer_available=profit_buffer_available,
    )
    financed_crowd_block_active = (
        not current_flat_book_rebuild
        and current_active_count >= financed_crowd_block_min_active_positions
        and free_margin_ratio <= financed_crowd_block_max_free_margin_ratio
        and profit_buffer_available <= financed_crowd_block_max_buffer_usd
        and len(symbol_positions) >= financed_crowd_block_min_symbol_positions
        and symbol_stress.get("all_losing")
        and (
            float(symbol_stress.get("score", 0.0) or 0.0) >= financed_crowd_block_min_stress_score
            or float(symbol_stress.get("drawdown_share", 0.0) or 0.0) >= financed_crowd_block_min_drawdown_share
        )
    )
    same_symbol_raw_positions = sum(
        1 for p in symbol_positions if (p.get("entry_regime") or "").upper() == "RAW"
    )
    same_symbol_price_positions = sum(
        1 for p in symbol_positions if (p.get("entry_regime") or "").upper() == "PRICE"
    )
    raw_trend_followon_same_symbol_block = (
        regime == "RAW" and same_symbol_raw_positions > 0
    )
    experimental_same_symbol_reentry_block = (
        regime in {"RAW", "PRICE", "GEMINI"}
        and same_symbol_direct_positions > 0
        and not current_flat_book_rebuild
        and youngest_same_symbol_direct_age_seconds is not None
        and youngest_same_symbol_direct_age_seconds < experimental_same_symbol_reentry_cooldown_seconds
    )
    raw_trend_followon_wave_block = (
        regime == "RAW"
        and rearm_active
        and not current_flat_book_rebuild
        and current_raw_positions >= raw_trend_followon_book_min_raw_positions
        and confidence < raw_trend_followon_min_confidence
    )
    price_same_symbol_cap_block = (
        regime == "PRICE"
        and same_symbol_price_positions >= price_same_symbol_continuation_cap
        and not current_flat_book_rebuild
    )
    symbol_total_lot = sum(p.get("volume", 0) for p in symbol_positions)

    return {
        "symbol_positions": symbol_positions,
        "direct_regime_positions": direct_regime_positions,
        "current_rearm_second_wave_experimental_cap": current_rearm_second_wave_experimental_cap,
        "symbol_stress": symbol_stress,
        "same_symbol_direct_positions": same_symbol_direct_positions,
        "anchor_drag": anchor_drag,
        "anchor_drag_active": anchor_drag_active,
        "anchor_symbol": anchor_symbol,
        "anchor_lane_penalty": anchor_lane_penalty,
        "anchor_symbol_relief": anchor_symbol_relief,
        "financed_crowd_relief": financed_crowd_relief,
        "financed_crowd_block_active": financed_crowd_block_active,
        "same_symbol_raw_positions": same_symbol_raw_positions,
        "same_symbol_price_positions": same_symbol_price_positions,
        "raw_trend_followon_same_symbol_block": raw_trend_followon_same_symbol_block,
        "youngest_same_symbol_direct_age_seconds": youngest_same_symbol_direct_age_seconds,
        "experimental_same_symbol_reentry_block": experimental_same_symbol_reentry_block,
        "raw_trend_followon_wave_block": raw_trend_followon_wave_block,
        "price_same_symbol_cap_block": price_same_symbol_cap_block,
        "symbol_total_lot": symbol_total_lot,
    }


def compute_late_entry_state(
    *,
    symbol,
    signal,
    confidence,
    mode,
    regime,
    atr,
    equity,
    rearm_active,
    free_margin_ratio,
    book_stress,
    rearm_profile,
    effective_adaptive_threshold,
    experimental_mode_floor,
    lane_health_floor_bump,
    current_flat_book_rebuild,
    current_post_cleanup_mercy_rebuild,
    post_cleanup_quality_gate_active,
    current_first_direct_flat_shot,
    brain,
    calc_equity_lot,
    get_symbol_stress,
    get_symbol_velocity_profile,
    is_exotic,
    price_pass_confidence,
    fire_modes,
    min_confidence_min,
    symbol_stress_confidence_bump_max,
    symbol_stress_extreme_drawdown_share,
    symbol_stress_extreme_score,
    low_velocity_entry_max_free_margin_ratio,
    low_velocity_entry_max_managed_positions,
    low_velocity_atr_pct_floor,
    low_velocity_price_exception_confidence,
    low_velocity_raw_exception_confidence,
    defensive_cross_fx_exception_confidence,
    defensive_exotic_fx_exception_confidence,
    defensive_core_major_fx_exception_confidence,
    defensive_core_major_fx_symbols,
    rearm_first_direct_confidence_bump,
    rearm_first_direct_min_confidence,
    post_cleanup_quality_confidence_bump,
    post_cleanup_quality_min_confidence,
    post_cleanup_mercy_confidence_bump,
    symbol_stress_lot_reduction_max,
    reversion_lot_scale,
):
    normalized_symbol = str(symbol or "").upper()
    is_fx_symbol = len(normalized_symbol) == 6 and normalized_symbol.isalpha()
    is_fx_cross_symbol = (
        is_fx_symbol
        and "USD" not in normalized_symbol
        and "JPY" not in normalized_symbol
    )
    is_defensive_core_major_fx_symbol = normalized_symbol in defensive_core_major_fx_symbols
    stress = get_symbol_stress(symbol)
    first_direct_rearm_shot = current_first_direct_flat_shot
    velocity_profile = get_symbol_velocity_profile(symbol, atr)

    fast_lane_candidate = regime in {"RAW", "GEMINI"} or mode in {"MACHINE_GUN", "NEVERLOSER"}
    low_velocity_exception_confidence = (
        low_velocity_price_exception_confidence
        if regime == "PRICE"
        else low_velocity_raw_exception_confidence
    )
    low_velocity_blocked = (
        not current_flat_book_rebuild
        and fast_lane_candidate
        and int((book_stress or {}).get("managed_positions", 0) or 0) > 0
        and int((book_stress or {}).get("managed_positions", 0) or 0) <= low_velocity_entry_max_managed_positions
        and float(free_margin_ratio or 0.0) <= low_velocity_entry_max_free_margin_ratio
        and confidence < low_velocity_exception_confidence
        and (
            velocity_profile["pegged_like"]
            or velocity_profile["atr_pct"] <= low_velocity_atr_pct_floor
        )
    )
    defensive_cross_fx_blocked = (
        regime in {"PRICE", "RAW", "GEMINI"}
        and not current_flat_book_rebuild
        and int((book_stress or {}).get("managed_positions", 0) or 0) > 0
        and is_fx_cross_symbol
        and confidence < defensive_cross_fx_exception_confidence
    )
    defensive_exotic_fx_blocked = (
        regime in {"PRICE", "RAW", "GEMINI"}
        and not current_flat_book_rebuild
        and int((book_stress or {}).get("managed_positions", 0) or 0) > 0
        and is_fx_symbol
        and (is_exotic(symbol) or velocity_profile["pegged_like"])
        and confidence < defensive_exotic_fx_exception_confidence
    )
    defensive_core_major_fx_blocked = (
        regime in {"PRICE", "RAW", "GEMINI"}
        and not current_flat_book_rebuild
        and int((book_stress or {}).get("managed_positions", 0) or 0) > 0
        and is_defensive_core_major_fx_symbol
        and confidence < defensive_core_major_fx_exception_confidence
    )

    base_lot = calc_equity_lot(symbol, mode, atr, equity)
    entry_params = brain.get_entry_params(symbol, effective_adaptive_threshold, base_lot)
    if not entry_params["allowed"]:
        return {
            "brain_blocked": True,
            "block_reason": entry_params.get("reason", "unknown"),
            "entry_params": entry_params,
        }

    stress_relief = 0.0
    if rearm_active and stress["drawdown_share"] < 0.25 and stress["score"] < 0.75:
        stress_relief = rearm_profile["stress_relief"]

    confidence_bump = min(
        0.05,
        symbol_stress_confidence_bump_max,
        stress["score"] * 0.12 + stress["drawdown_share"] * 0.18,
    )
    if stress["all_losing"]:
        confidence_bump = min(
            0.05,
            symbol_stress_confidence_bump_max,
            confidence_bump + 0.02,
        )
    confidence_bump *= (1.0 - stress_relief)

    mode_floor = experimental_mode_floor if regime in {"PRICE", "RAW", "GEMINI"} else (
        price_pass_confidence if regime == "PRICE" else fire_modes[mode]["min_confidence"]
    )
    if rearm_active:
        mode_floor = max(
            min_confidence_min,
            mode_floor - rearm_profile["mode_floor_relief"],
        )

    brain_confidence = min(entry_params["confidence_threshold"], mode_floor + 0.10)
    required_confidence = max(
        mode_floor,
        brain_confidence,
        effective_adaptive_threshold + confidence_bump,
    )
    if first_direct_rearm_shot:
        required_confidence = min(
            0.95,
            required_confidence + rearm_first_direct_confidence_bump,
        )
        required_confidence = max(required_confidence, rearm_first_direct_min_confidence)
    if post_cleanup_quality_gate_active and current_flat_book_rebuild:
        required_confidence = min(
            0.95,
            required_confidence + post_cleanup_quality_confidence_bump,
        )
        required_confidence = max(required_confidence, post_cleanup_quality_min_confidence)
    if current_post_cleanup_mercy_rebuild:
        required_confidence = min(
            0.95,
            required_confidence + post_cleanup_mercy_confidence_bump,
        )
    if regime in {"PRICE", "RAW", "GEMINI"}:
        managed_positions = int((book_stress or {}).get("managed_positions", 0) or 0)
        # Keep experimental lanes equal, but stop loaded books from falling back
        # to the raw experiment floor after we already computed stricter adaptive
        # and stress-aware quality requirements.
        if not current_flat_book_rebuild and managed_positions > 0:
            required_confidence = max(required_confidence, experimental_mode_floor)
        else:
            required_confidence = experimental_mode_floor

    extreme_symbol_stress = (
        stress["drawdown_share"] >= symbol_stress_extreme_drawdown_share
        or (
            stress["score"] >= symbol_stress_extreme_score
            and stress["position_ratio"] >= 0.60
        )
    )

    lot_reduction = min(
        symbol_stress_lot_reduction_max,
        stress["score"] * 0.28 + stress["drawdown_share"] * 0.32,
    )
    lot_reduction *= (1.0 - stress_relief)
    lot = max(0.01, round(entry_params["lot_size"] * (1.0 - lot_reduction), 2))
    if mode == "REVERSION":
        lot = max(0.01, round(lot * reversion_lot_scale, 2))
        lot = min(lot, 0.15)

    return {
        "brain_blocked": False,
        "block_reason": None,
        "stress": stress,
        "velocity_profile": velocity_profile,
        "low_velocity_blocked": low_velocity_blocked,
        "defensive_cross_fx_blocked": defensive_cross_fx_blocked,
        "defensive_exotic_fx_blocked": defensive_exotic_fx_blocked,
        "defensive_core_major_fx_blocked": defensive_core_major_fx_blocked,
        "first_direct_rearm_shot": first_direct_rearm_shot,
        "entry_params": entry_params,
        "stress_relief": stress_relief,
        "confidence_bump": confidence_bump,
        "mode_floor": mode_floor,
        "required_confidence": required_confidence,
        "extreme_symbol_stress": extreme_symbol_stress,
        "lot": lot,
        "lane_health_floor_bump": lane_health_floor_bump,
    }


def compute_preopen_entry_state(
    *,
    mt5,
    active_positions,
    alleyway_state,
    symbol,
    signal,
    confidence,
    mode,
    regime,
    atr,
    lot,
    equity,
    rearm_active,
    free_margin_ratio,
    book_stress,
    current_flat_book_rebuild,
    post_cleanup_quality_gate_active,
    current_post_cleanup_mercy_rebuild,
    current_loser_lane_defend_guard,
    current_loser_lane_nonflat_hard_block,
    current_experimental_pair_slot,
    current_price_positions,
    current_raw_positions,
    current_gemini_positions,
    current_first_direct_flat_shot,
    current_effective_active_count,
    direct_positions,
    adopted_positions,
    symbol_total_lot,
    experimental_mode_floor,
    learner,
    calc_sl_tp_prices,
    check_margin_safety,
    defend_loaded_no_add_active,
    get_market_closed_symbol_remaining,
    get_no_money_symbol_remaining,
    get_no_money_global_remaining,
    is_crypto,
    is_exotic,
    max_spread_pct_crypto,
    max_spread_pct_exotic,
    exotic_spread_multiplier,
    max_spread_pct_forex,
    spread_vs_stop_max_ratio,
    rearm_first_direct_max_spread_stop_ratio,
    post_cleanup_quality_max_spread_stop_ratio,
    no_money_global_release_free_margin_ratio,
    defend_competition_experimental_min_free_margin_ratio,
    defend_competition_experimental_max_active_positions,
    defend_experimental_continuation_max_per_regime,
    defend_midload_no_add_min_positions,
    defend_benchmark_midload_no_add_min_positions,
    rearm_first_direct_lot_scale,
    post_cleanup_quality_lot_scale,
    post_cleanup_mercy_lot_scale,
    max_symbol_exposure_pct,
    fire_modes,
    experimental_lanes,
    experimental_entry_shock_budget_usd,
    experimental_entry_shock_atr_mult,
):
    first_direct_rearm_shot = current_first_direct_flat_shot

    if first_direct_rearm_shot:
        sym_info = mt5.symbol_info(symbol)
        if not sym_info:
            return {"skip_reason": "symbol_info_missing"}
        guarded_lot = round(lot * rearm_first_direct_lot_scale, 2)
        guarded_lot = max(sym_info.volume_min, guarded_lot)
        if sym_info.volume_step > 0:
            guarded_lot = round(
                round(guarded_lot / sym_info.volume_step) * sym_info.volume_step,
                2,
            )
        lot = min(lot, guarded_lot)

    if post_cleanup_quality_gate_active and current_flat_book_rebuild:
        lot = max(0.01, round(lot * post_cleanup_quality_lot_scale, 2))
    if current_post_cleanup_mercy_rebuild:
        lot = max(0.01, round(lot * post_cleanup_mercy_lot_scale, 2))

    exposure_blocked = False
    if symbol_total_lot > 0 and atr > 0:
        sym_info_exposure = mt5.symbol_info(symbol)
        if sym_info_exposure and sym_info_exposure.trade_tick_value > 0 and sym_info_exposure.trade_tick_size > 0:
            sl_dist_exposure = atr * fire_modes[mode]["sl_atr_mult"]
            sl_ticks = sl_dist_exposure / sym_info_exposure.trade_tick_size
            risk_dollar = sl_ticks * sym_info_exposure.trade_tick_value * (symbol_total_lot + lot)
            exposure_blocked = risk_dollar > equity * max_symbol_exposure_pct
            if exposure_blocked:
                return {
                    "skip_reason": "symbol_exposure",
                    "lot": lot,
                }

    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        return {"skip_reason": "tick_missing", "lot": lot}

    spread_pct = abs(tick.ask - tick.bid) / tick.ask * 100
    if is_crypto(symbol):
        max_spread = max_spread_pct_crypto
    elif is_exotic(symbol):
        max_spread = max_spread_pct_exotic * exotic_spread_multiplier
    else:
        max_spread = max_spread_pct_forex

    spread_scaled = False
    original_lot = lot
    if spread_pct > max_spread * 0.5:
        spread_ratio = (spread_pct - (max_spread * 0.5)) / (max_spread * 0.5)
        lot_penalty = min(0.5, spread_ratio * 0.5)
        lot = max(0.01, round(lot * (1.0 - lot_penalty), 2))
        spread_scaled = lot != original_lot

    proposed_entry_price = tick.ask if signal == "BUY" else tick.bid
    sl_price, tp_price = calc_sl_tp_prices(
        symbol,
        signal,
        proposed_entry_price,
        atr,
        mode,
    )
    stop_distance = abs(proposed_entry_price - sl_price) if sl_price else 0
    spread_stop_ratio_limit = spread_vs_stop_max_ratio
    if first_direct_rearm_shot:
        spread_stop_ratio_limit = min(
            spread_stop_ratio_limit,
            rearm_first_direct_max_spread_stop_ratio,
        )
    if post_cleanup_quality_gate_active and current_flat_book_rebuild:
        spread_stop_ratio_limit = min(
            spread_stop_ratio_limit,
            post_cleanup_quality_max_spread_stop_ratio,
        )

    shock_scaled = False
    shock_original_lot = lot
    shock_projected_loss = 0.0
    shock_budget = 0.0
    shock_distance = 0.0
    shock_min_lot_projected_loss = 0.0
    shock_skip = False
    if regime in experimental_lanes and experimental_entry_shock_budget_usd > 0 and atr > 0:
        sym_info_shock = mt5.symbol_info(symbol)
        if sym_info_shock:
            tick_value = float(getattr(sym_info_shock, "trade_tick_value", 0.0) or 0.0)
            tick_size = float(getattr(sym_info_shock, "trade_tick_size", 0.0) or 0.0)
            volume_min = float(getattr(sym_info_shock, "volume_min", 0.01) or 0.01)
            volume_max = float(getattr(sym_info_shock, "volume_max", volume_min) or volume_min)
            volume_step = float(getattr(sym_info_shock, "volume_step", volume_min) or volume_min)
            if volume_step <= 0:
                volume_step = volume_min if volume_min > 0 else 0.01
            if tick_value > 0 and tick_size > 0:
                spread_distance = abs(float(tick.ask or 0.0) - float(tick.bid or 0.0))
                shock_distance = spread_distance + max(
                    spread_distance,
                    atr * experimental_entry_shock_atr_mult,
                )
                shock_dollar_per_lot = (shock_distance / tick_size) * tick_value
                shock_budget = float(experimental_entry_shock_budget_usd)
                if shock_dollar_per_lot > 0 and shock_budget > 0:
                    shock_min_lot_projected_loss = shock_dollar_per_lot * volume_min
                    if shock_min_lot_projected_loss > shock_budget + 1e-9:
                        shock_skip = True
                    else:
                        max_shock_lot = shock_budget / shock_dollar_per_lot
                        capped_lot = max(
                            volume_min,
                            min(
                                volume_max,
                                round(max_shock_lot / volume_step) * volume_step,
                            ),
                        )
                        capped_lot = round(capped_lot, 2)
                        projected_loss = shock_dollar_per_lot * lot
                        if capped_lot + 1e-9 < lot:
                            lot = capped_lot
                            shock_scaled = True
                            shock_projected_loss = shock_dollar_per_lot * lot
                        else:
                            shock_projected_loss = projected_loss

    if shock_skip:
        return {
            "skip_reason": "entry_shock_budget",
            "lot": lot,
            "shock_budget": shock_budget,
            "shock_distance": shock_distance,
            "shock_min_lot_projected_loss": shock_min_lot_projected_loss,
        }

    cooldown_remaining = learner.get_cooldown(symbol)
    experimental_cooldown_floor = (
        experimental_mode_floor if regime in {"PRICE", "RAW", "GEMINI"} else 0.60
    )
    experimental_cooldown_bypass = (
        regime in {"PRICE", "RAW", "GEMINI"}
        and confidence >= experimental_cooldown_floor
        and not current_loser_lane_defend_guard
        and not current_loser_lane_nonflat_hard_block
    )
    market_closed_remaining = get_market_closed_symbol_remaining(symbol)
    no_money_symbol_remaining = get_no_money_symbol_remaining(symbol)
    no_money_global_remaining = get_no_money_global_remaining(free_margin_ratio=free_margin_ratio)
    learner_params = learner.get_params(symbol)

    lot_before_margin = lot
    safe_lot, margin_ok = check_margin_safety(symbol, lot, signal)
    if margin_ok and safe_lot > 0 and safe_lot < lot:
        lot = safe_lot

    live_projected_active_count = len(active_positions) + 1
    live_entry_posture = alleyway_state.get("entry_posture")
    live_current_active_count = len(active_positions)
    live_defend_loaded_block = defend_loaded_no_add_active(
        current_flat_book_rebuild=current_flat_book_rebuild,
        entry_posture=live_entry_posture,
        current_active_count=live_current_active_count,
        effective_active_count=current_effective_active_count,
        projected_active_count=live_projected_active_count,
        free_margin_ratio=free_margin_ratio,
        managed_drawdown_pct=book_stress["managed_drawdown_pct"],
        top_symbol_drawdown_pct=book_stress["top_symbol_drawdown_pct"],
        direct_positions=direct_positions,
        adopted_positions=adopted_positions,
        candidate_regime=regime,
        current_price_positions=current_price_positions,
        current_raw_positions=current_raw_positions,
        current_gemini_positions=current_gemini_positions,
    )
    live_experimental_continuation_allowed = (
        regime in {"PRICE", "RAW", "GEMINI"}
        and (
            current_experimental_pair_slot
            or (
                live_entry_posture == "DEFEND"
                and free_margin_ratio >= defend_competition_experimental_min_free_margin_ratio
                and live_current_active_count <= defend_competition_experimental_max_active_positions
                and (
                    current_price_positions
                    if regime == "PRICE"
                    else (
                        current_raw_positions
                        if regime == "RAW"
                        else (current_gemini_positions if regime == "GEMINI" else 0)
                    )
                ) < defend_experimental_continuation_max_per_regime
            )
        )
    )
    sanity_midload_threshold = min(
        defend_midload_no_add_min_positions,
        defend_benchmark_midload_no_add_min_positions,
    )
    live_defend_veto_sanity = (
        not current_flat_book_rebuild
        and live_entry_posture == "DEFEND"
        and live_current_active_count > 0
        and live_projected_active_count >= sanity_midload_threshold
        and not live_experimental_continuation_allowed
        and (
            free_margin_ratio <= 0.35
            or book_stress["managed_drawdown_pct"] >= 0.06
            or book_stress["top_symbol_drawdown_pct"] >= 0.05
        )
    )

    return {
        "skip_reason": None,
        "lot": lot,
        "tick": tick,
        "spread_pct": spread_pct,
        "max_spread": max_spread,
        "spread_scaled": spread_scaled,
        "original_lot": original_lot,
        "spread_hard_block": spread_pct > max_spread * 1.2,
        "shock_scaled": shock_scaled,
        "shock_original_lot": shock_original_lot,
        "shock_projected_loss": shock_projected_loss,
        "shock_budget": shock_budget,
        "shock_distance": shock_distance,
        "proposed_entry_price": proposed_entry_price,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "stop_distance": stop_distance,
        "spread_stop_ratio_limit": spread_stop_ratio_limit,
        "spread_vs_stop_block": stop_distance > 0 and (tick.ask - tick.bid) > stop_distance * spread_stop_ratio_limit,
        "cooldown_remaining": cooldown_remaining,
        "experimental_cooldown_floor": experimental_cooldown_floor,
        "experimental_cooldown_bypass": experimental_cooldown_bypass,
        "market_closed_remaining": market_closed_remaining,
        "no_money_symbol_remaining": no_money_symbol_remaining,
        "no_money_global_remaining": no_money_global_remaining,
        "learner_params": learner_params,
        "margin_ok": margin_ok,
        "safe_lot": safe_lot,
        "lot_before_margin": lot_before_margin,
        "live_projected_active_count": live_projected_active_count,
        "live_entry_posture": live_entry_posture,
        "live_current_active_count": live_current_active_count,
        "live_defend_loaded_block": live_defend_loaded_block,
        "live_experimental_continuation_allowed": live_experimental_continuation_allowed,
        "sanity_midload_threshold": sanity_midload_threshold,
        "live_defend_veto_sanity": live_defend_veto_sanity,
        "no_money_global_release_free_margin_ratio": no_money_global_release_free_margin_ratio,
        "current_experimental_pair_slot": current_experimental_pair_slot,
    }


def build_open_position_state(
    *,
    ticket,
    symbol,
    signal,
    mode,
    confidence,
    atr,
    lot,
    regime,
    entry_context,
    live_entry_posture,
    rearm_reason,
    current_flat_book_rebuild,
    signal_type,
    tick,
):
    return {
        "ticket": int(ticket),
        "symbol": symbol,
        "direction": signal,
        "entry_price": tick.ask if signal == "BUY" else tick.bid,
        "entry_time": time.time(),
        "peak_pnl": 0.0,
        "mode": mode,
        "confidence": confidence,
        "last_pnl": 0.0,
        "atr": atr,
        "volume": lot,
        "pyramid_count": 0,
        "last_pyramid_pnl": 0,
        "mean_reversion": regime == "RANGING",
        "entry_context": (
            f"signal={entry_context or 'unknown'};"
            f"posture={live_entry_posture};"
            f"rearm_reason={rearm_reason or 'none'};"
            f"flat_rebuild={'yes' if current_flat_book_rebuild else 'no'}"
        ),
        "entry_signal_type": signal_type or "unlabeled",
        "entry_regime": regime or "unknown",
        "spread_at_entry": float(abs((tick.ask or 0.0) - (tick.bid or 0.0))),
        "time_to_first_green_seconds": None,
        "time_to_0_25_atr_seconds": None,
        "time_to_0_5_atr_seconds": None,
        "time_to_1_0_atr_seconds": None,
        "time_to_minus_0_35_atr_seconds": None,
        "max_favorable_excursion_pnl": 0.0,
        "max_adverse_excursion_pnl": 0.0,
    }


def mark_experimental_preopen_ready(
    *,
    reversion_diag,
    regime,
    cycle_has_actionable_experimental_pressure,
    reserved_rearm_experimental_regime,
):
    if regime in {"PRICE", "RAW", "GEMINI"}:
        reversion_diag["experimental_preopen_ready"] = reversion_diag.get("experimental_preopen_ready", 0) + 1
        cycle_has_actionable_experimental_pressure = True
        if regime == reserved_rearm_experimental_regime:
            reserved_rearm_experimental_regime = None
    return {
        "cycle_has_actionable_experimental_pressure": cycle_has_actionable_experimental_pressure,
        "reserved_rearm_experimental_regime": reserved_rearm_experimental_regime,
    }


def apply_successful_open(
    *,
    mt5,
    active_positions,
    mode_counts,
    regime_counts,
    alleyway_state,
    ticket,
    symbol,
    signal,
    mode,
    confidence,
    atr,
    lot,
    regime,
    entry_context,
    live_entry_posture,
    rearm_reason,
    current_flat_book_rebuild,
    signal_type,
    tick,
    entries_this_cycle,
    post_cleanup_quality_gate_active,
    post_cleanup_quality_gate_trigger,
    consume_post_cleanup_quality_gate,
    arm_post_cleanup_first_leg_rearm_holdoff,
    cycle_opened_symbols,
    get_book_stress,
    equity,
    reversion_diag,
):
    if mode == "REVERSION":
        reversion_diag["opened"] += 1
    if regime == "PRICE":
        reversion_diag["price_opened"] = reversion_diag.get("price_opened", 0) + 1
    elif regime == "RAW":
        reversion_diag["raw_opened"] = reversion_diag.get("raw_opened", 0) + 1
    elif regime == "GEMINI":
        reversion_diag["gemini_opened"] = reversion_diag.get("gemini_opened", 0) + 1

    active_positions[ticket] = build_open_position_state(
        ticket=ticket,
        symbol=symbol,
        signal=signal,
        mode=mode,
        confidence=confidence,
        atr=atr,
        lot=lot,
        regime=regime,
        entry_context=entry_context,
        live_entry_posture=live_entry_posture,
        rearm_reason=rearm_reason,
        current_flat_book_rebuild=current_flat_book_rebuild,
        signal_type=signal_type,
        tick=tick,
    )
    mode_counts[mode] += 1
    if regime in regime_counts:
        regime_counts[regime] += 1
        if regime in mode_counts:
            mode_counts[regime] = regime_counts[regime]

    entries_this_cycle += 1
    alleyway_state["cycles_without_trade"] = 0
    if post_cleanup_quality_gate_active and current_flat_book_rebuild:
        consume_post_cleanup_quality_gate()
        arm_post_cleanup_first_leg_rearm_holdoff(
            time.time(),
            post_cleanup_quality_gate_trigger or "unknown",
            symbol,
            mode,
        )
    cycle_opened_symbols.add(symbol)
    book_stress = get_book_stress(equity)
    acct = mt5.account_info()
    free_margin_ratio = (acct.margin_free / equity) if acct and equity > 0 else None

    return {
        "entries_this_cycle": entries_this_cycle,
        "book_stress": book_stress,
        "free_margin_ratio": free_margin_ratio,
        "entry_price": tick.ask if signal == "BUY" else tick.bid,
        "equity": equity,
    }


def execute_open_attempt(
    *,
    log,
    try_open_position,
    set_broker_sl_tp,
    apply_successful_open,
    reversion_diag,
    symbol,
    signal,
    mode,
    confidence,
    atr,
    signal_type,
    regime,
    lot,
):
    log(f"  [PRE_OPEN] {symbol} {mode} {signal} lot={lot} conf={confidence:.2f} type={signal_type}")
    ticket = try_open_position(symbol, signal, lot, mode, confidence, atr, signal_type)
    if ticket is None:
        if regime in {"PRICE", "RAW", "GEMINI"}:
            reversion_diag["experimental_open_failed"] = reversion_diag.get("experimental_open_failed", 0) + 1
        log(f"  [OPEN_FAILED] {symbol} {mode} {signal} — try_open_position returned None")
        return {"ticket": None, "open_result": None}

    open_result = apply_successful_open(ticket)
    entry_price = open_result["entry_price"]
    log(
        f"  OPEN [{mode}] {signal} {symbol} #{ticket} {lot}lot @ {entry_price:.5f} "
        f"(conf:{confidence:.2f} atr:{atr:.5f} eq:{open_result['equity']:.0f})"
    )
    set_broker_sl_tp(ticket, signal, entry_price, atr, mode)
    return {
        "ticket": ticket,
        "open_result": open_result,
    }
