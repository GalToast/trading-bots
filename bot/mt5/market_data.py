"""MT5 connection, bar/tick caches and broker-state markers."""
from __future__ import annotations
from .config import *  # noqa: F401,F403
from . import state
from .state import _bars_cache, _tick_cache, alleyway_state  # noqa: F401
from brain import TradingBrain
from mt5_config import LOGIN
from mt5_config import PASSWORD
from mt5_config import SERVER
from symbol_learner import SymbolLearner
import MetaTrader5 as mt5
import time
from .log import log


def connect_mt5():
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
                    state.mt5_connected = True
                    log(f"Connected to MT5 account {int(info.login)} (attempt {attempt + 1})")
                    return True
            else:
                log(f"MT5 init failed (attempt {attempt + 1}): {mt5.last_error()}")
        except Exception as e:
            log(f"MT5 connection error (attempt {attempt + 1}): {e}")
        time.sleep(3)
    state.mt5_connected = False
    return False

def ensure_mt5():
    if not state.mt5_connected:
        return connect_mt5()
    try:
        info = mt5.account_info()
        if info is None:
            raise ConnectionError("account_info returned None")
        if int(getattr(info, "login", 0) or 0) != LOGIN:
            log(f"ACCOUNT_MISMATCH detected: terminal={getattr(info, 'login', None)} expected={LOGIN}, reconnecting...")
            state.mt5_connected = False
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
        state.mt5_connected = False
        return connect_mt5()

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
    if current_cycle > 0 and current_cycle == state._tick_cache_cycle and symbol in _tick_cache:
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
    _tick_cache.clear()
    state._tick_cache_cycle = current_cycle
    
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

def get_broker_connection_backoff_remaining(now=None):
    if now is None:
        now = time.time()
    until = float(alleyway_state.get("broker_connection_backoff_until", 0.0) or 0.0)
    if until <= now:
        if until > 0.0:
            alleyway_state["broker_connection_backoff_until"] = 0.0
        return None
    return max(0.0, until - now)

def get_brain():
    if state._brain is None:
        state._brain = TradingBrain()
    return state._brain

def get_learner():
    if state._learner is None:
        state._learner = SymbolLearner()
    return state._learner
