"""
GEMINI_V2: Micro-Structure Liquidity Sweep (MSLS) & Mean Reversion Engine
========================================================================
Refined SMC/ICT validation: High Displacement + Volume Surge + Wick Rejection.
Optimized for US30/JPN225 Mean-Reversion during low-volatility sessions.
Integrates Qwen's 30-Second Rule & Volatility-Adjusted Stop (VAS).
"""
import math
import time
from datetime import datetime, timezone

def is_asian_session():
    """Asian session window: 00:00 - 07:59 UTC"""
    utc_hour = datetime.now(timezone.utc).hour
    return 0 <= utc_hour < 8

def detect_msls_signal(bars, lookback=40, body_multiplier=2.0, vol_multiplier=1.2, min_wick_ratio=0.35):
    """
    Detects a Liquidity Sweep followed by High-Impact Displacement (FVG).
    Includes Wick Rejection validation for true mean-reversion.
    """
    if len(bars) < lookback + 3:
        return None, 0.0, 0.0, None
        
    current_bar = bars[-1] # Potential entry
    disp_bar = bars[-2]    # The "Displacement" candle
    sweep_bar = bars[-3]   # The "Sweep" candle
    prev_bar = bars[-4]    # The bar BEFORE the sweep
    
    # 1. SMC Metrics (Body & Volume)
    historical_bars = bars[-(lookback+4):-4]
    avg_body = sum(abs(b['c'] - b['o']) for b in historical_bars) / lookback
    avg_vol = sum(b['v'] for b in historical_bars) / lookback
    
    # 2. Rolling Range for Sweep Detection
    range_high = max(b['h'] for b in historical_bars)
    range_low = min(b['l'] for b in historical_bars)
    
    # 3. Displacement & Wick Validation
    disp_body = abs(disp_bar['c'] - disp_bar['o'])
    is_displacement = (disp_body > avg_body * body_multiplier) and (disp_bar['v'] > avg_vol * vol_multiplier)
    
    # Calculate Wick Ratio for the Sweep bar
    sweep_range = sweep_bar['h'] - sweep_bar['l']
    wick_ratio = 0
    if sweep_range > 0:
        if disp_bar['c'] > disp_bar['o']: # Bullish reversal, need lower wick
            wick_ratio = (min(sweep_bar['o'], sweep_bar['c']) - sweep_bar['l']) / sweep_range
        else: # Bearish reversal, need upper wick
            wick_ratio = (sweep_bar['h'] - max(sweep_bar['o'], sweep_bar['c'])) / sweep_range

    if not is_displacement or wick_ratio < min_wick_ratio:
        return None, 0.0, 0.0, None

    # Calculate ATR for VAS
    atr_period = min(60, len(bars) - 1)
    tr_sum = 0
    for i in range(len(bars) - atr_period, len(bars)):
        tr = max(bars[i]['h'] - bars[i]['l'], 
                 abs(bars[i]['h'] - bars[i-1]['c']), 
                 abs(bars[i]['l'] - bars[i-1]['c']))
        tr_sum += tr
    atr = tr_sum / atr_period

    # =========================================================
    # BULLISH REVERSAL (Sweeping Lows)
    # =========================================================
    if sweep_bar['l'] < range_low:
        if prev_bar['h'] < current_bar['l'] and disp_bar['c'] > disp_bar['o']:
            confidence = 0.95
            stop_loss = sweep_bar['l'] - (atr * 0.1)
            return "BUY", confidence, stop_loss, "MSLS_SMC_BULL_REVERSION"

    # =========================================================
    # BEARISH REVERSAL (Sweeping Highs)
    # =========================================================
    if sweep_bar['h'] > range_high:
        if prev_bar['l'] > current_bar['h'] and disp_bar['c'] < disp_bar['o']:
            confidence = 0.95
            stop_loss = sweep_bar['h'] + (atr * 0.1)
            return "SELL", confidence, stop_loss, "MSLS_SMC_BEAR_REVERSION"
                        
    return None, 0.0, 0.0, None

def get_msls_edge_signal(symbol, get_bars):
    """Entry point for the bot architecture."""
    # Special parameters for Asian Session Indices
    is_asian = is_asian_session()
    is_target_index = symbol in ['US30', 'JPN225', 'NAS100']
    
    # Tighten requirements for off-session unless it's a target index
    body_mult = 2.0
    if is_asian and is_target_index:
        body_mult = 1.5 # Relax displacement for ranging indices
        
    bars_m1 = get_bars(symbol, "1Min", 100)
    if not bars_m1:
        return None, 0.0, 0.0, None, None, None
        
    signal, conf, sl, thesis = detect_msls_signal(bars_m1, body_multiplier=body_mult)
    
    if signal and conf >= 0.90:
        metadata = {
            "entry_time": time.time(),
            "rapid_fail_seconds": 30,
            "must_be_green_by": time.time() + 30,
            "session": "ASIAN" if is_asian else "MAIN"
        }
        return signal, conf, sl, thesis, "gemini_v2_msls", metadata
        
    return None, 0.0, 0.0, None, None, None
