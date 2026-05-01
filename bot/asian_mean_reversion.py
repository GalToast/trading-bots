"""
ASIAN SESSION MEAN-REVERSION SIGNAL
For low-volatility overnight markets (00:00-07:59 UTC)
Focus: US30, JPN225 - indices that gap overnight

Strategy:
- Buy at range lows, sell at range highs
- Use Bollinger Band reversion for range detection
- Require range-bound confirmation (ADX < 25)
- No trend following - fade the edges
"""

def get_asian_mean_reversion_signal(
    *,
    symbol,
    timeframe_m5,
    get_bars,
    calc_atr,
    calc_ema,
    calc_rsi,
    calc_adx=None,
    calc_bollinger=None,
):
    """
    Mean-reversion signal for Asian session indices.
    Returns: (signal, confidence, atr, regime, signal_type)
    
    Works on: US30, JPN225 during 00:00-07:59 UTC
    """
    # Only trade indices during Asian
    ASIAN_SYMBOLS = {"US30", "JPN225", "NAS100", "SPX500"}
    if symbol not in ASIAN_SYMBOLS:
        return None, 0.0, 0, None, None
    
    bars = get_bars(symbol, timeframe_m5, 50)
    if len(bars) < 30:
        return None, 0.0, 0, None, None
    
    closes = [b["c"] for b in bars]
    highs = [b["h"] for b in bars]
    lows = [b["l"] for b in bars]
    
    atr = calc_atr(bars, 14)
    if atr <= 0:
        return None, 0.0, 0, None, None
    
    rsi = calc_rsi(closes, 14)
    
    # Bollinger Bands for range detection
    period = 20
    sma = sum(closes[-period:]) / period
    variance = sum((c - sma) ** 2 for c in closes[-period:]) / period
    std = variance ** 0.5
    bb_upper = sma + 2 * std
    bb_lower = sma - 2 * std
    bb_width = (bb_upper - bb_lower) / sma  # Normalized width
    
    # Range-bound check: BB width must be narrow (no trending)
    # Typical BB width for ranging: < 0.5%
    is_ranging = bb_width < 0.005
    
    # ADX for trend strength (if available)
    adx = None
    if calc_adx is not None:
        adx = calc_adx(bars, 14)
        is_ranging = is_ranging or (adx is not None and adx < 25)
    
    if not is_ranging:
        # Market is trending - don't mean-revert
        return None, 0.0, atr, None, None
    
    current_price = closes[-1]
    prev_close = closes[-2]
    
    # Distance from bands
    dist_to_upper = (bb_upper - current_price) / atr
    dist_to_lower = (current_price - bb_lower) / atr
    
    signal = None
    confidence = 0.0
    signal_type = None
    
    # === BUY at lower band (oversold in range) ===
    # Price must be near or below lower band
    if dist_to_lower < 0.5:  # Within 0.5 ATR of lower band
        # RSI must be oversold
        if rsi < 35:
            signal = "BUY"
            confidence = 0.72 + (35 - rsi) / 100  # 0.72-0.85
            signal_type = "asian_range_buy"
            
            # Extra confirmation: price bounced off low
            if current_price > prev_close and lows[-1] <= bb_lower:
                confidence += 0.05  # Bounce confirmation
    
    # === SELL at upper band (overbought in range) ===
    # Price must be near or above upper band
    elif dist_to_upper < 0.5:  # Within 0.5 ATR of upper band
        # RSI must be overbought
        if rsi > 65:
            signal = "SELL"
            confidence = 0.72 + (rsi - 65) / 100  # 0.72-0.85
            signal_type = "asian_range_sell"
            
            # Extra confirmation: price rejected off high
            if current_price < prev_close and highs[-1] >= bb_upper:
                confidence += 0.05  # Rejection confirmation
    
    return signal, min(confidence, 0.90), atr, "ASIAN_REVERSION", signal_type
