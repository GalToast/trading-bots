"""Tuning constants for the MT5 worker (extracted verbatim from mt5_bot_v10)."""
from __future__ import annotations
import os

# Repo-root anchor: path constants that lived next to mt5_bot_v10.py before
# extraction. This module now sits two levels deeper (bot/mt5/).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CANONICAL_SUPERVISOR_ENV = "CANONICAL_MT5_SUPERVISOR"

ALLOW_STANDALONE_ENV = "ALLOW_STANDALONE_MT5_WORKER"

DISABLED_MODES = {'MACHINE_GUN', 'PRICE', 'GEMINI', 'REVERSION'}

MAX_TRADES_PER_DAY = 50  # Hard cap — 670 trades/day was bleeding $11K

MAX_DAILY_LOSS_USD = 500.0  # Circuit breaker: stop all entries after $500 daily loss

MAX_LOT_SIZE = 1.0  # Hard safety cap to prevent size explosions (like the 10-lot leak)

MSLS_ENABLED = False  # Set to True once the team approves the probationary run

MSLS_PROBATIONARY_LOT = 0.01

MSLS_MIN_GREEN_RATE = 0.90  # Only trade symbols with 90%+ immediate profit rate

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

SYMBOL_SIGNAL_WHITELIST = {
    # Top performers (min 3 trades, 45%+ WR, positive net P/L):
    ('USDCHF', 'candle_direction'),           # 28 trades, 64.3% WR, $+360.86, payoff=125
}

EXPERIMENTAL_PRIORITY_LANES = {
    ('USDJPY', 'breakout_hold_above_high', 'SNIPER', 'PRICE'),  # fresh realized sample: 21 trades, +$1.77
}

BRAIN_COOLDOWN_PRIORITY_LANE_MIN_CONFIDENCE = 0.70

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

ASIAN_BLOCKLIST = set()

NEW_YORK_BLOCKLIST = set()

OFF_SESSION_HOURS = set()

OFF_SESSION_ALLOWLIST = {
    'US30',      # +$1,372 off-session, 46% WR
    # JPN225 removed: -$214 off-session (was +$235 Asian-only, different window)
    'AUDCHF',    # +$298 off-session, 55% WR
    'NAS100',    # +$341 off-session
    'GBPAUD',    # +$349 off-session
    'NZDCAD',    # +$363 off-session, 50% WR
    'EURHKD',    # +$735 off-session
}

OFF_SESSION_SIGNAL_ALLOWLIST = {
    'breakout_hold_below_low',    # +$8.49 avg
    'breakout_hold_above_high',   # -$2.10 avg (manageable)
    'unlabeled',                   # -$5.95 avg but 36% WR at scale
    'asian_range_buy',             # Asian session mean-reversion BUY
    'asian_range_sell',            # Asian session mean-reversion SELL
}

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

MAX_SYMBOLS_TO_TRADE = 100       # Trade all available symbols

MAX_SPREAD_PCT_FOREX = 0.04

MAX_SPREAD_PCT_CRYPTO = 0.12

MAX_SPREAD_PCT_EXOTIC = 0.08

MAX_POSITIONS_PER_SYMBOL = 5    # Max 5 positions per symbol — allow pyramiding to work

MIN_CONFIDENCE_BASE = 0.58    # Restored from 0.50 — low floor let garbage through off-session

MIN_CONFIDENCE_MIN = 0.55     # Restored from 0.45 — quality over quantity

ALLEYWAY_ENABLED = True       # Enable dynamic threshold relaxation

RISK_GUARD_PCT = 0.40           # Only stop at 40% — competition mode, fight to the end

CHECK_INTERVAL = 2              # Ultra-fast cycles

RISK_PER_TRADE = {
    'SNIPER':      0.08,   # Risk 8% of equity per SNIPER (compounding)
    'SHOTGUN':     0.01,   # Risk 1% per SHOTGUN (conservative - was 5%)
    'MACHINE_GUN': 0.015,  # Risk 1.5% per MACHINE_GUN (slightly up from 1%)
    'REVERSION':   0.04,   # Risk 4% per mean-reversion trade
    'PRICE':       0.04,   # Isolated raw-price thesis lane (was 5%)
    'RAW':         0.04,
    'GEMINI':      0.05,    # Pure price action lane
}

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

PYRAMID_ENABLED = True

PYRAMID_MIN_PROFIT_ATR = 0.5    # Raised from 0.2 — only add after position proves beyond where 86% fail

PYRAMID_MIN_PROFIT_USD = 5     # Lower gate for rapid compounding

PYRAMID_MAX_ADDS = 5            # Allow 5 adds

PYRAMID_LOT_DECAY = 0.7         # Lowered from 1.0 — exponential decay caps max exposure at 2.77x

MAX_CONCURRENT_POSITIONS = 30   # Cap at 30 — let positions resolve before reloading

ADOPTED_BOOK_REARM_FREEZE_THRESHOLD = 8  # Freeze fresh REARM while inherited book is > 8 positions

MAX_SYMBOL_EXPOSURE_PCT = 0.30  # Max 30% of equity risked per symbol at any time

MAX_SINGLE_TRADE_LOSS_USD = 200.0  # Hard stop: no single trade loses more than $200

UNIVERSAL_ATR_HARD_STOP = 1.5  # Max adverse excursion before hard cut

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

EXOTIC_AVG_LOSS_THRESHOLD = 500.0  # $500 avg loss triggers floor (matched to daily loss limit)

EXOTIC_LOT_FLOOR = 0.01  # Minimum lot for bleeding symbols

EXOTIC_SPREAD_MULTIPLIER = 0.33  # Exotics use 1/3 of normal max_spread

SPREAD_VS_STOP_MAX_RATIO = 0.30  # If spread eats > 30% of stop, skip (was 0.45)

LOSS_STREAK_COOLDOWN_THRESHOLD = 3  # Cooldown after 3 consecutive losses on symbol

LOSS_STREAK_COOLDOWN_MINUTES = 30    # 30-minute cooldown per symbol

LOSS_COOLDOWN_MINUTES = 15           # Per-symbol cooldown after ANY loss (not just streak)

MARKET_CLOSED_SYMBOL_COOLDOWN_SECONDS = 300  # Avoid burning repeated entry attempts on closed venues

MARKET_CLOSED_SYMBOL_LOG_COOLDOWN_SECONDS = 60

INSUFFICIENT_MARGIN_SYMBOL_COOLDOWN_SECONDS = 120  # Back off a symbol briefly after broker rejects entry for no money.

BROKER_CONNECTION_BACKOFF_SECONDS = 45  # Pause new entries briefly after broker/network order rejects.

BROKER_CONNECTION_BACKOFF_LOG_COOLDOWN_SECONDS = 15

SESSION_LONDON = (7, 16)

SESSION_NY = (12, 21)

SESSION_OVERLAP = (12, 16)

SESSION_ASIAN = (23, 8)

ASIAN_SESSION_MIN_CONFIDENCE = 0.70

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

REARM_REBUILD_CAP_MIXED_BOOK_BLOCK = False

REARM_THRESHOLD_RELAXATION = 0.15        # Relax entry thresholds even more for 10x

REARM_STRESS_RELIEF = 0.60              # Even more aggressive relief

REARM_EXTRA_ENTRY_SLOTS = 5             # Allow 5 extra concurrent entries during REARM

REARM_NONFLAT_ENTRY_CYCLE_CAP = 10      # COMPETITION: Allow 10 trades per cycle for compounding (was 2)

REARM_SAME_SYMBOL_ENTRY_CYCLE_CAP = 1   # Do not stack repeated fresh opens on the same symbol in one scan

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

DEFEND_TWO_BOOK_MIXED_CLEANUP_MIN_POSITIONS = 2

DEFEND_TWO_BOOK_MIXED_CLEANUP_MAX_POSITIONS = 2

DEFEND_TWO_BOOK_MIXED_CLEANUP_MIN_FREE_MARGIN_RATIO = 0.32

DEFEND_TWO_BOOK_MIXED_CLEANUP_MIN_BLOCKED_DEFEND_LOADED = 8

DEFEND_TWO_BOOK_MIXED_CLEANUP_MIN_IDLE_CYCLES = 24

DEFEND_TWO_BOOK_MIXED_CLEANUP_TRIGGER_CYCLES = 8

DEFEND_TWO_BOOK_MIXED_CLEANUP_COOLDOWN_SECONDS = 180

DEFEND_TWO_BOOK_MIXED_CLEANUP_MAX_TOTAL_LOSS = 20.00

DEFEND_TWO_BOOK_MIXED_CLEANUP_MAX_SINGLE_LOSS = 10.00

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

ONE_POSITION_REARM_MIN_GREEN_PNL_USD = 0.10

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

TRIM_COOLDOWN_SECONDS = 120     # Don't re-enter a symbol for 2 min after stress trim

CACHE_TTL = 30    # seconds — raised from 15s to cover full multi-symbol scan cycle without mid-cycle expiry

ADOPT_EXISTING_POSITIONS = True

RUNTIME_STATE_FILE = os.path.join(REPO_ROOT, "runtime_state.json")

WORKER_STATE_FILE = os.path.join(REPO_ROOT, "canonical_worker_state.json")

WORKER_REFUSAL_STATE_FILE = os.path.join(REPO_ROOT, "canonical_worker_refusal_state.json")

TRADE_BEHAVIOR_LOG_FILE = os.path.join(REPO_ROOT, "trade_behavior_log.jsonl")

PRICE_CANDIDATE_LOG_FILE = os.path.join(REPO_ROOT, "price_candidate_log.jsonl")

BLOCKED_QUALITY_CANDIDATE_LOG_FILE = os.path.join(
    REPO_ROOT, "blocked_quality_candidates.jsonl"
)

STRATEGY_LAB_LOG_FILE = os.path.join(
    REPO_ROOT, "strategy_lab_events.jsonl"
)

LATTICE_LIVE_IGNORE_COMMENT_PREFIX = "PLIVE-LATTICE"

COMPETITION_LANE_NAMES = ("PRICE", "RAW", "GEMINI")

COMPETITION_LANE_SCORECARD_LIMIT = 60

COMPETITION_LANE_CLUSTER_WINDOW = 6

COMPETITION_LANE_CLUSTER_MAX_AGE_SECONDS = 20 * 60

COMPETITION_LANE_CLUSTER_MIN_EARLY_FAILS = 3

COMPETITION_LANE_CLUSTER_BRAKE_MIN_CONFIDENCE = 0.70

RAW_CANDLE_DIRECTION_MIN_CONFIDENCE = 0.60
