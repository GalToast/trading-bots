"""Signal generation (momentum, regime, mean reversion, price action)."""
from __future__ import annotations
from .config import *  # noqa: F401,F403
from bot import asian_mean_reversion as asian_policy
from bot import gemini as gemini_policy
from bot import price as price_policy
import MetaTrader5 as mt5
from .indicators import calc_atr, calc_ema, calc_rsi
from .log import log
from .market_data import get_bars
from .sessions import is_asian_session, is_exotic
from .strategy_lab import build_lane_key, get_post_cleanup_raw_shotgun_min_confidence


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
