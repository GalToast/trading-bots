"""Position-state helpers."""
from __future__ import annotations
from .config import *  # noqa: F401,F403
from .state import active_positions  # noqa: F401
from mt5_config import BOT_COMMENT_PREFIX
from mt5_config import BOT_MAGIC
import MetaTrader5 as mt5
import time
from .indicators import calc_atr
from .market_data import get_bars


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

def get_position_lane(pdata):
    lane = str((pdata or {}).get('entry_regime', '') or '').upper()
    if lane == 'UNKNOWN' or not lane:
        lane = str((pdata or {}).get('mode', '') or '').upper()
    return lane if lane else 'UNKNOWN'

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
