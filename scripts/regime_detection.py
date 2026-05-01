#!/usr/bin/env python3
"""
Regime Detection Module — For Omni-VIP-Fortress
Plugs into Predatory Logic Engine alongside Kraken-Lag and Gulp-Shield.

Returns a tradeability score (0-100) based on:
1. ATR% — Is volatility high enough to clear fees?
2. BTC correlation — Is the coin moving independently or just following BTC?
3. Volume ratio — Is there actual liquidity or are we trading against ourselves?
4. Trend regime (ADX) — Is the coin ranging (good for RSI MR) or trending (bad)?

API:
    score = regime_score(candles, btc_candles)
    # Returns 0-100
    # >70 = HIGH confidence (deploy full size)
    # 40-70 = MEDIUM confidence (deploy half size)  
    # <40 = LOW confidence (skip)
"""
import statistics
import math
from datetime import datetime, timezone


def regime_score(candles: list[dict], btc_candles: list[dict], 
                 atr_period: int = 14, adx_period: int = 14, 
                 volume_lookback: int = 20) -> dict:
    """
    Compute regime score for a coin.
    
    Args:
        candles: List of candle dicts with keys: start, open, high, low, close, volume
        btc_candles: List of BTC candle dicts (same format), aligned by time
        atr_period: Period for ATR calculation
        adx_period: Period for ADX calculation
        volume_lookback: Number of candles for volume average
    
    Returns:
        dict with keys:
            score: 0-100 tradeability score
            atr_pct: Current ATR as % of price
            btc_corr: Correlation with BTC (-1 to 1)
            volume_ratio: Current volume / average volume
            adx: ADX value (trend strength)
            components: dict with individual component scores
    """
    if len(candles) < max(atr_period, adx_period, volume_lookback) + 5:
        return {"score": 0, "reason": "insufficient_data",
                "atr_pct": 0, "btc_corr": 0, "volume_ratio": 0, "adx": 0}
    
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    volumes = [float(c["volume"]) for c in candles]
    
    # 1. ATR% Component (0-25 points)
    atr_pct = _compute_atr_pct(highs, lows, closes, atr_period)
    if atr_pct >= 4.0:
        atr_score = 25
    elif atr_pct >= 3.0:
        atr_score = 20
    elif atr_pct >= 2.0:
        atr_score = 15
    elif atr_pct >= 1.5:
        atr_score = 10
    elif atr_pct >= 1.0:
        atr_score = 5
    else:
        atr_score = 0
    
    # 2. BTC Correlation Component (0-25 points)
    btc_corr = _compute_correlation(closes, btc_candles, candles)
    # Low correlation is better for RSI MR (coin moves independently)
    if abs(btc_corr) < 0.1:
        corr_score = 25
    elif abs(btc_corr) < 0.2:
        corr_score = 20
    elif abs(btc_corr) < 0.3:
        corr_score = 15
    elif abs(btc_corr) < 0.5:
        corr_score = 10
    elif abs(btc_corr) < 0.7:
        corr_score = 5
    else:
        corr_score = 0
    
    # 3. Volume Ratio Component (0-25 points)
    if len(volumes) >= volume_lookback:
        recent_vol = statistics.mean(volumes[-5:])
        avg_vol = statistics.mean(volumes[-volume_lookback:])
        vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0
    else:
        vol_ratio = 1.0
    
    if vol_ratio >= 2.0:
        vol_score = 25
    elif vol_ratio >= 1.5:
        vol_score = 20
    elif vol_ratio >= 1.0:
        vol_score = 15
    elif vol_ratio >= 0.5:
        vol_score = 10
    else:
        vol_score = 5
    
    # 4. ADX Component (0-25 points)
    adx = _compute_adx(highs, lows, closes, adx_period)
    # Low ADX (ranging) is better for RSI MR
    if adx < 15:
        adx_score = 25
    elif adx < 20:
        adx_score = 20
    elif adx < 25:
        adx_score = 15
    elif adx < 30:
        adx_score = 10
    elif adx < 40:
        adx_score = 5
    else:
        adx_score = 0
    
    # Total score
    total_score = atr_score + corr_score + vol_score + adx_score
    
    return {
        "score": total_score,
        "atr_pct": round(atr_pct, 2),
        "btc_corr": round(btc_corr, 3),
        "volume_ratio": round(vol_ratio, 2),
        "adx": round(adx, 1),
        "components": {
            "atr_score": atr_score,
            "corr_score": corr_score,
            "vol_score": vol_score,
            "adx_score": adx_score,
        },
        "recommendation": _recommendation(total_score),
    }


def _recommendation(score: int) -> str:
    if score >= 70:
        return "HIGH — Deploy full size"
    elif score >= 40:
        return "MEDIUM — Deploy half size"
    else:
        return "LOW — Skip"


def _compute_atr_pct(highs: list[float], lows: list[float], 
                     closes: list[float], period: int) -> float:
    """Compute ATR as % of average price."""
    if len(highs) < period + 1:
        return 0.0
    
    trs = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        trs.append(tr)
    
    if len(trs) < period:
        return 0.0
    
    atr = statistics.mean(trs[-period:])
    avg_price = statistics.mean(closes[-period:])
    return (atr / avg_price * 100) if avg_price > 0 else 0.0


def _compute_correlation(closes: list[float], btc_candles: list[dict], 
                         candles: list[dict]) -> float:
    """Compute correlation between coin returns and BTC returns."""
    if len(closes) < 10 or len(btc_candles) < 10:
        return 0.0
    
    # Build BTC lookup by timestamp
    btc_lookup = {int(c["start"]): float(c["close"]) for c in btc_candles}
    
    # Compute returns for both, matching timestamps
    coin_returns = []
    btc_returns = []
    
    for i in range(1, len(closes)):
        ts = int(candles[i]["start"])
        prev_ts = int(candles[i-1]["start"])
        if ts in btc_lookup and prev_ts in btc_lookup:
            coin_ret = (closes[i] - closes[i-1]) / closes[i-1]
            btc_ret = (btc_lookup[ts] - btc_lookup[prev_ts]) / btc_lookup[prev_ts]
            # Sanity check: clip extreme returns
            if abs(coin_ret) < 0.5 and abs(btc_ret) < 0.5:
                coin_returns.append(coin_ret)
                btc_returns.append(btc_ret)
    
    if len(coin_returns) < 10:
        return 0.0
    
    # Pearson correlation
    mean_coin = statistics.mean(coin_returns)
    mean_btc = statistics.mean(btc_returns)
    
    cov = sum((c - mean_coin) * (b - mean_btc) for c, b in zip(coin_returns, btc_returns))
    var_coin = sum((c - mean_coin) ** 2 for c in coin_returns)
    var_btc = sum((b - mean_btc) ** 2 for b in btc_returns)
    
    denom = math.sqrt(var_coin * var_btc)
    if denom > 0:
        corr = cov / denom
        return max(-1.0, min(1.0, corr))  # Clamp to [-1, 1]
    return 0.0


def _compute_adx(highs: list[float], lows: list[float], 
                 closes: list[float], period: int) -> float:
    """Compute ADX (Average Directional Index)."""
    if len(highs) < period * 2:
        return 0.0
    
    # Compute +DM, -DM, and TR
    plus_dm = []
    minus_dm = []
    trs = []
    
    for i in range(1, len(highs)):
        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]
        
        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0)
        
        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0)
        
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        trs.append(tr)
    
    # Smooth with Wilder's method
    def wilder_smooth(values: list[float], period: int) -> float:
        if len(values) < period:
            return 0
        result = sum(values[:period]) / period
        for i in range(period, len(values)):
            result = (result * (period - 1) + values[i]) / period
        return result
    
    atr = wilder_smooth(trs, period)
    if atr == 0:
        return 0.0
    
    plus_di = 100 * wilder_smooth(plus_dm, period) / atr
    minus_di = 100 * wilder_smooth(minus_dm, period) / atr
    
    if plus_di + minus_di == 0:
        return 0.0
    
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    return dx


# Quick test
if __name__ == "__main__":
    # Test with synthetic data
    import random
    random.seed(42)
    
    base_price = 2.0
    candles = []
    btc_candles = []
    
    for i in range(100):
        ts = 1700000000 + i * 300
        # Simulate a volatile coin
        change = random.gauss(0, 0.02)
        base_price *= (1 + change)
        h = base_price * (1 + abs(random.gauss(0, 0.01)))
        l = base_price * (1 - abs(random.gauss(0, 0.01)))
        o = base_price * (1 + random.gauss(0, 0.005))
        v = random.uniform(1000, 5000)
        candles.append({
            "start": ts, "open": o, "high": h, "low": l, 
            "close": base_price, "volume": v
        })
        
        # BTC moves independently
        btc_change = random.gauss(0, 0.01)
        btc_price = 80000 * (1 + btc_change)
        btc_candles.append({
            "start": ts, "open": btc_price * 0.999, 
            "high": btc_price * 1.001, "low": btc_price * 0.998,
            "close": btc_price, "volume": random.uniform(100, 500)
        })
    
    result = regime_score(candles, btc_candles)
    print(f"Score: {result['score']}/100")
    print(f"ATR%: {result['atr_pct']}")
    print(f"BTC corr: {result['btc_corr']}")
    print(f"Volume ratio: {result['volume_ratio']}")
    print(f"ADX: {result['adx']}")
    print(f"Recommendation: {result['recommendation']}")
    print(f"Components: {result['components']}")
