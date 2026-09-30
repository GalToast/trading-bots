"""Main worker loop and process entry helpers."""
from __future__ import annotations
from .config import *  # noqa: F401,F403
from .state import _bars_cache, active_positions, alleyway_state, consecutive_losses, consecutive_wins, recently_trimmed_symbols, total_pnl, trades  # noqa: F401
from datetime import datetime
from datetime import timezone
from mt5_config import BOT_MAGIC
import MetaTrader5 as mt5
import os
import time
import traceback
from .book import arm_post_cleanup_first_leg_rearm_holdoff, arm_post_cleanup_flat_rearm_holdoff, arm_sync_close_reentry_freeze, check_pyramid_opportunities, cleanup_stale_adopted_positions, close_position, consume_post_cleanup_quality_gate, count_direct_positions, critical_margin_derisk_positions, defend_anchor_unwind_positions, defend_bag_winner_positions, defend_crowd_unwind_positions, defend_crowd_win_bag_positions, defend_crowding_derisk_positions, defend_financed_unwind_positions, defend_four_book_mixed_cleanup_positions, defend_loaded_no_add_active, defend_mixed_win_bag_positions, defend_no_expansion_active, defend_one_pos_exotic_mercy_exit_positions, defend_one_pos_index_mercy_exit_positions, defend_pinned_unwind_positions, defend_profit_capture_positions, defend_same_symbol_cluster_cleanup_positions, defend_small_book_unwind_positions, defend_three_book_same_symbol_cleanup_positions, defend_three_book_win_bag_positions, defend_two_book_mixed_cleanup_positions, defend_two_book_same_symbol_cleanup_positions, defend_two_book_win_bag_positions, get_active_post_cleanup_holdoff, get_post_cleanup_quality_gate, is_one_pos_exotic_mercy_trigger, manage_position, rearm_financed_unwind_positions, rearm_inherited_book_no_add_active, restore_post_cleanup_runtime_state, set_broker_sl_tp, trim_stressed_symbol_positions, try_open_position, update_entry_posture
from .indicators import calc_atr
from .journal import build_competition_lane_scorecard, emit_blocked_quality_candidate_record, emit_price_candidate_records, emit_strategy_lab_event, emit_trade_behavior_record, format_competition_lane_trigger, format_position_observability, get_competition_lane_recent_stats, hydrate_competition_lane_records_from_log, log_price_shadow_board_snapshot, log_price_watchlist_snapshot, log_stale_symbol, log_symbol_filter_snapshot, maybe_log_price_blocker_alert, maybe_log_price_watch_alert, maybe_log_strategy_lab_near_miss_alert, note_strategy_lab_near_miss, write_runtime_state, write_worker_state
from .log import get_process_command_line, log
from .market_data import connect_mt5, ensure_mt5, get_brain, get_broker_connection_backoff_remaining, get_insufficient_margin_symbol_remaining, get_learner, get_market_closed_symbol_remaining, get_tick_cached, is_tick_stale, refresh_tick_cache_for_cycle
from .positions import get_position_hold_seconds, is_bot_position, load_managed_positions, should_ignore_external_position
from .risk import calc_alleyway_relaxation, calc_equity_lot, calc_sl_tp_prices, check_correlation_limit, check_margin_safety, clamp_trade_lot, get_adaptive_threshold, get_alleyway_mapping, get_book_stress, get_effective_rearm_limits, get_mode_for_confidence, get_rearm_profile, get_symbol_family_bucket, get_symbol_stress
from .sessions import is_asian_session, is_crypto, is_exotic, is_good_session, is_overlap_session
from .signals import analyze, get_price_edge_signal, prioritize_experimental_opportunities, should_bypass_brain_cooldown_for_priority_lane, should_bypass_brain_cooldown_for_symbol_override
from .strategy_lab import build_lane_key, get_active_strategy_lab_lane_config, get_post_cleanup_raw_shotgun_min_confidence, get_resolved_strategy_lab_lane_id, get_strategy_lab_entry_gate, get_strategy_lab_lane_meta, get_strategy_lab_variant_label, is_experiment_allowed_lane, is_strategy_lab_lane, is_strategy_lab_symbol


def run():
    global consecutive_wins, consecutive_losses, total_pnl, trades
    brain = get_brain()
    write_worker_state("starting", "startup", "worker boot", "run entered")

    log("=" * 60)
    log("MT5 HUGOSWAY BOT V10 - 10x COMPETITION MODE")
    log("=" * 60)
    log(f"Multi-TF: M1 entry + M5 confirm + M15 direction")
    log(f"Risk/trade: SNIPER={RISK_PER_TRADE['SNIPER']*100:.0f}% SHOTGUN={RISK_PER_TRADE['SHOTGUN']*100:.0f}% MG={RISK_PER_TRADE['MACHINE_GUN']*100:.0f}%")
    log(f"Pyramiding: {PYRAMID_MAX_ADDS} adds, {PYRAMID_LOT_DECAY:.0%} decay")
    log(f"R:R targets: SNIPER 1:{FIRE_MODES['SNIPER']['tp_atr_mult']/FIRE_MODES['SNIPER']['sl_atr_mult']:.1f} SHOTGUN 1:{FIRE_MODES['SHOTGUN']['tp_atr_mult']/FIRE_MODES['SHOTGUN']['sl_atr_mult']:.1f}")
    log(f"Compounding: lot sizes scale with equity")
    log("=" * 60)
    (
        effective_rearm_max_direct_positions,
        effective_rearm_max_non_reversion_direct,
        effective_rearm_max_losing_direct_positions,
    ) = get_effective_rearm_limits()
    log(
        "Runtime contracts: "
        f"rearm_floor={effective_rearm_max_direct_positions}/"
        f"{effective_rearm_max_non_reversion_direct}/"
        f"{effective_rearm_max_losing_direct_positions} "
        f"loaded_financed_min_remaining_net=${DEFEND_LOADED_FINANCED_UNWIND_MIN_REMAINING_NET:.2f}"
    )

    if not connect_mt5():
        log("Failed to connect to MT5. Exiting.")
        return

    acct = mt5.account_info()
    log(f"Account: {acct.login} | Balance: ${acct.balance:.2f} | Leverage: 1:{acct.leverage}")
    
    # Track starting equity for alleyway performance measurement
    start_equity = acct.balance
    
    active_positions.clear()
    loaded_positions, adopted_positions = load_managed_positions()
    if loaded_positions:
        if adopted_positions:
            log(f"Loaded {loaded_positions} managed positions ({adopted_positions} adopted into V10 supervision)")
        else:
            log(f"Loaded {loaded_positions} existing V10 positions")
    restored_lane_scorecards = hydrate_competition_lane_records_from_log()
    restored_lane_fragments = [
        f"{lane}:{count}"
        for lane, count in sorted((restored_lane_scorecards.get('lanes') or {}).items())
        if count
    ]
    if restored_lane_fragments:
        malformed_suffix = ""
        if int(restored_lane_scorecards.get('malformed', 0) or 0):
            malformed_suffix = f" malformed={int(restored_lane_scorecards.get('malformed', 0) or 0)}"
        log(
            "Restored lane scorecards: "
            f"{', '.join(restored_lane_fragments)} "
            f"records={int(restored_lane_scorecards.get('records_loaded', 0) or 0)}"
            f"{malformed_suffix}"
        )
    restored_runtime_holds = restore_post_cleanup_runtime_state()
    if restored_runtime_holds:
        log(f"Restored runtime gates: {', '.join(restored_runtime_holds)}")
    write_runtime_state(balance=acct.balance, equity=acct.equity, margin_free=acct.margin_free)

    symbol_filter_diag = {}
    tradeable_symbols = get_active_symbols(diagnostics=symbol_filter_diag)
    log(f"Found {len(tradeable_symbols)} tradeable symbols (session-filtered)")
    log_symbol_filter_snapshot(symbol_filter_diag, context="startup")
    log_price_watchlist_snapshot(tradeable_symbols, context="startup")
    log_price_shadow_board_snapshot(context="startup")
    starting_lot_sniper = calc_equity_lot('EURUSD', 'SNIPER', 0.0005, acct.equity)
    log(f"Starting lot (EURUSD SNIPER): {starting_lot_sniper} (scales with equity)")
    log("=" * 60)

    # === 10x COMPOUNDING: Close all legacy positions in disabled modes ===
    # Legacy MACHINE_GUN/GEMINI/PRICE/REVERSION positions from old config are
    # blocking free margin. Close them at startup so the bot can trade cleanly.
    log("[10x STARTUP] Scanning for legacy positions in disabled modes...")
    legacy_closed = 0
    legacy_pnl = 0.0
    all_positions = mt5.positions_get()
    if all_positions is not None:
        for pos in all_positions:
            if should_ignore_external_position(pos):
                continue
            mode_label = (pos.comment or '').split(':')[-1] if ':' in (pos.comment or '') else ''
            # Check if position mode is in our disabled list
            pos_mode = mode_label.strip() if mode_label.strip() else ''
            should_close = False
            if pos_mode in DISABLED_MODES:
                should_close = True
            # Also close positions on blocklisted symbols
            if pos.symbol in SYMBOL_BLOCKLIST:
                should_close = True
            # Close if not in allowlist (legacy positions on non-target symbols)
            if SYMBOL_ALLOWLIST and pos.symbol not in SYMBOL_ALLOWLIST:
                should_close = True

            if should_close:
                pnl = (
                    float(getattr(pos, 'profit', 0.0) or 0.0)
                    + float(getattr(pos, 'swap', 0.0) or 0.0)
                    + float(getattr(pos, 'commission', 0.0) or 0.0)
                )
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": pos.symbol,
                    "volume": pos.volume,
                    "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                    "position": pos.ticket,
                    "price": mt5.symbol_info_tick(pos.symbol).bid if pos.type == mt5.POSITION_TYPE_BUY else mt5.symbol_info_tick(pos.symbol).ask,
                    "deviation": 20,
                    "magic": BOT_MAGIC,
                    "comment": "10x_legacy_cleanup",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_FOK,
                }
                result = mt5.order_send(request)
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    log(f"  [10x CLEANUP] Closed {pos.symbol} #{pos.ticket} {pos_mode} P/L=${pnl:+.2f}")
                    legacy_closed += 1
                    legacy_pnl += pnl
                else:
                    log(f"  [10x CLEANUP FAILED] {pos.symbol} #{pos.ticket} {pos_mode}: {result}")
    if legacy_closed:
        log(f"[10x STARTUP] Closed {legacy_closed} legacy positions, net P/L=${legacy_pnl:+.2f}")
    else:
        log("[10x STARTUP] No legacy positions to clean")
    log("=" * 60)

    write_worker_state("running", "loop_started", "worker loop active", "initialization complete")

    cycle = 0
    last_symbol_refresh = 0
    # === DAILY TRADE CAP TRACKING ===
    # Another agent: 670 trades/day was bleeding $11K. Hard cap at 50.
    today_entries_count = 0
    today_entries_date = datetime.now(timezone.utc).date()
    today_start_balance = acct.balance  # Track daily PnL for circuit breaker
    today_realized_pnl = 0.0  # Cumulative realized PnL today

    while True:
        cycle += 1
        reversion_diag = None  # Defensive: prevent UnboundLocalError on early continue/break
        try:
            if not ensure_mt5():
                log("Waiting for MT5 connection...")
                time.sleep(5)
                continue

            acct = mt5.account_info()
            if not acct:
                log("Account info unavailable")
                time.sleep(5)
                continue

            equity = acct.equity
            balance = acct.balance
            free_margin_ratio = (acct.margin_free / equity) if equity > 0 else 0.0

            # === DAILY TRADE CAP — reset at midnight UTC ===
            current_date = datetime.now(timezone.utc).date()
            if current_date != today_entries_date:
                log(f"  DAILY CAP RESET — yesterday: {today_entries_count} entries, daily P/L=${today_realized_pnl:+.2f}")
                today_entries_date = current_date
                today_entries_count = 0
                today_start_balance = acct.balance
                today_realized_pnl = 0.0

            # === DAILY LOSS CIRCUIT BREAKER ===
            # Stop all new entries if daily realized loss exceeds threshold
            daily_pnl = acct.balance - today_start_balance
            today_realized_pnl = daily_pnl  # Track for logging
            circuit_breaker_active = daily_pnl <= -MAX_DAILY_LOSS_USD
            if circuit_breaker_active:
                if cycle % 100 == 0:
                    log(f"  [CIRCUIT BREAKER] Daily loss ${daily_pnl:+.2f} hits cap (-${MAX_DAILY_LOSS_USD:.2f}) — blocking all entries")

            # === ALLEYWAY: Adaptive Threshold Relaxation ===
            # Measure market energy first
            avg_atr = 0
            atr_values = []
            momentum_values = []
            for sym in list(tradeable_symbols)[:10]:  # Sample first 10 symbols
                try:
                    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 50)
                    if rates is not None and len(rates) > 14:
                        atr = calc_atr(rates, 14)
                        if atr > 0:
                            atr_values.append(atr)
                            mom = (rates[-1]['close'] - rates[-5]['close']) / rates[-5]['close'] if rates[-5]['close'] > 0 else 0
                            momentum_values.append(abs(mom))
                except:
                    pass
            
            if atr_values:
                avg_atr = sum(atr_values) / len(atr_values)
                avg_momentum = sum(momentum_values) / len(momentum_values) if momentum_values else 0
            else:
                avg_momentum = 0
            
            # Determine volatility regime
            if avg_atr > 0:
                if avg_atr < 0.001:
                    vol_regime = 'LOW_VOL'
                elif avg_atr > 0.005:
                    vol_regime = 'HIGH_VOL'
                else:
                    vol_regime = 'NORMAL_VOL'
            else:
                vol_regime = 'UNKNOWN'
            
            alleyway_state['volatility_regime'] = vol_regime
            alleyway_state['market_momentum'] = avg_momentum
            alleyway_state['recent_atr_avg'] = avg_atr
            
            relaxation, relax_reasons = calc_alleyway_relaxation(
                equity, start_equity, trades, consecutive_wins, consecutive_losses
            )
            adaptive_threshold = get_adaptive_threshold(MIN_CONFIDENCE_BASE, relaxation)
            
            # Log alleyway state every 10 cycles
            if cycle % 10 == 0:
                relax_str = ', '.join(relax_reasons) if relax_reasons else 'none'
                log(f"ALLEYWAY: threshold={adaptive_threshold:.2f} (base={MIN_CONFIDENCE_BASE:.2f}, relax={relaxation:.2f}) reasons: {relax_str}")

            # === RISK GUARD ===
            if equity < balance * RISK_GUARD_PCT:
                log(f"RISK GUARD: Equity {equity:.2f} < {RISK_GUARD_PCT*100:.0f}% of balance {balance:.2f} - PAUSING")
                for ticket, pdata in list(active_positions.items()):
                    try:
                        manage_position(ticket, pdata, brain)
                    except Exception as e:
                        log(f"Error managing #{ticket}: {e}")
                write_runtime_state(balance=balance, equity=equity, margin_free=acct.margin_free)
                time.sleep(CHECK_INTERVAL)
                continue

            # Refresh symbols every 5 minutes
            if time.time() - last_symbol_refresh > 300:
                symbol_filter_diag = {}
                tradeable_symbols = get_active_symbols(diagnostics=symbol_filter_diag)
                last_symbol_refresh = time.time()
                if cycle > 1:
                    log(f"Refreshed symbols: {len(tradeable_symbols)} tradeable")
                    log_symbol_filter_snapshot(symbol_filter_diag, context="refresh")
                    log_price_watchlist_snapshot(tradeable_symbols, context="refresh")
                    log_price_shadow_board_snapshot(context="refresh")

            # Clean up closed positions
            open_tickets = set()
            all_positions = mt5.positions_get()
            if all_positions:
                for pos in all_positions:
                    if pos.ticket in active_positions or is_bot_position(pos):
                        open_tickets.add(pos.ticket)

            sync_closed_this_cycle = 0
            managed_exits_this_cycle = 0

            closed_tickets = set(active_positions.keys()) - open_tickets
            for ticket in closed_tickets:
                pdata = active_positions.pop(ticket, None)
                if pdata:
                    pnl = pdata.get('last_pnl', 0)
                    was_direct_position = not pdata.get('adopted')
                    if was_direct_position and pnl <= 0:
                        # External/manual loser closes can flatten the direct book without
                        # passing through the managed cleanup lanes that already arm this holdoff.
                        trigger = format_competition_lane_trigger(
                            "SYNC_CLOSE",
                            pdata,
                            pdata.get('symbol', '?'),
                        )
                        sync_close_symbol = str(pdata.get('symbol', '?') or '?').upper()
                        armed = arm_post_cleanup_flat_rearm_holdoff(
                            time.time(),
                            trigger,
                            pnl,
                        )
                        direct_positions_after = count_direct_positions()
                        sync_close_family = ""
                        sync_close_symbol_freeze_seconds = 0
                        sync_close_family_freeze_seconds = 0
                        (
                            sync_close_family,
                            sync_close_symbol_freeze_seconds,
                            sync_close_family_freeze_seconds,
                        ) = arm_sync_close_reentry_freeze(sync_close_symbol, time.time())
                        holdoff_event = (
                            f"ticket={ticket} symbol={pdata.get('symbol', '?')} "
                            f"pnl=${float(pnl or 0.0):+.2f} direct_after={direct_positions_after} "
                            f"armed={'yes' if armed else 'no'} trigger={trigger}"
                        )
                        if sync_close_symbol_freeze_seconds > 0:
                            holdoff_event += (
                                f" symbol_freeze={sync_close_symbol_freeze_seconds}s"
                            )
                        if sync_close_family and sync_close_family_freeze_seconds > 0:
                            holdoff_event += (
                                f" family_freeze={sync_close_family}:{sync_close_family_freeze_seconds}s"
                            )
                        alleyway_state['last_sync_close_holdoff_event'] = holdoff_event
                        alleyway_state['last_sync_close_holdoff_checked_at'] = datetime.now(timezone.utc).isoformat()
                        log(f"  SYNC_CLOSE_HOLDOFF {holdoff_event}")
                    sync_closed_this_cycle += 1
                    total_pnl += pnl
                    emit_trade_behavior_record(
                        ticket,
                        pdata,
                        format_competition_lane_trigger(
                            "SYNC_CLOSE",
                            pdata,
                            pdata.get('symbol', '?'),
                        ),
                        "sync_close",
                        realized_pnl=float(pnl or 0.0),
                    )
                    # Classify failure reason for sync-closed positions
                    failure_reason = None
                    if pnl <= 0:
                        mode = pdata.get('mode', 'MACHINE_GUN')
                        hold_sec = time.time() - pdata.get('entry_time', time.time())
                        if hold_sec < 300:
                            failure_reason = "SPREAD_KILL"  # Died almost immediately
                        else:
                            failure_reason = "WRONG_DIRECTION"
                    brain.record_exit(pdata['symbol'], pnl, pdata.get('mode', 'MACHINE_GUN'), 0, failure_reason=failure_reason)
                    # Record outcome with symbol learner for adaptive parameter tuning
                    learner = get_learner()
                    learner.record_outcome(pdata['symbol'], pnl, pdata.get('mode', 'MACHINE_GUN'), {"failure_reason": failure_reason} if failure_reason else {})
                    trades += 1
                    log(
                        f"  SYNC_CLOSE #{ticket} {format_position_observability(pdata)} "
                        f"source=main_loop_sync"
                    )
                    if pnl > 0:
                        consecutive_wins += 1
                        consecutive_losses = 0
                    else:
                        consecutive_losses += 1
                        consecutive_wins = 0

            critical_derisks_this_cycle = critical_margin_derisk_positions(brain)
            trims_this_cycle = trim_stressed_symbol_positions(brain)

            # Refresh tick cache once per cycle for all managed symbols
            managed_symbols = [pdata["symbol"] for pdata in active_positions.values()]
            refresh_tick_cache_for_cycle(managed_symbols, cycle)

            # Build position snapshot — single API call for all open positions
            all_broker_positions = mt5.positions_get()
            position_snapshot = {}
            if all_broker_positions:
                for bp in all_broker_positions:
                    if is_bot_position(bp) or bp.ticket in active_positions:
                        position_snapshot[bp.ticket] = bp

            # Manage existing positions (updates PnL data)
            for ticket, pdata in list(active_positions.items()):
                try:
                    live_pos = position_snapshot.get(ticket)
                    if manage_position(ticket, pdata, brain, live_position=live_pos):
                        managed_exits_this_cycle += 1
                except Exception as e:
                    log(f"Error managing #{ticket}: {e}")

            for ticket, pdata in active_positions.items():
                tick = get_tick_cached(pdata["symbol"], cycle)
                tick_stale, tick_age = is_tick_stale(tick)
                if tick_stale:
                    log_stale_symbol(
                        pdata["symbol"],
                        f"managed_position#{ticket}",
                        tick_age,
                    )

            # === CLEANUP STALE ADOPTED (after PnL refresh) ===
            adopted_cleaned = cleanup_stale_adopted_positions(brain)

            # === PYRAMIDING: Add to winners ===
            check_pyramid_opportunities(brain, equity)

            # Track fire-mode occupancy separately from experimental regime occupancy.
            # RAW entries execute through MACHINE_GUN fire mode, so mode-only counts
            # underreport live RAW exposure and poison continuation logic.
            mode_counts = {m: 0 for m in FIRE_MODES}
            regime_counts = {'RAW': 0, 'PRICE': 0, 'GEMINI': 0}
            for pdata in active_positions.values():
                mode = pdata.get('mode', 'MACHINE_GUN')
                regime = str(pdata.get('entry_regime', '') or '')
                if mode == 'PRICE' or regime == 'PRICE':
                    regime_counts['PRICE'] += 1
                if mode == 'GEMINI' or regime == 'GEMINI':
                    regime_counts['GEMINI'] += 1
                if regime == 'RAW':
                    regime_counts['RAW'] += 1
                if mode in mode_counts:
                    if mode == 'MACHINE_GUN' and regime == 'RAW':
                        continue
                    mode_counts[mode] += 1
            mode_counts['RAW'] = regime_counts['RAW']
            mode_counts['PRICE'] = regime_counts['PRICE']
            mode_counts['GEMINI'] = regime_counts['GEMINI']
            direct_losing_positions = 0
            direct_non_reversion = 0
            for pdata in active_positions.values():
                if pdata.get('adopted'):
                    continue
                if pdata.get('mode') != 'REVERSION':
                    direct_non_reversion += 1
                if float(pdata.get('last_pnl', 0.0) or 0.0) < 0:
                    direct_losing_positions += 1

            book_stress = get_book_stress(equity)
            rearm_active, rearm_reason = update_entry_posture(book_stress, free_margin_ratio)
            rearm_profile = get_rearm_profile()
            effective_adaptive_threshold = adaptive_threshold
            if rearm_active:
                effective_adaptive_threshold = max(
                    MIN_CONFIDENCE_MIN,
                    adaptive_threshold - rearm_profile["threshold_relaxation"],
                )
            (
                _effective_rearm_max_direct_positions,
                effective_rearm_max_non_reversion_direct,
                _effective_rearm_max_losing_direct_positions,
            ) = get_effective_rearm_limits()

            winner_bags_this_cycle = defend_profit_capture_positions(
                brain,
                free_margin_ratio,
                mode_counts,
            )
            rearm_financed_unwinds_this_cycle = 0
            if winner_bags_this_cycle == 0:
                rearm_financed_unwinds_this_cycle = rearm_financed_unwind_positions(
                    brain,
                    free_margin_ratio,
                    mode_counts,
                )
            if winner_bags_this_cycle == 0 and rearm_financed_unwinds_this_cycle == 0:
                winner_bags_this_cycle = defend_two_book_win_bag_positions(
                    brain,
                    free_margin_ratio,
                    mode_counts,
                )
            if winner_bags_this_cycle == 0 and rearm_financed_unwinds_this_cycle == 0:
                winner_bags_this_cycle = defend_three_book_win_bag_positions(
                    brain,
                    free_margin_ratio,
                    mode_counts,
                )
            if winner_bags_this_cycle == 0 and rearm_financed_unwinds_this_cycle == 0:
                winner_bags_this_cycle = defend_mixed_win_bag_positions(
                    brain,
                    free_margin_ratio,
                    mode_counts,
                )
            defend_derisks_this_cycle = 0
            if winner_bags_this_cycle == 0 and rearm_financed_unwinds_this_cycle == 0:
                defend_derisks_this_cycle = defend_crowding_derisk_positions(
                    brain,
                    mode_counts,
                    free_margin_ratio,
                )
                winner_bags_this_cycle += defend_bag_winner_positions(
                    brain,
                    free_margin_ratio,
                )

            # === ALLEYWAY: Update cycle counter ===
            alleyway_state['cycles_without_trade'] += 1
            if equity > alleyway_state['equity_peak']:
                alleyway_state['equity_peak'] = equity
            
            # === LOSS STREAK COOLDOWN ===
            cooldown_end = alleyway_state.get('cooldown_until', 0)
            if consecutive_losses >= 10:
                if cooldown_end == 0:
                    # Start cooldown
                    cooldown_end = time.time() + LOSS_STREAK_COOLDOWN_MINUTES * 60
                    alleyway_state['cooldown_until'] = cooldown_end
                    log(f"🛡️ LOSS STREAK COOLDOWN: {LOSS_STREAK_COOLDOWN_MINUTES}min partial freeze (10+ losses)")
            
            in_cooldown = False
            if time.time() < cooldown_end:
                in_cooldown = True
                remaining = int((cooldown_end - time.time()) / 60)
                if cycle % 30 == 0:
                    log(f"  [COOLDOWN] {remaining}min remaining - SNIPER mode only")
                # Cooldown Scaling: switch exclusively to SNIPER mode instead of a hard freeze
                effective_adaptive_threshold = max(effective_adaptive_threshold, FIRE_MODES['SNIPER']['min_confidence'])
            
            # Clear cooldown if streak clears
            if consecutive_losses == 0 and cooldown_end > 0:
                alleyway_state['cooldown_until'] = 0
                log("✅ COOLDOWN CLEARED - resuming full entries")

            cluster_cooldown_until = float(alleyway_state.get('cluster_cooldown_until', 0.0) or 0.0)
            cluster_cooldown_active = time.time() < cluster_cooldown_until
            if not cluster_cooldown_active and cluster_cooldown_until > 0:
                alleyway_state['cluster_cooldown_until'] = 0.0
            
            # === POSITION CAP CHECK ===
            active_count = len(active_positions)
            effective_active_count = (
                book_stress["direct_positions"]
                + book_stress["adopted_positions"] * ADOPTED_POSITION_CAP_WEIGHT
            )
            if effective_active_count >= MAX_CONCURRENT_POSITIONS:
                # === EQUITY-NEUTRAL PRUNER ===
                # If capped out, try to safely scratch one stagnant adopted position near breakeven
                pruned = False
                if book_stress["adopted_positions"] > 0:
                    for ticket, pdata in list(active_positions.items()):
                        if pdata.get('adopted', False):
                            pnl = pdata.get('last_pnl', 0)
                            hold_sec = get_position_hold_seconds(pdata)
                            # Prune if it's been active under V10 for > 15 mins and is within $0.50 of breakeven
                            if hold_sec > 900 and -0.50 <= pnl <= 0.50:
                                if close_position(ticket, exit_reason="CAPACITY_PRUNER", exit_type="prune"):
                                    log(f"  ✂️ PRUNER: Freed capacity by scratching adopted {pdata['symbol']} #{ticket} (${pnl:+.2f})")
                                    brain.record_exit(pdata['symbol'], pnl, pdata.get('mode', 'MACHINE_GUN'), hold_sec)
                                    brain.save()
                                    active_positions.pop(ticket, None)
                                    pruned = True
                                    break  # Only one per cycle
                
                if cycle % 20 == 0 and not pruned:
                    log(
                        f"  [CAP] effective={effective_active_count:.1f}/{MAX_CONCURRENT_POSITIONS} "
                        f"(total={active_count}, direct={book_stress['direct_positions']}, adopted={book_stress['adopted_positions']})"
                    )
                write_runtime_state(balance=balance, equity=equity, margin_free=acct.margin_free)
                time.sleep(CHECK_INTERVAL)
                continue

            # === SCAN FOR OPPORTUNITIES ===
            opportunities = []
            reversion_diag = {
                'scanned_symbols': 0,
                'opportunities': 0,
                'trend_opportunities': 0,
                'mg_opportunities': 0,
                'shotgun_opportunities': 0,
                'blocked_cluster': 0,
                'blocked_rearm_rebuild_cap': 0,
                'blocked_defend_cleanup': 0,
                'blocked_defend_noexp': 0,
                'blocked_defend_onepos': 0,
                'blocked_defend_loaded': 0,
                'blocked_defend_mg': 0,
                'blocked_crowding': 0,
                'blocked_exotic': 0,
                'blocked_correlation': 0,
                'blocked_trim_cooldown': 0,
                'blocked_confidence_gate': 0,
                'experimental_pair_slots': 0,
                'experimental_preopen_ready': 0,
                'experimental_blocked_late_confidence': 0,
                'price_blocked_late_confidence': 0,
                'raw_blocked_late_confidence': 0,
                'experimental_blocked_spread': 0,
                'experimental_blocked_margin': 0,
                'experimental_open_failed': 0,
                'price_opened': 0,
                'raw_opened': 0,
                'opened': 0,
            }
            for symbol in tradeable_symbols:
                try:
                    reversion_diag['scanned_symbols'] += 1
                    strategy_lab_price_preview = None
                    if is_strategy_lab_symbol(symbol):
                        preview_diag = {}
                        preview_signal, preview_confidence, _preview_atr, _preview_thesis, preview_signal_type = get_price_edge_signal(
                            symbol,
                            diagnostics=preview_diag,
                        )
                        strategy_lab_price_preview = {
                            "signal": str(preview_signal or ""),
                            "confidence": float(preview_confidence or 0.0),
                            "signal_type": str(preview_signal_type or ""),
                            "best_confidence": float(preview_diag.get("price_best_confidence", 0.0) or 0.0),
                            "best_score": float(preview_diag.get("price_best_score", 0.0) or 0.0),
                            "best_signal_type": str(
                                preview_diag.get("price_best_signal_type")
                                or preview_diag.get("price_best_score_signal_type")
                                or ""
                            ),
                        }
                    signal, confidence, atr, regime, signal_type, entry_context = analyze(symbol, effective_adaptive_threshold, diagnostics=reversion_diag)
                    if strategy_lab_price_preview:
                        preview_signal = strategy_lab_price_preview["signal"]
                        preview_confidence = float(strategy_lab_price_preview["confidence"] or 0.0)
                        preview_signal_type = strategy_lab_price_preview["signal_type"]
                        preview_best_confidence = float(strategy_lab_price_preview["best_confidence"] or 0.0)
                        preview_best_score = float(strategy_lab_price_preview["best_score"] or 0.0)
                        preview_best_signal_type = strategy_lab_price_preview["best_signal_type"]
                        if not signal:
                            if preview_signal:
                                note_strategy_lab_near_miss(
                                    reversion_diag,
                                    symbol=symbol,
                                    stage="analyze_suppressed",
                                    reason="price_signal_exists_but_analyze_returned_none",
                                    best_signal_type=preview_best_signal_type,
                                    best_confidence=preview_best_confidence,
                                    best_score=preview_best_score,
                                    emitted_signal=preview_signal,
                                    emitted_confidence=preview_confidence,
                                    emitted_signal_type=preview_signal_type,
                                    emitted_mode=get_mode_for_confidence(preview_confidence, 'PRICE'),
                                    emitted_regime="PRICE",
                                )
                            elif preview_best_confidence >= max(PRICE_PASS_CONFIDENCE - 0.04, 0.0):
                                note_strategy_lab_near_miss(
                                    reversion_diag,
                                    symbol=symbol,
                                    stage="price_engine_near_miss",
                                    reason=f"best_conf={preview_best_confidence:.2f}<pass={PRICE_PASS_CONFIDENCE:.2f}",
                                    best_signal_type=preview_best_signal_type,
                                    best_confidence=preview_best_confidence,
                                    best_score=preview_best_score,
                                )
                    if not signal:
                        continue

                    mode = get_mode_for_confidence(confidence, regime)
                    if strategy_lab_price_preview and regime != 'PRICE' and strategy_lab_price_preview["signal"]:
                        note_strategy_lab_near_miss(
                            reversion_diag,
                            symbol=symbol,
                            stage="price_replaced",
                            reason=f"analyze_returned_{regime}:{signal_type or '-'}",
                            best_signal_type=strategy_lab_price_preview["best_signal_type"],
                            best_confidence=float(strategy_lab_price_preview["best_confidence"] or 0.0),
                            best_score=float(strategy_lab_price_preview["best_score"] or 0.0),
                            emitted_signal=strategy_lab_price_preview["signal"],
                            emitted_confidence=float(strategy_lab_price_preview["confidence"] or 0.0),
                            emitted_signal_type=strategy_lab_price_preview["signal_type"],
                            emitted_mode=get_mode_for_confidence(float(strategy_lab_price_preview["confidence"] or 0.0), 'PRICE'),
                            emitted_regime="PRICE",
                        )

                    # SNIPER indices ban - Downgrade to SHOTGUN to prevent massive tail risk
                    if mode == 'SNIPER' and symbol in {'US30', 'NAS100', 'JPN225', 'SPX500'}:
                        mode = 'SHOTGUN'

                    # 10x compounding: block disabled modes (MACHINE_GUN/PRICE/GEMINI all bleeding)
                    if mode in DISABLED_MODES:
                        continue

                    # Symbol-signal blocklist — proven bad combinations from forensics
                    if (symbol, signal_type) in SYMBOL_SIGNAL_BLOCKLIST or ('*', signal_type) in SYMBOL_SIGNAL_BLOCKLIST:
                        continue

                    # 24/7 COMPOUNDING: Off-session profile (EXP-20260409-64)
                    now_utc = datetime.now(timezone.utc)
                    current_utc_hour = now_utc.hour
                    
                    # Session-specific toxic blocklists
                    is_asian = 22 <= current_utc_hour or current_utc_hour < 7
                    is_ny = 12 <= current_utc_hour < 20
                    if is_asian and (symbol, signal_type) in ASIAN_BLOCKLIST:
                        continue
                    if is_ny and (symbol, signal_type) in NEW_YORK_BLOCKLIST:
                        continue

                    if current_utc_hour in OFF_SESSION_HOURS:
                        current_hour_bucket = now_utc.strftime('%Y-%m-%dT%H')
                        if alleyway_state.get('off_session_entry_hour_bucket') != current_hour_bucket:
                            alleyway_state['off_session_entry_hour_bucket'] = current_hour_bucket
                            alleyway_state['off_session_entries_this_hour'] = 0
                        if symbol not in OFF_SESSION_ALLOWLIST:
                            continue
                        if signal_type in OFF_SESSION_SIGNAL_BLOCKLIST:
                            continue
                        if signal_type not in OFF_SESSION_SIGNAL_ALLOWLIST:
                            continue
                        if int(alleyway_state.get('off_session_entries_this_hour', 0) or 0) >= OFF_SESSION_MAX_TRADES_PER_HOUR:
                            if time.time() >= float(alleyway_state.get('off_session_cap_log_until', 0.0) or 0.0):
                                log(
                                    "  [OFF_SESSION_HOURLY_CAP] "
                                    f"hour={current_hour_bucket} cap={OFF_SESSION_MAX_TRADES_PER_HOUR} "
                                    f"symbol={symbol} signal={signal_type or 'unlabeled'}"
                                )
                                alleyway_state['off_session_cap_log_until'] = time.time() + 60.0
                            continue

                    if regime == 'PRICE':
                        reversion_diag['price_opportunities'] = reversion_diag.get('price_opportunities', 0) + 1
                        top_price_conf = float(reversion_diag.get('price_top_confidence', 0.0) or 0.0)
                        if confidence >= top_price_conf:
                            reversion_diag['price_top_confidence'] = confidence
                            reversion_diag['price_top_symbol'] = symbol
                            reversion_diag['price_top_signal_type'] = signal_type or 'price_unlabeled'
                            reversion_diag['price_top_context'] = entry_context or 'price_unlabeled'
                    elif regime == 'RAW':
                        reversion_diag['raw_opportunities'] = reversion_diag.get('raw_opportunities', 0) + 1
                    elif mode == 'REVERSION':
                        reversion_diag['opportunities'] += 1
                    elif mode == 'MACHINE_GUN':
                        reversion_diag['mg_opportunities'] += 1
                    elif mode == 'SHOTGUN':
                        reversion_diag['shotgun_opportunities'] += 1
                    if regime == 'TRENDING':
                        reversion_diag['trend_opportunities'] += 1
                    if not is_experiment_allowed_lane(symbol, signal_type, mode, regime):
                        if is_strategy_lab_symbol(symbol) and regime == 'PRICE':
                            note_strategy_lab_near_miss(
                                reversion_diag,
                                symbol=symbol,
                                stage="not_allowed_lane",
                                reason=f"lane={signal_type or '-'}:{mode}:{regime}",
                                best_signal_type=signal_type or "",
                                best_confidence=confidence,
                                best_score=float(reversion_diag.get('price_best_score', 0.0) or 0.0),
                                emitted_signal=signal or "",
                                emitted_confidence=confidence,
                                emitted_signal_type=signal_type or "",
                                emitted_mode=mode,
                                emitted_regime=regime,
                            )
                        continue
                    opportunities.append((symbol, signal, confidence, mode, atr, regime, signal_type, entry_context))
                except:
                    pass

            # Overlap session bonus: allow more entries
            overlap_active = is_overlap_session()
            flat_book_rebuild = (
                rearm_active
                and book_stress["managed_positions"] == 0
                and book_stress["direct_positions"] == 0
            )

            opportunities.sort(key=lambda item: item[2], reverse=True)
            opportunities = prioritize_experimental_opportunities(opportunities)
            emit_price_candidate_records(
                cycle,
                opportunities,
                alleyway_state.get('entry_posture'),
                rearm_reason,
                free_margin_ratio,
                book_stress,
            )

            if winner_bags_this_cycle == 0:
                winner_bags_this_cycle += defend_crowd_win_bag_positions(
                    brain,
                    free_margin_ratio,
                    reversion_diag,
                    mode_counts,
                )
            financed_unwinds_this_cycle = 0
            anchor_unwinds_this_cycle = 0
            small_book_unwinds_this_cycle = 0
            same_symbol_cleanups_this_cycle = 0
            four_book_mixed_cleanups_this_cycle = 0
            three_book_same_symbol_cleanups_this_cycle = 0
            two_book_same_symbol_cleanups_this_cycle = 0
            two_book_mixed_cleanups_this_cycle = 0
            one_pos_exotic_mercy_exits_this_cycle = 0
            one_pos_index_mercy_exits_this_cycle = 0
            pinned_unwinds_this_cycle = 0
            crowd_unwinds_this_cycle = 0
            if winner_bags_this_cycle == 0:
                financed_unwinds_this_cycle = defend_financed_unwind_positions(
                    brain,
                    free_margin_ratio,
                    reversion_diag,
                    mode_counts,
                )
            if winner_bags_this_cycle == 0 and financed_unwinds_this_cycle == 0:
                anchor_unwinds_this_cycle = defend_anchor_unwind_positions(
                    brain,
                    free_margin_ratio,
                    reversion_diag,
                    mode_counts,
                )
                if anchor_unwinds_this_cycle == 0:
                    small_book_unwinds_this_cycle = defend_small_book_unwind_positions(
                        brain,
                        free_margin_ratio,
                        reversion_diag,
                        mode_counts,
                    )
                if anchor_unwinds_this_cycle == 0 and small_book_unwinds_this_cycle == 0:
                    same_symbol_cleanups_this_cycle = defend_same_symbol_cluster_cleanup_positions(
                        brain,
                        free_margin_ratio,
                        reversion_diag,
                        mode_counts,
                    )
                if (
                    anchor_unwinds_this_cycle == 0
                    and small_book_unwinds_this_cycle == 0
                    and same_symbol_cleanups_this_cycle == 0
                ):
                    four_book_mixed_cleanups_this_cycle = defend_four_book_mixed_cleanup_positions(
                        brain,
                        free_margin_ratio,
                        reversion_diag,
                        mode_counts,
                    )
                if (
                    anchor_unwinds_this_cycle == 0
                    and small_book_unwinds_this_cycle == 0
                    and same_symbol_cleanups_this_cycle == 0
                    and four_book_mixed_cleanups_this_cycle == 0
                ):
                    three_book_same_symbol_cleanups_this_cycle = defend_three_book_same_symbol_cleanup_positions(
                        brain,
                        free_margin_ratio,
                        reversion_diag,
                        mode_counts,
                    )
                if (
                    anchor_unwinds_this_cycle == 0
                    and small_book_unwinds_this_cycle == 0
                    and same_symbol_cleanups_this_cycle == 0
                    and four_book_mixed_cleanups_this_cycle == 0
                    and three_book_same_symbol_cleanups_this_cycle == 0
                ):
                    two_book_same_symbol_cleanups_this_cycle = defend_two_book_same_symbol_cleanup_positions(
                        brain,
                        free_margin_ratio,
                        mode_counts,
                    )
                if (
                    anchor_unwinds_this_cycle == 0
                    and small_book_unwinds_this_cycle == 0
                    and same_symbol_cleanups_this_cycle == 0
                    and four_book_mixed_cleanups_this_cycle == 0
                    and three_book_same_symbol_cleanups_this_cycle == 0
                    and two_book_same_symbol_cleanups_this_cycle == 0
                ):
                    two_book_mixed_cleanups_this_cycle = defend_two_book_mixed_cleanup_positions(
                        brain,
                        free_margin_ratio,
                        reversion_diag,
                        mode_counts,
                    )
                if (
                    anchor_unwinds_this_cycle == 0
                    and small_book_unwinds_this_cycle == 0
                    and same_symbol_cleanups_this_cycle == 0
                    and four_book_mixed_cleanups_this_cycle == 0
                    and three_book_same_symbol_cleanups_this_cycle == 0
                    and two_book_same_symbol_cleanups_this_cycle == 0
                    and two_book_mixed_cleanups_this_cycle == 0
                ):
                    one_pos_exotic_mercy_exits_this_cycle = defend_one_pos_exotic_mercy_exit_positions(
                        brain,
                        free_margin_ratio,
                        reversion_diag,
                    )
                if (
                    anchor_unwinds_this_cycle == 0
                    and small_book_unwinds_this_cycle == 0
                    and same_symbol_cleanups_this_cycle == 0
                    and four_book_mixed_cleanups_this_cycle == 0
                    and three_book_same_symbol_cleanups_this_cycle == 0
                    and two_book_same_symbol_cleanups_this_cycle == 0
                    and two_book_mixed_cleanups_this_cycle == 0
                    and one_pos_exotic_mercy_exits_this_cycle == 0
                ):
                    one_pos_index_mercy_exits_this_cycle = defend_one_pos_index_mercy_exit_positions(
                        brain,
                        free_margin_ratio,
                        reversion_diag,
                    )
                if (
                    anchor_unwinds_this_cycle == 0
                    and small_book_unwinds_this_cycle == 0
                    and same_symbol_cleanups_this_cycle == 0
                    and four_book_mixed_cleanups_this_cycle == 0
                    and three_book_same_symbol_cleanups_this_cycle == 0
                    and two_book_same_symbol_cleanups_this_cycle == 0
                    and two_book_mixed_cleanups_this_cycle == 0
                    and one_pos_exotic_mercy_exits_this_cycle == 0
                    and one_pos_index_mercy_exits_this_cycle == 0
                ):
                    pinned_unwinds_this_cycle = defend_pinned_unwind_positions(
                        brain,
                        free_margin_ratio,
                        reversion_diag,
                    )
                    crowd_unwinds_this_cycle = defend_crowd_unwind_positions(
                        brain,
                        free_margin_ratio,
                        reversion_diag,
                        mode_counts,
                    )

            # Open new positions
            entries_this_cycle = 0
            # Trade aggressively once a book is working, but stage flat-book rebuilds.
            max_entries_per_cycle = 10 if overlap_active else 8
            if rearm_active:
                max_entries_per_cycle += rearm_profile["extra_entry_slots"]
            if flat_book_rebuild:
                # Flat-book restart was the failure mode on the new account; rebuild in stages.
                max_entries_per_cycle = min(max_entries_per_cycle, FLAT_BOOK_REBUILD_MAX_ENTRIES)
            if rearm_active and not flat_book_rebuild and book_stress["managed_positions"] > 0:
                max_entries_per_cycle = min(max_entries_per_cycle, REARM_NONFLAT_ENTRY_CYCLE_CAP)
            if in_cooldown:
                max_entries_per_cycle = 1  # limit sniper rate during cooldown
            post_cleanup_hold_remaining, post_cleanup_hold_trigger = get_active_post_cleanup_holdoff()
            post_cleanup_quality_gate_active, post_cleanup_quality_gate_trigger = get_post_cleanup_quality_gate()
            post_cleanup_entry_freeze_active = post_cleanup_hold_remaining > 0
            post_cleanup_first_leg_hold_active = (
                len(active_positions) <= 1
                and time.time() < float(alleyway_state.get('post_cleanup_first_leg_rearm_hold_until', 0.0) or 0.0)
            )
            profit_capture_freeze_active = (
                len(active_positions) > 0
                and time.time() < float(alleyway_state.get('profit_capture_entry_freeze_until', 0.0) or 0.0)
            )
            two_book_pending_freeze_active = (
                len(active_positions) <= 2
                and time.time() < float(alleyway_state.get('defend_two_book_pending_entry_freeze_until', 0.0) or 0.0)
            )
            # Honor post-cleanup freezes strictly. Experimental relief was reopening
            # the book immediately after flat exits and defeating staged rebuilds.
            post_cleanup_experimental_relief_window = False
            if post_cleanup_entry_freeze_active and not post_cleanup_experimental_relief_window:
                max_entries_per_cycle = 0
            elif post_cleanup_quality_gate_active:
                max_entries_per_cycle = min(max_entries_per_cycle, POST_CLEANUP_QUALITY_MAX_ENTRIES)
            if post_cleanup_first_leg_hold_active and not post_cleanup_experimental_relief_window:
                max_entries_per_cycle = 0
            if profit_capture_freeze_active:
                max_entries_per_cycle = 0
            if two_book_pending_freeze_active:
                max_entries_per_cycle = 0
                if opportunities:
                    pending_left = max(
                        0,
                        int(
                            float(alleyway_state.get('defend_two_book_pending_entry_freeze_until', 0.0) or 0.0)
                            - time.time()
                        ),
                    )
                    log(
                        "  [TWO_BOOK_PENDING_FREEZE] "
                        f"active={len(active_positions)} opp={len(opportunities)} "
                        f"posture={alleyway_state.get('entry_posture')} "
                        f"freeze_left={pending_left}s"
                    )
            if circuit_breaker_active:
                max_entries_per_cycle = 0

            # === ASIAN SESSION ENTRY FILTER ===
            # London data proves +$6.78/trade vs -$14.84 off-session (4.6x edge)
            # During 00:00-07:00 UTC, only allow entries with confidence > 0.70
            current_utc_hour = datetime.now(timezone.utc).hour
            is_asian_session = current_utc_hour < 7 or current_utc_hour >= 23
            if is_asian_session:
                # Filter opportunities: only keep high-confidence signals
                opportunities = [
                    opp for opp in opportunities
                    if (
                        opp[2] >= ASIAN_SESSION_MIN_CONFIDENCE
                        or is_strategy_lab_lane(opp[0], opp[6], opp[3], opp[5])
                    )
                ]
                if not opportunities and cycle % 200 == 0:
                    log(
                        f"  [ASIAN_FILTER] All entries filtered — confidence < "
                        f"{ASIAN_SESSION_MIN_CONFIDENCE:.2f} at hour {current_utc_hour} UTC"
                    )

            cycle_opened_symbols = set()
            cycle_has_experimental_opportunity = any(
                regime in {'PRICE', 'RAW', 'GEMINI'}
                for *_head, regime, _signal_type, _entry_context in opportunities
            )
            for symbol, signal, confidence, mode, atr, regime, signal_type, entry_context in opportunities:
                if entries_this_cycle >= max_entries_per_cycle:
                    break

                try:
                    current_active_count = len(active_positions)
                    projected_active_count = current_active_count + 1
                    current_raw_positions = regime_counts.get('RAW', 0)
                    current_price_positions = regime_counts.get('PRICE', 0)
                    current_gemini_positions = regime_counts.get('GEMINI', 0)
                    current_experimental_regime_positions = (
                        current_price_positions
                        if regime == 'PRICE'
                        else (
                            current_raw_positions
                            if regime == 'RAW'
                            else (current_gemini_positions if regime == 'GEMINI' else 0)
                        )
                    ) if regime in {'PRICE', 'RAW', 'GEMINI'} else 0
                    current_experimental_mode_open = (
                        (regime == 'RAW' and current_raw_positions > 0)
                        or (regime == 'PRICE' and current_price_positions > 0)
                        or (regime == 'GEMINI' and current_gemini_positions > 0)
                    )
                    current_experimental_continuation_cap = DEFEND_EXPERIMENTAL_CONTINUATION_MAX_PER_REGIME
                    if (
                        regime in {'PRICE', 'RAW', 'GEMINI'}
                        and rearm_active
                        and alleyway_state.get("entry_posture") == "REARM"
                        and free_margin_ratio >= 0.70
                        and current_active_count <= DEFEND_EXPERIMENTAL_CONTINUATION_MAX_ACTIVE_POSITIONS
                    ):
                        current_experimental_continuation_cap = max(
                            current_experimental_continuation_cap,
                            REARM_EXPERIMENTAL_CONTINUATION_MAX_PER_REGIME,
                        )
                    current_post_cleanup_experimental_relief = (
                        regime in {'PRICE', 'RAW', 'GEMINI'}
                        and (post_cleanup_entry_freeze_active or post_cleanup_first_leg_hold_active)
                        and post_cleanup_experimental_relief_window
                        and current_active_count <= 1
                        and not current_experimental_mode_open
                        and (current_raw_positions + current_price_positions + current_gemini_positions) == 0
                    )
                    if post_cleanup_entry_freeze_active and not current_post_cleanup_experimental_relief:
                        continue
                    if post_cleanup_first_leg_hold_active and not current_post_cleanup_experimental_relief:
                        continue
                    if two_book_pending_freeze_active:
                        continue
                    current_legacy_experiment_priority_block = (
                        cycle_has_experimental_opportunity
                        and regime not in {'PRICE', 'RAW', 'GEMINI'}
                        and not all(m in DISABLED_MODES for m in ['PRICE', 'GEMINI'])  # 10x: skip block if experimental modes disabled
                    )
                    if current_legacy_experiment_priority_block:
                        if entries_this_cycle == 0:
                            log(
                                "  [EXPERIMENTAL_PRIORITY] "
                                f"blocking legacy {symbol} {mode} {signal} "
                                f"because PRICE/RAW opportunity exists this cycle"
                            )
                        continue
                    lane_recent_stats = (
                        get_competition_lane_recent_stats(regime)
                        if regime in {'PRICE', 'RAW', 'GEMINI'}
                        else None
                    )
                    lane_cluster_brake_active = (
                        lane_recent_stats is not None
                        and lane_recent_stats['trade_count'] >= COMPETITION_LANE_CLUSTER_MIN_EARLY_FAILS
                        and lane_recent_stats['early_fails'] >= COMPETITION_LANE_CLUSTER_MIN_EARLY_FAILS
                        and lane_recent_stats['wins'] == 0
                        and confidence < COMPETITION_LANE_CLUSTER_BRAKE_MIN_CONFIDENCE
                    )
                    if lane_cluster_brake_active:
                        blocker_logs = alleyway_state.setdefault('experimental_blocker_log_until', {})
                        blocker_key = f"lane_cluster_brake:{regime}"
                        now = time.time()
                        next_log_allowed = float(blocker_logs.get(blocker_key, 0.0) or 0.0)
                        if now >= next_log_allowed:
                            freshness = "fresh" if lane_recent_stats.get('fresh') else "stale"
                            log(
                                f"  [EXPERIMENTAL_BLOCKER] {symbol} regime={regime} "
                                f"reason=lane_early_fail_cluster conf={confidence:.2f} "
                                f"ef={lane_recent_stats['early_fails']} wins={lane_recent_stats['wins']} "
                                f"pnl={lane_recent_stats['realized_pnl']:+.2f} "
                                f"window={lane_recent_stats['trade_count']} {freshness}"
                            )
                            blocker_logs[blocker_key] = now + 30.0
                        reversion_diag['experimental_blocked_quality'] = reversion_diag.get('experimental_blocked_quality', 0) + 1
                        continue
                    current_first_direct_flat_shot = (
                        book_stress["managed_positions"] == 0
                        and current_active_count == 0
                    )
                    current_flat_book_rebuild = (
                        rearm_active
                        and current_first_direct_flat_shot
                    )
                    if (
                        rearm_active
                        and not current_flat_book_rebuild
                        and symbol in cycle_opened_symbols
                    ):
                        continue
                    current_effective_active_count = (
                        book_stress["direct_positions"]
                        + book_stress["adopted_positions"] * ADOPTED_POSITION_CAP_WEIGHT
                    )
                    current_effective_active_count += max(
                        0,
                        current_active_count - book_stress["managed_positions"],
                    )
                    if current_effective_active_count >= MAX_CONCURRENT_POSITIONS:
                        break

                    current_financed_shape_reason = str(
                        alleyway_state.get('defend_financed_unwind_last_shape_reason') or ''
                    )
                    current_financed_shape_logged_at = float(
                        alleyway_state.get('defend_financed_unwind_last_shape_logged_at', 0.0) or 0.0
                    )
                    # Live hotfix: generic DEFEND continuation relief was
                    # repeatedly re-seeding tiny RAW/SHOTGUN entries in quiet
                    # books. Keep only the much narrower shape-based relief.
                    current_small_defend_experimental_relief = False
                    current_loaded_defend_experimental_relief = False
                    current_experimental_shape_relief = (
                        regime in {'PRICE', 'RAW', 'GEMINI'}
                        and alleyway_state.get("entry_posture") == "DEFEND"
                        and current_active_count > 0
                        and free_margin_ratio >= DEFEND_EXPERIMENTAL_CONTINUATION_MIN_FREE_MARGIN_RATIO
                        and book_stress["managed_drawdown_pct"] <= REARM_MAX_MANAGED_DRAWDOWN_PCT
                        and current_active_count <= DEFEND_EXPERIMENTAL_CONTINUATION_MAX_ACTIVE_POSITIONS
                        and current_experimental_regime_positions < DEFEND_EXPERIMENTAL_CONTINUATION_MAX_PER_REGIME
                        and current_financed_shape_reason in DEFEND_EXPERIMENTAL_SHAPE_RELIEF_REASONS
                        and (time.time() - current_financed_shape_logged_at) <= DEFEND_EXPERIMENTAL_SHAPE_RELIEF_MAX_AGE_SECONDS
                    )
                    current_defend_experimental_relief = (
                        current_experimental_shape_relief
                        or current_small_defend_experimental_relief
                        or current_loaded_defend_experimental_relief
                    )

                    mode_config = FIRE_MODES[mode]
                    experimental_mode_floor = (
                        PRICE_PASS_CONFIDENCE if regime == 'PRICE' else mode_config['min_confidence']
                    )
                    current_defend_hard_freeze = (
                        alleyway_state.get("entry_posture") == "DEFEND"
                        and book_stress["managed_positions"] > 0
                        and current_active_count >= DEFEND_NO_EXPANSION_STRESS_MIN_POSITIONS
                        and not current_defend_experimental_relief
                    )
                    current_defend_cleanup_freeze = (
                        not current_flat_book_rebuild
                        and alleyway_state.get("entry_posture") == "DEFEND"
                        and book_stress["managed_positions"] > 0
                        and current_active_count >= DEFEND_CLEANUP_FREEZE_MIN_POSITIONS
                        and free_margin_ratio <= DEFEND_CLEANUP_FREEZE_MAX_FREE_MARGIN_RATIO
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
                        candidate_regime=regime,
                        current_price_positions=current_price_positions,
                        current_raw_positions=current_raw_positions,
                        current_gemini_positions=current_gemini_positions,
                    )
                    current_direct_positions = [
                        pdata for pdata in active_positions.values()
                        if not pdata.get('adopted')
                    ]
                    current_lone_direct_pnl = None
                    if len(current_direct_positions) == 1:
                        current_lone_direct_pnl = float(
                            current_direct_positions[0].get('last_pnl', 0.0) or 0.0
                        )
                    # Live proof on 2026-04-07 showed that a one-position red
                    # DEFEND state must block fresh adds immediately. A same-cycle
                    # REVERSION open after `one-pos-red` recreated the mixed
                    # 2-book quiet-book loop instead of resolving the survivor.
                    current_onepos_release_locked = (
                        current_lone_direct_pnl is not None
                        and current_lone_direct_pnl < ONE_POSITION_REARM_MIN_GREEN_PNL_USD
                    )
                    current_defend_onepos_no_add_active = (
                        not current_flat_book_rebuild
                        and alleyway_state.get("entry_posture") == "DEFEND"
                        and book_stress["managed_positions"] == 1
                        and current_active_count >= 1
                        and book_stress["direct_positions"] == 1
                        and current_onepos_release_locked
                    )
                    # Mirror the lone-red survivor guard in REARM so a single
                    # losing first leg cannot reopen the book through RAW/PRICE
                    # experimental continuation slots.
                    current_rearm_onepos_no_add_active = (
                        not current_flat_book_rebuild
                        and alleyway_state.get("entry_posture") == "REARM"
                        and book_stress["managed_positions"] == 1
                        and current_active_count >= 1
                        and book_stress["direct_positions"] == 1
                        and current_onepos_release_locked
                    )
                    current_defend_noexp_active = (
                        not current_flat_book_rebuild
                        and defend_no_expansion_active(free_margin_ratio, current_active_count)
                        and not current_defend_experimental_relief
                    )
                    current_defend_non_reversion_freeze = (
                        DEFEND_NONFLAT_BLOCK_NON_REVERSION
                        and alleyway_state.get("entry_posture") == "DEFEND"
                        and book_stress["managed_positions"] > 0
                        and mode != 'REVERSION'
                        and not current_defend_experimental_relief
                        # Allow non-REVERSION entries if book is healthy
                        # Competition mode: much more aggressive — only freeze on real stress
                        and (
                            free_margin_ratio < 0.30  # Only freeze below 30% free margin
                            or book_stress["managed_drawdown_pct"] > 0.08  # Or >8% drawdown
                            or book_stress["managed_positions"] >= 7  # Or 7+ positions
                        )
                    )
                    current_rearm_non_reversion_freeze = (
                        REARM_NONFLAT_BLOCK_NON_REVERSION
                        and not current_flat_book_rebuild
                        and rearm_active
                        and book_stress["managed_positions"] > 0
                        and mode != 'REVERSION'
                        and direct_non_reversion >= effective_rearm_max_non_reversion_direct
                    )
                    current_flat_rebuild_raw_shotgun_exception = (
                        regime == 'RAW'
                        and mode == 'SHOTGUN'
                        and confidence >= get_post_cleanup_raw_shotgun_min_confidence(symbol)
                    )
                    current_flat_rebuild_non_reversion_freeze = (
                        current_first_direct_flat_shot
                        and mode not in {'SNIPER', 'REVERSION', 'PRICE', 'MACHINE_GUN', 'GEMINI'}
                        and not current_flat_rebuild_raw_shotgun_exception
                    )
                    current_post_cleanup_quality_mode_block = (
                        post_cleanup_quality_gate_active
                        and current_flat_book_rebuild
                        and POST_CLEANUP_QUALITY_FIRST_WAVE_SNIPER_ONLY
                        and mode not in {'SNIPER', 'PRICE', 'MACHINE_GUN', 'GEMINI'}
                        and not current_flat_rebuild_raw_shotgun_exception
                    )
                    current_post_cleanup_mercy_rebuild = (
                        post_cleanup_quality_gate_active
                        and current_flat_book_rebuild
                        and is_one_pos_exotic_mercy_trigger(post_cleanup_quality_gate_trigger)
                    )
                    current_post_cleanup_quality_symbol_block = (
                        post_cleanup_quality_gate_active
                        and current_flat_book_rebuild
                        and symbol in POST_CLEANUP_QUALITY_BLOCKED_SYMBOLS
                    )
                    current_post_cleanup_quality_exotic_block = (
                        post_cleanup_quality_gate_active
                        and current_flat_book_rebuild
                        and POST_CLEANUP_QUALITY_BLOCK_EXOTICS
                        and is_exotic(symbol)
                    )
                    current_post_cleanup_mercy_symbol_block = (
                        current_post_cleanup_mercy_rebuild
                        and POST_CLEANUP_MERCY_FIRST_WAVE_BLOCK_EXOTICS
                        and is_exotic(symbol)
                    )
                    current_offense_quality_floor = 0.0
                    if not current_flat_book_rebuild:
                        if alleyway_state.get("entry_posture") == "DEFEND" and book_stress["managed_positions"] > 0:
                            if current_defend_experimental_relief and regime in {'PRICE', 'RAW', 'GEMINI'}:
                                current_offense_quality_floor = experimental_mode_floor
                            elif mode == 'MACHINE_GUN':
                                current_offense_quality_floor = DEFEND_MACHINE_GUN_MIN_CONFIDENCE
                            elif mode != 'REVERSION':
                                current_offense_quality_floor = DEFEND_NONFLAT_MIN_CONFIDENCE
                        elif rearm_active and book_stress["managed_positions"] > 0:
                            if mode == 'MACHINE_GUN':
                                current_offense_quality_floor = REARM_MACHINE_GUN_MIN_CONFIDENCE
                            else:
                                current_offense_quality_floor = REARM_NONFLAT_MIN_CONFIDENCE
                    current_defend_machine_gun_freeze = (
                        current_defend_non_reversion_freeze
                        and mode == 'MACHINE_GUN'
                    )
                    current_defend_reversion_rebuild_block = (
                        not current_flat_book_rebuild
                        and mode == 'REVERSION'
                        and book_stress["managed_positions"] > 0
                        and (
                            alleyway_state.get("entry_posture") == "DEFEND"
                            or rearm_active
                        )
                        and (
                            current_active_count >= DEFEND_REVERSION_REBUILD_MAX_POSITIONS
                            or free_margin_ratio < DEFEND_REVERSION_REBUILD_MIN_FREE_MARGIN_RATIO
                            or book_stress["managed_drawdown_pct"] > DEFEND_REVERSION_REBUILD_MAX_MANAGED_DRAWDOWN_PCT
                            or book_stress["top_symbol_drawdown_pct"] > DEFEND_REVERSION_REBUILD_MAX_TOP_SYMBOL_DRAWDOWN_PCT
                            or direct_non_reversion >= 7  # Competition mode: allow REVERSION alongside stressed book
                            or direct_losing_positions > DEFEND_REVERSION_REBUILD_MAX_LOSING_DIRECT_POSITIONS
                        )
                    )
                    current_rearm_rebuild_cap = (
                        not current_flat_book_rebuild
                        and rearm_active
                        and book_stress["managed_positions"] > 0
                        and current_active_count >= REARM_REBUILD_CAP_MIN_POSITIONS
                        and (
                            (
                                REARM_REBUILD_CAP_MIXED_BOOK_BLOCK
                                and (
                                    mode_counts.get('MACHINE_GUN', 0) > 0
                                    or mode_counts.get('SHOTGUN', 0) > 0
                                    or mode_counts.get('SNIPER', 0) > 0
                                )
                            )
                            or
                            free_margin_ratio < REARM_REBUILD_CAP_MIN_FREE_MARGIN_RATIO
                            or book_stress["managed_drawdown_pct"] > REARM_REBUILD_CAP_MAX_MANAGED_DRAWDOWN_PCT
                            or book_stress["top_symbol_drawdown_pct"] > REARM_REBUILD_CAP_MAX_TOP_SYMBOL_DRAWDOWN_PCT
                        )
                    )
                    current_experimental_pair_slot = (
                        regime in {'RAW', 'PRICE', 'GEMINI'}
                        and (
                            (
                                rearm_active
                                and alleyway_state.get("entry_posture") == "REARM"
                                and free_margin_ratio >= 0.30  # Lowered for competition
                                and book_stress["managed_positions"] <= 30  # Increased for larger books
                            )
                            or current_defend_experimental_relief
                            # Also allow in DEFEND if PRICE/RAW has high-confidence signal
                            or (
                                alleyway_state.get("entry_posture") == "DEFEND"
                                and free_margin_ratio >= DEFEND_COMPETITION_EXPERIMENTAL_MIN_FREE_MARGIN_RATIO
                                and current_effective_active_count <= DEFEND_COMPETITION_EXPERIMENTAL_MAX_ACTIVE_POSITIONS
                                and book_stress["managed_drawdown_pct"] <= 0.75  # Relaxed for competition
                        )
                        )
                        and current_experimental_regime_positions < current_experimental_continuation_cap
                        and (
                            current_raw_positions + current_price_positions + current_gemini_positions
                            < DEFEND_COMPETITION_EXPERIMENTAL_TOTAL_CAP
                        )
                    )

                    # Don't block REVERSION on flat book — mean-reversion IS the rebuild engine
                    if cluster_cooldown_active and mode == 'REVERSION' and not rearm_active:
                        reversion_diag['blocked_cluster'] += 1
                        continue

                    # Allow high-confidence PRICE/RAW even when rebuild_cap is hit
                    high_conf_experimental = (
                        regime in {'RAW', 'PRICE'} 
                        and confidence >= 0.60 
                    )
                    if current_rearm_rebuild_cap and not current_experimental_pair_slot and not high_conf_experimental:
                        reversion_diag['blocked_rearm_rebuild_cap'] += 1
                        continue

                    if current_post_cleanup_experimental_relief:
                        log(
                            "  [POST_CLEANUP_EXPERIMENTAL_RELIEF] "
                            f"{symbol} {regime} {signal} conf={confidence:.2f} "
                            f"posture={alleyway_state.get('entry_posture')} "
                            f"active={current_active_count} "
                            f"holdoff={post_cleanup_hold_remaining}s "
                            f"first_leg={'yes' if post_cleanup_first_leg_hold_active else 'no'}"
                        )

                    if current_flat_rebuild_non_reversion_freeze:
                        if regime in {'PRICE', 'RAW', 'GEMINI'}:
                            blocker_logs = alleyway_state.setdefault('experimental_blocker_log_until', {})
                            blocker_key = f"flat_rebuild_quality:{symbol}:{regime}:{signal}"
                            now = time.time()
                            next_log_allowed = float(blocker_logs.get(blocker_key, 0.0) or 0.0)
                            if now >= next_log_allowed:
                                log(
                                    f"  [EXPERIMENTAL_BLOCKER] {symbol} regime={regime} "
                                    f"reason=flat_rebuild_non_reversion_freeze signal={signal} "
                                    f"mode={mode} conf={confidence:.2f}"
                                )
                                blocker_logs[blocker_key] = now + 30.0
                            emit_blocked_quality_candidate_record(
                                symbol=symbol,
                                regime=regime,
                                signal=signal,
                                mode=mode,
                                confidence=confidence,
                                reason="flat_rebuild_non_reversion_freeze",
                                trigger=post_cleanup_quality_gate_trigger,
                                entry_posture=alleyway_state.get("entry_posture", ""),
                            )
                            reversion_diag['experimental_blocked_quality'] = (
                                reversion_diag.get('experimental_blocked_quality', 0) + 1
                            )
                        if mode == 'REVERSION':
                            reversion_diag['blocked_rearm_rebuild_cap'] += 1
                        continue

                    if current_post_cleanup_quality_mode_block:
                        if regime in {'PRICE', 'RAW', 'GEMINI'}:
                            blocker_logs = alleyway_state.setdefault('experimental_blocker_log_until', {})
                            blocker_key = f"post_cleanup_quality_mode:{symbol}:{regime}:{signal}"
                            now = time.time()
                            next_log_allowed = float(blocker_logs.get(blocker_key, 0.0) or 0.0)
                            if now >= next_log_allowed:
                                log(
                                    f"  [EXPERIMENTAL_BLOCKER] {symbol} regime={regime} "
                                    f"reason=post_cleanup_quality_mode signal={signal} "
                                    f"mode={mode} conf={confidence:.2f} "
                                    f"trigger={post_cleanup_quality_gate_trigger or 'unknown'}"
                                )
                                blocker_logs[blocker_key] = now + 30.0
                            emit_blocked_quality_candidate_record(
                                symbol=symbol,
                                regime=regime,
                                signal=signal,
                                mode=mode,
                                confidence=confidence,
                                reason="post_cleanup_quality_mode",
                                trigger=post_cleanup_quality_gate_trigger,
                                entry_posture=alleyway_state.get("entry_posture", ""),
                            )
                            reversion_diag['experimental_blocked_quality'] = (
                                reversion_diag.get('experimental_blocked_quality', 0) + 1
                            )
                        continue

                    if current_post_cleanup_quality_symbol_block:
                        continue

                    if current_post_cleanup_quality_exotic_block:
                        continue

                    if current_post_cleanup_mercy_symbol_block:
                        continue

                    current_quiet_book_raw_shotgun_signal_block = (
                        not current_flat_book_rebuild
                        and rearm_active
                        and str(rearm_reason or "").startswith("quiet-book")
                        and regime == 'RAW'
                        and mode == 'SHOTGUN'
                        and (symbol, signal_type) in QUIET_BOOK_RAW_SHOTGUN_SIGNAL_BLOCKLIST
                    )
                    if current_quiet_book_raw_shotgun_signal_block:
                        reversion_diag['experimental_blocked_quality'] = (
                            reversion_diag.get('experimental_blocked_quality', 0) + 1
                        )
                        continue

                    current_rearm_low_conf_raw_shotgun_signal_block = (
                        rearm_active
                        and regime == 'RAW'
                        and mode == 'SHOTGUN'
                        and confidence < REARM_RAW_SHOTGUN_LOW_CONF_MAX_CONFIDENCE
                        and (symbol, signal_type) in REARM_RAW_SHOTGUN_LOW_CONF_SIGNAL_BLOCKLIST
                    )
                    if current_rearm_low_conf_raw_shotgun_signal_block:
                        reversion_diag['experimental_blocked_quality'] = (
                            reversion_diag.get('experimental_blocked_quality', 0) + 1
                        )
                        continue

                    if confidence < current_offense_quality_floor:
                        # Competition: Let PRICE/RAW/GEMINI pass if they cleared lane admission
                        if not (regime in {'PRICE', 'RAW', 'GEMINI'} and confidence >= experimental_mode_floor):
                            reversion_diag['blocked_confidence_gate'] += 1
                            continue

                    if current_defend_cleanup_freeze:
                        reversion_diag['blocked_defend_cleanup'] += 1
                        continue

                    if current_defend_loaded_no_add_active:
                        reversion_diag['blocked_defend_loaded'] += 1
                        continue

                    if current_defend_onepos_no_add_active:
                        reversion_diag['blocked_defend_onepos'] += 1
                        continue

                    if current_rearm_onepos_no_add_active:
                        reversion_diag['blocked_defend_onepos'] += 1
                        continue

                    if current_defend_machine_gun_freeze:
                        reversion_diag['blocked_defend_mg'] += 1
                        continue

                    if current_defend_non_reversion_freeze:
                        reversion_diag['blocked_defend_mg'] += 1
                        continue

                    if mode == 'GEMINI' and GEMINI_NEW_ENTRY_DISABLED:
                        continue

                    if current_rearm_non_reversion_freeze and not current_experimental_pair_slot:
                        reversion_diag['blocked_rearm_rebuild_cap'] += 1
                        continue

                    if current_experimental_pair_slot:
                        reversion_diag['experimental_pair_slots'] += 1
                        if current_small_defend_experimental_relief:
                            log(
                                "  [DEFEND_EXPERIMENTAL_RELIEF] "
                                f"{symbol} {regime} {signal} conf={confidence:.2f} "
                                f"active={current_active_count} fm={free_margin_ratio:.2f} "
                                f"dd={book_stress['managed_drawdown_pct']:.3f}"
                            )

                    if current_defend_hard_freeze:
                        if mode == 'REVERSION':
                            reversion_diag['blocked_defend_noexp'] += 1
                            if reversion_diag['blocked_defend_noexp'] == 1:
                                log(f"  [DEBUG_BLOCK] defend_hard_freeze: active={current_active_count} thresh={DEFEND_NO_EXPANSION_STRESS_MIN_POSITIONS} posture={alleyway_state.get('entry_posture')} managed_pos={book_stress['managed_positions']}")
                        continue

                    if current_defend_noexp_active:
                        if mode == 'REVERSION':
                            reversion_diag['blocked_defend_noexp'] += 1
                        continue

                    if current_defend_reversion_rebuild_block:
                        reversion_diag['blocked_defend_noexp'] += 1
                        continue

                    critical_margin_loaded_book = (
                        not current_flat_book_rebuild
                        and free_margin_ratio <= CRITICAL_MARGIN_NO_ADD_RATIO
                    )
                    if critical_margin_loaded_book:
                        if mode == 'REVERSION':
                            reversion_diag['blocked_portfolio_guard'] = reversion_diag.get('blocked_portfolio_guard', 0) + 1
                        continue

                    if mode == 'REVERSION':
                        if alleyway_state.get("entry_posture") == "REARM":
                            if (
                                not current_flat_book_rebuild
                                and (
                                    mode_counts['REVERSION'] >= REVERSION_STRESS_MAX_POSITIONS
                                    or mode_counts['REVERSION'] / max(1, current_active_count) >= REVERSION_STRESS_MAX_BOOK_SHARE
                                )
                            ):
                                reversion_diag['blocked_crowding'] += 1
                                continue
                        else:
                            defend_loaded_book = (
                                not current_flat_book_rebuild
                                and (
                                    alleyway_state.get("entry_posture") == "DEFEND"
                                    or free_margin_ratio <= REVERSION_STRESS_MAX_FREE_MARGIN_RATIO
                                )
                            )
                            active_book_count = max(1, current_active_count)
                            reversion_share = mode_counts['REVERSION'] / active_book_count
                            if defend_loaded_book and (
                                mode_counts['REVERSION'] >= REVERSION_STRESS_MAX_POSITIONS
                                or reversion_share >= REVERSION_STRESS_MAX_BOOK_SHARE
                            ):
                                reversion_diag['blocked_crowding'] += 1
                                continue

                    # Mode capacity check
                    if mode_counts[mode] >= mode_config['max_positions']:
                        if mode == 'REVERSION':
                            reversion_diag['blocked_mode_cap'] = reversion_diag.get('blocked_mode_cap', 0) + 1
                        continue

                    # Per-symbol limit
                    symbol_positions = [p for p in active_positions.values() if p['symbol'] == symbol]
                    same_symbol_raw_positions = sum(
                        1 for p in symbol_positions if (p.get('entry_regime') or '').upper() == 'RAW'
                    )
                    if regime == 'RAW' and signal_type == 'trend_continuation':
                        blocker_logs = alleyway_state.setdefault('experimental_blocker_log_until', {})
                        if same_symbol_raw_positions > 0:
                            blocker_key = f"raw_quality_same_symbol:{symbol}"
                            now = time.time()
                            next_log_allowed = float(blocker_logs.get(blocker_key, 0.0) or 0.0)
                            if now >= next_log_allowed:
                                log(
                                    f"  [EXPERIMENTAL_BLOCKER] {symbol} regime=RAW "
                                    f"reason=trend_followon_same_symbol existing_raw={same_symbol_raw_positions}"
                                )
                                blocker_logs[blocker_key] = now + 30.0
                            reversion_diag['experimental_blocked_quality'] = reversion_diag.get('experimental_blocked_quality', 0) + 1
                            continue
                        if (
                            rearm_active
                            and not current_flat_book_rebuild
                            and current_raw_positions >= RAW_TREND_FOLLOWON_BOOK_MIN_RAW_POSITIONS
                            and confidence < RAW_TREND_FOLLOWON_MIN_CONFIDENCE
                        ):
                            blocker_key = f"raw_quality_wave:{symbol}"
                            now = time.time()
                            next_log_allowed = float(blocker_logs.get(blocker_key, 0.0) or 0.0)
                            if now >= next_log_allowed:
                                log(
                                    f"  [EXPERIMENTAL_BLOCKER] {symbol} regime=RAW "
                                    f"reason=trend_followon_wave conf={confidence:.2f} "
                                    f"raw_positions={current_raw_positions}"
                                )
                                blocker_logs[blocker_key] = now + 30.0
                            reversion_diag['experimental_blocked_quality'] = reversion_diag.get('experimental_blocked_quality', 0) + 1
                            continue
                    if len(symbol_positions) >= MAX_POSITIONS_PER_SYMBOL:
                        if mode == 'REVERSION':
                            reversion_diag['blocked_symbol_cap'] = reversion_diag.get('blocked_symbol_cap', 0) + 1
                        continue

                    # Per-symbol exposure limit — max 2% of equity at risk per symbol
                    symbol_total_lot = sum(p.get('volume', 0) for p in symbol_positions)

                    # Correlation check
                    if not check_correlation_limit(symbol):
                        if mode == 'REVERSION':
                            reversion_diag['blocked_correlation'] += 1
                        continue

                    if mode in ('REVERSION', 'MACHINE_GUN') and not REVERSION_ALLOW_EXOTICS and is_exotic(symbol):
                        reversion_diag['blocked_exotic'] += 1
                        continue

                    # Anti-death-spiral: skip recently stress-trimmed symbols
                    last_trim = recently_trimmed_symbols.get(symbol, 0)
                    if time.time() - last_trim < TRIM_COOLDOWN_SECONDS:
                        if mode == 'REVERSION':
                            reversion_diag['blocked_trim_cooldown'] += 1
                        continue

                    symbol_freeze_until = float(
                        get_alleyway_mapping('defend_three_book_win_bag_symbol_freeze_until').get(symbol, 0.0) or 0.0
                    )
                    if time.time() < symbol_freeze_until:
                        continue

                    two_book_symbol_freeze_until = float(
                        get_alleyway_mapping('defend_two_book_win_bag_symbol_freeze_until').get(symbol, 0.0) or 0.0
                    )
                    if time.time() < two_book_symbol_freeze_until:
                        continue

                    sync_close_symbol_freeze_until = float(
                        get_alleyway_mapping('sync_close_reentry_symbol_freeze_until').get(str(symbol or '').upper(), 0.0) or 0.0
                    )
                    if time.time() < sync_close_symbol_freeze_until:
                        # Carveout: allow high-confidence PRICE/RAW to bypass symbol freeze
                        if not (regime in {'PRICE', 'RAW', 'GEMINI'} and confidence >= 0.65):
                            if regime in {'PRICE', 'RAW', 'GEMINI'} and current_experimental_pair_slot:
                                blocker_logs = alleyway_state.setdefault('experimental_blocker_log_until', {})
                                blocker_key = f"sync_close_freeze:{symbol}"
                                now = time.time()
                                next_log_allowed = float(blocker_logs.get(blocker_key, 0.0) or 0.0)
                                if now >= next_log_allowed:
                                    remaining_seconds = max(1, int(round(sync_close_symbol_freeze_until - now)))
                                    log(
                                        f"  [EXPERIMENTAL_BLOCKER] {symbol} regime={regime} "
                                        f"reason=sync_close_freeze remaining={remaining_seconds}s"
                                    )
                                    blocker_logs[blocker_key] = now + 30.0
                            continue

                    sync_close_family = get_symbol_family_bucket(symbol)
                    sync_close_family_freeze_until = (
                        float(
                            get_alleyway_mapping('sync_close_reentry_family_freeze_until').get(sync_close_family, 0.0) or 0.0
                        )
                        if sync_close_family
                        else 0.0
                    )
                    if time.time() < sync_close_family_freeze_until:
                        # Carveout: allow high-confidence PRICE/RAW to bypass family freeze
                        if not (regime in {'PRICE', 'RAW', 'GEMINI'} and confidence >= 0.65):
                            continue

                    stress = get_symbol_stress(symbol)
                    first_direct_rearm_shot = current_first_direct_flat_shot

                    # Brain adaptation (use equity-based lot as base)
                    base_lot = calc_equity_lot(symbol, mode, atr, equity)
                    entry_params = brain.get_entry_params(symbol, effective_adaptive_threshold, base_lot)
                    priority_lane_match = (
                        build_lane_key(symbol, signal_type, mode, regime) in EXPERIMENTAL_PRIORITY_LANES
                    )
                    if not entry_params["allowed"]:
                        block_reason = entry_params.get('reason', 'unknown')
                        if should_bypass_brain_cooldown_for_symbol_override(
                            symbol=symbol,
                            regime=regime,
                            mode=mode,
                            confidence=confidence,
                            block_reason=block_reason,
                            flat_book_rebuild=current_flat_book_rebuild,
                            post_cleanup_quality_gate_active=post_cleanup_quality_gate_active,
                        ):
                            log(
                                f"  BRAIN_BYPASS [{symbol}] mode={mode} "
                                f"reason={block_reason} conf={confidence:.2f}"
                            )
                            entry_params = {
                                **entry_params,
                                "allowed": True,
                                "confidence_threshold": effective_adaptive_threshold,
                                "lot_size": base_lot,
                            }
                        elif should_bypass_brain_cooldown_for_priority_lane(
                            symbol=symbol,
                            regime=regime,
                            mode=mode,
                            signal_type=signal_type,
                            confidence=confidence,
                            block_reason=block_reason,
                            flat_book_rebuild=current_flat_book_rebuild,
                            post_cleanup_quality_gate_active=post_cleanup_quality_gate_active,
                        ):
                            log(
                                f"  BRAIN_BYPASS_PRIORITY [{symbol}] mode={mode} "
                                f"signal={signal_type or '-'} reason={block_reason} "
                                f"conf={confidence:.2f}"
                            )
                            emit_strategy_lab_event(
                                event_type="brain_bypass_priority",
                                symbol=symbol,
                                signal_type=signal_type,
                                mode=mode,
                                regime=regime,
                                confidence=confidence,
                                reason=str(block_reason or ""),
                                flat_rebuild=bool(current_flat_book_rebuild),
                                quality_gate=bool(post_cleanup_quality_gate_active),
                            )
                            entry_params = {
                                **entry_params,
                                "allowed": True,
                                "confidence_threshold": effective_adaptive_threshold,
                                "lot_size": base_lot,
                            }
                        else:
                            log(
                                f"  BRAIN_BLOCK [{symbol}] mode={mode} signal={signal_type or '-'} "
                                f"regime={regime} conf={confidence:.2f} "
                                f"flat_rebuild={'yes' if current_flat_book_rebuild else 'no'} "
                                f"quality_gate={'yes' if post_cleanup_quality_gate_active else 'no'} "
                                f"priority_lane={'yes' if priority_lane_match else 'no'} "
                                f"reason={block_reason}"
                            )
                            emit_strategy_lab_event(
                                event_type="brain_block",
                                symbol=symbol,
                                signal_type=signal_type,
                                mode=mode,
                                regime=regime,
                                confidence=confidence,
                                reason=str(block_reason or ""),
                                flat_rebuild=bool(current_flat_book_rebuild),
                                quality_gate=bool(post_cleanup_quality_gate_active),
                            )
                            if mode == 'REVERSION':
                                reversion_diag['blocked_brain'] = reversion_diag.get('blocked_brain', 0) + 1
                            continue

                    stress_relief = 0.0
                    if rearm_active and stress["drawdown_share"] < 0.25 and stress["score"] < 0.75:
                        stress_relief = rearm_profile["stress_relief"]

                    # === BRAIN NEEDS WIDER STOPS SIGNAL ===
                    # When the brain detects a symbol is getting chopped by tight stops
                    # (STOP_HIT/TIGHT_STOP pattern), apply a temporary confidence bump
                    # to reduce entry frequency until the oscillation calms.
                    # This wires up a signal the brain was already computing but nobody consumed.
                    wider_stops_bump = 0.0
                    if entry_params.get("needs_wider_stops", False):
                        wider_stops_bump = 0.05  # Raise bar by 5% when stops are too tight
                        if cycle % 100 == 0:
                            log(
                                f"  WIDER_STOPS [{symbol}] brain detected tight-stop chop, "
                                f"bumping required confidence +{wider_stops_bump:.2f}"
                            )

                    confidence_bump = min(
                        0.05,  # Tight cap: adaptive(0.40) + bump(0.05) = 0.45 reachable by MG signals
                        SYMBOL_STRESS_CONFIDENCE_BUMP_MAX,
                        stress["score"] * 0.12 + stress["drawdown_share"] * 0.18,
                    )
                    if stress["all_losing"]:
                        confidence_bump = min(
                            0.05,  # Same tight cap
                            SYMBOL_STRESS_CONFIDENCE_BUMP_MAX,
                            confidence_bump + 0.02,
                        )
                    confidence_bump *= (1.0 - stress_relief)

                    # === SYMBOL_SIGNAL_WHITELIST BONUS ===
                    # Proven winning combos get a confidence bonus, increasing entry rate
                    # without sacrificing quality. Based on 690 fresh-entry trades analysis.
                    whitelist_bonus = 0.0
                    if (symbol, signal_type) in SYMBOL_SIGNAL_WHITELIST:
                        whitelist_bonus = 0.03  # Proven combos enter more easily
                    elif (symbol, '*') in SYMBOL_SIGNAL_WHITELIST:
                        whitelist_bonus = 0.02  # Symbol-level whitelist

                    mode_floor = PRICE_PASS_CONFIDENCE if regime == 'PRICE' else mode_config['min_confidence']
                    if rearm_active:
                        mode_floor = max(
                            MIN_CONFIDENCE_MIN,
                            mode_floor - rearm_profile["mode_floor_relief"],
                        )
                    # Cap brain's confidence threshold so it can't override mode floor by more than 0.10
                    brain_confidence = entry_params["confidence_threshold"]
                    max_brain_override = mode_floor + 0.10
                    brain_confidence = min(brain_confidence, max_brain_override)
                    required_confidence = max(
                        mode_floor,
                        brain_confidence,
                        effective_adaptive_threshold + confidence_bump,
                    )
                    # Wider stops bump: brain detected tight-stop chop, raise the bar
                    if wider_stops_bump > 0.0:
                        required_confidence = min(0.95, required_confidence + wider_stops_bump)
                    # Whitelist bonus: proven winning combos need slightly less confidence to enter
                    if whitelist_bonus > 0:
                        required_confidence = max(mode_floor, required_confidence - whitelist_bonus)
                    if first_direct_rearm_shot:
                        required_confidence = min(
                            0.95,
                            required_confidence + REARM_FIRST_DIRECT_CONFIDENCE_BUMP,
                        )
                        required_confidence = max(
                            required_confidence,
                            REARM_FIRST_DIRECT_MIN_CONFIDENCE,
                        )
                    if post_cleanup_quality_gate_active and current_flat_book_rebuild:
                        required_confidence = min(
                            0.95,
                            required_confidence + POST_CLEANUP_QUALITY_CONFIDENCE_BUMP,
                        )
                        required_confidence = max(
                            required_confidence,
                            POST_CLEANUP_QUALITY_MIN_CONFIDENCE,
                        )
                    if current_post_cleanup_mercy_rebuild:
                        required_confidence = min(
                            0.95,
                            required_confidence + POST_CLEANUP_MERCY_CONFIDENCE_BUMP,
                        )
                    if regime in {'PRICE', 'RAW', 'GEMINI'}:
                        # Experimental lanes earned admission at their lane thresholds.
                        # Do not let the later shared gate silently re-raise that bar
                        # in non-flat books and kill honest passes.
                        required_confidence = mode_floor
                    extreme_symbol_stress = (
                        stress["drawdown_share"] >= SYMBOL_STRESS_EXTREME_DRAWDOWN_SHARE
                        or (
                            stress["score"] >= SYMBOL_STRESS_EXTREME_SCORE
                            and stress["position_ratio"] >= 0.60
                        )
                    )
                    if extreme_symbol_stress and confidence < 0.92:
                        continue
                    # Add epsilon tolerance for floating-point precision (0.70 vs 0.69999)
                    if confidence < required_confidence - 0.01:
                        if regime in {'PRICE', 'RAW', 'GEMINI'}:
                            reversion_diag['experimental_blocked_late_confidence'] = reversion_diag.get('experimental_blocked_late_confidence', 0) + 1
                            if regime == 'PRICE':
                                reversion_diag['price_blocked_late_confidence'] = reversion_diag.get('price_blocked_late_confidence', 0) + 1
                            elif regime == 'RAW':
                                reversion_diag['raw_blocked_late_confidence'] = reversion_diag.get('raw_blocked_late_confidence', 0) + 1
                        if mode == 'REVERSION':
                            reversion_diag['blocked_confidence_gate'] += 1
                        if cycle % 50 == 0 and entries_this_cycle == 0:
                            log(f"  CONF_GATE [{mode}] {symbol} conf={confidence:.2f} < req={required_confidence:.2f} (floor={mode_floor:.2f}, adapt={effective_adaptive_threshold:.2f})")
                        continue

                    # Use brain-adjusted lot (which scales off equity-based lot)
                    lot_reduction = min(
                        SYMBOL_STRESS_LOT_REDUCTION_MAX,
                        stress["score"] * 0.28 + stress["drawdown_share"] * 0.32,
                    )
                    lot_reduction *= (1.0 - stress_relief)
                    lot = max(0.01, round(entry_params["lot_size"] * (1.0 - lot_reduction), 2))

                    if mode == 'REVERSION':
                        # Cap REVERSION lots — mean-reversion catches small bounces,
                        # not home runs. Keep lots small and manageable.
                        lot = max(0.01, round(lot * REVERSION_LOT_SCALE, 2))
                        # Hard cap: max 0.15 lots for REVERSION (small bounces only)
                        lot = min(lot, 0.15)
                        lot = max(0.01, round(lot, 2))

                    if first_direct_rearm_shot:
                        sym_info = mt5.symbol_info(symbol)
                        if not sym_info:
                            continue
                        guarded_lot = round(entry_params["lot_size"] * REARM_FIRST_DIRECT_LOT_SCALE, 2)
                        guarded_lot = max(sym_info.volume_min, guarded_lot)
                        if sym_info.volume_step > 0:
                            guarded_lot = round(
                                round(guarded_lot / sym_info.volume_step) * sym_info.volume_step,
                                2,
                            )
                        lot = min(lot, guarded_lot)

                    if post_cleanup_quality_gate_active and current_flat_book_rebuild:
                        lot = max(0.01, round(lot * POST_CLEANUP_QUALITY_LOT_SCALE, 2))
                    if current_post_cleanup_mercy_rebuild:
                        lot = max(0.01, round(lot * POST_CLEANUP_MERCY_LOT_SCALE, 2))

                    lot = clamp_trade_lot(symbol, mode, lot, atr=atr, equity=equity)
                    strategy_lab_variant = get_strategy_lab_variant_label(symbol, signal_type, mode, regime)
                    strategy_lab_meta = get_strategy_lab_lane_meta(symbol, signal_type, mode, regime)
                    if strategy_lab_variant:
                        strategy_lab_lane_config = get_active_strategy_lab_lane_config(
                            symbol,
                            signal_type,
                            mode,
                            regime,
                        ) or {}
                        lot = clamp_trade_lot(
                            symbol,
                            mode,
                            float(strategy_lab_lane_config.get("probationary_lot", 0.01) or 0.01),
                            atr=atr,
                            equity=equity,
                        )

                    if symbol_total_lot > 0 and atr > 0:
                        sym_info_exposure = mt5.symbol_info(symbol)
                        if sym_info_exposure and sym_info_exposure.trade_tick_value > 0 and sym_info_exposure.trade_tick_size > 0:
                            mode_cfg_exposure = FIRE_MODES[mode]
                            sl_dist_exposure = atr * mode_cfg_exposure['sl_atr_mult']
                            sl_ticks = sl_dist_exposure / sym_info_exposure.trade_tick_size
                            risk_dollar = sl_ticks * sym_info_exposure.trade_tick_value * (symbol_total_lot + lot)
                            if risk_dollar > equity * MAX_SYMBOL_EXPOSURE_PCT:
                                if mode == 'REVERSION':
                                    reversion_diag['blocked_exposure'] = reversion_diag.get('blocked_exposure', 0) + 1
                                continue

                    # Spread re-check at entry time
                    tick = mt5.symbol_info_tick(symbol)
                    if not tick:
                        continue
                    spread_pct = abs(tick.ask - tick.bid) / tick.ask * 100
                    
                    # Distinguish spread limits
                    if is_crypto(symbol):
                        max_spread = MAX_SPREAD_PCT_CRYPTO
                    elif is_exotic(symbol):
                        max_spread = MAX_SPREAD_PCT_EXOTIC * EXOTIC_SPREAD_MULTIPLIER  # Exotics need 3x tighter
                    else:
                        max_spread = MAX_SPREAD_PCT_FOREX
                        
                    # Spread-Adjusted Lot Scaling:
                    # If spread > 50% of max, reduce lot size proportional to the "spread tax"
                    if spread_pct > max_spread * 0.5:
                        spread_ratio = (spread_pct - (max_spread * 0.5)) / (max_spread * 0.5)
                        lot_penalty = min(0.5, spread_ratio * 0.5) # Max 50% reduction
                        original_lot = lot
                        lot = max(0.01, round(lot * (1.0 - lot_penalty), 2))
                        if lot != original_lot:
                            log(f"  [SPREAD_SCALING] {symbol} lot {original_lot} -> {lot} due to {spread_pct:.3f}% spread")

                    lot = clamp_trade_lot(symbol, mode, lot, atr=atr, equity=equity)

                    if spread_pct > max_spread * 1.2:  # Hard limit (allow 20% slippage buffer)
                        if regime in {'PRICE', 'RAW', 'GEMINI'}:
                            reversion_diag['experimental_blocked_spread'] = reversion_diag.get('experimental_blocked_spread', 0) + 1
                        continue

# Spread vs stop-distance filter:
                    # If spread eats > 30% of the stop distance, the trade is mathematically doomed
                    # Compute SL/TP first to get stop distance
                    proposed_entry_price = tick.ask if signal == 'BUY' else tick.bid
                    sl_price, tp_price = calc_sl_tp_prices(
                        symbol,
                        signal,
                        proposed_entry_price,
                        atr,
                        mode,
                    )
                    stop_distance = abs(proposed_entry_price - sl_price) if sl_price else 0
                    spread_stop_ratio_limit = SPREAD_VS_STOP_MAX_RATIO
                    if first_direct_rearm_shot:
                        spread_stop_ratio_limit = min(
                            spread_stop_ratio_limit,
                            REARM_FIRST_DIRECT_MAX_SPREAD_STOP_RATIO,
                        )
                    if post_cleanup_quality_gate_active and current_flat_book_rebuild:
                        spread_stop_ratio_limit = min(
                            spread_stop_ratio_limit,
                            POST_CLEANUP_QUALITY_MAX_SPREAD_STOP_RATIO,
                        )
                    if stop_distance > 0 and (tick.ask - tick.bid) > stop_distance * spread_stop_ratio_limit:
                        if regime in {'PRICE', 'RAW', 'GEMINI'}:
                            reversion_diag['experimental_blocked_spread'] = reversion_diag.get('experimental_blocked_spread', 0) + 1
                        log(
                            f"  [SPREAD_VS_STOP] {symbol} spread {tick.ask - tick.bid:.5f} > "
                            f"{spread_stop_ratio_limit*100:.0f}% of stop distance {stop_distance:.5f} — SKIP"
                        )
                        continue

# Symbol learner consultation — skip if learner says this symbol is on cooldown
                    # COMPETITION: Bypass cooldown for high-confidence experimental lanes
                    learner = get_learner()
                    cooldown_remaining = learner.get_cooldown(symbol)
                    if cooldown_remaining is not None:
                        if regime in {'PRICE', 'RAW', 'GEMINI'} and current_experimental_pair_slot:
                            blocker_logs = alleyway_state.setdefault('experimental_blocker_log_until', {})
                            blocker_key = f"learner_cooldown:{symbol}"
                            now = time.time()
                            next_log_allowed = float(blocker_logs.get(blocker_key, 0.0) or 0.0)
                            if now >= next_log_allowed:
                                log(
                                    f"  [EXPERIMENTAL_BLOCKER] {symbol} regime={regime} "
                                    f"reason=learner_cooldown remaining={cooldown_remaining:.0f}m"
                                )
                                blocker_logs[blocker_key] = now + 30.0
                        log(f"  [LEARNER_COOLDOWN] {symbol} on cooldown ({cooldown_remaining:.0f}m remaining) — SKIP")
                        continue
                    market_closed_remaining = get_market_closed_symbol_remaining(symbol)
                    if market_closed_remaining is not None:
                        if regime in {'PRICE', 'RAW', 'GEMINI'} and current_experimental_pair_slot:
                            blocker_logs = alleyway_state.setdefault('experimental_blocker_log_until', {})
                            blocker_key = f"market_closed:{symbol}"
                            now = time.time()
                            next_log_allowed = float(blocker_logs.get(blocker_key, 0.0) or 0.0)
                            if now >= next_log_allowed:
                                log(
                                    f"  [EXPERIMENTAL_BLOCKER] {symbol} regime={regime} "
                                    f"reason=market_closed remaining={max(1, int(round(market_closed_remaining)))}s"
                                )
                                blocker_logs[blocker_key] = now + 30.0
                        remaining_seconds = max(1, int(round(market_closed_remaining)))
                        log_cooldowns = alleyway_state.setdefault("market_closed_symbol_log_until", {})
                        now = time.time()
                        next_log_allowed = float(log_cooldowns.get(symbol, 0.0) or 0.0)
                        if now >= next_log_allowed:
                            log(
                                f"  [MARKET_CLOSED_SKIP] {symbol} venue cooldown active "
                                f"({remaining_seconds}s remaining) — SKIP"
                            )
                            log_cooldowns[symbol] = now + MARKET_CLOSED_SYMBOL_LOG_COOLDOWN_SECONDS
                        continue

                    insufficient_margin_remaining = get_insufficient_margin_symbol_remaining(symbol)
                    if insufficient_margin_remaining is not None:
                        remaining_seconds = max(1, int(round(insufficient_margin_remaining)))
                        log_cooldowns = alleyway_state.setdefault("insufficient_margin_symbol_log_until", {})
                        now = time.time()
                        next_log_allowed = float(log_cooldowns.get(symbol, 0.0) or 0.0)
                        if now >= next_log_allowed:
                            log(
                                f"  [INSUFFICIENT_MARGIN_SKIP] {symbol} symbol cooldown active "
                                f"({remaining_seconds}s remaining) - SKIP"
                            )
                            log_cooldowns[symbol] = now + 30.0
                        continue

                    # Let learner adjust stop distance based on failure history
                    learner_params = learner.get_params(symbol)
                    if learner_params and learner_params.get("atr_multiplier"):
                        log(f"  [LEARNER_ADJUST] {symbol} ATR mult={learner_params['atr_multiplier']:.1f}, conf_bump={learner_params.get('confidence_bump', 0):.2f}")

                    # === MARGIN SAFETY CHECK ===
                    # For exotics with high margin requirements, scale lot down to avoid
                    # triggering CRITICAL_MARGIN_DERISK immediately after entry
                    safe_lot, margin_ok = check_margin_safety(symbol, lot, signal)
                    if not margin_ok or safe_lot <= 0:
                        if regime in {'PRICE', 'RAW', 'GEMINI'}:
                            reversion_diag['experimental_blocked_margin'] = reversion_diag.get('experimental_blocked_margin', 0) + 1
                        if is_exotic(symbol):
                            log(f"  [MARGIN_GUARD] {symbol} skipped - insufficient margin for {lot:.2f} lot (exotic)")
                        continue
                    if safe_lot < lot:
                        log(f"  [MARGIN_GUARD] {symbol} lot {lot:.2f} -> {safe_lot:.2f} (margin safety)")
                        lot = safe_lot

                    live_projected_active_count = len(active_positions) + 1
                    live_entry_posture = alleyway_state.get("entry_posture")
                    live_current_active_count = len(active_positions)
                    live_direct_positions = [
                        pdata for pdata in active_positions.values()
                        if not pdata.get('adopted')
                    ]
                    live_lone_direct_pnl = None
                    if len(live_direct_positions) == 1:
                        live_lone_direct_pnl = float(
                            live_direct_positions[0].get('last_pnl', 0.0) or 0.0
                        )
                    live_defend_loaded_block = defend_loaded_no_add_active(
                        current_flat_book_rebuild=current_flat_book_rebuild,
                        entry_posture=live_entry_posture,
                        current_active_count=live_current_active_count,
                        projected_active_count=live_projected_active_count,
                        free_margin_ratio=free_margin_ratio,
                        managed_drawdown_pct=book_stress["managed_drawdown_pct"],
                        top_symbol_drawdown_pct=book_stress["top_symbol_drawdown_pct"],
                        candidate_regime=regime,
                        current_price_positions=current_price_positions,
                        current_raw_positions=current_raw_positions,
                        current_gemini_positions=current_gemini_positions,
                    )
                    live_experimental_continuation_allowed = (
                        regime in {'PRICE', 'RAW', 'GEMINI'}
                        and live_entry_posture == "DEFEND"
                        and free_margin_ratio >= DEFEND_COMPETITION_EXPERIMENTAL_MIN_FREE_MARGIN_RATIO
                        and live_current_active_count <= DEFEND_COMPETITION_EXPERIMENTAL_MAX_ACTIVE_POSITIONS
                        and (
                            current_price_positions
                            if regime == 'PRICE'
                            else (
                                current_raw_positions
                                if regime == 'RAW'
                                else (current_gemini_positions if regime == 'GEMINI' else 0)
                            )
                        ) < DEFEND_EXPERIMENTAL_CONTINUATION_MAX_PER_REGIME
                    )
                    if live_defend_loaded_block:
                        reversion_diag['blocked_defend_loaded'] += 1
                        continue

                    # Live proof on 2026-04-09 showed the one-position release
                    # fence can race with a managed exit: a near-flat survivor
                    # was still open when a new same-cycle USDCHF order slipped
                    # through pre-open. Keep the $+0.10 release threshold
                    # authoritative here too so exit handoffs cannot expand the
                    # book before the lone direct leg is actually gone.
                    live_onepos_release_locked = (
                        not current_flat_book_rebuild
                        and live_entry_posture in {"DEFEND", "REARM"}
                        and live_current_active_count >= 1
                        and len(live_direct_positions) == 1
                        and live_lone_direct_pnl is not None
                        and live_lone_direct_pnl < ONE_POSITION_REARM_MIN_GREEN_PNL_USD
                    )
                    if live_onepos_release_locked:
                        reversion_diag['blocked_defend_onepos'] += 1
                        continue

                    live_rearm_inherited_block = rearm_inherited_book_no_add_active(
                        current_flat_book_rebuild=current_flat_book_rebuild,
                        entry_posture=live_entry_posture,
                        adopted_positions=book_stress["adopted_positions"],
                    )
                    if live_rearm_inherited_block:
                        log(
                            "  [REARM_INHERITED_FREEZE] "
                            f"{symbol} {mode} blocked at pre-open "
                            f"adopted={book_stress['adopted_positions']} "
                            f"active={live_current_active_count} projected={live_projected_active_count} "
                            f"posture={live_entry_posture} fm={free_margin_ratio:.2f}"
                        )
                        reversion_diag['blocked_rearm_inherited'] = (
                            reversion_diag.get('blocked_rearm_inherited', 0) + 1
                        )
                        continue

                    # Sanity veto: only block at midload when book is actually stressed
                    # Sanity veto: only block at midload when book is actually stressed
                    sanity_midload_threshold = min(
                        DEFEND_MIDLOAD_NO_ADD_MIN_POSITIONS,
                        DEFEND_BENCHMARK_MIDLOAD_NO_ADD_MIN_POSITIONS,
                    )
                    if (
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
                    ):
                        log(
                            "  [DEFEND_VETO_SANITY] "
                            f"{symbol} {mode} blocked at pre-open "
                            f"active={live_current_active_count} projected={live_projected_active_count} "
                            f"posture={live_entry_posture} fm={free_margin_ratio:.2f} "
                            f"dd={book_stress['managed_drawdown_pct']:.3f} "
                            f"top={book_stress['top_symbol_drawdown_pct']:.3f}"
                        )
                        reversion_diag['blocked_defend_loaded'] += 1
                        continue

                    if regime in {'PRICE', 'RAW', 'GEMINI'}:
                        reversion_diag['experimental_preopen_ready'] = reversion_diag.get('experimental_preopen_ready', 0) + 1

                    if circuit_breaker_active:
                        if cycle % 100 == 0:
                            log(
                                f"  [CIRCUIT_BREAKER_BLOCK] {symbol} {mode} {signal} "
                                f"daily_pnl=${daily_pnl:+.2f} cap=-${MAX_DAILY_LOSS_USD:.2f}"
                            )
                        continue

                    # === DAILY TRADE CAP CHECK ===
                    if today_entries_count >= MAX_TRADES_PER_DAY:
                        if cycle % 100 == 0:  # Log periodically, not every cycle
                            log(f"  [DAILY CAP] Hit {MAX_TRADES_PER_DAY} entries today — blocking new entries")
                        continue

                    broker_backoff_remaining = get_broker_connection_backoff_remaining()
                    if broker_backoff_remaining is not None:
                        if cycle % 20 == 0:
                            log(
                                f"  [BROKER_BACKOFF_SKIP] {symbol} {mode} {signal} "
                                f"hold={max(1, int(round(broker_backoff_remaining)))}s"
                            )
                        continue

                    current_utc_hour = datetime.now(timezone.utc).hour
                    is_asian_session = current_utc_hour < 7 or current_utc_hour >= 23
                    if (
                        is_asian_session
                        and confidence < ASIAN_SESSION_MIN_CONFIDENCE
                        and not is_strategy_lab_lane(symbol, signal_type, mode, regime)
                    ):
                        if cycle % 50 == 0:
                            log(
                                f"  [ASIAN_PREOPEN_BLOCK] {symbol} {mode} {signal} "
                                f"conf={confidence:.2f} < {ASIAN_SESSION_MIN_CONFIDENCE:.2f} "
                                f"hour={current_utc_hour} UTC"
                            )
                        continue

                    strategy_lab_holdoff_key = None
                    if is_strategy_lab_lane(symbol, signal_type, mode, regime):
                        strategy_lab_lane_config = get_active_strategy_lab_lane_config(
                            symbol,
                            signal_type,
                            mode,
                            regime,
                        ) or {}
                        lane_gate_ok, lane_gate_reason = get_strategy_lab_entry_gate(
                            symbol,
                            signal_type,
                            mode,
                            regime,
                            signal,
                        )
                        if not lane_gate_ok:
                            emit_strategy_lab_event(
                                event_type="entry_style_blocked",
                                symbol=symbol,
                                signal_type=signal_type,
                                mode=mode,
                                regime=regime,
                                confidence=confidence,
                                direction=str(signal or ""),
                                reason=str(lane_gate_reason or ""),
                                experiment_variant=str(strategy_lab_variant or ""),
                                **strategy_lab_meta,
                            )
                            continue
                        holdoff_seconds = float(
                            strategy_lab_lane_config.get("entry_holdoff_seconds", 30.0) or 30.0
                        )
                        holdoff_reset_seconds = float(
                            strategy_lab_lane_config.get("entry_holdoff_reset_seconds", 20.0) or 20.0
                        )
                        strategy_lab_lane_id = get_resolved_strategy_lab_lane_id(
                            symbol,
                            signal_type,
                            mode,
                            regime,
                        )
                        strategy_lab_holdoff_key = (
                            f"{symbol}|{signal_type}|{mode}|{regime}|{signal}|{strategy_lab_lane_id}"
                        )
                        strategy_lab_holdoffs = alleyway_state.setdefault(
                            "strategy_lab_entry_holdoffs",
                            {},
                        )
                        now_ts = time.time()
                        holdoff_state = strategy_lab_holdoffs.get(strategy_lab_holdoff_key)
                        if (
                            holdoff_state is None
                            or now_ts - float(holdoff_state.get("last_seen", 0.0) or 0.0)
                            > holdoff_reset_seconds
                        ):
                            if holdoff_state is not None:
                                emit_strategy_lab_event(
                                    event_type="entry_holdoff_expired",
                                    symbol=symbol,
                                    signal_type=signal_type,
                                    mode=mode,
                                    regime=regime,
                                    confidence=confidence,
                                    reason="signal_lost",
                                )
                            strategy_lab_holdoffs[strategy_lab_holdoff_key] = {
                                "first_seen": now_ts,
                                "last_seen": now_ts,
                                "next_log_at": now_ts,
                                "admitted": False,
                            }
                            log(
                                "  [STRATEGY_LAB_HOLDOFF] "
                                f"{symbol} {mode} {signal_type or '-'} armed="
                                f"{int(holdoff_seconds)}s"
                            )
                            emit_strategy_lab_event(
                                event_type="entry_holdoff_started",
                                symbol=symbol,
                                signal_type=signal_type,
                                mode=mode,
                                regime=regime,
                                confidence=confidence,
                                direction=str(signal or ""),
                                holdoff_seconds=holdoff_seconds,
                            )
                            continue

                        holdoff_state["last_seen"] = now_ts
                        elapsed = now_ts - float(holdoff_state.get("first_seen", now_ts) or now_ts)
                        remaining = holdoff_seconds - elapsed
                        if remaining > 0:
                            if now_ts >= float(holdoff_state.get("next_log_at", 0.0) or 0.0):
                                log(
                                    "  [STRATEGY_LAB_HOLDOFF_WAIT] "
                                    f"{symbol} {mode} {signal_type or '-'} "
                                    f"remaining={max(1, int(round(remaining)))}s"
                                )
                                holdoff_state["next_log_at"] = now_ts + 5.0
                            emit_strategy_lab_event(
                                event_type="entry_holdoff_wait",
                                symbol=symbol,
                                signal_type=signal_type,
                                mode=mode,
                                regime=regime,
                                confidence=confidence,
                                direction=str(signal or ""),
                                remaining_seconds=round(remaining, 2),
                            )
                            continue

                        if not holdoff_state.get("admitted"):
                            holdoff_state["admitted"] = True
                            emit_strategy_lab_event(
                                event_type="entry_admitted",
                                symbol=symbol,
                                signal_type=signal_type,
                                mode=mode,
                                regime=regime,
                                confidence=confidence,
                                direction=str(signal or ""),
                            )

                    log(f"  [PRE_OPEN] {symbol} {mode} {signal} lot={lot} conf={confidence:.2f}")
                    emit_strategy_lab_event(
                        event_type="pre_open",
                        symbol=symbol,
                        signal_type=signal_type,
                        mode=mode,
                        regime=regime,
                        confidence=confidence,
                        lot=float(lot or 0.0),
                        direction=str(signal or ""),
                        entry_context=str(entry_context or ""),
                        flat_rebuild=bool(current_flat_book_rebuild),
                        quality_gate=bool(post_cleanup_quality_gate_active),
                        experiment_variant=str(strategy_lab_variant or ""),
                        **strategy_lab_meta,
                    )
                    ticket = try_open_position(symbol, signal, lot, mode, confidence, atr)
                    if ticket is None:
                        if regime in {'PRICE', 'RAW', 'GEMINI'}:
                            reversion_diag['experimental_open_failed'] = reversion_diag.get('experimental_open_failed', 0) + 1
                        log(f"  [OPEN_FAILED] {symbol} {mode} {signal} — try_open_position returned None")
                        emit_strategy_lab_event(
                            event_type="open_failed",
                            symbol=symbol,
                            signal_type=signal_type,
                            mode=mode,
                            regime=regime,
                            confidence=confidence,
                            lot=float(lot or 0.0),
                            direction=str(signal or ""),
                            entry_context=str(entry_context or ""),
                            experiment_variant=str(strategy_lab_variant or ""),
                            **strategy_lab_meta,
                        )

                    if ticket:
                        if strategy_lab_holdoff_key:
                            try:
                                alleyway_state.get("strategy_lab_entry_holdoffs", {}).pop(strategy_lab_holdoff_key, None)
                            except Exception:
                                pass
                        today_entries_count += 1  # Count against daily cap
                        if mode == 'REVERSION':
                            reversion_diag['opened'] += 1
                        if regime == 'PRICE':
                            reversion_diag['price_opened'] = reversion_diag.get('price_opened', 0) + 1
                        elif regime == 'RAW':
                            reversion_diag['raw_opened'] = reversion_diag.get('raw_opened', 0) + 1
                        active_positions[ticket] = {
                            'ticket': int(ticket),
                            'symbol': symbol,
                            'direction': signal,
                            'entry_price': tick.ask if signal == 'BUY' else tick.bid,
                            'entry_time': time.time(),
                            'peak_pnl': 0.0,
                            'mode': mode,
                            'confidence': confidence,
                            'last_pnl': 0.0,
                            'atr': atr,
                            'volume': lot,
                            'pyramid_count': 0,
                            'last_pyramid_pnl': 0,
                            'mean_reversion': regime == 'RANGING',
                            'entry_context': (
                                f"signal={entry_context or 'unknown'};"
                                f"posture={live_entry_posture};"
                                f"rearm_reason={rearm_reason or 'none'};"
                                f"flat_rebuild={'yes' if current_flat_book_rebuild else 'no'}"
                            ),
                            'entry_signal_type': signal_type or 'unlabeled',
                            'entry_regime': regime or 'unknown',
                            'strategy_lab_variant': strategy_lab_variant or '',
                            'strategy_lab_lane_id': strategy_lab_meta.get('lane_id', ''),
                            'strategy_lab_role': strategy_lab_meta.get('role', ''),
                            'strategy_lab_hypothesis': strategy_lab_meta.get('hypothesis', ''),
                            'spread_at_entry': float(abs((tick.ask or 0.0) - (tick.bid or 0.0))),
                            'time_to_first_green_seconds': None,
                            'time_to_0_25_atr_seconds': None,
                            'time_to_0_5_atr_seconds': None,
                            'time_to_1_0_atr_seconds': None,
                            'time_to_minus_0_35_atr_seconds': None,
                            'max_favorable_excursion_pnl': 0.0,
                            'max_adverse_excursion_pnl': 0.0,
                        }
                        emit_strategy_lab_event(
                            event_type="opened",
                            symbol=symbol,
                            signal_type=signal_type,
                            mode=mode,
                            regime=regime,
                            confidence=confidence,
                            ticket=int(ticket),
                            lot=float(lot or 0.0),
                            direction=str(signal or ""),
                            entry_context=str(entry_context or ""),
                            flat_rebuild=bool(current_flat_book_rebuild),
                            quality_gate=bool(post_cleanup_quality_gate_active),
                            experiment_variant=str(strategy_lab_variant or ""),
                            **strategy_lab_meta,
                        )
                        mode_counts[mode] += 1
                        if regime in regime_counts:
                            regime_counts[regime] += 1
                            if regime in mode_counts:
                                mode_counts[regime] = regime_counts[regime]
                        entries_this_cycle += 1
                        if current_utc_hour in OFF_SESSION_HOURS:
                            alleyway_state['off_session_entries_this_hour'] = int(
                                alleyway_state.get('off_session_entries_this_hour', 0) or 0
                            ) + 1
                        # Reset alleyway idle counter on successful trade
                        alleyway_state['cycles_without_trade'] = 0
                        if post_cleanup_quality_gate_active and current_flat_book_rebuild:
                            log(
                                f"  POST_CLEANUP_QUALITY_CONSUMED trigger={post_cleanup_quality_gate_trigger or 'unknown'} "
                                f"symbol={symbol} mode={mode} conf={confidence:.2f}"
                            )
                            consume_post_cleanup_quality_gate()
                            arm_post_cleanup_first_leg_rearm_holdoff(
                                time.time(),
                                post_cleanup_quality_gate_trigger or 'unknown',
                                symbol,
                                mode,
                            )
                        cycle_opened_symbols.add(symbol)
                        # FIX: Recalculate book_stress and free_margin_ratio after each successful
                        # open to prevent stale state race condition where subsequent opportunities
                        # in the same cycle see outdated position counts and margin
                        book_stress = get_book_stress(equity)
                        # Refresh margin ratio after position open (margin changes with new position)
                        acct = mt5.account_info()
                        if acct:
                            free_margin_ratio = (acct.margin_free / equity) if equity > 0 else 0.0
                        if current_flat_book_rebuild:
                            # Recompute holds/posture on the next cycle instead of stacking
                            # follow-on rebuild entries from stale pre-open state.
                            break
                        entry_price = proposed_entry_price
                        log(f"  OPEN [{mode}] {signal} {symbol} #{ticket} {lot}lot @ {entry_price:.5f} (conf:{confidence:.2f} atr:{atr:.5f} eq:{equity:.0f})")
                        # Set broker-side SL/TP for crash safety
                        set_broker_sl_tp(ticket, signal, entry_price, atr, mode)
                except Exception as entry_exc:
                    log(f"  [ENTRY_ERROR] {symbol} {mode} {signal}: {entry_exc}")
                    import traceback
                    log(f"  [ENTRY_TRACE] {traceback.format_exc()}")
                    import traceback
                    log(f"  [ENTRY_TRACE] {traceback.format_exc()}")
                    log(traceback.format_exc(limit=6).strip())

            # Summary line
            if cycle % 4 == 0 or entries_this_cycle > 0 or trims_this_cycle > 0 or critical_derisks_this_cycle > 0 or defend_derisks_this_cycle > 0 or winner_bags_this_cycle > 0 or financed_unwinds_this_cycle > 0 or anchor_unwinds_this_cycle > 0 or small_book_unwinds_this_cycle > 0 or pinned_unwinds_this_cycle > 0 or crowd_unwinds_this_cycle > 0 or adopted_cleaned > 0 or managed_exits_this_cycle > 0 or sync_closed_this_cycle > 0:
                mode_summary = ", ".join([f"{m}:{c}" for m, c in mode_counts.items()])
                lane_score_summary = build_competition_lane_scorecard(active_positions)
                session = "OVERLAP" if overlap_active else "ACTIVE"
                log(
                    f"  [{session}] Active:{len(active_positions)} ({mode_summary}) "
                    f"Trades:{trades} P/L:${total_pnl:+.2f} W:{consecutive_wins} L:{consecutive_losses} "
                    f"Eq:${equity:.2f} Trims:{trims_this_cycle} Derisks:{critical_derisks_this_cycle} SoftDerisks:{defend_derisks_this_cycle} WinBags:{winner_bags_this_cycle} Unwinds:{financed_unwinds_this_cycle + anchor_unwinds_this_cycle + small_book_unwinds_this_cycle + pinned_unwinds_this_cycle + crowd_unwinds_this_cycle} Cleanups:{adopted_cleaned} ManagedExits:{managed_exits_this_cycle} SyncCloses:{sync_closed_this_cycle} "
                    f"Posture:{alleyway_state['entry_posture']}"
                )
                log(f"  LANE_SCORE {lane_score_summary}")
                if rearm_active:
                    log(
                        f"  REARM_ACTIVE {rearm_reason} "
                        f"idle={rearm_profile['idle_cycles']} "
                        f"esc={rearm_profile['escalation']:.3f} "
                        f"thr={effective_adaptive_threshold:.2f}"
                    )
            if (
                cycle % 6 == 0
                or reversion_diag.get('opened', 0) > 0
                or reversion_diag.get('opportunities', 0) > 0
                or reversion_diag.get('price_opportunities', 0) > 0
            ):
                alleyway_state['last_blocked_defend_loaded'] = int(
                    reversion_diag.get('blocked_defend_loaded', 0) or 0
                )
                log(
                    "  REV_DIAG "
                    f"scan={reversion_diag.get('scanned_symbols', 0)} "
                    f"ranging={reversion_diag.get('ranging_symbols', 0)} "
                    f"regime_ok={reversion_diag.get('mr_pass_regime_score', 0)} "
                    f"signal_ready={reversion_diag.get('mr_signal_ready', 0)} "
                    f"thr_ok={reversion_diag.get('mr_pass_threshold', 0)} "
                    f"price_opp={reversion_diag.get('price_opportunities', 0)} "
                    f"raw_opp={reversion_diag.get('raw_opportunities', 0)} "
                    f"opp={reversion_diag.get('opportunities', 0)} "
                    f"mg_opp={reversion_diag.get('mg_opportunities', 0)} "
                    f"open={reversion_diag.get('opened', 0)} "
                    f"blk_conf={reversion_diag.get('blocked_confidence_gate', 0)} "
                    f"blk_cluster={reversion_diag.get('blocked_cluster', 0)} "
                    f"blk_rearm_cap={reversion_diag.get('blocked_rearm_rebuild_cap', 0)} "
                    f"blk_defend_cleanup={reversion_diag.get('blocked_defend_cleanup', 0)} "
                    f"blk_defend_loaded={reversion_diag.get('blocked_defend_loaded', 0)} "
                    f"blk_defend_onepos={reversion_diag.get('blocked_defend_onepos', 0)} "
                    f"blk_defend={reversion_diag.get('blocked_defend_noexp', 0)} "
                    f"blk_defend_mg={reversion_diag.get('blocked_defend_mg', 0)} "
                    f"blk_crowd={reversion_diag.get('blocked_crowding', 0)} "
                    f"blk_book={reversion_diag.get('blocked_portfolio_guard', 0)} "
                    f"blk_trim={reversion_diag.get('blocked_trim_cooldown', 0)} "
                    f"blk_corr={reversion_diag.get('blocked_correlation', 0)} "
                    f"blk_exotic={reversion_diag.get('blocked_exotic', 0)} "
                    f"exp_pair={reversion_diag.get('experimental_pair_slots', 0)} "
                    f"exp_ready={reversion_diag.get('experimental_preopen_ready', 0)} "
                    f"exp_blk_quality={reversion_diag.get('experimental_blocked_quality', 0)} "
                    f"exp_blk_conf={reversion_diag.get('experimental_blocked_late_confidence', 0)} "
                    f"price_blk_conf={reversion_diag.get('price_blocked_late_confidence', 0)} "
                    f"raw_blk_conf={reversion_diag.get('raw_blocked_late_confidence', 0)} "
                    f"exp_blk_spread={reversion_diag.get('experimental_blocked_spread', 0)} "
                    f"exp_blk_margin={reversion_diag.get('experimental_blocked_margin', 0)} "
                    f"exp_open_fail={reversion_diag.get('experimental_open_failed', 0)} "
                    f"price_open={reversion_diag.get('price_opened', 0)} "
                    f"raw_open={reversion_diag.get('raw_opened', 0)} "
                    f"fail_regime={reversion_diag.get('mr_fail_regime_score', 0)} "
                    f"fail_mid={reversion_diag.get('mr_fail_mid', 0)} "
                    f"fail_rsi={reversion_diag.get('mr_fail_rsi_band', 0)} "
                    f"price_breakout={reversion_diag.get('price_breakout_continuation', 0)} "
                    f"price_pullback={reversion_diag.get('price_pullback_continuation', 0)} "
                    f"price_reject={reversion_diag.get('price_range_rejection', 0)} "
                    f"price_near={reversion_diag.get('price_near_miss', 0)} "
                    f"price_top={reversion_diag.get('price_top_symbol', '-')}:"
                    f"{reversion_diag.get('price_top_signal_type', '-')}:"
                    f"{float(reversion_diag.get('price_top_confidence', 0.0) or 0.0):.2f} "
                    f"price_best={reversion_diag.get('price_best_symbol', '-')}:"
                    f"{reversion_diag.get('price_best_signal_type', '-')}:" 
                    f"{float(reversion_diag.get('price_best_confidence', 0.0) or 0.0):.2f} "
                    f"price_score={reversion_diag.get('price_best_score_symbol', '-')}:"
                    f"{reversion_diag.get('price_best_score_signal_type', '-')}:"
                    f"{float(reversion_diag.get('price_best_score', 0.0) or 0.0):.1f}"
                )
                maybe_log_price_blocker_alert(
                    reversion_diag,
                    active_positions_count=len(active_positions),
                    direct_positions_count=count_direct_positions(),
                    post_cleanup_hold_remaining=post_cleanup_hold_remaining,
                    post_cleanup_hold_trigger=post_cleanup_hold_trigger,
                    post_cleanup_quality_gate_active=post_cleanup_quality_gate_active,
                    post_cleanup_quality_gate_trigger=post_cleanup_quality_gate_trigger,
                    context="revdiag",
                )
                maybe_log_strategy_lab_near_miss_alert(
                    reversion_diag,
                    context="revdiag",
                )
                maybe_log_price_watch_alert(tradeable_symbols, context="revdiag")

            # Clear bar cache periodically
            if cycle % 20 == 0:
                _bars_cache.clear()

            write_runtime_state(balance=balance, equity=equity, margin_free=acct.margin_free)
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            log("\nStopping V10. Positions left open.")
            write_worker_state("stopped", "keyboard_interrupt", "worker keyboard interrupt", "loop interrupted by operator")
            break
        except Exception as e:
            log(f"Cycle {cycle} error: {e}")
            write_worker_state("running", "cycle_error", str(e), f"cycle {cycle}")
            time.sleep(5)

    try:
        mt5.shutdown()
    except:
        pass
    write_worker_state("stopped", "clean_exit", "run returned normally", "mt5 shutdown complete", exit_code=0)

def get_active_symbols(diagnostics=None):
    """Get tradeable symbols filtered by spread and session"""
    try:
        all_symbols = mt5.symbols_get()
        if not all_symbols:
            return []

        active = []
        now = time.time()
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update(
                {
                    'total_symbols': 0,
                    'disabled_or_hidden': 0,
                    'session_blocked': 0,
                    'no_tick': 0,
                    'stale_tick': 0,
                    'spread_blocked': 0,
                    'active': 0,
                    'watchlist_spread_blocked': [],
                    'watchlist_stale': [],
                }
            )
        for s in all_symbols:
            if diagnostics is not None:
                diagnostics['total_symbols'] += 1
            if s.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED or not s.visible:
                if diagnostics is not None:
                    diagnostics['disabled_or_hidden'] += 1
                continue

            name = s.name

            # Hard blocklist — skip proven bleeders
            if name in SYMBOL_BLOCKLIST:
                if diagnostics is not None:
                    diagnostics['disabled_or_hidden'] += 1
                continue

            # Symbol allowlist — only trade proven winners (USDCHF, AUDCHF, USDJPY, NAS100)
            if SYMBOL_ALLOWLIST and name not in SYMBOL_ALLOWLIST:
                if diagnostics is not None:
                    diagnostics['disabled_or_hidden'] += 1
                continue

            # Session-gated symbols: US30, JPN225 only during Asian (00:00-07:59 UTC)
            if name in ASIAN_SESSION_SYMBOLS and not is_asian_session():
                if diagnostics is not None:
                    diagnostics['asian_symbol_blocked'] = diagnostics.get('asian_symbol_blocked', 0) + 1
                continue

            # Session filter
            if not is_good_session(name):
                if diagnostics is not None:
                    diagnostics['session_blocked'] += 1
                continue

            tick = mt5.symbol_info_tick(name)
            if not tick or tick.ask <= 0:
                if diagnostics is not None:
                    diagnostics['no_tick'] += 1
                continue
            tick_stale, tick_age = is_tick_stale(tick, now=now)
            if tick_stale:
                if diagnostics is not None:
                    diagnostics['stale_tick'] += 1
                    if name in PRICE_UNIVERSE_WATCHLIST:
                        age_text = '?' if tick_age is None else str(int(tick_age))
                        diagnostics['watchlist_stale'].append(f"{name}:{age_text}s")
                continue

            spread_pct = abs(tick.ask - tick.bid) / tick.ask * 100

            # Spread limits by type
            if is_crypto(name):
                max_spread = MAX_SPREAD_PCT_CRYPTO
            elif is_exotic(name):
                max_spread = MAX_SPREAD_PCT_EXOTIC
            else:
                max_spread = MAX_SPREAD_PCT_FOREX

            if spread_pct > max_spread:
                if diagnostics is not None:
                    diagnostics['spread_blocked'] += 1
                    if name in PRICE_UNIVERSE_WATCHLIST:
                        diagnostics['watchlist_spread_blocked'].append(
                            f"{name}:{spread_pct:.3f}>{max_spread:.3f}"
                        )
                continue

            active.append(name)

        if diagnostics is not None:
            diagnostics['active'] = len(active)
        return active[:MAX_SYMBOLS_TO_TRADE]
    except:
        return []

def canonical_launch_allowed():
    if os.environ.get(CANONICAL_SUPERVISOR_ENV) == "1":
        return True, "canonical supervisor env"
    if os.environ.get(ALLOW_STANDALONE_ENV) == "1":
        return True, "explicit standalone override"

    parent_cmd = get_process_command_line(os.getppid())
    if "mt5_bot.py" in parent_cmd:
        return True, "parent launcher detected"

    return False, f"missing {CANONICAL_SUPERVISOR_ENV}=1 and no mt5_bot.py parent"
