"""
MT5 HUGOSWAY BOT V10 - Competition Killer (10x Mode)
=====================================================
Built to 10x a demo account in a trading competition.
1:500 leverage, equity-based lot sizing, compounding, pyramiding.

Changes from V9:
- Multi-timeframe analysis (M1 entry + M5 confirmation + M15 direction)
- RSI filter (14-period on M5, avoids overbought/oversold entries)
- ATR-based adaptive stops (scales to each symbol's volatility)
- Proper SL/TP using tick_value/tick_size (no more broken math)
- Session filter (London/NY overlap = prime time)
- Correlation limiter (max 2 per currency group)
- Directional bias from M15 (don't fight the trend)
- Asymmetric R:R (minimum 2:1 reward:risk)
- Momentum burst detection (breakout entries, not MA crossovers)
- Clean brain integration (fixed mode tracking)
"""
import json
import MetaTrader5 as mt5
import os
import sys
import subprocess
import time
import math
import traceback
from collections import deque
from collections.abc import Mapping
from datetime import datetime, timezone

# Add current directory to sys.path for local imports
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from bot import gemini as gemini_policy
from bot import price as price_policy
from bot import gemini_v2 as msls_policy
from bot import asian_mean_reversion as asian_policy
from brain import TradingBrain
from symbol_learner import SymbolLearner
from mt5_config import BOT_COMMENT_PREFIX, BOT_MAGIC, LOGIN, PASSWORD, SERVER

CANONICAL_SUPERVISOR_ENV = "CANONICAL_MT5_SUPERVISOR"
ALLOW_STANDALONE_ENV = "ALLOW_STANDALONE_MT5_WORKER"

# === 10x COMPOUNDING CONFIG (EXP-20260409-61) ===
# Only proven winning modes. MACHINE_GUN/PRICE/GEMINI all losing heavily.
# MACHINE_GUN: 275 trades, -$3,317 | PRICE: 123 trades, -$1,784 | GEMINI: 215 trades, -$5,786
DISABLED_MODES = {'MACHINE_GUN', 'PRICE', 'GEMINI', 'REVERSION'}
MAX_TRADES_PER_DAY = 50  # Hard cap — 670 trades/day was bleeding $11K
MAX_DAILY_LOSS_USD = 500.0  # Circuit breaker: stop all entries after $500 daily loss
MAX_LOT_SIZE = 1.0  # Hard safety cap to prevent size explosions (like the 10-lot leak)
MSLS_ENABLED = False  # Set to True once the team approves the probationary run
MSLS_PROBATIONARY_LOT = 0.01
MSLS_MIN_GREEN_RATE = 0.90  # Only trade symbols with 90%+ immediate profit rate

# Symbol-signal blocklist — proven bad combinations from behavior forensics
# ride_momentum: 0/10 green, -$2,273 total — worst signal across all symbols
# candle_direction on AUDCHF: 0/3 green, -$210
# candle_direction on USDJPY: net negative despite high green rate
# candle_direction on NAS100: 1/10 wins, -$14.93 avg, includes -$102 outlier
# candle_direction on GBPUSD: 0% green-in-30s, -$22.26 avg, includes -$177 outlier
# candle_direction on GBPAUD: 0/3 wins, -$4.03 total, never green in fresh DEFEND wave
# breakout_hold_below_low on USDCHF: 0/2 green, -$228
# breakout_hold_below_low on NAS100: 0/4 wins, -$38.28 total, all recent trims/closes before green
# breakout_hold_below_low on NZDCAD: fresh active sample 0/5, -$1.65 after the stale outlier
# pullback_from_structure_fail on NAS100: 1/5 wins, -$45.10 total, last four all STRESS_TRIM
# breakout_hold_above_high on USDCHF: fresh sample 1/3, -$0.67 total
# trend_continuation on USDCHF: 0/2 wins, -$569
# ride_momentum on USDCHF: 14% green, -$20.53 avg (Qwen green-time analyzer)
# ride_momentum on USDJPY: 14% green, negative avg
# trend_ride on GBPUSD: 20% green, -$4.39 avg
# legacy_trend_following on NAS100: 1/3 wins, -$10.66 total
# legacy_trend_following on GBPUSD: 0/2 wins, -$3.94 total
# breakout_hold_below_low on GBPUSD: 0/2 wins, -$0.62 total
# breakout_hold_below_low on US30: 0/1 wins, -$406.20 total
# breakout_hold_below_low on AUDCHF: 0/2 wins, -$11.96 total
# gemini pullbacks on USDJPY: 0% green, -$16.36 avg
# trend_ride on USDCHF: 25% green, -$3.35 avg (green-time analyzer)
# trend_ride on USDJPY: 0% green, -$13.27 avg (green-time analyzer)
# trend_continuation on USDJPY: 0/2 wins, -$0.22 total
# candle_direction on USDJPY (SHOTGUN): 3/7 wins, still net negative and 0% green-in-30s
SYMBOL_SIGNAL_BLOCKLIST = {
    ('*', 'ride_momentum'),
    ('AUDCHF', 'candle_direction'),
    ('USDJPY', 'candle_direction'),
    ('NAS100', 'candle_direction'),
    ('GBPUSD', 'candle_direction'),
    ('GBPAUD', 'candle_direction'),
    ('USDCHF', 'breakout_hold_below_low'),
    ('NAS100', 'breakout_hold_below_low'),
    ('NZDCAD', 'breakout_hold_below_low'),
    ('NAS100', 'pullback_from_structure_fail'),
    ('USDCHF', 'breakout_hold_above_high'),
    ('USDCHF', 'trend_continuation'),
    ('USDCHF', 'ride_momentum'),
    ('USDJPY', 'ride_momentum'),
    ('GBPUSD', 'trend_ride'),
    ('GBPUSD', 'legacy_trend_following'),
    ('GBPUSD', 'breakout_hold_below_low'),
    ('US30', 'breakout_hold_below_low'),
    ('AUDCHF', 'breakout_hold_below_low'),
    ('NAS100', 'legacy_trend_following'),
    ('USDCHF', 'trend_ride'),
    ('USDJPY', 'trend_continuation'),
    ('USDJPY', 'trend_ride'),
    ('USDJPY', 'gemini_trend_pullback_buy'),
    ('USDJPY', 'gemini_trend_pullback_sell'),
    ('NAS100', 'trend_continuation'),
    ('US30', 'legacy_trend_following'),
    ('NZDCAD', 'candle_direction'),
}

# === SYMBOL SIGNAL WHITELIST (EXP-20260409-WHITELIST) ===
# Proven winning combos from 690 fresh-entry trades analysis.
# These combos get a +0.03 confidence bonus (easier entry) because
# historical data shows they have positive expectancy.
# Generated by scripts/find_top_combos.py — update when new data accumulates.
SYMBOL_SIGNAL_WHITELIST = {
    # Top performers (min 3 trades, 45%+ WR, positive net P/L):
    ('USDCHF', 'candle_direction'),           # 28 trades, 64.3% WR, $+360.86, payoff=125
}

# === PRIORITY-ONLY PROMOTIONS (MEETING-20260409) ===
# These lanes keep the normal confidence gates but win ties / near-ties in the
# experimental queue. Current evidence supports priority, not easier admission.
EXPERIMENTAL_PRIORITY_LANES = {
    ('USDJPY', 'breakout_hold_above_high', 'SNIPER', 'PRICE'),  # fresh realized sample: 21 trades, +$1.77
}
BRAIN_COOLDOWN_PRIORITY_LANE_MIN_CONFIDENCE = 0.70

# Keep one promoted setup family under a microscope so we can improve entry/exit
# quality without blending it back into the full bot. The current evidence-backed
# basket keeps the same confirmed-displacement motif on the same two symbols,
# admits both breakout directions, and allows symbol-specific thresholds where
# the optimizer found cleaner stability.
STRATEGY_LAB_TARGET_LANES = {
    ('USDJPY', 'breakout_hold_above_high', 'SNIPER', 'PRICE'),
    ('USDJPY', 'breakout_hold_below_low', 'SNIPER', 'PRICE'),
    ('GBPUSD', 'breakout_hold_above_high', 'SNIPER', 'PRICE'),
    ('GBPUSD', 'breakout_hold_below_low', 'SNIPER', 'PRICE'),
}
STRATEGY_LAB_LANES = {
    "confirm_disp_1p5_rx2p5_ret60": {
        "lane_id": "confirm_disp_1p5_rx2p5_ret60",
        "role": "promoted_live",
        "hypothesis": "close_1p5_pips_beyond_structure_then_hold_level_next_bar_keep_60pct_of_peak",
        "variant_label": "confirmed_displacement__confirm_1p5__range_2p5x_atr__retain_60pct__lot_0.01",
        "entry_holdoff_seconds": 0.0,
        "entry_holdoff_reset_seconds": 10.0,
        "probationary_lot": 0.01,
        "entry_style": "confirmed_displacement_recipe",
        "lookback_bars": 20,
        "min_body_atr_expansion": 2.5,
        "signal_break_margin_pips": 1.5,
        "confirm_window_bars": 1,
        "exit_retain_ratio": 0.60,
        "exit_min_profit_floor_usd": 0.03,
    },
    "confirm_disp_gbpusd_2p0_rx2p0_ret60": {
        "lane_id": "confirm_disp_gbpusd_2p0_rx2p0_ret60",
        "role": "promoted_live",
        "hypothesis": "gbpusd_close_2p0_pips_beyond_structure_then_hold_level_next_bar_keep_60pct_of_peak",
        "variant_label": "confirmed_displacement__confirm_2p0__range_2p0x_atr__retain_60pct__lot_0.01",
        "entry_holdoff_seconds": 0.0,
        "entry_holdoff_reset_seconds": 10.0,
        "probationary_lot": 0.01,
        "entry_style": "confirmed_displacement_recipe",
        "lookback_bars": 20,
        "min_body_atr_expansion": 2.0,
        "signal_break_margin_pips": 2.0,
        "confirm_window_bars": 1,
        "exit_retain_ratio": 0.60,
        "exit_min_profit_floor_usd": 0.03,
    },
}
STRATEGY_LAB_LANE_OVERRIDES = {
    ('USDJPY', 'breakout_hold_above_high', 'SNIPER', 'PRICE'): "confirm_disp_1p5_rx2p5_ret60",
    ('USDJPY', 'breakout_hold_below_low', 'SNIPER', 'PRICE'): "confirm_disp_1p5_rx2p5_ret60",
    ('GBPUSD', 'breakout_hold_above_high', 'SNIPER', 'PRICE'): "confirm_disp_gbpusd_2p0_rx2p0_ret60",
    ('GBPUSD', 'breakout_hold_below_low', 'SNIPER', 'PRICE'): "confirm_disp_gbpusd_2p0_rx2p0_ret60",
}
STRATEGY_LAB_SYMBOLS = tuple(sorted({lane[0] for lane in STRATEGY_LAB_TARGET_LANES}))
STRATEGY_LAB_OWNER_POOL = (
    "confirm_disp_1p5_rx2p5_ret60",
)
STRATEGY_LAB_OWNER_WINDOW_TRADES = 4
STRATEGY_LAB_OWNER_MIN_SAMPLES = 2
STRATEGY_LAB_OWNER_BOOTSTRAP_MIN_TRADES = 2
DEFAULT_STRATEGY_LAB_LANE_ID = "confirm_disp_1p5_rx2p5_ret60"
EXPERIMENT_ONLY_MODE = True
EXPERIMENT_ONLY_ALLOWED_LANES = set(STRATEGY_LAB_TARGET_LANES)

# Session-specific combo filters are intentionally disabled until they have
# verified session-local realized support. The currently toxic combos are
# already covered by the global SYMBOL_SIGNAL_BLOCKLIST above, so keeping these
# empty is both safe and explicit.
ASIAN_BLOCKLIST = set()
NEW_YORK_BLOCKLIST = set()

# === OFF-SESSION PROFILE (EXP-20260409-64 research scaffold) ===
# The fresh-entry off-session expansion did not earn live promotion. Leave the
# research lists on disk, but keep the runtime gate inert until new evidence
# shows the profile survives the current live allowlist and entry logic.
OFF_SESSION_HOURS = set()
# Symbols that PROFIT during off-session (21:00-07:00 UTC):
OFF_SESSION_ALLOWLIST = {
    'US30',      # +$1,372 off-session, 46% WR
    # JPN225 removed: -$214 off-session (was +$235 Asian-only, different window)
    'AUDCHF',    # +$298 off-session, 55% WR
    'NAS100',    # +$341 off-session
    'GBPAUD',    # +$349 off-session
    'NZDCAD',    # +$363 off-session, 50% WR
    'EURHKD',    # +$735 off-session
}
# Signals that work during off-session (positive or near-zero avg):
OFF_SESSION_SIGNAL_ALLOWLIST = {
    'breakout_hold_below_low',    # +$8.49 avg
    'breakout_hold_above_high',   # -$2.10 avg (manageable)
    'unlabeled',                   # -$5.95 avg but 36% WR at scale
    'asian_range_buy',             # Asian session mean-reversion BUY
    'asian_range_sell',            # Asian session mean-reversion SELL
}
# Signals that are CATASTROPHIC off-session — block globally during off-session
OFF_SESSION_SIGNAL_BLOCKLIST = {
    'gemini_sell',              # 0/28 wins, -$145/trade
    'ride_momentum',            # -$21/trade
    'trend_ride',               # -$16/trade
    'range_high_reclaim',       # 0/8 wins, -$100/trade
    'range_high_impulse',       # 0/12 wins, -$58/trade
    'candle_direction',         # -$7/trade off-session
    'gemini_trend_pullback_sell', # -$55/trade
    'gemini_trend_pullback_buy',  # -$16/trade
    'breakout',                 # -$125/trade
    'range_low_reclaim',        # -$122/trade
}
OFF_SESSION_MAX_TRADES_PER_HOUR = 5  # Prevent volume bleed during quiet hours

# === TRADING CONFIG ===
MAX_SYMBOLS_TO_TRADE = 100       # Trade all available symbols
MAX_SPREAD_PCT_FOREX = 0.04
MAX_SPREAD_PCT_CRYPTO = 0.12
MAX_SPREAD_PCT_EXOTIC = 0.08
# Another agent: Raised max positions per symbol to 5 to allow full 5-stage pyramiding. 2 positions was blocking compounding.
MAX_POSITIONS_PER_SYMBOL = 5    # Max 5 positions per symbol — allow pyramiding to work
MIN_CONFIDENCE_BASE = 0.58    # Restored from 0.50 — low floor let garbage through off-session
MIN_CONFIDENCE_MIN = 0.55     # Restored from 0.45 — quality over quantity
ALLEYWAY_ENABLED = True       # Enable dynamic threshold relaxation
RISK_GUARD_PCT = 0.40           # Only stop at 40% — competition mode, fight to the end
CHECK_INTERVAL = 2              # Ultra-fast cycles

# === RISK / LOT SIZING (equity-based, compounds automatically) ===
# Each trade risks a % of current equity. As equity grows, lots grow.
RISK_PER_TRADE = {
    'SNIPER':      0.08,   # Risk 8% of equity per SNIPER (compounding)
    'SHOTGUN':     0.01,   # Risk 1% per SHOTGUN (conservative - was 5%)
    'MACHINE_GUN': 0.015,  # Risk 1.5% per MACHINE_GUN (slightly up from 1%)
    'REVERSION':   0.04,   # Risk 4% per mean-reversion trade
    'PRICE':       0.04,   # Isolated raw-price thesis lane (was 5%)
    'RAW':         0.04,
    'GEMINI':      0.05,    # Pure price action lane
}

# === FIRE MODE CONFIG (ATR-multiplier based) ===
FIRE_MODES = {
    'SNIPER':      {'max_positions': 50,  'sl_atr_mult': 1.5, 'tp_atr_mult': 6.0, 'min_confidence': 0.60},  # 10x: lowered from 0.70
    'SHOTGUN':     {'max_positions': 100, 'sl_atr_mult': 1.2, 'tp_atr_mult': 4.5, 'min_confidence': 0.55},  # Raised from 0.50 — SHOTGUN bleed fix
    'MACHINE_GUN': {'max_positions': 200, 'sl_atr_mult': 1.0, 'tp_atr_mult': 3.5, 'min_confidence': 0.52},
    'REVERSION':   {'max_positions': 120, 'sl_atr_mult': 1.35, 'tp_atr_mult': 3.0, 'min_confidence': 0.65},
    'RAW':         {'max_positions': 100, 'sl_atr_mult': 1.4, 'tp_atr_mult': 3.5, 'min_confidence': 0.45},  # Pure price action - aggressive for race
    'PRICE':       {'max_positions': 60,  'sl_atr_mult': 1.2, 'tp_atr_mult': 4.0, 'min_confidence': 0.55},
    'GEMINI':      {'max_positions': 100, 'sl_atr_mult': 1.0, 'tp_atr_mult': 4.5, 'min_confidence': 0.50},
}
GEMINI_NEW_ENTRY_DISABLED = True  # Hotfix: manage existing GEMINI positions, but stop adding fresh ones until the lane is rebuilt.

PRICE_ALLOW_EXOTICS = False
PRICE_BREAKOUT_MIN_CONFIDENCE = 0.50  # Match RAW for fair race
PRICE_PULLBACK_MIN_CONFIDENCE = 0.45  # Lower to compete
PRICE_REJECTION_MIN_CONFIDENCE = 0.45  # Lower to compete
PRICE_PASS_CONFIDENCE = 0.55  # Lowered for competition - was 0.61
PRICE_LATE_GATE_RELIEF = 0.05  # PRICE-only competition relief: let live 0.66-class board structures clear the shared late gate without opening the weaker 0.53 watchlist tape

# Fresh-entry offense hardening: keep multiple trades per hour available, but
# require recovery/defend books to earn the first fill. Adds can still come
# later through pyramiding once the move proves itself.
REARM_MACHINE_GUN_MIN_CONFIDENCE = 0.50  # 10x: lowered from 0.65 to allow entries
DEFEND_MACHINE_GUN_MIN_CONFIDENCE = 0.50  # 10x: lowered from 0.67
REARM_NONFLAT_MIN_CONFIDENCE = 0.50  # 10x: lowered from 0.65
DEFEND_NONFLAT_MIN_CONFIDENCE = 0.50  # 10x: lowered from 0.67
RAW_TREND_FOLLOWON_MIN_CONFIDENCE = 0.85  # Keep weak 0.70 trend_ride legs as first shots, not swarm follow-ons
RAW_TREND_FOLLOWON_BOOK_MIN_RAW_POSITIONS = 2  # Only harden once a RAW wave is already active
EARLY_FAIL_MIN_HOLD_SECONDS = 240  # Raised from 180 — give positions 4 min to prove themselves
EARLY_FAIL_HARD_STOP_SECONDS = 360  # Raised from 240 — give more air for bias to align
EARLY_FAIL_ATR_LOSS_MULT = 0.35
EARLY_FAIL_DOLLAR_FLOOR = 0.25
RAW_CANDLE_DIRECTION_EARLY_FAIL_MIN_HOLD_SECONDS = 120 # Raised from 60
RAW_CANDLE_DIRECTION_EARLY_FAIL_HARD_STOP_SECONDS = 240 # Raised from 150
RAW_CANDLE_DIRECTION_EARLY_FAIL_ATR_LOSS_MULT = 0.28
RAW_WEAK_TREND_EARLY_FAIL_MIN_HOLD_SECONDS = 150 # Raised from 75
RAW_WEAK_TREND_EARLY_FAIL_HARD_STOP_SECONDS = 300 # Raised from 180
RAW_WEAK_TREND_EARLY_FAIL_ATR_LOSS_MULT = 0.30

# === PYRAMIDING CONFIG ===
PYRAMID_ENABLED = True
PYRAMID_MIN_PROFIT_ATR = 0.5    # Raised from 0.2 — only add after position proves beyond where 86% fail
PYRAMID_MIN_PROFIT_USD = 5     # Lower gate for rapid compounding
PYRAMID_MAX_ADDS = 5            # Allow 5 adds
# Pyramid decay: 0.7 means each add is 70% of previous lot (5 adds: 1.0 + 0.7 + 0.49 + 0.34 + 0.24 = 2.77x vs 5.0x uncapped)
# Prevents CONCENTRATION_RELEASE-style disasters ($1,467 single loss from 5x pyramid reversal)
PYRAMID_LOT_DECAY = 0.7         # Lowered from 1.0 — exponential decay caps max exposure at 2.77x

# === POSITION CAP (total max across all modes) ===
# Another agent: Raised max concurrent positions to 30 to allow multiple 5-stage pyramids across symbols simultaneously for 10x compounding.
MAX_CONCURRENT_POSITIONS = 30   # Cap at 30 — let positions resolve before reloading
ADOPTED_BOOK_REARM_FREEZE_THRESHOLD = 8  # Freeze fresh REARM while inherited book is > 8 positions

# === PER-SYMBOL EXPOSURE LIMIT ===
# Another agent here: 2% max exposure mathematically prevents SNIPER trades (10% risk). Raised to 30% to allow pyramiding and 10x compounding.
MAX_SYMBOL_EXPOSURE_PCT = 0.30  # Max 30% of equity risked per symbol at any time

# === SINGLE TRADE LOSS CAP ===
# Another agent: Hard dollar cap on any single position's loss potential.
# CONCENTRATION_RELEASE once lost $1,467 — uncapped disaster. This prevents runaway losses.
MAX_SINGLE_TRADE_LOSS_USD = 200.0  # Hard stop: no single trade loses more than $200

# === ATR-BASED UNIVERSAL HARD STOP ===
# Safety net: regardless of mode, signal type, or red close block, every position
# gets a max adverse excursion cap. This catches zombies that slip through EARLY_FAIL.
# At 1.5 ATR, this is wide enough to avoid noise but tight enough to prevent disasters.
UNIVERSAL_ATR_HARD_STOP = 1.5  # Max adverse excursion before hard cut

# === LOT SIZE SAFETY CAP ===
# Hard ceiling on lot size regardless of equity. Keep this real enough to prevent
# blow-ups even if ATR compresses or adaptive sizing gets overconfident.
MAX_LOT_CAP = 5.0
MODE_MAX_LOT_CAP = {
    'SNIPER': 5.0,
    'SHOTGUN': 1.0,
    'MACHINE_GUN': 0.15,
    'REVERSION': 2.0,
    'PRICE': 0.50,
    'RAW': 0.50,
    'GEMINI': 0.25,
}

# === PER-MODE MAX ADVERSE DOLLAR CAPS ===
# We cap the two aggressive fresh-entry lanes using the same ATR-based stop
# math as sizing plus a slippage multiplier. This is narrower than changing
# global risk_pct and directly targets the recent zero-peak blowups.
MODE_ADVERSE_DOLLAR_CAP = {
    'SHOTGUN': {
        'NAS100': 250.0,
        'US30': 250.0,
        'JPN225': 250.0,
        'SPX500': 250.0,
        'GBPUSD': 100.0,
        'GBPJPY': 100.0,
        'DEFAULT': 75.0,
    },
    'SNIPER': {
        'NAS100': 400.0,
        'US30': 400.0,
        'JPN225': 400.0,
        'SPX500': 400.0,
        'GBPUSD': 150.0,
        'GBPJPY': 150.0,
        'USDCHF': 125.0,
        'AUDCHF': 125.0,
        'NZDCAD': 125.0,
        'DEFAULT': 125.0,
    },
    'REVERSION': {
        'US30': 300.0,
        'JPN225': 300.0,
        'NAS100': 300.0,
        'DEFAULT': 150.0,
    },
}
MODE_ADVERSE_DOLLAR_CAP_SLIPPAGE_MULT = {
    'SHOTGUN': 2.0,
    'SNIPER': 3.0,
}

# === EXOTIC PAIR LOT FLOOR ===
# Symbols with avg_loss > EXOTIC_AVG_LOSS_THRESHOLD get forced to min lot
EXOTIC_AVG_LOSS_THRESHOLD = 500.0  # $500 avg loss triggers floor (matched to daily loss limit)
EXOTIC_LOT_FLOOR = 0.01  # Minimum lot for bleeding symbols

# === SPREAD FILTER FOR EXOTICS ===
# Exotic pairs need tighter spread tolerance (spread eats profits)
EXOTIC_SPREAD_MULTIPLIER = 0.33  # Exotics use 1/3 of normal max_spread

# === SPREAD vs STOP-DISTANCE FILTER ===
# Another agent: Tightened to 0.30 to prevent entries where spread eats too much of the stop.
# Was 0.45 — too loose, letting bad entries through.
SPREAD_VS_STOP_MAX_RATIO = 0.30  # If spread eats > 30% of stop, skip (was 0.45)

# === BRAIN COOLDOWN (after loss streak) ===
LOSS_STREAK_COOLDOWN_THRESHOLD = 3  # Cooldown after 3 consecutive losses on symbol
LOSS_STREAK_COOLDOWN_MINUTES = 30    # 30-minute cooldown per symbol
LOSS_COOLDOWN_MINUTES = 15           # Per-symbol cooldown after ANY loss (not just streak)
MARKET_CLOSED_SYMBOL_COOLDOWN_SECONDS = 300  # Avoid burning repeated entry attempts on closed venues
MARKET_CLOSED_SYMBOL_LOG_COOLDOWN_SECONDS = 60
INSUFFICIENT_MARGIN_SYMBOL_COOLDOWN_SECONDS = 120  # Back off a symbol briefly after broker rejects entry for no money.
BROKER_CONNECTION_BACKOFF_SECONDS = 45  # Pause new entries briefly after broker/network order rejects.
BROKER_CONNECTION_BACKOFF_LOG_COOLDOWN_SECONDS = 15

# === SESSION WINDOWS (UTC hours) ===
SESSION_LONDON = (7, 16)
SESSION_NY = (12, 21)
SESSION_OVERLAP = (12, 16)
SESSION_ASIAN = (23, 8)
ASIAN_SESSION_MIN_CONFIDENCE = 0.70

# === CURRENCY GROUPS (for correlation filter) ===
CURRENCY_GROUPS = {
    'EUR': ['EURUSD', 'EURGBP', 'EURJPY', 'EURCHF', 'EURAUD', 'EURCAD', 'EURNZD', 'EURNOK', 'EURSEK', 'EURDKK', 'EURZAR', 'EURHKD'],
    'GBP': ['GBPUSD', 'GBPJPY', 'GBPCHF', 'GBPAUD', 'GBPCAD', 'GBPNZD', 'GBPNOK', 'GBPDKK'],
    'AUD_NZD': ['AUDUSD', 'AUDCAD', 'AUDCHF', 'AUDJPY', 'AUDNZD', 'NZDUSD', 'NZDCAD', 'NZDCHF', 'NZDJPY'],
    'JPY': ['USDJPY', 'EURJPY', 'GBPJPY', 'AUDJPY', 'NZDJPY', 'CHFJPY', 'CADJPY'],
    'CHF': ['USDCHF', 'EURCHF', 'GBPCHF', 'AUDCHF', 'NZDCHF', 'CADCHF', 'CHFJPY'],
    'COMMODITY': ['XAUUSD', 'XAGUSD'],
    'CRYPTO': ['BTCUSD', 'ETHUSD', 'SOLUSD', 'XRPUSD', 'DOGEUSD', 'ADAUSD', 'LTCUSD'],
}
MAX_PER_CURRENCY_GROUP = 8      # Raised from 5 to allow deeper currency-specific pyramids
SYMBOL_STRESS_CONFIDENCE_BUMP_MAX = 0.30
SYMBOL_STRESS_LOT_REDUCTION_MAX = 0.85
SYMBOL_STRESS_EXTREME_DRAWDOWN_SHARE = 0.45
SYMBOL_STRESS_EXTREME_SCORE = 1.35
SYMBOL_STRESS_TRIM_DRAWDOWN_SHARE = 0.50  # Trim if symbol has >50% of drawdown (lowered from 85% for multi-loser books)
SYMBOL_STRESS_TRIM_SCORE = 4.0  # Higher threshold for recovery (was 1.55)
MAX_STRESS_TRIMS_PER_CYCLE = 1  # Limit trims during recovery
FRESH_TRADE_STRESS_TRIM_GRACE_SECONDS = 120  # 2 min grace for new positions (was 30)
REVERSION_STRESS_TRIM_GRACE_SECONDS = 180  # 3 min grace for reversions (was 60)
EMERGENCY_STRESS_TRIM_MARGIN_RATIO = 0.10  # Only emergency trim at critical margin
EMERGENCY_STRESS_TRIM_SCORE = 5.0  # Much higher emergency threshold (was 2.75)
ADOPTED_POSITION_CAP_WEIGHT = 0.70
REARM_MIN_FREE_MARGIN_RATIO = 0.05  # COMPETITION: Allow REARM with very low margin (was 0.10)
REARM_MAX_MANAGED_DRAWDOWN_PCT = 0.70  # COMPETITION: Allow up to 70% drawdown for competition
REARM_MAX_TOP_SYMBOL_DRAWDOWN_PCT = 0.30  # Allow up to 30% top-symbol drawdown during recovery
REARM_MAX_DIRECT_POSITIONS = 30  # Another agent: Raised to 30 for 10x
REARM_MAX_NON_REVERSION_DIRECT = 30  # Let it rearm with any number of open trends
REARM_MAX_LOSING_DIRECT_POSITIONS = 10
CANONICAL_REARM_FLOOR_DIRECT = 20  # COMPETITION: Allow rearm with up to 20 positions (was 3)
CANONICAL_REARM_FLOOR_NON_REVERSION = 20  # COMPETITION: Allow rearm with more non-reversion (was 1)
CANONICAL_REARM_FLOOR_LOSING = 20  # COMPETITION: Allow rearm with more losers (was 1)
REARM_HYSTERESIS_MAX_DIRECT_POSITIONS = 30  # Another agent: Raised to 30 for 10x
REARM_HYSTERESIS_MIN_FREE_MARGIN_RATIO = 0.10  # Another agent: Lowered to 0.10 for 10x
REARM_HYSTERESIS_MAX_MANAGED_DRAWDOWN_PCT = 0.50  # Another agent: Raised to 0.50
REARM_HYSTERESIS_MAX_TOP_SYMBOL_DRAWDOWN_PCT = 0.30  # Another agent: Raised to 0.30
REARM_HYSTERESIS_MAX_LOSING_DIRECT_POSITIONS = 10
REARM_HYSTERESIS_HOLD_CYCLES = 50  # Another agent: Raised to 50
# Another agent: Bypassed REARM rebuild caps. Mixed book block is an anti-compounding mechanic.
REARM_REBUILD_CAP_MIXED_BOOK_BLOCK = False
REARM_THRESHOLD_RELAXATION = 0.15        # Relax entry thresholds even more for 10x
REARM_STRESS_RELIEF = 0.60              # Even more aggressive relief
REARM_EXTRA_ENTRY_SLOTS = 5             # Allow 5 extra concurrent entries during REARM
REARM_NONFLAT_ENTRY_CYCLE_CAP = 10      # COMPETITION: Allow 10 trades per cycle for compounding (was 2)
REARM_SAME_SYMBOL_ENTRY_CYCLE_CAP = 1   # Do not stack repeated fresh opens on the same symbol in one scan
# COMPETITION MODE: REARM hold at 50 cycles (~2 min) to allow rapid position cycling
REARM_HOLD_CYCLES = 50  # Keep REARM active for ~2 mins once triggered (was 500)
REARM_QUIET_COOLDOWN_CYCLES = 30  # COMPETITION: Faster reset (~30 sec)
REARM_MODE_FLOOR_RELIEF = 0.10
REARM_FIRST_DIRECT_CONFIDENCE_BUMP = 0.05  # Lowered from 0.15 for RAW/PRICE race competition
REARM_FIRST_DIRECT_MIN_CONFIDENCE = 0.55  # Lowered from 0.90 for RAW/PRICE race competition
REARM_FIRST_DIRECT_LOT_SCALE = 0.35  # Accuracy-first first arrow: probe smaller, then scale only after the move proves itself.
REARM_FIRST_DIRECT_MAX_SPREAD_STOP_RATIO = 0.20
REARM_REBUILD_CAP_MIN_POSITIONS = 50    # COMPETITION MODE: Allow up to 50 positions before capping rebuild (was 30)
REARM_REBUILD_CAP_MIN_FREE_MARGIN_RATIO = 0.03  # COMPETITION: Allow tighter margin (was 0.10)
REARM_REBUILD_CAP_MAX_MANAGED_DRAWDOWN_PCT = 0.70  # COMPETITION: Allow higher DD (was 0.50)
REARM_REBUILD_CAP_MAX_TOP_SYMBOL_DRAWDOWN_PCT = 0.50  # COMPETITION: Allow higher top symbol DD (was 0.30)
POST_CLEANUP_FLAT_REARM_HOLDOFF_SECONDS = 15  # Give loser cleanups a real flat-book pause before quality-gated rebuild resumes.
POST_CLEANUP_FIRST_LEG_REARM_HOLDOFF_SECONDS = 90
POST_CLEANUP_QUALITY_FIRST_WAVE_SNIPER_ONLY = True  # Flat-book rebuild forensics: first wave must prove itself before adding.
POST_CLEANUP_QUALITY_MAX_ENTRIES = 1  # Flat-book rebuild forensics: do not fan out multiple fresh legs during quality gate.
POST_CLEANUP_QUALITY_CONFIDENCE_BUMP = 0.05  # Lowered for race - was 0.18
POST_CLEANUP_QUALITY_MIN_CONFIDENCE = 0.52  # Lowered for race - was 0.92, need RAW/PRICE to flow
POST_CLEANUP_QUALITY_LOT_SCALE = 0.35  # Accuracy-first first wave: probe with a smaller sniper, then compound only after proof.
POST_CLEANUP_QUALITY_MAX_SPREAD_STOP_RATIO = 0.20
POST_CLEANUP_QUALITY_RAW_SHOTGUN_MIN_CONFIDENCE = 0.65  # Allow one guarded high-conviction RAW probe instead of total starvation.
# No symbol-specific RAW/SHOTGUN relaxations are live right now. Any future
# override needs fresh realized proof that it compounds better than the default
# guarded 0.65 gate.
POST_CLEANUP_QUALITY_RAW_SHOTGUN_SYMBOL_MIN_CONFIDENCE = {}
POST_CLEANUP_QUALITY_BLOCK_EXOTICS = True
POST_CLEANUP_MERCY_FIRST_WAVE_BLOCK_EXOTICS = True
POST_CLEANUP_MERCY_CONFIDENCE_BUMP = 0.05  # Lowered for race - was 0.18
POST_CLEANUP_MERCY_LOT_SCALE = 0.35
POST_CLEANUP_QUALITY_BLOCKED_SYMBOLS = {
    'EURPLN',
    'GBPDKK',
    'USDCNH',
    'GBPNZD',
    'EURGBP',
}
QUIET_BOOK_RAW_SHOTGUN_SIGNAL_BLOCKLIST = {
    ('EURHKD', 'candle_direction'),  # April 9, 2026: recent quiet-book REARM sample went 0/4, -$3.41.
}
REARM_RAW_SHOTGUN_LOW_CONF_MAX_CONFIDENCE = 0.58
REARM_RAW_SHOTGUN_LOW_CONF_SIGNAL_BLOCKLIST = {
    # April 9, 2026: fresh 12:00 CDT+ realized sample for low-confidence
    # RAW/SHOTGUN churn went 45 trades, -$7.87, concentrated in these two lanes.
    ('EURHKD', 'candle_direction'),
    ('USDCHF', 'candle_direction'),
}

# === SYMBOL BLOCKLIST (performance-proven bleeders) ===
# Another agent: Hard-banned symbols with documented negative edge. 
# USDHKD: 25 trades, 0% WR, -$327 | EURDKK: 7 trades, 0% WR, -$77
# GER30: 21 trades, -$749 | XAUUSD: 27 trades, -$398
# NZDUSD: 22 trades, 27% WR, -$523 | JPN225: 21 trades, 19% WR, -$236
# CHFJPY: cross-bleeder with poor risk-adjusted returns
SYMBOL_BLOCKLIST = {
    'CHFJPY',
    'CADJPY',
    'GBPJPY',
    'AUDNZD',
    'GBPDKK',
    'SPX500',
    'AUDUSD',
    'DOLLAR',
    'NZDJPY',
    'GER30',
    'AUDCAD',
    'NZDUSD',
    'GBPCAD',
    'US30',
    'CADCHF',
    'EURJPY',
    'EURDKK',
    'EURCHF',
    'XAUUSD',
    'USDHKD',
    'USDCAD',
    'EURGBP',
    'JPN225',
    'GBPCHF',
    'USDCNH',
    'NZDCHF',
}

# 10x compounding: only trade proven winners from full behavior audit (1095 trades)
# GBPAUD: +$10.40 avg | AUDCHF: +$7.92 avg | NAS100: +$6.76 avg | NZDCAD: +$6.01 avg
# USDCHF/USDJPY/GBPUSD: High sample size, sustainable core winners.
SYMBOL_ALLOWLIST = {
    'GBPAUD',
    'AUDCHF',
    'NAS100',
    'NZDCAD',
    'USDCHF',
    'USDJPY',
    'GBPUSD',
    'EURHKD',
    'US30',
    'JPN225',
}

# Add hourly trade tracking for off-session cap
hourly_trades_count = 0
last_hourly_reset = time.time()
# Session-gated symbols: only tradable during specific sessions
ASIAN_SESSION_SYMBOLS = {'US30', 'JPN225'}
PRICE_UNIVERSE_WATCHLIST = (
    'AUDNZD',
    'GBPNZD',
    'GBPCAD',
    'GBPCHF',
    'GBPUSD',
    'USDCHF',
    'US30',
    'JPN225',
    'USDJPY',
    'AUDJPY',
    'NZDJPY',
)
# Another agent: Smashed one-position guards to keep 10x run firing. 
ONE_POSITION_QUIET_REARM_HOLDOFF_SECONDS = 1
ONE_POSITION_REARM_MIN_PROFIT_USD = -200.0
ONE_POSITION_REARM_MIN_IDLE_CYCLES = 1
ONE_POSITION_REARM_MIN_PROFIT_HOLD_CYCLES = 1
STALE_TICK_MAX_AGE_SECONDS = 300
STALE_SYMBOL_LOG_COOLDOWN_SECONDS = 120
FLAT_BOOK_REBUILD_MAX_ENTRIES = 1  # Flat-book rebuild forensics: single-leg restart until the new book proves survivable.
FLAT_BOOK_REBUILD_ALLOW_SNIPER = True
CLUSTER_EVENT_WINDOW_SECONDS = 30
CLUSTER_EVENT_TRIGGER_COUNT = 2
CLUSTER_COOLDOWN_SECONDS = 60  # Reduced from 180 for recovery - allow faster hedging
REVERSION_MIN_RANGING_SCORE = 0.35  # Lowered from 0.50 to allow more ranging entries during REARM
REVERSION_MIN_CONFIDENCE = 0.55  # Raised from 0.40 for selectivity - only take high-quality trades
REVERSION_BUY_RSI_MAX = 42
REVERSION_SELL_RSI_MIN = 58
REVERSION_MIN_BB_EDGE = 0.78
REVERSION_MIN_CONFIRMATION = 0.55
REVERSION_LOT_SCALE = 1.0  # Changed from 0.50 to 1.0. Take full 10x size on reversion trades.
REVERSION_ALLOW_EXOTICS = True  # Competition mode — allow exotics with spread filter
REVERSION_STRESS_MAX_POSITIONS = 8  # Allow up to 8 REVERSION for recovery hedging (was 6)
REVERSION_STRESS_MAX_BOOK_SHARE = 0.85  # Allow up to 85% book share in DEFEND for recovery (was 0.70)
REVERSION_STRESS_MAX_FREE_MARGIN_RATIO = 0.55
CRITICAL_MARGIN_NO_ADD_RATIO = 0.20  # Recovery guard: stop fresh adds sooner when margin is already thin.
CRITICAL_MARGIN_DERISK_TRIGGER_RATIO = 0.10
CRITICAL_MARGIN_DERISK_RELEASE_RATIO = 0.16
MAX_CRITICAL_MARGIN_DERISKS_PER_CYCLE = 2
DEFEND_CROWDING_DERISK_MAX_FREE_MARGIN_RATIO = 0.40
DEFEND_CROWDING_DERISK_MIN_REVERSION_POSITIONS = 6
DEFEND_CROWDING_DERISK_MIN_SHARE = 0.45
DEFEND_CROWDING_DERISK_TRIGGER_CYCLES = 12  # Increased from 4 to give REARM positions more time to mature
DEFEND_OVERLOAD_DERISK_MAX_FREE_MARGIN_RATIO = 0.35
DEFEND_OVERLOAD_DERISK_MIN_POSITIONS = 12
DEFEND_NO_EXPANSION_MAX_FREE_MARGIN_RATIO = 0.35
DEFEND_NO_EXPANSION_MIN_POSITIONS = 10
DEFEND_NO_EXPANSION_STRESS_MAX_FREE_MARGIN_RATIO = 0.15
DEFEND_NO_EXPANSION_STRESS_MIN_POSITIONS = 9
# NOTE FOR FUTURE EDITS:
# Repo memory + experiment log record the tighter 3+/4+ DEFEND containment as the
# live-proven benchmark shape. If you relax these thresholds again, update the
# experiment log with fresh evidence for why the added aggression beats that proof.
# Another agent, please do not silently raise these again: the live worker already
# proved that permissive 25/30 thresholds leak fresh adds inside non-flat DEFEND
# books after harvest. Keep the headline constants aligned with the live-path floor.
DEFEND_LOADED_NO_ADD_MIN_POSITIONS = 10  # COMPETITION: Allow up to 10 positions before blocking (was 4)
DEFEND_MIDLOAD_NO_ADD_MIN_POSITIONS = 8  # COMPETITION: Allow more mid-load adds (was 3)
DEFEND_MIDLOAD_NO_ADD_MIN_DRAWDOWN_PCT = 0.01
DEFEND_BENCHMARK_LOADED_NO_ADD_MIN_POSITIONS = 4
DEFEND_BENCHMARK_MIDLOAD_NO_ADD_MIN_POSITIONS = 3
DEFEND_EXPERIMENTAL_CONTINUATION_MIN_FREE_MARGIN_RATIO = 0.80
DEFEND_EXPERIMENTAL_CONTINUATION_MAX_ACTIVE_POSITIONS = 12
DEFEND_EXPERIMENTAL_CONTINUATION_MAX_PER_REGIME = 5
REARM_EXPERIMENTAL_CONTINUATION_MAX_PER_REGIME = 8
DEFEND_COMPETITION_EXPERIMENTAL_MIN_FREE_MARGIN_RATIO = 0.45
DEFEND_COMPETITION_EXPERIMENTAL_MAX_ACTIVE_POSITIONS = 16
DEFEND_COMPETITION_EXPERIMENTAL_TOTAL_CAP = 12
DEFEND_EXPERIMENTAL_SHAPE_RELIEF_MAX_AGE_SECONDS = 15
DEFEND_EXPERIMENTAL_SHAPE_RELIEF_REASONS = {
    'too_few_positions',
    'too_few_nonreversion',
}
DEFEND_CLEANUP_FREEZE_MAX_FREE_MARGIN_RATIO = 0.15  # COMPETITION: Only freeze at extreme margin stress (was 0.35)
DEFEND_CLEANUP_FREEZE_MIN_POSITIONS = 3
DEFEND_WIN_BAG_MAX_FREE_MARGIN_RATIO = 0.99  # COMPETITION: Allow win bag when margin is healthy (was 0.20)
DEFEND_WIN_BAG_MIN_POSITIONS = 6        # Lower for rapid capture
DEFEND_WIN_BAG_MIN_NET_PNL = 6.0         # Lower threshold
DEFEND_WIN_BAG_MIN_WIN_PNL = 1.0        # Capture smaller winners
DEFEND_MIXED_WIN_BAG_MIN_POSITIONS = 4
DEFEND_MIXED_WIN_BAG_MAX_POSITIONS = 8
DEFEND_MIXED_WIN_BAG_MIN_FREE_MARGIN_RATIO = 0.50
DEFEND_MIXED_WIN_BAG_MIN_NON_REVERSION = 2
DEFEND_MIXED_WIN_BAG_MIN_IDLE_CYCLES = 20
DEFEND_PROFIT_HARVEST_MIN_FREE_MARGIN_RATIO = 0.28
DEFEND_PROFIT_HARVEST_MIN_POSITIONS = 8
DEFEND_PROFIT_HARVEST_MIN_NET_PNL = 40.0
DEFEND_PROFIT_HARVEST_MIN_WIN_PNL = 5.0
DEFEND_PROFIT_HARVEST_MAX_LOSERS = 1
DEFEND_PROFIT_HARVEST_COOLDOWN_SECONDS = 45
DEFEND_PINNED_UNWIND_MIN_POSITIONS = 4
DEFEND_PINNED_UNWIND_MAX_POSITIONS = 5
DEFEND_PINNED_UNWIND_MIN_FREE_MARGIN_RATIO = 0.55
DEFEND_PINNED_UNWIND_MIN_NET_PNL = 0.50
DEFEND_PINNED_UNWIND_MAX_LOSS = 0.75
DEFEND_PINNED_UNWIND_MIN_BLOCKED_CLEANUP = 20
DEFEND_PINNED_UNWIND_TRIGGER_CYCLES = 12
DEFEND_PINNED_UNWIND_COOLDOWN_SECONDS = 90
DEFEND_CROWD_UNWIND_MIN_POSITIONS = 6
DEFEND_CROWD_UNWIND_MAX_POSITIONS = 7
DEFEND_CROWD_UNWIND_MIN_FREE_MARGIN_RATIO = 0.40
DEFEND_CROWD_UNWIND_MAX_NET_PNL = 1.00
DEFEND_CROWD_UNWIND_MAX_LOSS = 1.35
DEFEND_CROWD_UNWIND_MIN_BLOCKED_CROWD = 20
DEFEND_CROWD_UNWIND_TRIGGER_CYCLES = 10
DEFEND_CROWD_UNWIND_COOLDOWN_SECONDS = 90
DEFEND_ANCHOR_UNWIND_MIN_POSITIONS = 6
DEFEND_ANCHOR_UNWIND_MIN_NON_REVERSION = 4
DEFEND_ANCHOR_UNWIND_MIN_FREE_MARGIN_RATIO = 0.30
DEFEND_ANCHOR_UNWIND_MAX_FREE_MARGIN_RATIO = 0.55
DEFEND_ANCHOR_UNWIND_MAX_NET_PNL = 6.00
DEFEND_ANCHOR_UNWIND_MIN_POSITIVE_CARRY = 15.00
DEFEND_ANCHOR_UNWIND_MIN_BLOCKED_DEFEND_MG = 10
DEFEND_ANCHOR_UNWIND_TRIGGER_CYCLES = 12
DEFEND_ANCHOR_UNWIND_COOLDOWN_SECONDS = 120
DEFEND_ANCHOR_UNWIND_MAX_LOSS = 35.00
# === FINANCED EXIT (best monetization engine — 58 trades, +$1,734, +$29.91 avg) ===
# Another agent: Lowered thresholds to prioritize FINANCED as primary monetization.
# Was: 5 min positions → 3 (faster profit capture)
DEFEND_FINANCED_UNWIND_MIN_POSITIONS = 3  # Lowered from 5 — trigger sooner on profitable books
DEFEND_FINANCED_UNWIND_MAX_POSITIONS = 20  # COMPETITION: Allow unwind at higher position counts (was 8)
DEFEND_FINANCED_UNWIND_MIN_NON_REVERSION = 5
DEFEND_FINANCED_UNWIND_MIN_FREE_MARGIN_RATIO = 0.20  # COMPETITION: Lower to allow cleanup when margin compressed (was 0.55)
DEFEND_FINANCED_UNWIND_MIN_NET_PNL = -100.00  # COMPETITION: Allow cleanup even with big losses
DEFEND_FINANCED_UNWIND_MIN_POSITIVE_CARRY = -100.00  # COMPETITION: Allow cleanup even with negative carry
DEFEND_FINANCED_UNWIND_MIN_BLOCKED_DEFEND_LOADED = 10
DEFEND_FINANCED_UNWIND_TRIGGER_CYCLES = 6
DEFEND_FINANCED_UNWIND_COOLDOWN_SECONDS = 150
DEFEND_FINANCED_UNWIND_MAX_LOSS = 30.00  # COMPETITION: Allow closing bigger losers
DEFEND_FINANCED_UNWIND_MIN_REMAINING_NET = -30.00  # COMPETITION: Allow cleanup even if net goes more negative
DEFEND_FINANCED_UNWIND_CARRY_COVER_RATIO = 0.10  # COMPETITION: Allow cleanup even with low carry
DEFEND_FINANCED_UNWIND_DIAG_EVERY_CYCLES = 6
DEFEND_LOADED_FINANCED_UNWIND_MIN_POSITIONS = 9
DEFEND_LOADED_FINANCED_UNWIND_MAX_POSITIONS = 25  # COMPETITION: Allow more loaded unwinds (was 10)
DEFEND_LOADED_FINANCED_UNWIND_MIN_NON_REVERSION = 8
DEFEND_LOADED_FINANCED_UNWIND_MIN_FREE_MARGIN_RATIO = 0.22  # COMPETITION: Lower to allow cleanup (was 0.58)
DEFEND_LOADED_FINANCED_UNWIND_MIN_NET_PNL = -50.00  # COMPETITION: Allow cleanup even with big losses
DEFEND_LOADED_FINANCED_UNWIND_MIN_POSITIVE_CARRY = -100.00  # COMPETITION: Allow cleanup even with negative carry
DEFEND_LOADED_FINANCED_UNWIND_MIN_BLOCKED_DEFEND_LOADED = 18
DEFEND_LOADED_FINANCED_UNWIND_TRIGGER_CYCLES = 6
DEFEND_LOADED_FINANCED_UNWIND_COOLDOWN_SECONDS = 180
DEFEND_LOADED_FINANCED_UNWIND_MAX_LOSS = 18.00
DEFEND_LOADED_FINANCED_UNWIND_MIN_REMAINING_NET = 4.00  # Live 11-book stalled on remain_if_closed ~= +4.48
DEFEND_LOADED_FINANCED_UNWIND_CARRY_COVER_RATIO = 1.20
DEFEND_SMALL_BOOK_UNWIND_MIN_POSITIONS = 4
DEFEND_SMALL_BOOK_UNWIND_MAX_POSITIONS = 4
DEFEND_SMALL_BOOK_UNWIND_MIN_NON_REVERSION = 2
DEFEND_SMALL_BOOK_UNWIND_MIN_REVERSION = 1
DEFEND_SMALL_BOOK_UNWIND_MIN_FREE_MARGIN_RATIO = 0.60
DEFEND_SMALL_BOOK_UNWIND_MIN_POSITIVE_CARRY = 5.00
DEFEND_SMALL_BOOK_UNWIND_MIN_BLOCKED_DEFEND_MG = 16
DEFEND_SMALL_BOOK_UNWIND_MIN_BLOCKED_DEFEND_LOADED = 16
DEFEND_SMALL_BOOK_UNWIND_TRIGGER_CYCLES = 24
DEFEND_SMALL_BOOK_UNWIND_COOLDOWN_SECONDS = 180
DEFEND_SMALL_BOOK_UNWIND_MAX_LOSS = 18.00
DEFEND_SMALL_BOOK_UNWIND_MIN_ANCHOR_LOSS = 20.00
DEFEND_SMALL_BOOK_UNWIND_CARRY_COVER_RATIO = 0.45
DEFEND_SAME_SYMBOL_CLEANUP_MIN_POSITIONS = 4
DEFEND_SAME_SYMBOL_CLEANUP_MAX_POSITIONS = 4
DEFEND_SAME_SYMBOL_CLEANUP_MIN_FREE_MARGIN_RATIO = 0.90
DEFEND_SAME_SYMBOL_CLEANUP_MIN_BLOCKED_DEFEND_LOADED = 10
DEFEND_SAME_SYMBOL_CLEANUP_TRIGGER_CYCLES = 12
DEFEND_SAME_SYMBOL_CLEANUP_COOLDOWN_SECONDS = 180
DEFEND_SAME_SYMBOL_CLEANUP_MAX_TOTAL_LOSS = 2.00
DEFEND_SAME_SYMBOL_CLEANUP_MAX_SINGLE_LOSS = 0.75
DEFEND_THREE_BOOK_SAME_SYMBOL_CLEANUP_MIN_POSITIONS = 3
DEFEND_THREE_BOOK_SAME_SYMBOL_CLEANUP_MAX_POSITIONS = 3
DEFEND_THREE_BOOK_SAME_SYMBOL_CLEANUP_MIN_FREE_MARGIN_RATIO = 0.90
DEFEND_THREE_BOOK_SAME_SYMBOL_CLEANUP_MIN_BLOCKED_DEFEND_LOADED = 8
DEFEND_THREE_BOOK_SAME_SYMBOL_CLEANUP_TRIGGER_CYCLES = 10
DEFEND_THREE_BOOK_SAME_SYMBOL_CLEANUP_COOLDOWN_SECONDS = 180
DEFEND_THREE_BOOK_SAME_SYMBOL_CLEANUP_MAX_TOTAL_LOSS = 1.50
DEFEND_THREE_BOOK_SAME_SYMBOL_CLEANUP_MAX_SINGLE_LOSS = 0.90
DEFEND_TWO_BOOK_SAME_SYMBOL_CLEANUP_MIN_POSITIONS = 2
DEFEND_TWO_BOOK_SAME_SYMBOL_CLEANUP_MAX_POSITIONS = 2
DEFEND_TWO_BOOK_SAME_SYMBOL_CLEANUP_MIN_FREE_MARGIN_RATIO = 0.95
DEFEND_TWO_BOOK_SAME_SYMBOL_CLEANUP_MIN_IDLE_CYCLES = 10
DEFEND_TWO_BOOK_SAME_SYMBOL_CLEANUP_TRIGGER_CYCLES = 8
DEFEND_TWO_BOOK_SAME_SYMBOL_CLEANUP_COOLDOWN_SECONDS = 180
DEFEND_TWO_BOOK_SAME_SYMBOL_CLEANUP_MAX_TOTAL_LOSS = 0.50
DEFEND_TWO_BOOK_SAME_SYMBOL_CLEANUP_MAX_SINGLE_LOSS = 0.25
# Live 2026-04-07 exposed a different 2-book dead-end: mixed-symbol DEFEND
# pairs like `USDHKD + EURDKK` can stay honestly frozen with `open=0`, healthy
# margin, and no winner leg for the existing two-book harvest helper to track.
# Keep this helper-owned lane exact and narrow; do not broaden the generic
# two-book win-bag path to cover all-red pairs.
DEFEND_TWO_BOOK_MIXED_CLEANUP_MIN_POSITIONS = 2
DEFEND_TWO_BOOK_MIXED_CLEANUP_MAX_POSITIONS = 2
DEFEND_TWO_BOOK_MIXED_CLEANUP_MIN_FREE_MARGIN_RATIO = 0.32
DEFEND_TWO_BOOK_MIXED_CLEANUP_MIN_BLOCKED_DEFEND_LOADED = 8
DEFEND_TWO_BOOK_MIXED_CLEANUP_MIN_IDLE_CYCLES = 24
DEFEND_TWO_BOOK_MIXED_CLEANUP_TRIGGER_CYCLES = 8
DEFEND_TWO_BOOK_MIXED_CLEANUP_COOLDOWN_SECONDS = 180
DEFEND_TWO_BOOK_MIXED_CLEANUP_MAX_TOTAL_LOSS = 20.00
DEFEND_TWO_BOOK_MIXED_CLEANUP_MAX_SINGLE_LOSS = 10.00
# Live 2026-04-07 showed a different endgame stall: after the 4->3->2 cleanup
# chain completes, a lone exotic non-REVERSION loser can sit honestly frozen in
# `one-pos-red` with `open=0`, healthy free margin, and repeated blocked adds.
# Keep this mercy lane narrow and helper-owned so it only retires stranded
# leftovers that are already wasting benchmark time.
DEFEND_ONE_POS_EXOTIC_MERCY_MIN_FREE_MARGIN_RATIO = 0.30
DEFEND_ONE_POS_EXOTIC_MERCY_MIN_IDLE_CYCLES = 0
DEFEND_ONE_POS_EXOTIC_MERCY_MIN_HOLD_SECONDS = 900
DEFEND_ONE_POS_EXOTIC_MERCY_TRIGGER_CYCLES = 8
DEFEND_ONE_POS_EXOTIC_MERCY_COOLDOWN_SECONDS = 240
DEFEND_ONE_POS_EXOTIC_MERCY_MAX_LOSS = 35.00
DEFEND_ONE_POS_INDEX_MERCY_MIN_FREE_MARGIN_RATIO = 0.35
DEFEND_ONE_POS_INDEX_MERCY_MIN_IDLE_CYCLES = 0
DEFEND_ONE_POS_INDEX_MERCY_MIN_HOLD_SECONDS = 300
DEFEND_ONE_POS_INDEX_MERCY_TRIGGER_CYCLES = 6
DEFEND_ONE_POS_INDEX_MERCY_COOLDOWN_SECONDS = 240
DEFEND_ONE_POS_INDEX_MERCY_MAX_LOSS = 40.00
# Live 2026-04-09 showed that "not red anymore" is still too loose: a lone
# first-leg survivor can hover around flat, flip quiet-book REARM back on, and
# immediately rebuild a larger RAW/SHOTGUN cluster. Require a small real green
# buffer before one-position non-flat expansion is allowed again.
ONE_POSITION_REARM_MIN_GREEN_PNL_USD = 0.10
# Live 2026-04-07 exposed the next exact endgame after the 5-book financed lane
# compresses: mixed-symbol 4-book DEFEND baskets can become honestly frozen with
# `open=0`, strong margin, and no same-symbol cleanup path. Keep this helper
# exact and helper-owned so other agents cannot "fix" it by broadening generic
# 4-book logic or trusting permissive top-of-file competition overrides.
DEFEND_FOUR_BOOK_MIXED_CLEANUP_MIN_POSITIONS = 4
DEFEND_FOUR_BOOK_MIXED_CLEANUP_MAX_POSITIONS = 4
DEFEND_FOUR_BOOK_MIXED_CLEANUP_MIN_FREE_MARGIN_RATIO = 0.20
DEFEND_FOUR_BOOK_MIXED_CLEANUP_MIN_IDLE_CYCLES = 12
DEFEND_FOUR_BOOK_MIXED_CLEANUP_MIN_BLOCKED_DEFEND_LOADED = 5
DEFEND_FOUR_BOOK_MIXED_CLEANUP_TRIGGER_CYCLES = 8
DEFEND_FOUR_BOOK_MIXED_CLEANUP_COOLDOWN_SECONDS = 180
DEFEND_FOUR_BOOK_MIXED_CLEANUP_MAX_TOTAL_LOSS = 40.00
DEFEND_FOUR_BOOK_MIXED_CLEANUP_MAX_SINGLE_LOSS = 2.50
DEFEND_FOUR_BOOK_MIXED_CLEANUP_MAX_GREEN_LEGS = 2
DEFEND_FOUR_BOOK_MIXED_CLEANUP_MAX_POSITIVE_CARRY = 80.00
DEFEND_FOUR_BOOK_MIXED_CLEANUP_MIN_REMAINING_NET = 6.00
DEFEND_THREE_BOOK_WIN_BAG_MIN_POSITIONS = 3
DEFEND_THREE_BOOK_WIN_BAG_MAX_POSITIONS = 3
DEFEND_THREE_BOOK_WIN_BAG_MIN_FREE_MARGIN_RATIO = 0.65
DEFEND_THREE_BOOK_WIN_BAG_MIN_WIN_PNL = 2.50
DEFEND_THREE_BOOK_WIN_BAG_TRIGGER_CYCLES = 8
DEFEND_THREE_BOOK_WIN_BAG_COOLDOWN_SECONDS = 150
DEFEND_THREE_BOOK_WIN_BAG_SYMBOL_FREEZE_SECONDS = 420
DEFEND_THREE_BOOK_NET_GREEN_MIN_TOTAL_PNL = 10.00
DEFEND_THREE_BOOK_NET_GREEN_MIN_WINNERS = 2
DEFEND_THREE_BOOK_NET_GREEN_MAX_LOSERS = 1
DEFEND_THREE_BOOK_NET_GREEN_MAX_LOSER_ABS = 4.00
DEFEND_THREE_BOOK_NET_GREEN_MIN_PRIMARY_WIN_PNL = 5.00
DEFEND_TWO_BOOK_WIN_BAG_MIN_POSITIONS = 2
DEFEND_TWO_BOOK_WIN_BAG_MAX_POSITIONS = 2
# Live 2026-04-07 mixed 2-book DEFEND holds (`FRA40` winner + `USDHKD` loser)
# repeatedly sat around 0.48 free margin while otherwise matching the intended
# harvest shape. Keeping the old 0.80 floor left the lane permanently
# `shape_blocked`, so use a live-shaped floor here instead of spawning a new
# bespoke cleanup path for the same endgame.
DEFEND_TWO_BOOK_WIN_BAG_MIN_FREE_MARGIN_RATIO = 0.45
DEFEND_TWO_BOOK_WIN_BAG_MIN_WIN_PNL = 3.00
DEFEND_TWO_BOOK_GREEN_MIN_NET_PNL = 6.00
DEFEND_TWO_BOOK_WIN_BAG_TRIGGER_CYCLES = 10
DEFEND_TWO_BOOK_WIN_BAG_COOLDOWN_SECONDS = 180
DEFEND_TWO_BOOK_WIN_BAG_SYMBOL_FREEZE_SECONDS = 480
DEFEND_TWO_BOOK_PENDING_ENTRY_FREEZE_SECONDS = 90
REARM_FINANCED_UNWIND_MIN_POSITIONS = 5
REARM_FINANCED_UNWIND_MAX_POSITIONS = 6
REARM_FINANCED_UNWIND_MIN_FREE_MARGIN_RATIO = 0.72
REARM_FINANCED_UNWIND_MIN_NET_PNL = 2.00
REARM_FINANCED_UNWIND_MIN_POSITIVE_CARRY = 5.00
REARM_FINANCED_UNWIND_MAX_LOSERS = 3
REARM_FINANCED_UNWIND_MAX_LOSS = 18.00
REARM_FINANCED_UNWIND_MIN_REMAINING_NET = 1.00
REARM_FINANCED_UNWIND_CARRY_COVER_RATIO = 0.50
REARM_FINANCED_UNWIND_MIN_IDLE_CYCLES = 12
REARM_FINANCED_UNWIND_COOLDOWN_SECONDS = 60
DEFEND_CROWD_WIN_BAG_MIN_POSITIONS = 4        # Lower threshold
DEFEND_CROWD_WIN_BAG_MAX_POSITIONS = 7
DEFEND_CROWD_WIN_BAG_MIN_FREE_MARGIN_RATIO = 0.30
DEFEND_CROWD_WIN_BAG_MIN_NET_PNL = 2.00        # Lower
DEFEND_CROWD_WIN_BAG_MIN_WIN_PNL = 0.75       # Capture small winners
DEFEND_CROWD_WIN_BAG_MIN_BLOCKED_CROWD = 10  # Fewer cycles needed
DEFEND_CROWD_WIN_BAG_MAX_LOSERS = 1
DEFEND_CROWD_WIN_BAG_TRIGGER_CYCLES = 8
DEFEND_CROWD_WIN_BAG_COOLDOWN_SECONDS = 120

DEFEND_MIXED_WIN_BAG_MIN_POSITIONS = 4         # Lower
DEFEND_MIXED_WIN_BAG_MAX_POSITIONS = 6
DEFEND_MIXED_WIN_BAG_MIN_FREE_MARGIN_RATIO = 0.25
DEFEND_MIXED_WIN_BAG_MIN_NET_PNL = 2.50        # Lower
DEFEND_MIXED_WIN_BAG_MIN_WIN_PNL = 0.75        # Smaller winners
DEFEND_MIXED_WIN_BAG_MIN_NON_REVERSION = 1
DEFEND_MIXED_WIN_BAG_MAX_LOSERS = 2
DEFEND_MIXED_WIN_BAG_MIN_IDLE_CYCLES = 2       # Faster
DEFEND_MIXED_WIN_BAG_TRIGGER_CYCLES = 6
DEFEND_MIXED_WIN_BAG_COOLDOWN_SECONDS = 90
DEFEND_MIXED_GREEN_HARVEST_MIN_BLOCKED_DEFEND_LOADED = 20
DEFEND_MIXED_GREEN_HARVEST_MIN_NET_PNL = 20.00
DEFEND_MIXED_GREEN_HARVEST_MIN_WIN_PNL = 5.00
SYNC_CLOSE_REENTRY_SYMBOL_FREEZE_SECONDS = 120   # Reduced from 480 - 8min was too long for small losses
SYNC_CLOSE_REENTRY_INDEX_FAMILY_FREEZE_SECONDS = 300  # Reduced from 720
INDEX_FAMILY_SYMBOL_KEYS = (
    'NAS100',
    'SPX500',
    'US30',
    'GER30',
    'JPN225',
    'UK100',
    'AUS200',
    'FRA40',
    'ESP35',
    'NETH25',
    'HK50',
)
PROFIT_CAPTURE_MIN_POSITIONS = 3        # Lower for rapid compounding
PROFIT_CAPTURE_MAX_POSITIONS = 8
PROFIT_CAPTURE_MIN_FREE_MARGIN_RATIO = 0.30
PROFIT_CAPTURE_MIN_NET_PNL = 4.00        # Conservative - cover costs
PROFIT_CAPTURE_ALL_GREEN_MIN_NET_PNL = 4.00
PROFIT_CAPTURE_MIN_WIN_PNL = 2.00        # Require meaningful winner
PROFIT_CAPTURE_MAX_LOSERS = 2
PROFIT_CAPTURE_MIN_IDLE_CYCLES = 2       # Faster triggering
PROFIT_CAPTURE_COOLDOWN_SECONDS = 120    # Let market settle
PROFIT_CAPTURE_ENTRY_FREEZE_SECONDS = 180 # Prevent whipsaw
DEFEND_NONFLAT_BLOCK_NON_REVERSION = True
REARM_NONFLAT_BLOCK_NON_REVERSION = True
DEFEND_REVERSION_REBUILD_MAX_POSITIONS = 8  # Raised from 4 — competition mode needs REVERSION entries alongside existing book
DEFEND_REVERSION_REBUILD_MIN_FREE_MARGIN_RATIO = 0.58
DEFEND_REVERSION_REBUILD_MAX_MANAGED_DRAWDOWN_PCT = 0.40  # 40% drawdown threshold (raised from 6% to allow compounding)
DEFEND_REVERSION_REBUILD_MAX_TOP_SYMBOL_DRAWDOWN_PCT = 0.25  # Raised from 5% to allow true risk
DEFEND_REVERSION_REBUILD_MAX_LOSING_DIRECT_POSITIONS = 50  # COMPETITION: Raised to 50 to allow REVERSION entries even with losing book

# === COOLDOWN STATE ===
equity_peak = 0                 # Track peak equity for lot scaling
cooldown_until = 0              # Timestamp for loss-streak cooldown (0 = no cooldown)
cycles_without_trade = 0        # Alleyway cycle counter
recently_trimmed_symbols = {}   # symbol -> timestamp (anti-death-spiral)
TRIM_COOLDOWN_SECONDS = 120     # Don't re-enter a symbol for 2 min after stress trim
recent_risk_events = []         # timestamps of reversal exits / stress trims

# === GLOBALS ===
active_positions = {}
consecutive_wins = 0
consecutive_losses = 0
total_pnl = 0
trades = 0
mt5_connected = False
_brain = None
_learner = None

_bars_cache = {}  # symbol -> {timeframe: (timestamp, bars)}
CACHE_TTL = 30    # seconds — raised from 15s to cover full multi-symbol scan cycle without mid-cycle expiry
_tick_cache = {}   # symbol -> (timestamp, tick) — per-cycle tick cache to avoid redundant symbol_info_tick() calls
_tick_cache_cycle = 0  # cycle number when tick cache was last populated
ADOPT_EXISTING_POSITIONS = True
RUNTIME_STATE_FILE = os.path.join(os.path.dirname(__file__), "runtime_state.json")
WORKER_STATE_FILE = os.path.join(os.path.dirname(__file__), "canonical_worker_state.json")
WORKER_REFUSAL_STATE_FILE = os.path.join(os.path.dirname(__file__), "canonical_worker_refusal_state.json")
TRADE_BEHAVIOR_LOG_FILE = os.path.join(os.path.dirname(__file__), "trade_behavior_log.jsonl")
PRICE_CANDIDATE_LOG_FILE = os.path.join(os.path.dirname(__file__), "price_candidate_log.jsonl")
BLOCKED_QUALITY_CANDIDATE_LOG_FILE = os.path.join(
    os.path.dirname(__file__), "blocked_quality_candidates.jsonl"
)
STRATEGY_LAB_LOG_FILE = os.path.join(
    os.path.dirname(__file__), "strategy_lab_events.jsonl"
)
LATTICE_LIVE_IGNORE_COMMENT_PREFIX = "PLIVE-LATTICE"

def get_brain():
    global _brain
    if _brain is None:
        _brain = TradingBrain()
    return _brain

def get_learner():
    global _learner
    if _learner is None:
        _learner = SymbolLearner()
    return _learner

def is_bot_position(pos):
    comment = getattr(pos, "comment", "") or ""
    return getattr(pos, "magic", None) == BOT_MAGIC or comment.startswith(f"{BOT_COMMENT_PREFIX}-")


def should_ignore_external_position(pos):
    comment = str(getattr(pos, "comment", "") or "")
    return comment.startswith(LATTICE_LIVE_IGNORE_COMMENT_PREFIX)


def build_position_state(pos, adopted=False):
    atr = 0.0
    try:
        bars_m5 = get_bars(pos.symbol, mt5.TIMEFRAME_M5, 30)
        if len(bars_m5) > 14:
            atr = calc_atr(bars_m5, 14)
    except Exception:
        atr = 0.0

    mode = get_position_mode(pos)

    entry_time = getattr(pos, "time", 0) or 0
    if entry_time:
        entry_time = float(entry_time)
    else:
        entry_time = time.time()

    return {
        'ticket': int(getattr(pos, 'ticket', 0) or 0),
        'symbol': pos.symbol,
        'direction': 'BUY' if pos.type == 0 else 'SELL',
        'entry_price': pos.price_open,
        'volume': pos.volume,
        'mode': mode,
        'entry_time': entry_time,
        'peak_pnl': max(0.0, pos.profit),
        'peak_volume': pos.volume,  # Track volume at peak for proper scaling
        'last_pnl': pos.profit,
        'atr': atr,
        'confidence': 0.0,
        'pyramid_count': 0,
        'last_pyramid_pnl': 0,
        'adopted': adopted,
        'entry_context': 'reloaded_position',
        'entry_signal_type': 'unlabeled',
        'entry_regime': 'unknown',
        'time_to_first_green_seconds': 0.0 if pos.profit > 0 else None,
        'time_to_0_25_atr_seconds': None,
        'time_to_0_5_atr_seconds': None,
        'time_to_1_0_atr_seconds': None,
        'time_to_minus_0_35_atr_seconds': None,
        'max_favorable_excursion_pnl': max(0.0, float(pos.profit or 0.0)),
        'max_adverse_excursion_pnl': max(0.0, -float(pos.profit or 0.0)),
        # Reloaded REVERSION trades still need the fast mean-reversion exit path.
        'mean_reversion': mode == 'REVERSION',
    }


def append_jsonl_record(path, payload):
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    except Exception as exc:
        log(f"  TRADE_BEHAVIOR_LOG_FAIL reason=append error={exc}")


def build_lane_key(symbol, signal_type, mode, regime):
    return (
        str(symbol or "").upper(),
        str(signal_type or ""),
        str(mode or "").upper(),
        str(regime or "").upper(),
    )


def is_strategy_lab_symbol(symbol):
    return str(symbol or "").upper() in STRATEGY_LAB_SYMBOLS


def is_strategy_lab_lane(symbol, signal_type, mode, regime):
    return build_lane_key(symbol, signal_type, mode, regime) in STRATEGY_LAB_TARGET_LANES


def strategy_lab_pip_size(symbol):
    symbol_text = str(symbol or "").upper()
    if symbol_text.endswith("JPY") or "JPY" in symbol_text:
        return 0.01
    return 0.0001


def _strategy_lab_mean(values):
    if not values:
        return 0.0
    return sum(values) / len(values)


def _strategy_lab_iter_closed_lane_records(path=TRADE_BEHAVIOR_LOG_FILE):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if build_lane_key(
                    record.get("symbol"),
                    record.get("entry_signal_type"),
                    record.get("entry_mode"),
                    record.get("regime_at_entry"),
                ) not in STRATEGY_LAB_TARGET_LANES:
                    continue
                lane_id = str(
                    record.get("strategy_lab_lane_id")
                    or record.get("lane_id")
                    or ""
                ).strip()
                if lane_id not in STRATEGY_LAB_OWNER_POOL:
                    continue
                yield lane_id, float(record.get("realized_pnl", 0.0) or 0.0)
    except FileNotFoundError:
        return
    except Exception as exc:
        log(f"  [STRATEGY_LAB_OWNER_LOG_FAIL] {exc}")


def choose_strategy_lab_owner_lane(completed_lane_id=None):
    lane_history = {lane_id: [] for lane_id in STRATEGY_LAB_OWNER_POOL}
    for lane_id, realized in _strategy_lab_iter_closed_lane_records():
        lane_history.setdefault(lane_id, []).append(realized)

    lane_counts = {lane_id: len(values) for lane_id, values in lane_history.items()}
    lane_id = str(completed_lane_id or "").strip()
    bootstrap_pending = [
        candidate for candidate in STRATEGY_LAB_OWNER_POOL
        if lane_counts.get(candidate, 0) < STRATEGY_LAB_OWNER_BOOTSTRAP_MIN_TRADES
    ]
    if bootstrap_pending:
        if lane_id in STRATEGY_LAB_OWNER_POOL:
            current_index = STRATEGY_LAB_OWNER_POOL.index(lane_id)
            for offset in range(1, len(STRATEGY_LAB_OWNER_POOL) + 1):
                candidate = STRATEGY_LAB_OWNER_POOL[
                    (current_index + offset) % len(STRATEGY_LAB_OWNER_POOL)
                ]
                if candidate in bootstrap_pending:
                    reason = (
                        f"bootstrap counts="
                        + ",".join(
                            f"{key}:{lane_counts.get(key, 0)}"
                            for key in STRATEGY_LAB_OWNER_POOL
                        )
                    )
                    return candidate, reason
        candidate = bootstrap_pending[0]
        reason = (
            f"bootstrap counts="
            + ",".join(f"{key}:{lane_counts.get(key, 0)}" for key in STRATEGY_LAB_OWNER_POOL)
        )
        return candidate, reason

    ranked = []
    for candidate in STRATEGY_LAB_OWNER_POOL:
        recent = lane_history.get(candidate, [])[-STRATEGY_LAB_OWNER_WINDOW_TRADES:]
        if len(recent) < STRATEGY_LAB_OWNER_MIN_SAMPLES:
            ranked.append((float("-inf"), len(recent), candidate, recent))
            continue
        ranked.append((_strategy_lab_mean(recent), len(recent), candidate, recent))
    ranked.sort(reverse=True, key=lambda item: (item[0], item[1], item[2]))
    if ranked and ranked[0][0] != float("-inf"):
        best_score, _, best_lane_id, _ = ranked[0]
        reason = (
            f"owner_recent{STRATEGY_LAB_OWNER_WINDOW_TRADES} "
            + ", ".join(
                f"{candidate}:{score:+.3f}"
                for score, _, candidate, _ in ranked
            )
        )
        return best_lane_id, reason

    return DEFAULT_STRATEGY_LAB_LANE_ID, "fallback_default"


def refresh_strategy_lab_owner_lane_on_startup(current_lane_id=None):
    lane_id = str(current_lane_id or "").strip()
    if lane_id not in STRATEGY_LAB_OWNER_POOL:
        lane_id = DEFAULT_STRATEGY_LAB_LANE_ID
    next_lane_id, selection_reason = choose_strategy_lab_owner_lane(lane_id)
    alleyway_state["strategy_lab_active_lane_id"] = next_lane_id
    alleyway_state["strategy_lab_owner_last_reason"] = selection_reason
    return next_lane_id, selection_reason


def is_experiment_allowed_lane(symbol, signal_type, mode, regime):
    lane = build_lane_key(symbol, signal_type, mode, regime)
    if not EXPERIMENT_ONLY_MODE:
        return True
    return lane in EXPERIMENT_ONLY_ALLOWED_LANES


def get_current_strategy_lab_lane_id():
    lane_id = str(
        alleyway_state.get("strategy_lab_active_lane_id", DEFAULT_STRATEGY_LAB_LANE_ID)
        or DEFAULT_STRATEGY_LAB_LANE_ID
    )
    if lane_id not in STRATEGY_LAB_LANES or lane_id not in STRATEGY_LAB_OWNER_POOL:
        lane_id = DEFAULT_STRATEGY_LAB_LANE_ID
    alleyway_state["strategy_lab_active_lane_id"] = lane_id
    return lane_id


def get_current_strategy_lab_lane_config():
    return STRATEGY_LAB_LANES[get_current_strategy_lab_lane_id()]


def get_resolved_strategy_lab_lane_id(symbol, signal_type, mode, regime):
    lane_id = STRATEGY_LAB_LANE_OVERRIDES.get(
        build_lane_key(symbol, signal_type, mode, regime),
    )
    if lane_id in STRATEGY_LAB_LANES:
        return lane_id
    return get_current_strategy_lab_lane_id()


def advance_strategy_lab_lane(completed_lane_id):
    lane_id = str(completed_lane_id or "").strip()
    if lane_id not in STRATEGY_LAB_OWNER_POOL:
        return
    next_lane_id, selection_reason = choose_strategy_lab_owner_lane(lane_id)
    alleyway_state["strategy_lab_active_lane_id"] = next_lane_id
    alleyway_state["strategy_lab_last_completed_lane_id"] = lane_id
    alleyway_state["strategy_lab_lane_rotated_at"] = datetime.now(timezone.utc).isoformat()
    alleyway_state["strategy_lab_owner_last_reason"] = selection_reason
    log(f"  [STRATEGY_LAB_ROTATE] {lane_id} -> {next_lane_id} ({selection_reason})")


def get_active_strategy_lab_lane_config(symbol, signal_type, mode, regime):
    if not is_strategy_lab_lane(symbol, signal_type, mode, regime):
        return None
    return STRATEGY_LAB_LANES[get_resolved_strategy_lab_lane_id(symbol, signal_type, mode, regime)]


def get_strategy_lab_symbol_variants():
    variants = {}
    seen_symbols = set()
    for symbol, signal_type, mode, regime in sorted(STRATEGY_LAB_TARGET_LANES):
        symbol_key = str(symbol or "").upper()
        if symbol_key in seen_symbols:
            continue
        lane_config = get_active_strategy_lab_lane_config(symbol, signal_type, mode, regime)
        if not lane_config:
            continue
        variants[symbol_key] = str(lane_config.get("variant_label", "") or "")
        seen_symbols.add(symbol_key)
    return variants


def get_strategy_lab_trail_floor(pdata, mode, scaled_peak, hold_sec):
    lane_config = get_active_strategy_lab_lane_config(
        pdata.get("symbol"),
        pdata.get("entry_signal_type"),
        mode,
        pdata.get("entry_regime"),
    )
    if not lane_config:
        return None
    scaled_peak = float(scaled_peak or 0.0)
    if scaled_peak <= 0:
        return None
    retain_ratio = None
    tiered_peak_capture = lane_config.get("tiered_peak_capture") or ()
    if tiered_peak_capture:
        for peak_cap, candidate_ratio in tiered_peak_capture:
            if scaled_peak <= float(peak_cap):
                retain_ratio = float(candidate_ratio)
                break
    time_decay_capture = lane_config.get("time_decay_capture") or ()
    if retain_ratio is None and time_decay_capture:
        time_since_first_green = None
        if pdata.get("time_to_first_green_seconds") is not None:
            time_since_first_green = max(
                0.0,
                float(hold_sec or 0.0) - float(pdata.get("time_to_first_green_seconds") or 0.0),
            )
        if time_since_first_green is not None:
            for elapsed_cap, candidate_ratio in time_decay_capture:
                if time_since_first_green <= float(elapsed_cap):
                    retain_ratio = float(candidate_ratio)
                    break
    large_peak_threshold = lane_config.get("large_peak_threshold_usd")
    large_peak_ratio = lane_config.get("large_peak_retain_ratio")
    if (
        retain_ratio is None
        and large_peak_threshold is not None
        and large_peak_ratio is not None
        and scaled_peak >= float(large_peak_threshold)
    ):
        retain_ratio = float(large_peak_ratio)
    if retain_ratio is None and lane_config.get("exit_retain_ratio") is not None:
        retain_ratio = float(lane_config.get("exit_retain_ratio"))
    if retain_ratio is None:
        return None
    min_profit_floor_usd = float(lane_config.get("exit_min_profit_floor_usd", 0.0) or 0.0)
    min_floor = (
        min_profit_floor_usd
        if scaled_peak >= min_profit_floor_usd
        else 0.0
    )
    return max(min_floor, scaled_peak * float(retain_ratio))


def get_strategy_lab_stall_exit_reason(pdata, mode, hold_sec):
    lane_config = get_active_strategy_lab_lane_config(
        pdata.get("symbol"),
        pdata.get("entry_signal_type"),
        mode,
        pdata.get("entry_regime"),
    ) or {}
    if not lane_config:
        return None

    max_hold_seconds = lane_config.get("max_hold_seconds")
    if max_hold_seconds is not None and hold_sec >= float(max_hold_seconds):
        return f"STALL_TIMEOUT ({int(hold_sec)}s, lane={lane_config.get('lane_id', '')})"

    if not lane_config.get("stall_exit_on_nonprogress_close"):
        return None

    first_check = float(lane_config.get("stall_exit_check_after_seconds", 60.0) or 60.0)
    if hold_sec < first_check:
        return None

    bars = get_bars(pdata.get("symbol"), mt5.TIMEFRAME_M1, 4)
    if len(bars) < 3:
        return None

    prev_close = float(bars[-3]["c"])
    last_close = float(bars[-2]["c"])
    direction = str(pdata.get("direction", "") or "").upper()
    progressed = last_close > prev_close if direction == "BUY" else last_close < prev_close
    if progressed:
        return None

    return (
        f"STALL_EXIT ({int(hold_sec)}s, lane={lane_config.get('lane_id', '')}, "
        f"prev_close={prev_close:.3f}, last_close={last_close:.3f})"
    )


def get_strategy_lab_variant_label(symbol, signal_type, mode, regime):
    lane_config = get_active_strategy_lab_lane_config(symbol, signal_type, mode, regime)
    if not lane_config:
        return ""
    return str(lane_config.get("variant_label", "") or "")


def get_strategy_lab_lane_meta(symbol, signal_type, mode, regime):
    lane_config = get_active_strategy_lab_lane_config(symbol, signal_type, mode, regime)
    if not lane_config:
        return {}
    return {
        "lane_id": str(lane_config.get("lane_id", "") or ""),
        "role": str(lane_config.get("role", "") or ""),
        "hypothesis": str(lane_config.get("hypothesis", "") or ""),
        "variant_label": str(lane_config.get("variant_label", "") or ""),
    }


def get_strategy_lab_entry_gate(symbol, signal_type, mode, regime, signal):
    lane_config = get_active_strategy_lab_lane_config(symbol, signal_type, mode, regime)
    if not lane_config:
        return True, "not_lab_lane"

    entry_style = str(lane_config.get("entry_style", "") or "").strip().lower()
    if not entry_style:
        return True, "no_entry_gate"

    direction = str(signal or "").upper()
    if direction not in {"BUY", "SELL"}:
        return False, "no_direction"

    lookback_bars = max(4, int(lane_config.get("lookback_bars", 8) or 8))
    bars = get_bars(symbol, mt5.TIMEFRAME_M1, max(lookback_bars + 20, 40))
    if len(bars) < lookback_bars + 3:
        return False, "insufficient_bars"

    signal_bar = bars[-2]
    prior = bars[-2 - lookback_bars : -2]
    pip_size = strategy_lab_pip_size(symbol)
    prior_high = max(float(bar["h"]) for bar in prior)
    prior_low = min(float(bar["l"]) for bar in prior)

    if entry_style == "confirmed_displacement_recipe":
        atr = calc_atr(prior + [signal_bar], period=14)
        atr_pips = float(atr or 0.0) / pip_size if pip_size > 0 else 0.0
        signal_body_pips = abs(float(signal_bar["c"]) - float(signal_bar["o"])) / pip_size
        required_expansion = float(lane_config.get("min_body_atr_expansion", 0.0) or 0.0)
        signal_break_margin_pips = float(lane_config.get("signal_break_margin_pips", 0.0) or 0.0)
        breakout_margin_ok = (
            float(signal_bar["c"]) >= prior_high + signal_break_margin_pips * pip_size
            if direction == "BUY"
            else float(signal_bar["c"]) <= prior_low - signal_break_margin_pips * pip_size
        )
        if not breakout_margin_ok:
            return False, f"signal_margin<{signal_break_margin_pips:.2f}p"
        if atr_pips <= 0:
            return False, "atr_unavailable"
        if signal_body_pips < required_expansion * atr_pips:
            return False, f"body_atr<{required_expansion:.2f}x"

        confirm_window_bars = max(1, int(lane_config.get("confirm_window_bars", 1) or 1))
        now_ts = time.time()
        confirm_windows = alleyway_state.setdefault("strategy_lab_confirm_windows", {})
        lane_id = get_resolved_strategy_lab_lane_id(symbol, signal_type, mode, regime)
        confirm_key = (
            f"{symbol}|{signal_type}|{mode}|{regime}|{direction}|{lane_id}"
        )
        state = confirm_windows.get(confirm_key)
        if (
            not isinstance(state, dict)
            or int(state.get("signal_bar_time", 0) or 0) != int(signal_bar["t"])
        ):
            state = {
                "signal_bar_time": int(signal_bar["t"]),
                "structure_level": float(prior_high if direction == "BUY" else prior_low),
                "started_at": now_ts,
                "expires_at": now_ts + confirm_window_bars * 60.0,
            }
            confirm_windows[confirm_key] = state
            return False, f"confirm_window_start<{signal_break_margin_pips:.2f}p"

        if now_ts > float(state.get("expires_at", 0.0) or 0.0):
            confirm_windows.pop(confirm_key, None)
            return False, f"confirm_window_expired<{signal_break_margin_pips:.2f}p"

        structure_level = float(state.get("structure_level", prior_high if direction == "BUY" else prior_low))
        for bar in bars:
            if int(bar["t"]) <= int(state.get("signal_bar_time", 0) or 0):
                continue
            if direction == "BUY" and float(bar["c"]) >= structure_level:
                confirm_windows.pop(confirm_key, None)
                return True, entry_style
            if direction == "SELL" and float(bar["c"]) <= structure_level:
                confirm_windows.pop(confirm_key, None)
                return True, entry_style
        return False, f"confirm_hold_wait<{signal_break_margin_pips:.2f}p"

    signal_range_pips = max((float(signal_bar["h"]) - float(signal_bar["l"])) / pip_size, 0.01)
    signal_body_pips = abs(float(signal_bar["c"]) - float(signal_bar["o"])) / pip_size
    signal_body_ratio = signal_body_pips / signal_range_pips
    avg_volume = _strategy_lab_mean([float(bar["v"]) for bar in prior])
    avg_range_pips = _strategy_lab_mean(
        [max((float(bar["h"]) - float(bar["l"])) / pip_size, 0.01) for bar in prior]
    )
    breakout_ok = (
        float(signal_bar["c"]) > prior_high if direction == "BUY"
        else float(signal_bar["c"]) < prior_low
    )
    candle_ok = (
        float(signal_bar["c"]) > float(signal_bar["o"]) if direction == "BUY"
        else float(signal_bar["c"]) < float(signal_bar["o"])
    )
    body_ok = signal_body_pips >= float(lane_config.get("min_body_pips", 0.0) or 0.0)
    ratio_ok = signal_body_ratio >= float(lane_config.get("min_body_ratio", 0.0) or 0.0)
    burst_ratio = float(lane_config.get("volume_burst_ratio", 0.0) or 0.0)
    burst_ok = True if burst_ratio <= 0 or avg_volume <= 0 else float(signal_bar["v"]) >= avg_volume * burst_ratio
    expansion_ratio = float(lane_config.get("min_range_expansion", 0.0) or 0.0)
    expansion_ok = True if expansion_ratio <= 0 or avg_range_pips <= 0 else signal_range_pips >= avg_range_pips * expansion_ratio

    if not breakout_ok:
        return False, "breakout_missing"
    if not candle_ok:
        return False, "candle_not_directional"
    if not body_ok:
        return False, f"body<{float(lane_config.get('min_body_pips', 0.0) or 0.0):.2f}"
    if not ratio_ok:
        return False, f"body_ratio<{float(lane_config.get('min_body_ratio', 0.0) or 0.0):.2f}"
    if not burst_ok:
        return False, f"volume_burst<{burst_ratio:.2f}"
    if not expansion_ok:
        return False, f"range_expand<{expansion_ratio:.2f}"

    if entry_style == "confirmed_displacement":
        confirm_pips = float(lane_config.get("confirm_pips", 0.0) or 0.0)
        confirm_window_bars = max(1, int(lane_config.get("confirm_window_bars", 2) or 2))
        now_ts = time.time()
        confirm_windows = alleyway_state.setdefault("strategy_lab_confirm_windows", {})
        lane_id = get_resolved_strategy_lab_lane_id(symbol, signal_type, mode, regime)
        confirm_key = (
            f"{symbol}|{signal_type}|{mode}|{regime}|{direction}|{lane_id}"
        )
        target_price = (
            float(signal_bar["c"]) + confirm_pips * pip_size
            if direction == "BUY"
            else float(signal_bar["c"]) - confirm_pips * pip_size
        )
        state = confirm_windows.get(confirm_key)
        if (
            not isinstance(state, dict)
            or int(state.get("signal_bar_time", 0) or 0) != int(signal_bar["t"])
        ):
            state = {
                "signal_bar_time": int(signal_bar["t"]),
                "target_price": float(target_price),
                "started_at": now_ts,
                "expires_at": now_ts + confirm_window_bars * 60.0,
            }
            confirm_windows[confirm_key] = state
            return False, f"confirm_window_start<{confirm_pips:.2f}p"

        if now_ts > float(state.get("expires_at", 0.0) or 0.0):
            confirm_windows.pop(confirm_key, None)
            return False, f"confirm_window_expired<{confirm_pips:.2f}p"

        window_target = float(state.get("target_price", target_price) or target_price)
        for bar in bars:
            if int(bar["t"]) <= int(state.get("signal_bar_time", 0) or 0):
                continue
            if direction == "BUY" and float(bar["h"]) >= window_target:
                confirm_windows.pop(confirm_key, None)
                return True, entry_style
            if direction == "SELL" and float(bar["l"]) <= window_target:
                confirm_windows.pop(confirm_key, None)
                return True, entry_style

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return False, "no_tick"
        live_price = float(tick.ask if direction == "BUY" else tick.bid)
        confirmed = live_price >= window_target if direction == "BUY" else live_price <= window_target
        if not confirmed:
            return False, f"confirm_wait<{confirm_pips:.2f}p"
        confirm_windows.pop(confirm_key, None)

    return True, entry_style


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


def get_post_cleanup_raw_shotgun_min_confidence(symbol):
    normalized_symbol = str(symbol or "").upper()
    return float(
        POST_CLEANUP_QUALITY_RAW_SHOTGUN_SYMBOL_MIN_CONFIDENCE.get(
            normalized_symbol,
            POST_CLEANUP_QUALITY_RAW_SHOTGUN_MIN_CONFIDENCE,
        )
    )


def should_bypass_brain_cooldown_for_symbol_override(
    symbol,
    regime,
    mode,
    confidence,
    block_reason,
    flat_book_rebuild,
    post_cleanup_quality_gate_active,
):
    if "Cooldown (" not in str(block_reason or ""):
        return False
    if not (flat_book_rebuild and post_cleanup_quality_gate_active):
        return False
    if str(regime or "").upper() != "RAW" or str(mode or "").upper() != "SHOTGUN":
        return False
    normalized_symbol = str(symbol or "").upper()
    if normalized_symbol not in POST_CLEANUP_QUALITY_RAW_SHOTGUN_SYMBOL_MIN_CONFIDENCE:
        return False
    return float(confidence or 0.0) >= get_post_cleanup_raw_shotgun_min_confidence(normalized_symbol)


def should_bypass_brain_cooldown_for_priority_lane(
    symbol,
    regime,
    mode,
    signal_type,
    confidence,
    block_reason,
    flat_book_rebuild,
    post_cleanup_quality_gate_active,
):
    if "Cooldown (" not in str(block_reason or ""):
        return False
    if not (flat_book_rebuild and post_cleanup_quality_gate_active):
        return False
    lane = build_lane_key(symbol, signal_type, mode, regime)
    if lane not in EXPERIMENTAL_PRIORITY_LANES:
        return False
    return float(confidence or 0.0) >= BRAIN_COOLDOWN_PRIORITY_LANE_MIN_CONFIDENCE


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


COMPETITION_LANE_NAMES = ("PRICE", "RAW", "GEMINI")
COMPETITION_LANE_SCORECARD_LIMIT = 60
COMPETITION_LANE_CLUSTER_WINDOW = 6
COMPETITION_LANE_CLUSTER_MAX_AGE_SECONDS = 20 * 60
COMPETITION_LANE_CLUSTER_MIN_EARLY_FAILS = 3
COMPETITION_LANE_CLUSTER_BRAKE_MIN_CONFIDENCE = 0.70
RAW_CANDLE_DIRECTION_MIN_CONFIDENCE = 0.60


def get_position_lane(pdata):
    lane = str((pdata or {}).get('entry_regime', '') or '').upper()
    if lane == 'UNKNOWN' or not lane:
        lane = str((pdata or {}).get('mode', '') or '').upper()
    return lane if lane else 'UNKNOWN'


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


def prioritize_experimental_opportunities(opportunities):
    """
    Let PRICE / RAW / GEMINI compete for entry slots in rounds instead of
    promoting one head candidate and then allowing a denser lane to consume
    the rest of the front of the queue.
    """
    if not opportunities:
        return opportunities

    def priority_rank(item):
        symbol, _signal, _confidence, mode, _atr, regime, signal_type, _entry_context = item
        return 1 if (symbol, signal_type, mode, regime) in EXPERIMENTAL_PRIORITY_LANES else 0

    experimental_regimes = ('GEMINI', 'PRICE', 'RAW')
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

    for bucket in buckets.values():
        bucket.sort(key=lambda item: (priority_rank(item), item[2]), reverse=True)

    promoted = []
    while True:
        available = [
            (regime, priority_rank(bucket[0]), bucket[0][2])
            for regime, bucket in buckets.items()
            if bucket
        ]
        if not available:
            break
        # Highest-confidence lane goes first each round, but every active lane
        # gets one pass before any lane gets a second turn.
        available.sort(key=lambda item: (item[1], item[2]), reverse=True)
        for regime, _priority, _top_confidence in available:
            if buckets[regime]:
                promoted.append(buckets[regime].pop(0))

    return promoted + remainder


def get_position_hold_seconds(pdata, pos=None):
    """Compute hold time using broker tick time when local wall clock is skewed."""
    now = time.time()
    entry_time = float(pdata.get('entry_time', now) or now)
    hold_sec = max(0.0, now - entry_time)

    symbol = str(pdata.get('symbol', '') or '')
    if pos is None:
        try:
            positions = mt5.positions_get(ticket=int(pdata.get('ticket', 0) or 0))
            if positions:
                pos = positions[0]
        except Exception:
            pos = None

    pos_time = float(getattr(pos, 'time', 0) or 0.0) if pos is not None else 0.0
    if pos_time > 0 and symbol:
        try:
            tick = mt5.symbol_info_tick(symbol)
            broker_now = float(getattr(tick, 'time', 0) or 0.0) if tick else 0.0
            if broker_now > 0 and broker_now >= pos_time:
                hold_sec = max(0.0, broker_now - pos_time)
            elif pos_time <= now:
                hold_sec = max(0.0, now - pos_time)
        except Exception:
            if pos_time <= now:
                hold_sec = max(0.0, now - pos_time)

    return hold_sec

def get_position_mode(pos):
    comment = (getattr(pos, "comment", "") or "").upper()
    for mode in FIRE_MODES:
        if mode in comment:
            return mode
    return "MACHINE_GUN"


def load_managed_positions():
    existing = mt5.positions_get()
    if not existing:
        return 0, 0

    loaded = 0
    adopted = 0
    for pos in existing:
        if should_ignore_external_position(pos):
            continue
        owned = is_bot_position(pos)
        if not owned and not ADOPT_EXISTING_POSITIONS:
            continue

        active_positions[pos.ticket] = build_position_state(pos, adopted=not owned)
        loaded += 1
        if not owned:
            adopted += 1

    return loaded, adopted

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    safe = msg.encode('ascii', 'ignore').decode('ascii')
    line = f"[{ts}] {safe}"
    try:
        print(line, flush=True)
    except OSError:
        try:
            fallback_path = os.path.join(os.path.dirname(__file__), "mt5_canonical_worker_out.log")
            with open(fallback_path, "a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")
        except OSError:
            pass


def get_process_command_line(pid):
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId = {int(pid)}\"; if ($p) {{ $p.CommandLine }}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return (result.stdout or "").strip()
    except Exception:
        return ""


def canonical_launch_allowed():
    if os.environ.get(CANONICAL_SUPERVISOR_ENV) == "1":
        return True, "canonical supervisor env"
    if os.environ.get(ALLOW_STANDALONE_ENV) == "1":
        return True, "explicit standalone override"

    parent_cmd = get_process_command_line(os.getppid())
    if "mt5_bot.py" in parent_cmd:
        return True, "parent launcher detected"

    return False, f"missing {CANONICAL_SUPERVISOR_ENV}=1 and no mt5_bot.py parent"


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

# ============================================================
# CONNECTION
# ============================================================

def connect_mt5():
    global mt5_connected
    for attempt in range(5):
        try:
            try:
                mt5.shutdown()
            except Exception:
                pass
            if mt5.initialize(login=LOGIN, password=PASSWORD, server=SERVER):
                info = mt5.account_info()
                if info is None:
                    log(f"MT5 account_info missing after init (attempt {attempt + 1})")
                elif int(getattr(info, "login", 0) or 0) != LOGIN:
                    log(
                        f"MT5 wrong account after init (attempt {attempt + 1}): "
                        f"got={getattr(info, 'login', None)} expected={LOGIN}"
                    )
                else:
                    mt5_connected = True
                    log(f"Connected to MT5 account {int(info.login)} (attempt {attempt + 1})")
                    return True
            else:
                log(f"MT5 init failed (attempt {attempt + 1}): {mt5.last_error()}")
        except Exception as e:
            log(f"MT5 connection error (attempt {attempt + 1}): {e}")
        time.sleep(3)
    mt5_connected = False
    return False

def ensure_mt5():
    global mt5_connected
    if not mt5_connected:
        return connect_mt5()
    try:
        info = mt5.account_info()
        if info is None:
            raise ConnectionError("account_info returned None")
        if int(getattr(info, "login", 0) or 0) != LOGIN:
            log(f"ACCOUNT_MISMATCH detected: terminal={getattr(info, 'login', None)} expected={LOGIN}, reconnecting...")
            mt5_connected = False
            try:
                mt5.shutdown()
            except:
                pass
            return connect_mt5()
        return True
    except:
        log("MT5 connection lost, reconnecting...")
        try:
            mt5.shutdown()
        except:
            pass
        mt5_connected = False
        return connect_mt5()

# ============================================================
# DATA RETRIEVAL (with caching)
# ============================================================

def get_bars(symbol, timeframe=mt5.TIMEFRAME_M1, count=50):
    """Get bars with caching to avoid redundant API calls"""
    cache_key = f"{symbol}_{timeframe}"
    now = time.time()

    if cache_key in _bars_cache:
        ts, bars = _bars_cache[cache_key]
        if now - ts < CACHE_TTL:
            return bars

    try:
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None or len(rates) == 0:
            return []
        bars = [{'t': r['time'], 'o': r['open'], 'h': r['high'],
                 'l': r['low'], 'c': r['close'], 'v': r['tick_volume']} for r in rates]
        _bars_cache[cache_key] = (now, bars)
        return bars
    except:
        return []


def get_tick_cached(symbol, current_cycle=0):
    """Get tick data with per-cycle caching to avoid redundant symbol_info_tick() calls.
    
    Returns the tick object or None. Within the same cycle, returns cached data.
    """
    global _tick_cache, _tick_cache_cycle
    
    if current_cycle > 0 and current_cycle == _tick_cache_cycle and symbol in _tick_cache:
        ts, tick = _tick_cache[symbol]
        return tick
    
    try:
        tick = mt5.symbol_info_tick(symbol)
        if current_cycle > 0:
            _tick_cache[symbol] = (time.time(), tick)
        return tick
    except:
        return None


def refresh_tick_cache_for_cycle(symbols, current_cycle=0):
    """Pre-populate tick cache for all symbols at the start of a cycle.
    
    This eliminates N redundant symbol_info_tick() calls that would otherwise
    happen when different functions (manage_position, stale checks, etc.)
    each call symbol_info_tick() for the same symbol.
    """
    global _tick_cache, _tick_cache_cycle
    
    _tick_cache.clear()
    _tick_cache_cycle = current_cycle
    
    for symbol in symbols:
        try:
            tick = mt5.symbol_info_tick(symbol)
            _tick_cache[symbol] = (time.time(), tick)
        except:
            pass


def get_tick_age_seconds(tick, now=None):
    if not tick:
        return None
    tick_time = getattr(tick, "time", 0) or 0
    if tick_time <= 0:
        return None
    if now is None:
        now = time.time()
    return max(0.0, float(now) - float(tick_time))


def is_tick_stale(tick, now=None, max_age_seconds=STALE_TICK_MAX_AGE_SECONDS):
    age = get_tick_age_seconds(tick, now=now)
    if age is None:
        return True, None
    return age > max_age_seconds, age


def log_stale_symbol(symbol, context, age_seconds=None):
    now = time.time()
    cooldowns = alleyway_state.setdefault("stale_symbol_log_until", {})
    next_allowed = float(cooldowns.get(symbol, 0.0) or 0.0)
    if now < next_allowed:
        return
    cooldowns[symbol] = now + STALE_SYMBOL_LOG_COOLDOWN_SECONDS
    age_text = "unknown" if age_seconds is None else f"{int(age_seconds)}s"
    log(f"  STALE_SYMBOL {symbol} context={context} tick_age={age_text}")


def get_market_closed_symbol_remaining(symbol, now=None):
    if now is None:
        now = time.time()
    cooldowns = alleyway_state.setdefault("market_closed_symbol_until", {})
    next_allowed = float(cooldowns.get(symbol, 0.0) or 0.0)
    if next_allowed <= now:
        if next_allowed > 0.0:
            cooldowns.pop(symbol, None)
        return None
    return max(0.0, next_allowed - now)


def mark_symbol_market_closed(symbol, retcode=None, comment="", cooldown_seconds=MARKET_CLOSED_SYMBOL_COOLDOWN_SECONDS):
    now = time.time()
    cooldowns = alleyway_state.setdefault("market_closed_symbol_until", {})
    log_cooldowns = alleyway_state.setdefault("market_closed_symbol_log_until", {})
    until = now + cooldown_seconds
    existing_until = float(cooldowns.get(symbol, 0.0) or 0.0)
    if until > existing_until:
        cooldowns[symbol] = until
    next_log_allowed = float(log_cooldowns.get(symbol, 0.0) or 0.0)
    if now >= next_log_allowed:
        retcode_text = "?" if retcode is None else str(retcode)
        comment_text = str(comment or "market closed")
        log(
            f"  [MARKET_CLOSED_COOLDOWN] {symbol} retcode={retcode_text} "
            f"comment={comment_text} hold={int(cooldown_seconds)}s"
        )
        log_cooldowns[symbol] = now + MARKET_CLOSED_SYMBOL_LOG_COOLDOWN_SECONDS


def get_insufficient_margin_symbol_remaining(symbol, now=None):
    if now is None:
        now = time.time()
    cooldowns = alleyway_state.setdefault("insufficient_margin_symbol_until", {})
    next_allowed = float(cooldowns.get(symbol, 0.0) or 0.0)
    if next_allowed <= now:
        if next_allowed > 0.0:
            cooldowns.pop(symbol, None)
        return None
    return max(0.0, next_allowed - now)


def mark_symbol_insufficient_margin(symbol, retcode=None, comment="", cooldown_seconds=INSUFFICIENT_MARGIN_SYMBOL_COOLDOWN_SECONDS):
    now = time.time()
    cooldowns = alleyway_state.setdefault("insufficient_margin_symbol_until", {})
    log_cooldowns = alleyway_state.setdefault("insufficient_margin_symbol_log_until", {})
    until = now + cooldown_seconds
    existing_until = float(cooldowns.get(symbol, 0.0) or 0.0)
    if until > existing_until:
        cooldowns[symbol] = until
    next_log_allowed = float(log_cooldowns.get(symbol, 0.0) or 0.0)
    if now >= next_log_allowed:
        retcode_text = "?" if retcode is None else str(retcode)
        comment_text = str(comment or "insufficient margin")
        log(
            f"  [INSUFFICIENT_MARGIN_COOLDOWN] {symbol} retcode={retcode_text} "
            f"comment={comment_text} hold={int(cooldown_seconds)}s"
        )
        log_cooldowns[symbol] = now + 30.0


def get_broker_connection_backoff_remaining(now=None):
    if now is None:
        now = time.time()
    until = float(alleyway_state.get("broker_connection_backoff_until", 0.0) or 0.0)
    if until <= now:
        if until > 0.0:
            alleyway_state["broker_connection_backoff_until"] = 0.0
        return None
    return max(0.0, until - now)


def mark_broker_connection_backoff(retcode=None, comment="", cooldown_seconds=BROKER_CONNECTION_BACKOFF_SECONDS):
    now = time.time()
    until = now + cooldown_seconds
    existing_until = float(alleyway_state.get("broker_connection_backoff_until", 0.0) or 0.0)
    if until > existing_until:
        alleyway_state["broker_connection_backoff_until"] = until
    next_log_allowed = float(alleyway_state.get("broker_connection_backoff_log_until", 0.0) or 0.0)
    if now >= next_log_allowed:
        retcode_text = "?" if retcode is None else str(retcode)
        comment_text = str(comment or "broker connection issue")
        log(
            f"  [BROKER_CONNECTION_BACKOFF] retcode={retcode_text} "
            f"comment={comment_text} hold={int(cooldown_seconds)}s"
        )
        alleyway_state["broker_connection_backoff_log_until"] = (
            now + BROKER_CONNECTION_BACKOFF_LOG_COOLDOWN_SECONDS
        )

# ============================================================
# TECHNICAL INDICATORS
# ============================================================

def calc_rsi(closes, period=14):
    """Calculate RSI"""
    if len(closes) < period + 1:
        return 50.0  # neutral default

    gains = []
    losses = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i-1]
        if delta > 0:
            gains.append(delta)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(delta))

    # Initial averages
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Smoothed averages (Wilder's method)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def calc_atr(bars, period=14):
    """Calculate Average True Range"""
    if len(bars) < period + 1:
        return 0.0

    trs = []
    for i in range(1, len(bars)):
        tr = max(
            bars[i]['h'] - bars[i]['l'],
            abs(bars[i]['h'] - bars[i-1]['c']),
            abs(bars[i]['l'] - bars[i-1]['c'])
        )
        trs.append(tr)

    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0.0

    # Wilder's smoothing
    atr = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
    return atr

def calc_ema(values, period):
    """Calculate Exponential Moving Average"""
    if len(values) < period:
        return sum(values) / len(values) if values else 0
    multiplier = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for val in values[period:]:
        ema = (val - ema) * multiplier + ema
    return ema

def detect_momentum_burst(bars, lookback=5):
    """Detect sudden volume + price expansion (breakout)"""
    if len(bars) < lookback + 10:
        return None, 0.0

    recent = bars[-lookback:]
    prior = bars[-(lookback+10):-lookback]

    avg_vol_prior = sum(b['v'] for b in prior) / len(prior)
    avg_range_prior = sum(b['h'] - b['l'] for b in prior) / len(prior)

    recent_vol = sum(b['v'] for b in recent) / len(recent)
    recent_range = sum(b['h'] - b['l'] for b in recent) / len(recent)

    vol_expansion = recent_vol / avg_vol_prior if avg_vol_prior > 0 else 1
    range_expansion = recent_range / avg_range_prior if avg_range_prior > 0 else 1

    # Need BOTH volume and range expansion
    if vol_expansion < 1.5 or range_expansion < 1.3:
        return None, 0.0

    # Direction: net movement over the burst
    net_move = recent[-1]['c'] - recent[0]['o']
    burst_strength = min(1.0, (vol_expansion - 1) * 0.3 + (range_expansion - 1) * 0.3)

    if net_move > 0:
        return 'BUY', burst_strength
    elif net_move < 0:
        return 'SELL', burst_strength
    return None, 0.0

# ============================================================
# MULTI-TIMEFRAME ANALYSIS
# ============================================================

def detect_market_regime(symbol):
    """Detect if market is trending or ranging (still)."""
    try:
        bars = get_bars(symbol, mt5.TIMEFRAME_M15, 50)
        if len(bars) < 30:
            return 'UNKNOWN', 0.0
        closes = [b['c'] for b in bars]
        # ADX proxy: compare ATR to price range
        atr_val = calc_atr(bars, 14)
        price_range = max(closes) - min(closes)
        avg_price = sum(closes) / len(closes)
        if avg_price == 0 or atr_val == 0:
            return 'UNKNOWN', 0.0
        # Normalized ATR: high = trending, low = ranging
        norm_atr = (atr_val / avg_price) * 10000  # in pips-like units
        # RSI distance from 50: far = trending, near = ranging
        rsi = calc_rsi(closes, 14)
        rsi_from_mid = abs(rsi - 50)
        # EMA spread: wide = trending, tight = ranging
        ema8 = calc_ema(closes, 8)
        ema21 = calc_ema(closes, 21)
        ema_spread_pct = abs(ema8 - ema21) / avg_price * 100 if avg_price > 0 else 0
        # Composite regime score
        trend_score = min(1.0, (rsi_from_mid / 25) * 0.4 + (ema_spread_pct / 0.05) * 0.4 + (norm_atr / 15) * 0.2)
        if trend_score > 0.55:
            return 'TRENDING', trend_score
        else:
            return 'RANGING', 1.0 - trend_score
    except:
        return 'UNKNOWN', 0.0

def get_mean_reversion_signal(symbol, diagnostics=None):
    """
    Mean-reversion for still/ranging markets.
    Simple and opportunistic: in a range, fade RSI divergence from 50.
    No need for BB touches or reversal candles — just catch the bounce.
    """
    try:
        if diagnostics is not None:
            diagnostics['mr_scanned'] = diagnostics.get('mr_scanned', 0) + 1
        bars_m5 = get_bars(symbol, mt5.TIMEFRAME_M5, 50)
        if len(bars_m5) < 30:
            if diagnostics is not None:
                diagnostics['mr_fail_bars'] = diagnostics.get('mr_fail_bars', 0) + 1
            return None, 0.0, 0
        closes = [b['c'] for b in bars_m5]
        price = closes[-1]
        # RSI — the core signal
        rsi = calc_rsi(closes, 14)
        # ATR for stops
        atr = calc_atr(bars_m5, 14)
        if atr <= 0:
            if diagnostics is not None:
                diagnostics['mr_fail_atr'] = diagnostics.get('mr_fail_atr', 0) + 1
            return None, 0.0, 0
# Distance from RSI 50 = conviction
        rsi_from_mid = abs(rsi - 50)
        if rsi_from_mid < 1:  # COMPETITION: Lowered from 3 to allow entries in ranging markets
            if diagnostics is not None:
                diagnostics['mr_fail_mid'] = diagnostics.get('mr_fail_mid', 0) + 1
            return None, 0.0, 0  # Dead flat, no edge
        signal = None
        confidence = 0.0
        if rsi < 45:
            if diagnostics is not None:
                diagnostics['mr_buy_zone'] = diagnostics.get('mr_buy_zone', 0) + 1
            # Oversold = BUY the bounce
            signal = 'BUY'
            confidence = min(0.85, (45 - rsi) / 25 + 0.25)
        elif rsi > 55:
            if diagnostics is not None:
                diagnostics['mr_sell_zone'] = diagnostics.get('mr_sell_zone', 0) + 1
            # Overbought = SELL the fade
            signal = 'SELL'
            confidence = min(0.85, (rsi - 55) / 25 + 0.25)
        else:
            if diagnostics is not None:
                diagnostics['mr_fail_rsi_band'] = diagnostics.get('mr_fail_rsi_band', 0) + 1
        # Bonus if near Bollinger Band extremes
        period = 20
        sma = sum(closes[-period:]) / period
        variance = sum((c - sma) ** 2 for c in closes[-period:]) / period
        std_dev = variance ** 0.5
        upper_bb = sma + 2 * std_dev
        lower_bb = sma - 2 * std_dev
        if signal == 'BUY' and price <= lower_bb * 1.005:
            confidence = min(0.90, confidence + 0.10)
            if diagnostics is not None:
                diagnostics['mr_bb_bonus'] = diagnostics.get('mr_bb_bonus', 0) + 1
        elif signal == 'SELL' and price >= upper_bb * 0.995:
            confidence = min(0.90, confidence + 0.10)
            if diagnostics is not None:
                diagnostics['mr_bb_bonus'] = diagnostics.get('mr_bb_bonus', 0) + 1
        if signal and diagnostics is not None:
            diagnostics['mr_signal_ready'] = diagnostics.get('mr_signal_ready', 0) + 1
        return signal, confidence, atr
    except:
        if diagnostics is not None:
            diagnostics['mr_fail_exception'] = diagnostics.get('mr_fail_exception', 0) + 1
        return None, 0.0, 0

def get_htf_bias(symbol):
    """Get higher-timeframe directional bias from M15"""
    bars_m15 = get_bars(symbol, mt5.TIMEFRAME_M15, 30)
    if len(bars_m15) < 20:
        return None, 0.0

    closes = [b['c'] for b in bars_m15]
    ema_fast = calc_ema(closes, 8)
    ema_slow = calc_ema(closes, 21)
    rsi = calc_rsi(closes, 14)

    price = closes[-1]

    # Strong uptrend: price > EMA8 > EMA21, RSI > 50
    if price > ema_fast > ema_slow and rsi > 50:
        strength = min(1.0, (price - ema_slow) / (ema_slow * 0.002) * 0.5 + (rsi - 50) / 50 * 0.5)
        return 'BUY', strength
    # Strong downtrend: price < EMA8 < EMA21, RSI < 50
    elif price < ema_fast < ema_slow and rsi < 50:
        strength = min(1.0, (ema_slow - price) / (ema_slow * 0.002) * 0.5 + (50 - rsi) / 50 * 0.5)
        return 'SELL', strength

    return None, 0.0

def get_m5_confirmation(symbol, direction):
    """Confirm M1 signal using M5 data — tightened for higher win rate"""
    bars_m5 = get_bars(symbol, mt5.TIMEFRAME_M5, 30)
    if len(bars_m5) < 20:
        return False, 0.0

    closes = [b['c'] for b in bars_m5]
    rsi = calc_rsi(closes, 14)
    ema8 = calc_ema(closes, 8)
    ema21 = calc_ema(closes, 21)
    price = closes[-1]

    if direction == 'BUY':
        # Require full EMA alignment and a sane RSI band. The weaker
        # "ema aligned but maybe good enough" override was producing
        # too many instant red entries.
        ema_aligned = price > ema8 and ema8 > ema21
        rsi_valid = 40 < rsi < 75
        if ema_aligned and rsi_valid:
            strength = (rsi - 40) / 35
            return True, max(0.4, min(1.0, strength))
    elif direction == 'SELL':
        ema_aligned = price < ema8 and ema8 < ema21
        rsi_valid = 25 < rsi < 60
        if ema_aligned and rsi_valid:
            strength = (60 - rsi) / 35
            return True, max(0.4, min(1.0, strength))

    return False, 0.0


# ============================================================
# RAW PREDICTIVE SIGNAL - Lead, don't trail
# Only raw OHLCV + velocity + volume confirmation
# ============================================================



def get_gemini_signal(symbol, diagnostics=None):
    return gemini_policy.get_gemini_signal(
        symbol=symbol,
        diagnostics=diagnostics,
        timeframe_m5=mt5.TIMEFRAME_M5,
        get_bars=get_bars,
        calc_atr=calc_atr,
        calc_ema=calc_ema,
        calc_rsi=calc_rsi,
    )

def get_raw_price_action_signal(symbol, diagnostics=None):
    """
    PREDICTIVE raw price action - anticipate momentum before it peaks.
    Returns: (signal, confidence, atr, thesis, signal_type)
    
    Key insight: RIDE VELOCITY, FADE EXHAUSTION
    """
    try:
        if diagnostics is not None:
            diagnostics['raw_scanned'] = diagnostics.get('raw_scanned', 0) + 1
        
        bars = get_bars(symbol, mt5.TIMEFRAME_M5, 30)
        if len(bars) < 20:
            if diagnostics is not None:
                diagnostics['raw_fail_bars'] = diagnostics.get('raw_fail_bars', 0) + 1
            return None, 0.0, 0, None, None
        
        # RAW DATA ONLY
        c = [b['c'] for b in bars]
        o = [b['o'] for b in bars]
        h = [b['h'] for b in bars]
        l = [b['l'] for b in bars]
        v = [b['v'] for b in bars]
        
        # Current + previous
        pc, po, ph, pl, pv = c[-1], o[-1], h[-1], l[-1], v[-1]
        p2c, p2o, p2h, p2l = c[-2], o[-2], h[-2], l[-2]
        p3c, p3o = c[-3], o[-3]
        
        signal = None
        confidence = 0.0
        thesis = None
        signal_type = None
        
        # === PREDICTIVE METRICS ===
        
        # 1. VELOCITY (price change rate) - PREDICTS CONTINUATION
        velocity = (pc - p2c) / p2c  # % change from prev close
        velocity2 = (p2c - p3c) / p3c  # prev velocity
        
        accelerating = velocity > 0 and velocity > velocity2  # Speeding up
        decelerating = velocity > 0 and velocity < velocity2  # Slowing down (exhaustion)
        
        # 2. VOLUME-MOMENTUM ALIGNMENT - PREDICTS STRENGTH
        avg_v = sum(v[-4:-1]) / 3 if len(v) >= 4 else sum(v) / len(v) if v else 1
        vol_confirm = pv > avg_v * 1.3  # Relaxed from 1.5
        
        # 3. CLOSE POSITION - PREDICTS NEXT MOVE
        # Closed at high = buyers in control = likely continues up
        current_range = ph - pl
        close_position = (pc - pl) / current_range if current_range > 0 else 0.5
        
        # 4. BODY STRENGTH - WHO WON THIS CANDLE?
        body_curr = pc - po
        body_strong = abs(body_curr) / current_range > 0.6 if current_range > 0 else False
        
        # 5. MOMENTUM TRIAD - 3 consecutive moves in same direction
        up_trend = pc > p2c and p2c > p3c
        down_trend = pc < p2c and p2c < p3c
        
        # Calculate ATR for stops
        atr = calc_atr(bars, 14) if len(bars) >= 15 else 0.0001
        
        # ============================
        # BUY PREDICTION (ride the acceleration)
        # ============================
        
        # Case 1: Accelerating + volume confirm = RIDE THE WAVE
        if accelerating and vol_confirm and pc > p2c:
            signal = 'BUY'
            confidence = 0.95
            thesis = 'velocity_acceleration'
            signal_type = 'ride_momentum'
        
        # Case 2: Strong momentum triad with volume
        elif up_trend and vol_confirm and body_strong:
            signal = 'BUY'
            confidence = 0.90
            thesis = 'momentum_triad'
            signal_type = 'three_push_up'
        
        # Case 3: Close at top + volume in = buyers winning
        elif close_position > 0.75 and vol_confirm:
            signal = 'BUY'
            confidence = 0.85
            thesis = 'close_strength'
            signal_type = 'closed_at_top'
        
        # Case 4: Velocity turning positive (bottom caught)
        elif velocity > 0 and p2c < p3c and vol_confirm:
            signal = 'BUY'
            confidence = 0.80
            thesis = 'momentum_turn'
            signal_type = 'reversal_catch'
        
        # Case 5: Riding the trend (conservative)
        elif pc > p2c and p2c > p3c and pv > avg_v:
            signal = 'BUY'
            confidence = 0.70
            thesis = 'trend_ride'
            signal_type = 'trend_continuation'
        
        # Case 6: Simple momentum (no volume filter)
        elif pc > p2c:
            signal = 'BUY'
            confidence = 0.55
            thesis = 'simple_momentum'
            signal_type = 'candle_direction'
        
        # ============================
        # SELL PREDICTION (ride the drop)
        # ============================
        
        # Case 1: Accelerating down + volume confirm
        elif velocity < 0 and velocity < velocity2 and vol_confirm and pc < p2c:
            signal = 'SELL'
            confidence = 0.95
            thesis = 'velocity_acceleration'
            signal_type = 'ride_momentum'
        
        # Case 2: Strong down triad with volume
        elif down_trend and vol_confirm and body_strong:
            signal = 'SELL'
            confidence = 0.90
            thesis = 'momentum_triad'
            signal_type = 'three_push_down'
        
        # Case 3: Close at bottom + volume in = sellers winning
        elif close_position < 0.25 and vol_confirm:
            signal = 'SELL'
            confidence = 0.85
            thesis = 'close_strength'
            signal_type = 'closed_at_bottom'
        
        # Case 4: Velocity turning negative (top caught)
        elif velocity < 0 and p2c > p3c and vol_confirm:
            signal = 'SELL'
            confidence = 0.80
            thesis = 'momentum_turn'
            signal_type = 'reversal_catch'
        
        # Case 5: Riding the drop
        elif pc < p2c and p2c < p3c and pv > avg_v:
            signal = 'SELL'
            confidence = 0.70
            thesis = 'trend_ride'
            signal_type = 'trend_continuation'
        
        # Case 6: Simple momentum (no volume filter)
        elif pc < p2c:
            signal = 'SELL'
            confidence = 0.55
            thesis = 'simple_momentum'
            signal_type = 'candle_direction'
        
        if signal and diagnostics is not None:
            diagnostics['raw_signal'] = diagnostics.get('raw_signal', 0) + 1
            thesis_key = f"raw_{thesis}"
            diagnostics[thesis_key] = diagnostics.get(thesis_key, 0) + 1
        
        return signal, confidence, atr, thesis, signal_type
        
    except Exception as e:
        # Log error for debugging
        try:
            log(f"  [RAW_DEBUG] {symbol} error: {str(e)[:50]}")
        except:
            pass
        return None, 0.0, 0, None, None


def get_price_edge_signal(symbol, diagnostics=None):
    return price_policy.get_price_edge_signal(
        symbol=symbol,
        diagnostics=diagnostics,
        price_allow_exotics=PRICE_ALLOW_EXOTICS,
        is_exotic=is_exotic,
        get_bars=get_bars,
        calc_atr=calc_atr,
        mt5_module=mt5,
        price_breakout_min_confidence=PRICE_BREAKOUT_MIN_CONFIDENCE,
        price_pullback_min_confidence=PRICE_PULLBACK_MIN_CONFIDENCE,
        price_rejection_min_confidence=PRICE_REJECTION_MIN_CONFIDENCE,
        price_pass_confidence=PRICE_PASS_CONFIDENCE,
    )


def analyze(symbol, adaptive_threshold=MIN_CONFIDENCE_BASE, diagnostics=None):
    """
    Multi-timeframe analysis:
    1. M15 sets directional bias
    2. M5 confirms with RSI + EMA
    3. M1 provides entry timing via momentum burst or pullback
    Returns: (signal, confidence, atr_m5)
    """
    try:
        # Step 1: Detect market regime
        regime, regime_score = detect_market_regime(symbol)

        # === ASIAN SESSION MEAN-REVERSION PATH (00:00-07:59 UTC) ===
        # Only for indices during low-volatility Asian hours
        # Data shows US30 +$1,382, JPN225 +$235 during Asian
        if is_asian_session():
            asian_signal, asian_conf, asian_atr, asian_regime, asian_type = asian_policy.get_asian_mean_reversion_signal(
                symbol=symbol,
                timeframe_m5=mt5.TIMEFRAME_M5,
                get_bars=get_bars,
                calc_atr=calc_atr,
                calc_ema=calc_ema,
                calc_rsi=calc_rsi,
            )
            if asian_signal and asian_conf >= 0.72:  # High confidence for off-session
                if diagnostics is not None:
                    diagnostics['asian_mr_passed'] = diagnostics.get('asian_mr_passed', 0) + 1
                return asian_signal, asian_conf, asian_atr, 'ASIAN_REVERSION', asian_type, 'asian_range'

        # === GEMINI PATH (disabled but still evaluated for compatibility) ===
        # Test pure price action signals in parallel
        gemini_signal, gemini_confidence, gemini_atr, gemini_thesis, gemini_type = get_gemini_signal(symbol, diagnostics=diagnostics)
        if gemini_signal and gemini_confidence >= 0.60:
            if diagnostics is not None:
                diagnostics['gemini_passed'] = diagnostics.get('gemini_passed', 0) + 1
            return gemini_signal, gemini_confidence, gemini_atr, 'GEMINI', gemini_type, gemini_thesis

        price_signal, price_confidence, price_atr, price_thesis, price_signal_type = get_price_edge_signal(symbol, diagnostics=diagnostics)
        if price_signal and price_confidence >= PRICE_PASS_CONFIDENCE:
            if diagnostics is not None:
                diagnostics['price_passed'] = diagnostics.get('price_passed', 0) + 1
            price_context = price_thesis or 'price_unlabeled'
            return price_signal, price_confidence, price_atr, 'PRICE', price_signal_type or 'price_unlabeled', price_context

        raw_signal, raw_confidence, raw_atr, raw_thesis, raw_type = get_raw_price_action_signal(symbol, diagnostics=diagnostics)
        if raw_signal and raw_confidence >= 0.55:  # Predictive threshold - lowered from 0.65 for competition
            if diagnostics is not None:
                diagnostics['raw_passed'] = diagnostics.get('raw_passed', 0) + 1
            raw_context = raw_thesis or 'raw_predictive'
            return raw_signal, raw_confidence, raw_atr, 'RAW', raw_type or 'predictive', raw_context

        # === MEAN-REVERSION PATH (ranging/still markets) ===
        if regime == 'RANGING':
            if diagnostics is not None:
                diagnostics['ranging_symbols'] = diagnostics.get('ranging_symbols', 0) + 1
            if regime_score < REVERSION_MIN_RANGING_SCORE:
                if diagnostics is not None:
                    diagnostics['mr_fail_regime_score'] = diagnostics.get('mr_fail_regime_score', 0) + 1
            else:
                if diagnostics is not None:
                    diagnostics['mr_pass_regime_score'] = diagnostics.get('mr_pass_regime_score', 0) + 1
                signal, mr_confidence, atr_mr = get_mean_reversion_signal(symbol, diagnostics=diagnostics)
                if signal and mr_confidence >= max(REVERSION_MIN_CONFIDENCE, adaptive_threshold):
                    if diagnostics is not None:
                        diagnostics['mr_pass_threshold'] = diagnostics.get('mr_pass_threshold', 0) + 1
                    return signal, mr_confidence, atr_mr, 'RANGING', 'legacy_mean_reversion', 'indicator_stack'
                if signal and diagnostics is not None:
                    diagnostics['mr_fail_threshold'] = diagnostics.get('mr_fail_threshold', 0) + 1
        elif diagnostics is not None:
            diagnostics['non_ranging_symbols'] = diagnostics.get('non_ranging_symbols', 0) + 1

        # === TREND-FOLLOWING PATH ===
        # Step 1: M15 directional bias
        htf_bias, htf_strength = get_htf_bias(symbol)
        if not htf_bias:
            # DEBUG: log why no bias
            pass  # log(f"  ⏸️ {symbol}: no M15 bias")

        # Step 2: M5 confirmation
        confirmed, m5_strength = get_m5_confirmation(symbol, htf_bias)
        if not confirmed:
            return None, 0, 0, regime, None, None

        # Step 3: M1 entry timing
        bars_m1 = get_bars(symbol, mt5.TIMEFRAME_M1, 30)
        if len(bars_m1) < 20:
            return None, 0, 0, regime, None, None

        closes_m1 = [b['c'] for b in bars_m1]
        price = closes_m1[-1]

        # Check for momentum burst on M1
        burst_dir, burst_strength = detect_momentum_burst(bars_m1)

        # M1 momentum alignment
        mom3 = (closes_m1[-1] - closes_m1[-3]) / closes_m1[-3] if closes_m1[-3] != 0 else 0
        mom5 = (closes_m1[-1] - closes_m1[-5]) / closes_m1[-5] if closes_m1[-5] != 0 else 0

        m1_aligned = False
        m1_score = 0
        if htf_bias == 'BUY' and mom3 > 0 and mom5 > 0:
            m1_aligned = True
            m1_score = min(1.0, (abs(mom3) + abs(mom5)) / 0.001)
        elif htf_bias == 'SELL' and mom3 < 0 and mom5 < 0:
            m1_aligned = True
            m1_score = min(1.0, (abs(mom3) + abs(mom5)) / 0.001)

        # Burst alignment bonus
        burst_bonus = 0
        if burst_dir == htf_bias and burst_strength > 0:
            burst_bonus = burst_strength * 0.2

        # Must have EITHER M1 alignment OR burst
        if not m1_aligned and burst_dir != htf_bias:
            return None, 0, 0, regime, None, None

        # Calculate ATR on M5 for stops
        bars_m5 = get_bars(symbol, mt5.TIMEFRAME_M5, 30)
        atr_m5 = calc_atr(bars_m5, 14) if len(bars_m5) > 14 else 0
        if atr_m5 <= 0:
            return None, 0, 0, regime, None, None

        # Composite confidence — weighted toward M15/M5, M1 is just timing
        # Tightened: weaker signals get lower scores, only strong alignment passes
        confidence = (
            htf_strength * 0.40 +    # M15 trend strength (dominant)
            m5_strength * 0.30 +     # M5 confirmation strength
            m1_score * 0.20 +        # M1 momentum alignment
            burst_bonus              # Breakout bonus
        )

        # Volume confirmation on M1 (reduced bonus — volume alone doesn't predict direction)
        avg_vol = sum(b['v'] for b in bars_m1[-10:-1]) / 9 if len(bars_m1) >= 11 else 0
        if avg_vol > 0 and bars_m1[-1]['v'] > avg_vol * 1.5:
            confidence += 0.05  # Reduced from 0.10 — volume spike is weak signal

        confidence = min(1.0, confidence)

        if confidence >= adaptive_threshold:
            return htf_bias, confidence, atr_m5, regime, 'legacy_trend_following', 'indicator_stack'

        return None, 0, 0, regime, None, None

    except Exception as e:
        return None, 0, 0, 'UNKNOWN', None, None

# ============================================================
# SESSION FILTER
# ============================================================

def is_crypto(symbol):
    crypto_tickers = {'BTC', 'ETH', 'XRP', 'DOGE', 'ADA', 'SOL', 'LTC', 'XMR', 'ZEC', 'DASH', 'DOT', 'MATIC', 'AVAX', 'LINK', 'UNI', 'BNB', 'SHIB', 'PEPE'}
    for t in crypto_tickers:
        if symbol.startswith(t):
            return True
    return False

def is_commodity(symbol):
    return symbol.startswith('XAU') or symbol.startswith('XAG')

def is_good_session(symbol):
    """Check if current time is a good session for this symbol"""
    # DISABLED: Trade all symbols regardless of session
    return True

def is_overlap_session():
    """Check if we're in the London/NY overlap (best liquidity)"""
    utc_hour = datetime.now(timezone.utc).hour
    return SESSION_OVERLAP[0] <= utc_hour < SESSION_OVERLAP[1]

def is_off_session():
    """Check if we're in the low-liquidity off-session period."""
    utc_hour = datetime.now(timezone.utc).hour
    return utc_hour in OFF_SESSION_HOURS

def is_msls_symbol_valid(symbol):
    """Check if a symbol has a proven 90%+ green rate for MSLS."""
    proven_msls_symbols = {
        'NAS100',  # 96.6% Green Next
        'US30',    # 95.7% Green Next
        'AUDCHF',  # 95.4% Green Next
        'EURJPY',  # 95.2% Green Next
        'XAUUSD',  # 92.2% Green Next
    }
    return symbol in proven_msls_symbols

def is_asian_session():
    """Check if we're in Asian session (00:00-07:59 UTC)"""
    utc_hour = datetime.now(timezone.utc).hour
    return 0 <= utc_hour < 8

def is_london_session():
    """Check if we're in London session (07:00-16:59 UTC)"""
    utc_hour = datetime.now(timezone.utc).hour
    return 7 <= utc_hour < 17

# ============================================================
# ALLEYWAY - Adaptive Threshold Relaxation System
# ============================================================
# 
# The "alleyway" concept: Under certain market conditions, the bot
# should widen its entry criteria (lower thresholds) to capture
# opportunities that would otherwise be filtered out.
#
# Measured conditions:
# 1. EQUITY_GROWTH: If we're winning, market is favorable -> relax
# 2. LOW_ACTIVITY: No trades for N cycles -> need to find entries -> relax
# 3. VOLATILITY_SPIKE: High ATR across symbols -> momentum opportunity -> relax
# 4. SESSION_QUALITY: Overlap session = prime time -> stay strict, off-hours -> relax
# 5. STREAK_PRESSURE: Long losing streak -> tighten, winning streak -> relax
#

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


def register_risk_event():
    now = time.time()
    recent_risk_events.append(now)
    cutoff = now - CLUSTER_EVENT_WINDOW_SECONDS
    while recent_risk_events and recent_risk_events[0] < cutoff:
        recent_risk_events.pop(0)

    if len(recent_risk_events) >= CLUSTER_EVENT_TRIGGER_COUNT:
        cooldown_until = now + CLUSTER_COOLDOWN_SECONDS
        alleyway_state['cluster_cooldown_until'] = max(
            float(alleyway_state.get('cluster_cooldown_until', 0.0) or 0.0),
            cooldown_until,
        )
        return True
    return False

def measure_market_energy(symbols_sample):
    """
    Scan a sample of symbols to measure overall market energy.
    Returns: (avg_atr_pct, momentum_score, volatility_regime)
    
    High energy = market is moving -> can use tighter thresholds
    Low energy = market is quiet -> need to relax to find trades
    """
    try:
        atr_values = []
        momentum_scores = []
        
        for symbol in symbols_sample[:20]:  # Sample up to 20 symbols
            bars = get_bars(symbol, mt5.TIMEFRAME_M5, 30)
            if len(bars) < 20:
                continue
            
            closes = [b['c'] for b in bars]
            atr = calc_atr(bars, 14)
            
            if atr > 0 and closes[-1] > 0:
                atr_pct = atr / closes[-1] * 100
                atr_values.append(atr_pct)
                
                # Momentum: recent price movement
                mom5 = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0
                mom10 = (closes[-1] - closes[-10]) / closes[-10] * 100 if len(closes) >= 10 else 0
                momentum_scores.append(abs(mom5) + abs(mom10))
        
        if not atr_values:
            return 0.0, 0.0, 'UNKNOWN'
        
        avg_atr = sum(atr_values) / len(atr_values)
        avg_momentum = sum(momentum_scores) / len(momentum_scores) if momentum_scores else 0
        
        # Classify volatility regime
        if avg_atr > 0.5:  # High volatility (0.5% ATR = significant movement)
            regime = 'HIGH_VOL'
        elif avg_atr < 0.15:  # Low volatility
            regime = 'LOW_VOL'
        else:
            regime = 'NORMAL'
        
        return avg_atr, avg_momentum, regime
        
    except:
        return 0.0, 0.0, 'UNKNOWN'

def calc_alleyway_relaxation(equity, start_equity, trades_count, win_streak, lose_streak):
    """
    Calculate how much to relax thresholds based on market conditions.
    Returns: relaxation_factor (0.0 = no relax, 0.3 = max relax)
    
    This creates the "alleyway" - widening entry criteria when conditions warrant.
    """
    if not ALLEYWAY_ENABLED:
        return 0.0
    
    relaxation = 0.0
    reasons = []
    
    # 1. EQUITY GROWTH: Winning = market is favorable
    if equity > start_equity * 1.05:  # 5% profit
        growth_pct = (equity - start_equity) / start_equity
        relaxation += min(0.15, growth_pct * 0.5)  # Cap at 0.15
        reasons.append(f'equity_up_{growth_pct:.1%}')
    
    # 2. STREAK PRESSURE (check FIRST - this overrides idle relaxation)
    if lose_streak >= 10:
        # Heavy losing streak - MAX tightening, no relaxation allowed
        relaxation -= 0.30
        reasons.append(f'STOP_LOSS_{lose_streak}')
        return max(-0.20, relaxation), reasons  # Early return - stop trading aggressively
    elif lose_streak >= 5:
        # Losing streak - tighten significantly
        relaxation -= 0.15 - (lose_streak - 5) * 0.02  # Progressive tightening
        reasons.append(f'lose_streak_{lose_streak}')
    elif win_streak >= 3:
        relaxation += 0.10  # Winning streak = confidence
        reasons.append(f'win_streak_{win_streak}')
    
    # 3. LOW ACTIVITY: No trades recently -> need entries (SKIP if losing)
    cycles_idle = alleyway_state['cycles_without_trade']
    if cycles_idle > 10 and lose_streak < 5:  # Only relax if NOT in losing streak
        relaxation += min(0.15, cycles_idle * 0.008)  # Gentler relaxation
        reasons.append(f'idle_{cycles_idle}')
    
    # 4. SESSION QUALITY: No penalty for off-hours — we trade 24/7
    #    (mean-reversion works great in thin markets)
    if is_overlap_session():
        relaxation += 0.03  # Bonus for prime time
        reasons.append('overlap_bonus')
    
    # 5. Market volatility regime (passed from main loop)
    market_regime = alleyway_state.get('volatility_regime', 'UNKNOWN')
    market_momentum = alleyway_state.get('market_momentum', 0.0)
    
    if market_regime == 'LOW_VOL' and market_momentum < 0.5:
        # Low volatility + low momentum = dead market, TIGHTEN (no real moves)
        relaxation -= 0.10
        reasons.append('low_energy_tighten')
    elif market_regime == 'HIGH_VOL' and market_momentum > 1.0:
        # High volatility + momentum = trending, can relax slightly
        relaxation += 0.05
        reasons.append('high_energy')
    
    # Clamp relaxation
    relaxation = max(-0.10, min(0.30, relaxation))
    
    return relaxation, reasons

def get_adaptive_threshold(base_threshold, relaxation):
    """
    Apply relaxation to get the actual threshold.
    More relaxation = lower threshold = easier to enter trades.
    """
    adjusted = base_threshold - relaxation
    return max(MIN_CONFIDENCE_MIN, adjusted)

# ============================================================
# CORRELATION FILTER
# ============================================================

def get_currency_groups_for_symbol(symbol):
    """Return which currency groups a symbol belongs to"""
    groups = []
    for group_name, symbols in CURRENCY_GROUPS.items():
        if symbol in symbols:
            groups.append(group_name)
    return groups

def check_correlation_limit(symbol):
    """Check if adding a position for this symbol would exceed correlation limits"""
    my_groups = get_currency_groups_for_symbol(symbol)
    if not my_groups:
        return True  # Unknown symbol, allow it

    for group in my_groups:
        group_symbols = CURRENCY_GROUPS.get(group, [])
        count = sum(1 for _, p in active_positions.items() if p['symbol'] in group_symbols)
        if count >= MAX_PER_CURRENCY_GROUP:
            return False

    return True


def get_symbol_family_bucket(symbol):
    """Group obvious index symbols so sync-close replacement freezes can span close substitutes."""
    text = str(symbol or "").upper()
    if any(key in text for key in INDEX_FAMILY_SYMBOL_KEYS):
        return "INDEX"
    return ""


def get_alleyway_mapping(key):
    """Return a safe mapping snapshot for state slots that should hold dicts."""
    value = alleyway_state.get(key)
    if isinstance(value, Mapping):
        return dict(value)
    return {}


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


def get_symbol_stress(symbol):
    """Estimate how much this symbol already dominates risk in the live book."""
    symbol_positions = [pdata for pdata in active_positions.values() if pdata['symbol'] == symbol]
    if not symbol_positions:
        return {
            "score": 0.0,
            "drawdown_share": 0.0,
            "volume_share": 0.0,
            "position_ratio": 0.0,
            "all_losing": False,
        }

    total_drawdown = sum(max(0.0, -(pdata.get('last_pnl', 0.0) or 0.0)) for pdata in active_positions.values())
    symbol_drawdown = sum(max(0.0, -(pdata.get('last_pnl', 0.0) or 0.0)) for pdata in symbol_positions)

    total_volume = sum(float(pdata.get('volume', 0.0) or 0.0) for pdata in active_positions.values())
    symbol_volume = sum(float(pdata.get('volume', 0.0) or 0.0) for pdata in symbol_positions)

    drawdown_share = (symbol_drawdown / total_drawdown) if total_drawdown > 0 else 0.0
    volume_share = (symbol_volume / total_volume) if total_volume > 0 else 0.0
    position_ratio = len(symbol_positions) / max(1, MAX_POSITIONS_PER_SYMBOL)
    all_losing = all((pdata.get('last_pnl', 0.0) or 0.0) <= 0 for pdata in symbol_positions)

    score = 0.0
    score += min(1.5, drawdown_share * 1.6)
    score += min(1.0, volume_share * 1.2)
    score += min(1.0, position_ratio * 0.8)
    if all_losing:
        score += 0.35

    return {
        "score": score,
        "drawdown_share": drawdown_share,
        "volume_share": volume_share,
        "position_ratio": position_ratio,
        "all_losing": all_losing,
    }


def get_book_stress(equity):
    """Summarize overall managed-book stress so entry posture can react cleanly."""
    managed_positions = list(active_positions.values())
    
    # CRITICAL FIX: Use TRUE equity peak drawdown, not just open position losses
    # This tracks actual account drawdown from the peak, including realized losses
    equity_peak = alleyway_state.get('equity_peak', equity)
    if equity_peak <= 0:
        equity_peak = equity
    
    # True drawdown from peak equity (includes realized losses)
    true_drawdown = max(0.0, equity_peak - equity)
    true_drawdown_pct = true_drawdown / equity_peak if equity_peak > 0 else 0.0
    
    # Also track open-only drawdown for symbol-level stress
    total_open_drawdown = 0.0
    symbol_drawdowns = {}
    direct_positions = 0
    adopted_positions = 0

    for pdata in managed_positions:
        pnl = float(pdata.get("last_pnl", 0.0) or 0.0)
        drawdown = max(0.0, -pnl)
        total_open_drawdown += drawdown
        symbol = pdata.get("symbol", "UNKNOWN")
        symbol_drawdowns[symbol] = symbol_drawdowns.get(symbol, 0.0) + drawdown
        if pdata.get("adopted"):
            adopted_positions += 1
        else:
            direct_positions += 1

    top_symbol = None
    top_symbol_drawdown = 0.0
    if symbol_drawdowns:
        top_symbol, top_symbol_drawdown = max(symbol_drawdowns.items(), key=lambda item: item[1])

    return {
        "managed_drawdown": total_open_drawdown,  # Keep for symbol-level analysis
        "managed_drawdown_pct": true_drawdown_pct,  # FIXED: Use TRUE equity peak drawdown
        "true_drawdown": true_drawdown,
        "true_drawdown_pct": true_drawdown_pct,
        "equity_peak": equity_peak,
        "top_symbol": top_symbol,
        "top_symbol_drawdown": top_symbol_drawdown,
        "top_symbol_drawdown_pct": top_symbol_drawdown / equity_peak if equity_peak > 0 else 0.0,  # Use peak for consistency
        "top_symbol_drawdown_share": (top_symbol_drawdown / total_open_drawdown) if total_open_drawdown > 0 else 0.0,
        "direct_positions": direct_positions,
        "adopted_positions": adopted_positions,
        "managed_positions": len(managed_positions),
    }


def get_effective_rearm_limits():
    """
    Benchmark floor for live REARM posture eligibility.
    Live proof on 2026-04-07 showed that letting loaded books inherit drifted
    headline caps reclassified 14-position DEFEND books as quiet-book REARM,
    which immediately rebuilt new exposure.

    Contract:
    - Do not bypass this helper inside update_entry_posture().
    - Do not replace these caps with the top-of-file REARM_* headline limits.
    - If a more aggressive floor ever wins again, change it here and leave
      fresh monitor/log proof in memory.md first.
    """
    # Canonical live floor: keep literal helper-owned numbers here so another
    # agent cannot silently widen REARM by drifting top-of-file constants.
    # Live truth on 2026-04-07 remains 3 / 1 / 1 until fresh monitor proof
    # says otherwise.
    canonical_direct_cap = 3
    canonical_non_reversion_cap = 1
    canonical_losing_cap = 1
    return (
        min(REARM_MAX_DIRECT_POSITIONS, canonical_direct_cap),
        min(REARM_MAX_NON_REVERSION_DIRECT, canonical_non_reversion_cap),
        min(REARM_MAX_LOSING_DIRECT_POSITIONS, canonical_losing_cap),
    )


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


def get_rearm_profile():
    """Apply a fixed REARM profile without desperation ramps from idle time."""
    if not alleyway_state.get("rearm_active"):
        return {
            "threshold_relaxation": 0.0,
            "stress_relief": 0.0,
            "mode_floor_relief": 0.0,
            "extra_entry_slots": 0,
            "idle_cycles": alleyway_state.get("cycles_without_trade", 0),
            "escalation": 0.0,
        }

    idle_cycles = alleyway_state.get("cycles_without_trade", 0)

    return {
        "threshold_relaxation": REARM_THRESHOLD_RELAXATION,
        "stress_relief": REARM_STRESS_RELIEF,
        "mode_floor_relief": REARM_MODE_FLOOR_RELIEF,
        "extra_entry_slots": REARM_EXTRA_ENTRY_SLOTS,
        "idle_cycles": idle_cycles,
        "escalation": 0.0,
    }


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


def count_direct_positions():
    return sum(1 for pdata in active_positions.values() if not pdata.get('adopted'))


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

# ============================================================
# SYMBOL SELECTION
# ============================================================

def is_exotic(symbol):
    """Check if symbol is an exotic pair"""
    exotics = {'ZAR', 'MXN', 'NOK', 'SEK', 'DKK', 'HKD', 'CNH', 'SGD', 'CZK', 'PLN', 'HUF', 'TRY'}
    for e in exotics:
        if e in symbol:
            return True
    return False

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

# ============================================================
# ORDER EXECUTION
# ============================================================

def get_mode_for_confidence(confidence, regime='TRENDING'):
    # 10x compounding: route everything to SNIPER/SHOTGUN only
    # Disabled: GEMINI (-$5,786), MACHINE_GUN (-$3,317), PRICE (-$1,784), REVERSION (DEFEND cascade source)
    if regime == 'GEMINI':
        # GEMINI regime: route to SNIPER on high confidence, skip otherwise
        if confidence >= 0.70:
            return 'SNIPER'
        return 'GEMINI'  # Will be blocked by DISABLED_MODES

    if regime == 'PRICE':
        # PRICE regime: route to SNIPER on high confidence
        if confidence >= 0.70:
            return 'SNIPER'
        return 'PRICE'  # Will be blocked

    # RAW mode: route to SHOTGUN instead of MACHINE_GUN
    if regime == 'RAW':
        if confidence >= 0.70:
            return 'SNIPER'
        return 'SHOTGUN'  # Was MACHINE_GUN, now routed to safer mode

    if regime == 'RANGING':
        if alleyway_state.get('entry_posture') == 'REARM' or alleyway_state.get('rearm_active'):
            if confidence < 0.60:
                return 'SHOTGUN'  # Was MACHINE_GUN
            elif confidence < 0.78:
                return 'SHOTGUN'
            else:
                return 'SNIPER'
        elif alleyway_state.get('entry_posture') == 'DEFEND':
            if confidence >= 0.74:
                return 'SNIPER'  # Was REVERSION
            elif confidence >= 0.64:
                return 'SHOTGUN'
            else:
                return 'SHOTGUN'  # Was MACHINE_GUN
        return 'SNIPER'  # Was REVERSION fallback
    # 10x compounding: raised thresholds back to 0.70 to require high conviction
    if confidence >= 0.70:
        return 'SNIPER'
    elif confidence >= 0.55:
        return 'SHOTGUN'
    else:
        return 'SHOTGUN'  # Was MACHINE_GUN — now SHOTGUN for safety

def calc_equity_lot(symbol, mode, atr, equity):
    """
    Calculate lot size based on current equity and ATR.
    As equity grows (compounding), lot sizes automatically increase.
    On drawdown from peak, lot sizes shrink to preserve capital.
    Risk = equity * risk_pct. Lot = risk / (atr * tick_value_per_lot).
    """
    try:
        sym_info = mt5.symbol_info(symbol)
        if not sym_info or atr <= 0:
            return 0.01

        # === DYNAMIC LOT SHRINK ON DRAWDOWN ===
        peak = alleyway_state.get('equity_peak', equity)
        if peak > 0 and equity < peak:
            drawdown_pct = (peak - equity) / peak
            shrink_factor = max(0.3, 1.0 - drawdown_pct * 2.0)  # 50% DD = 0.0x lots (cap at 0.3 minimum)
            effective_equity = equity * shrink_factor
        else:
            effective_equity = equity

        risk_pct = RISK_PER_TRADE.get(mode, 0.03)
        risk_dollars = effective_equity * risk_pct

        mode_config = FIRE_MODES[mode]
        sl_distance = atr * mode_config['sl_atr_mult']

        # tick_value = profit per 1 point move per 1 lot
        tick_value = sym_info.trade_tick_value
        tick_size = sym_info.trade_tick_size

        if tick_value <= 0 or tick_size <= 0:
            return 0.01

        # How many ticks in our SL distance
        sl_ticks = sl_distance / tick_size

        # Dollar risk per lot = sl_ticks * tick_value
        risk_per_lot = sl_ticks * tick_value

        if risk_per_lot <= 0:
            return 0.01

        # Lot size = total risk / risk per lot
        lot = risk_dollars / risk_per_lot

        # === EXOTIC PAIR LOT FLOOR ===
        # Check brain.json for symbols bleeding money (avg_loss > threshold)
        # Force them to minimum lot to prevent catastrophic losses
        try:
            brain_file = os.path.join(os.path.dirname(__file__), "brain.json")
            if os.path.exists(brain_file):
                with open(brain_file) as bf:
                    brain_data = json.load(bf)
                symbols_blob = brain_data.get("symbols", {})
                if not isinstance(symbols_blob, Mapping):
                    symbols_blob = {}
                sym_data = symbols_blob.get(symbol, {})
                if not isinstance(sym_data, Mapping):
                    sym_data = {}
                avg_loss = sym_data.get("avg_loss", 0)
                trades = sym_data.get("trades", 0)
                if trades >= 3 and avg_loss > EXOTIC_AVG_LOSS_THRESHOLD:
                    lot = min(lot, EXOTIC_LOT_FLOOR)
        except Exception:
            pass  # If brain.json read fails, proceed with normal lot

        return clamp_trade_lot(symbol, mode, lot, atr=atr, equity=equity)

    except Exception as e:
        return 0.01


def clamp_trade_lot(symbol, mode, lot, atr=None, equity=None):
    """Clamp lot size to symbol limits and mode-aware safety caps."""
    try:
        sym_info = mt5.symbol_info(symbol)
        if not sym_info:
            return max(0.01, round(float(lot or 0.01), 2))

        min_lot = float(sym_info.volume_min or 0.01)
        lot_step = float(sym_info.volume_step or 0.01)
        
        # 10x compounding scaler: as equity grows from 69k, scale caps.
        # Use the same drawdown-adjusted equity logic as calc_equity_lot() so
        # cap growth does not stay expanded while the bot is shrinking risk.
        BASE_EQUITY = 69000.0
        cap_equity = float(equity or 0.0)
        try:
            peak = float(alleyway_state.get('equity_peak', cap_equity) or cap_equity)
            if peak > 0 and cap_equity > 0 and cap_equity < peak:
                drawdown_pct = (peak - cap_equity) / peak
                shrink_factor = max(0.3, 1.0 - drawdown_pct * 2.0)
                cap_equity = cap_equity * shrink_factor
        except Exception:
            pass

        scale_factor = 1.0
        if cap_equity > BASE_EQUITY:
            scale_factor = (cap_equity / BASE_EQUITY) ** 0.5
            
        scaled_max_lot = MAX_LOT_CAP * scale_factor
        
        symbol_max = float(sym_info.volume_max or scaled_max_lot)
        mode_cap = float(MODE_MAX_LOT_CAP.get(mode, MAX_LOT_CAP)) * scale_factor
        hard_cap = max(min_lot, min(symbol_max, scaled_max_lot, mode_cap))

        clamped = max(min_lot, min(hard_cap, float(lot or min_lot)))

        # Apply the audited fresh-entry lane caps with real ATR sizing when ATR is available.
        mode_cap_map = MODE_ADVERSE_DOLLAR_CAP.get(mode)
        if atr and atr > 0 and mode_cap_map:
            try:
                mode_config = FIRE_MODES.get(mode, {})
                sl_mult = float(mode_config.get('sl_atr_mult', 0.0) or 0.0)
                tick_value = float(sym_info.trade_tick_value or 0.0)
                tick_size = float(sym_info.trade_tick_size or 0.0)
                if sl_mult > 0 and tick_value > 0 and tick_size > 0:
                    symbol_cap = mode_cap_map.get(symbol, mode_cap_map['DEFAULT']) * scale_factor
                    slippage_mult = float(MODE_ADVERSE_DOLLAR_CAP_SLIPPAGE_MULT.get(mode, 2.0) or 2.0)
                    sl_distance = float(atr) * sl_mult
                    adverse_ticks = (sl_distance * slippage_mult) / tick_size
                    adverse_dollar_per_lot = adverse_ticks * tick_value
                    if adverse_dollar_per_lot > 0:
                        max_lot_by_cap = symbol_cap / adverse_dollar_per_lot
                        clamped = min(clamped, max_lot_by_cap)
            except Exception:
                pass

        if lot_step > 0:
            clamped = round(round(clamped / lot_step) * lot_step, 2)
        clamped = max(min_lot, min(hard_cap, clamped))
        return round(clamped, 2)
    except Exception:
        return 0.01

def calc_sl_tp_prices(symbol, signal, price, atr, mode):
    """Calculate SL/TP using ATR and proper symbol info"""
    try:
        sym_info = mt5.symbol_info(symbol)
        if not sym_info:
            return 0, 0

        mode_config = FIRE_MODES[mode]
        sl_mult = mode_config['sl_atr_mult']
        # index volatility buffer (higher gamma = more air)
        if any(idx in symbol for idx in ['NAS100', 'SPX500', 'JPN225']):
            sl_mult *= 1.25
        sl_distance = atr * sl_mult
        tp_distance = atr * mode_config['tp_atr_mult']

        # Ensure minimum distance (at least 2x spread)
        spread = abs(sym_info.ask - sym_info.bid) if hasattr(sym_info, 'ask') else 0
        if spread == 0:
            tick = mt5.symbol_info_tick(symbol)
            if tick:
                spread = abs(tick.ask - tick.bid)

        min_distance = spread * 3  # At least 3x spread
        # Ensure minimum stop distance in points to avoid spread-eaten stops
        min_stop_points = sym_info.trade_stops_level if sym_info.trade_stops_level > 0 else 10
        min_stop_distance = min_stop_points * sym_info.point
        sl_distance = max(sl_distance, min_distance, min_stop_distance)
        tp_distance = max(tp_distance, min_distance * 2)

        # Apply stops distance limit from broker with safety buffer
        stops_level_points = sym_info.trade_stops_level if sym_info.trade_stops_level > 0 else 10
        stops_level = (stops_level_points + 5) * sym_info.point
        sl_distance = max(sl_distance, stops_level, min_distance)
        tp_distance = max(tp_distance, stops_level, min_distance * 2)

        digits = sym_info.digits

        if signal == 'BUY':
            sl_price = round(price - sl_distance, digits)
            tp_price = round(price + tp_distance, digits)
        else:
            sl_price = round(price + sl_distance, digits)
            tp_price = round(price - tp_distance, digits)

        return sl_price, tp_price

    except Exception as e:
        return 0, 0

def check_margin_safety(symbol, requested_lot, signal):
    """
    Check if opening this lot size would consume too much margin.
    Returns (safe_lot, margin_ok) where safe_lot may be scaled down.
    
    For exotics with high margin requirements, we scale lot down to
    preserve free_margin_ratio above CRITICAL_MARGIN_DERISK_TRIGGER_RATIO.
    """
    try:
        sym_info = mt5.symbol_info(symbol)
        if not sym_info:
            return requested_lot, True
        
        # Query margin required for this lot
        order_type = mt5.ORDER_TYPE_BUY if signal == 'BUY' else mt5.ORDER_TYPE_SELL
        price = mt5.symbol_info_tick(symbol).ask if signal == 'BUY' else mt5.symbol_info_tick(symbol).bid
        
        margin_check = mt5.order_calc_margin(order_type, symbol, requested_lot, price)
        if margin_check is None or margin_check <= 0:
            # Can't determine margin, allow entry at reduced size for safety
            return requested_lot * 0.5, True
        
        # Get current account state
        acct = mt5.account_info()
        if not acct:
            return requested_lot, True
        
        current_free_margin = float(acct.margin_free)
        current_equity = float(acct.equity)
        
        # Calculate projected free margin after entry
        projected_free_margin = current_free_margin - margin_check
        projected_free_margin_ratio = projected_free_margin / current_equity if current_equity > 0 else 0
        
        # Keep projected free margin above the active no-add floor, not just the derisk trigger.
        min_safe_free_margin_ratio = max(
            CRITICAL_MARGIN_NO_ADD_RATIO,
            CRITICAL_MARGIN_DERISK_TRIGGER_RATIO + 0.05,
        )
        
        if projected_free_margin_ratio >= min_safe_free_margin_ratio:
            # Safe to enter at requested size
            return requested_lot, True
        
        # Scale down lot to fit margin
        if margin_check > 0:
            # Calculate how much margin we can afford to use
            max_margin_use = current_free_margin - (min_safe_free_margin_ratio * current_equity)
            if max_margin_use <= 0:
                # No margin available, skip entry
                return 0, False
            
            # Scale lot proportionally
            safe_lot = requested_lot * (max_margin_use / margin_check)
            safe_lot = max(sym_info.volume_min, min(safe_lot, requested_lot))
            safe_lot = round(safe_lot / sym_info.volume_step) * sym_info.volume_step
            safe_lot = round(safe_lot, 2)
            
            if safe_lot < sym_info.volume_min:
                return 0, False
            
            return safe_lot, True
        
        return requested_lot * 0.3, True  # Conservative fallback
        
    except Exception as e:
        # On error, use conservative scaling
        return requested_lot * 0.5, True


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

# ============================================================
# POSITION MANAGEMENT
# ============================================================

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

# ============================================================
# PYRAMIDING - Add to winners
# ============================================================

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

# ============================================================
# MAIN LOOP
# ============================================================

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

if __name__ == "__main__":
    try:
        allowed, launch_reason = canonical_launch_allowed()
        if not allowed:
            log(f"REFUSING standalone launch: {launch_reason}")
            write_worker_state(
                "refused",
                "standalone_refused",
                launch_reason,
                "worker requires mt5_bot.py supervisor or explicit override",
                exit_code=2,
                state_file=WORKER_REFUSAL_STATE_FILE,
            )
            raise SystemExit(2)
        run()
    except KeyboardInterrupt:
        write_worker_state("stopped", "keyboard_interrupt", "top-level keyboard interrupt", "worker interrupted before clean shutdown")
        raise
    except Exception as exc:
        write_worker_state("crashed", "fatal_exception", str(exc), traceback.format_exc(), exit_code=1)
        raise

