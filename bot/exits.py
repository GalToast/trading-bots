from __future__ import annotations

import time


def arm_zombie_salvage_target(*, alleyway_state, log, ttl_seconds, symbol, detail="", now=None):
    if now is None:
        now = time.time()
    symbol = str(symbol or "").upper()
    if not symbol:
        return
    alleyway_state["zombie_salvage_target_symbol"] = symbol
    alleyway_state["zombie_salvage_target_until"] = now + ttl_seconds
    log(
        f"  ZOMBIE_SALVAGE_TARGET symbol={symbol} "
        f"ttl={ttl_seconds}s {detail}".rstrip()
    )


def adopted_book_salvage_only_mode_active(
    *,
    book_stress,
    free_margin_ratio,
    posture,
    min_positions,
    max_free_margin_ratio,
):
    managed_positions = int((book_stress or {}).get("managed_positions", 0) or 0)
    if managed_positions < min_positions:
        return False
    if posture != "DEFEND":
        return False
    return float(free_margin_ratio or 0.0) <= max(max_free_margin_ratio, 0.40)


def adopted_book_earn_back_mode_active(
    *,
    book_stress,
    free_margin_ratio,
    posture,
):
    managed_positions = int((book_stress or {}).get("managed_positions", 0) or 0)
    if managed_positions <= 0 or managed_positions > 6:
        return False
    if posture != "DEFEND":
        return False
    if float(free_margin_ratio or 0.0) < 0.34:
        return False
    if float((book_stress or {}).get("managed_drawdown_pct", 0.0) or 0.0) > 0.10:
        return False
    if float((book_stress or {}).get("top_symbol_drawdown_pct", 0.0) or 0.0) > 0.06:
        return False
    return True


def filter_earn_back_opportunities(
    *,
    opportunities,
    book_stress,
    free_margin_ratio,
    alleyway_state,
    active_positions,
    get_experimental_lane_floor_bump,
    is_symbol_spread_eligible,
    get_symbol_family_bucket,
    price_pass_confidence,
    fire_modes,
    log,
):
    if not adopted_book_earn_back_mode_active(
        book_stress=book_stress,
        free_margin_ratio=free_margin_ratio,
        posture=str(alleyway_state.get("entry_posture") or ""),
    ):
        alleyway_state["earn_back_mode"] = False
        return opportunities

    alleyway_state["earn_back_mode"] = True
    active_symbols = {
        str((pdata or {}).get("symbol") or "").upper()
        for pdata in active_positions.values()
    }
    candidates = []
    selected_regimes = set()
    affordability_bias_active = float(free_margin_ratio or 0.0) <= 0.55
    for idx, item in enumerate(opportunities):
        if len(item) < 8:
            continue
        symbol, _signal, confidence, mode, _atr, regime, _signal_type, _entry_context = item
        regime = str(regime or "").upper()
        symbol = str(symbol or "").upper()
        if regime not in {"PRICE", "RAW", "GEMINI"}:
            continue
        if symbol in active_symbols:
            continue
        if regime in selected_regimes:
            continue
        if not is_symbol_spread_eligible(symbol):
            continue

        lane_bump = get_experimental_lane_floor_bump(
            regime,
            book_stress=book_stress,
            free_margin_ratio=free_margin_ratio,
        )
        regime_floor = (
            max(price_pass_confidence + 0.05, 0.60)
            if regime == "PRICE"
            else (
                max(fire_modes[mode]["min_confidence"] + 0.10, 0.65)
                if regime == "RAW"
                else max(fire_modes[mode]["min_confidence"] + 0.10 + lane_bump, 0.72)  # GEMINI: lowered from 0.90 to 0.72
            )
        )
        if confidence < regime_floor:
            continue

        affordability_penalty = 0
        if (
            affordability_bias_active
            and get_symbol_family_bucket(symbol) == "INDEX"
            and confidence < (regime_floor + 0.20)
        ):
            affordability_penalty = 1

        candidates.append((affordability_penalty, idx, item))

    candidates.sort(key=lambda row: (row[0], row[1]))

    selected = []
    managed_positions = int((book_stress or {}).get("managed_positions", 0) or 0)
    max_selected = 1 if managed_positions > 5 else 2
    for _affordability_penalty, _idx, item in candidates:
        _symbol, _signal, _confidence, _mode, _atr, regime, _signal_type, _entry_context = item
        selected_regimes.add(regime)
        selected.append(item)
        if len(selected) >= max_selected:
            break

    if selected:
        log(
            "  [EARN_BACK_MODE] "
            + " | ".join(
                f"{sym}:{reg}:{conf:.2f}"
                for sym, _sig, conf, _mode, _atr, reg, _stype, _ctx in selected
            )
        )
        return selected

    return []


def zombie_salvage_retry_active(*, alleyway_state, ticket, now=None):
    if now is None:
        now = time.time()
    retry_map = alleyway_state.get("zombie_salvage_retry_until_by_ticket") or {}
    return now < float(retry_map.get(str(ticket), 0.0) or 0.0)


def mark_zombie_salvage_retry_blocked(*, alleyway_state, ticket, cooldown_seconds, now=None):
    if now is None:
        now = time.time()
    retry_map = dict(alleyway_state.get("zombie_salvage_retry_until_by_ticket") or {})
    retry_map[str(ticket)] = now + cooldown_seconds
    alleyway_state["zombie_salvage_retry_until_by_ticket"] = retry_map


def clear_zombie_salvage_retry(*, alleyway_state, ticket):
    retry_map = dict(alleyway_state.get("zombie_salvage_retry_until_by_ticket") or {})
    retry_map.pop(str(ticket), None)
    alleyway_state["zombie_salvage_retry_until_by_ticket"] = retry_map


def salvage_adopted_only_book_positions(
    *,
    brain,
    free_margin_ratio,
    active_positions,
    alleyway_state,
    mt5,
    min_positions,
    max_free_margin_ratio,
    min_win_pnl,
    max_financed_loss,
    retrace_min_ratio,
    retrace_close_loss_usd,
    cooldown_seconds,
    get_alleyway_mapping,
    get_red_close_finance_requirement,
    get_position_hold_seconds,
    close_position,
    evaluate_red_close_permission,
    arm_profit_capture_freeze,
    get_position_lane,
    log,
    arm_zombie_salvage_target,
    now=None,
):
    if now is None:
        now = time.time()
    active_count = len(active_positions)
    if (
        active_count < min_positions
        or free_margin_ratio > max_free_margin_ratio
    ):
        return 0

    if now < float(alleyway_state.get("adopted_salvage_cooldown_until", 0.0) or 0.0):
        return 0

    zombie_target_until = float(alleyway_state.get("zombie_salvage_target_until", 0.0) or 0.0)
    zombie_target_symbol = str(alleyway_state.get("zombie_salvage_target_symbol", "") or "").upper()
    if now >= zombie_target_until:
        zombie_target_symbol = ""
        alleyway_state["zombie_salvage_target_symbol"] = ""
        alleyway_state["zombie_salvage_target_until"] = 0.0

    total_pnl = 0.0
    green_candidates = []
    red_candidates = []
    buffer_available = max(0.0, float(alleyway_state.get("profit_offset_buffer_usd", 0.0) or 0.0))
    symbol_buffers = get_alleyway_mapping("profit_offset_buffer_by_symbol")

    for ticket, pdata in active_positions.items():
        symbol = str(pdata.get("symbol") or "?").upper()
        pnl = float(pdata.get("last_pnl", 0.0) or 0.0)
        volume = float(pdata.get("volume", 0.0) or 0.0)
        hold_sec = max(0.0, now - float(pdata.get("entry_time", now) or now))
        confidence = float(pdata.get("confidence", 0.0) or 0.0)
        try:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                pos = positions[0]
                pnl = float(pos.profit)
                volume = float(pos.volume)
                hold_sec = get_position_hold_seconds(pdata, pos)
        except Exception:
            pass

        total_pnl += pnl
        if pnl >= min_win_pnl or (zombie_target_symbol and symbol == zombie_target_symbol and pnl > 0):
            green_candidates.append((ticket, pdata, pnl, volume, hold_sec, confidence))
            continue
        if pnl >= 0:
            continue

        if abs(pnl) <= max_financed_loss:
            symbol_buffer = max(0.0, float(symbol_buffers.get(symbol, 0.0) or 0.0))
            required = get_red_close_finance_requirement(abs(pnl))
            loss_abs = abs(pnl)
            max_adverse = max(
                loss_abs,
                float(pdata.get("max_adverse_excursion_pnl", loss_abs) or loss_abs),
            )
            retrace_ratio = 0.0
            if max_adverse > 1e-9:
                retrace_ratio = max(0.0, min(1.0, (max_adverse - loss_abs) / max_adverse))
            close_to_breakeven = loss_abs <= retrace_close_loss_usd
            red_candidates.append(
                (
                    ticket,
                    pdata,
                    pnl,
                    volume,
                    hold_sec,
                    confidence,
                    required,
                    symbol_buffer,
                    retrace_ratio,
                    close_to_breakeven,
                )
            )

    green_candidates.sort(
        key=lambda item: (
            0 if zombie_target_symbol and str(item[1].get("symbol") or "").upper() == zombie_target_symbol else 1,
            -item[2],
            -item[4],
            -item[5],
        )
    )

    if green_candidates:
        ticket, pdata, pnl, volume, hold_sec, confidence = green_candidates[0]
        symbol = pdata.get("symbol", "?")
        if close_position(ticket, exit_reason="ADOPTED_WIN_BAG", exit_type="managed"):
            mode = pdata.get("mode", "ADOPTED")
            brain.record_exit(symbol, pnl, mode, hold_sec, failure_reason="ADOPTED_WIN_BAG")
            brain.save()
            active_positions.pop(ticket, None)
            clear_zombie_salvage_retry(alleyway_state=alleyway_state, ticket=ticket)
            log(
                f"  ADOPTED_WIN_BAG lane={get_position_lane(pdata)} {symbol} #{ticket} "
                f"P/L=${pnl:+.2f} vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
                f"active={active_count} defend_fm={free_margin_ratio:.2f} net=${total_pnl:+.2f}"
            )
            salvage_count = 1

            refreshed_buffer = max(
                0.0,
                float(alleyway_state.get("profit_offset_buffer_usd", 0.0) or 0.0),
            )
            refreshed_symbol_buffers = get_alleyway_mapping("profit_offset_buffer_by_symbol")
            prioritized_reds = sorted(
                red_candidates,
                key=lambda item: (
                    0 if zombie_target_symbol and str(item[1].get("symbol") or "").upper() == zombie_target_symbol else (
                        1 if str(item[1].get("symbol") or "?") == str(symbol) else 2
                    ),
                    0 if item[9] else 1,
                    -item[8],
                    abs(item[2]),
                    -item[4],
                    item[5],
                ),
            )
            for red_ticket, red_pdata, red_pnl, red_volume, red_hold_sec, red_confidence, required, _, retrace_ratio, close_to_breakeven in prioritized_reds:
                if red_ticket not in active_positions:
                    continue
                red_symbol = str(red_pdata.get("symbol") or "?")
                red_symbol_buffer = max(0.0, float(refreshed_symbol_buffers.get(red_symbol, 0.0) or 0.0))
                close_allowed, _ = evaluate_red_close_permission(
                    red_ticket,
                    red_pdata,
                    red_pnl,
                    "ADOPTED_PAIRED_UNWIND",
                    exit_type="managed",
                )
                if not close_allowed:
                    continue
                if close_position(red_ticket, exit_reason="ADOPTED_PAIRED_UNWIND", exit_type="managed"):
                    red_mode = red_pdata.get("mode", "ADOPTED")
                    brain.record_exit(red_symbol, red_pnl, red_mode, red_hold_sec, failure_reason="ADOPTED_PAIRED_UNWIND")
                    brain.save()
                    active_positions.pop(red_ticket, None)
                    clear_zombie_salvage_retry(alleyway_state=alleyway_state, ticket=red_ticket)
                    salvage_count += 1
                    if zombie_target_symbol and red_symbol.upper() == zombie_target_symbol:
                        alleyway_state["zombie_salvage_target_symbol"] = ""
                        alleyway_state["zombie_salvage_target_until"] = 0.0
                    log(
                        f"  ADOPTED_PAIRED_UNWIND lane={get_position_lane(red_pdata)} {red_symbol} #{red_ticket} "
                        f"P/L=${red_pnl:+.2f} vol={red_volume:.2f} hold={int(red_hold_sec)}s conf={red_confidence:.2f} "
                        f"need=${required:.2f} symbol_buffer=${red_symbol_buffer:.2f} "
                        f"retrace={retrace_ratio:.2f} close_be={'yes' if close_to_breakeven else 'no'} "
                        f"buffer=${refreshed_buffer:.2f} paired_with={symbol}"
                    )
                    break

            alleyway_state["adopted_salvage_cooldown_until"] = now + cooldown_seconds
            arm_profit_capture_freeze(now)
            return salvage_count

    financed_red_candidates = [
        item for item in red_candidates
        if item[7] + 1e-9 >= item[6] or buffer_available + 1e-9 >= item[6]
    ]
    financed_red_candidates.sort(
        key=lambda item: (
            0 if zombie_target_symbol and str(item[1].get("symbol") or "").upper() == zombie_target_symbol else 1,
            0 if item[9] else 1,
            -item[8],
            abs(item[2]),
            -item[4],
            item[5],
        )
    )
    if financed_red_candidates:
        ticket, pdata, pnl, volume, hold_sec, confidence, required, symbol_buffer, retrace_ratio, close_to_breakeven = financed_red_candidates[0]
        symbol = pdata.get("symbol", "?")
        exit_reason = (
            "ADOPTED_RETRACE_UNWIND"
            if close_to_breakeven or retrace_ratio >= retrace_min_ratio
            else "ADOPTED_FINANCED_UNWIND"
        )
        if close_position(ticket, exit_reason=exit_reason, exit_type="managed"):
            mode = pdata.get("mode", "ADOPTED")
            brain.record_exit(symbol, pnl, mode, hold_sec, failure_reason=exit_reason)
            brain.save()
            active_positions.pop(ticket, None)
            clear_zombie_salvage_retry(alleyway_state=alleyway_state, ticket=ticket)
            if zombie_target_symbol and str(symbol).upper() == zombie_target_symbol:
                alleyway_state["zombie_salvage_target_symbol"] = ""
                alleyway_state["zombie_salvage_target_until"] = 0.0
            alleyway_state["adopted_salvage_cooldown_until"] = now + cooldown_seconds
            arm_profit_capture_freeze(now)
            log(
                f"  {exit_reason} lane={get_position_lane(pdata)} {symbol} #{ticket} "
                f"P/L=${pnl:+.2f} vol={volume:.2f} hold={int(hold_sec)}s conf={confidence:.2f} "
                f"need=${required:.2f} symbol_buffer=${symbol_buffer:.2f} "
                f"retrace={retrace_ratio:.2f} close_be={'yes' if close_to_breakeven else 'no'} "
                f"buffer=${buffer_available:.2f} defend_fm={free_margin_ratio:.2f} net=${total_pnl:+.2f}"
            )
            return 1

    return 0
