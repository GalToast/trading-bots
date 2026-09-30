"""Risk measurement: stress, sizing, margin safety (no order execution)."""
from __future__ import annotations
from .config import *  # noqa: F401,F403
from .state import active_positions, alleyway_state, equity_peak, recent_risk_events  # noqa: F401
from collections.abc import Mapping
import MetaTrader5 as mt5
import json
import os
import time
from .indicators import calc_atr
from .market_data import get_bars
from .sessions import is_overlap_session


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
            brain_file = os.path.join(REPO_ROOT, "brain.json")
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
