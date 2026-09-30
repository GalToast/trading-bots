"""Shared mutable runtime state for the MT5 worker (extracted verbatim)."""
from __future__ import annotations
import time
from .config import DEFAULT_STRATEGY_LAB_LANE_ID

hourly_trades_count = 0

last_hourly_reset = time.time()

equity_peak = 0                 # Track peak equity for lot scaling

cooldown_until = 0              # Timestamp for loss-streak cooldown (0 = no cooldown)

cycles_without_trade = 0        # Alleyway cycle counter

recently_trimmed_symbols = {}   # symbol -> timestamp (anti-death-spiral)

recent_risk_events = []         # timestamps of reversal exits / stress trims

active_positions = {}

consecutive_wins = 0

consecutive_losses = 0

total_pnl = 0

trades = 0

mt5_connected = False

_brain = None

_learner = None

_bars_cache = {}  # symbol -> {timeframe: (timestamp, bars)}

_tick_cache = {}   # symbol -> (timestamp, tick) — per-cycle tick cache to avoid redundant symbol_info_tick() calls

_tick_cache_cycle = 0  # cycle number when tick cache was last populated

alleyway_state = {
    'cycles_without_trade': 0,
    'recent_atr_avg': 0,
    'equity_peak': 1500,  # Seed with competition starting balance
    'last_relaxation': 0,
    'strategy_lab_active_lane_id': DEFAULT_STRATEGY_LAB_LANE_ID,
    'strategy_lab_last_completed_lane_id': '',
    'strategy_lab_lane_rotated_at': '',
    'rearm_cycles_remaining': 0,
    'rearm_active': False,
    'entry_posture': 'DEFEND',
    'rearm_reason': '',
    'post_cleanup_flat_rearm_hold_until': 0.0,
    'post_cleanup_flat_rearm_trigger': '',
    'post_cleanup_flat_rearm_armed_at': '',
    'post_cleanup_flat_rearm_last_pnl': 0.0,
    'post_cleanup_quality_gate_pending': False,
    'post_cleanup_quality_gate_trigger': '',
    'post_cleanup_quality_gate_armed_at': '',
    'post_cleanup_first_leg_rearm_hold_until': 0.0,
    'post_cleanup_first_leg_rearm_trigger': '',
    'post_cleanup_first_leg_rearm_armed_at': '',
    'last_sync_close_holdoff_event': '',
    'last_sync_close_holdoff_checked_at': '',
    'one_position_quiet_rearm_hold_until': 0.0,
    'one_position_quiet_rearm_trigger': '',
    'stale_symbol_log_until': {},
    'market_closed_symbol_until': {},
    'market_closed_symbol_log_until': {},
    'off_session_entry_hour_bucket': '',
    'off_session_entries_this_hour': 0,
    'off_session_cap_log_until': 0.0,
    'managed_drawdown_pct': 0.0,
    'top_symbol_drawdown_pct': 0.0,
    'free_margin_ratio': 0.0,
    'cluster_cooldown_until': 0.0,
    'defend_crowding_cycles': 0,
    'defend_profit_harvest_cooldown_until': 0.0,
    'defend_pinned_cycles': 0,
    'defend_pinned_unwind_cooldown_until': 0.0,
    'defend_crowd_unwind_cycles': 0,
    'defend_crowd_unwind_cooldown_until': 0.0,
    'defend_anchor_unwind_cycles': 0,
    'defend_anchor_unwind_cooldown_until': 0.0,
    'defend_small_book_unwind_cycles': 0,
    'defend_small_book_unwind_cooldown_until': 0.0,
    'defend_three_book_same_symbol_cleanup_cycles': 0,
    'defend_three_book_same_symbol_cleanup_cooldown_until': 0.0,
    'defend_two_book_same_symbol_cleanup_cycles': 0,
    'defend_two_book_same_symbol_cleanup_cooldown_until': 0.0,
    'defend_two_book_mixed_cleanup_cycles': 0,
    'defend_two_book_mixed_cleanup_cooldown_until': 0.0,
    'defend_one_pos_exotic_mercy_cycles': 0,
    'defend_one_pos_exotic_mercy_cooldown_until': 0.0,
    'defend_four_book_mixed_cleanup_cycles': 0,
    'defend_four_book_mixed_cleanup_cooldown_until': 0.0,
    'defend_three_book_win_bag_ticket': 0,
    'defend_three_book_win_bag_cycles': 0,
    'defend_three_book_win_bag_cooldown_until': 0.0,
    'defend_three_book_win_bag_symbol_freeze_until': {},
    'defend_two_book_win_bag_ticket': 0,
    'defend_two_book_win_bag_cycles': 0,
    'defend_two_book_win_bag_cooldown_until': 0.0,
    'defend_two_book_win_bag_symbol_freeze_until': {},
    'sync_close_reentry_symbol_freeze_until': {},
    'sync_close_reentry_family_freeze_until': {},
    'defend_two_book_win_bag_last_reason': '',
    'defend_two_book_win_bag_last_logged_cycle': 0,
    'defend_crowd_win_bag_cycles': 0,
    'defend_crowd_win_bag_cooldown_until': 0.0,
    'defend_mixed_win_bag_cycles': 0,
    'defend_mixed_win_bag_cooldown_until': 0.0,
    'profit_capture_cooldown_until': 0.0,
    'profit_capture_entry_freeze_until': 0.0,
    'competition_lane_records': {},
}
