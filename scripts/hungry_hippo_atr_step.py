#!/usr/bin/env python3
"""
ATR-Scaled Step Function for HUNGRY HIPPO — Sprint 1
Replaces fixed step with ATR-relative computation.

Usage:
    python scripts/hungry_hippo_atr_step.py

Outputs:
    reports/hungry_hippo_atr_step_params.json — per-symbol ATR-scaled step params
"""
import MetaTrader5 as mt5
import json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "reports" / "hungry_hippo_atr_step_params.json"

# Empirical coefficients from the 8,201-close unified analysis
REGIME_COEFFICIENTS = {
    "STRONG_TREND": 1.5,
    "WEAK_TREND": 1.0,
    "TRANSITION": 0.8,
    "RANGE": 0.5,
}

# Session windows from symbol-specific analysis (UTC hours)
SESSION_WINDOWS = {
    "BTCUSD": {"active": [14, 15, 16, 17, 18, 19, 20], "off_weight": 0.4},
    "ETHUSD": {"active": [4, 5, 14, 15, 16, 17, 18, 19, 20], "off_weight": 0.6},
    "NAS100": {"active": [14, 15, 16, 17, 18, 19], "off_weight": 0.2},
    "US30": {"active": [14, 15, 16, 17, 18, 19], "off_weight": 0.2},
    "SOLUSD": {"active": [14, 15, 16, 17, 18, 19, 20], "off_weight": 0.3},
    "XRPUSD": {"active": [14, 15, 16, 17, 18, 19, 20], "off_weight": 0.5},
    "GBPUSD": {"active": [6, 7, 8, 9, 13, 14, 15, 16], "off_weight": 0.5},
    "EURUSD": {"active": [6, 7, 8, 9, 13, 14, 15, 16], "off_weight": 0.5},
    "NZDUSD": {"active": list(range(24)), "off_weight": 1.0},  # no gate
    "USDCHF": {"active": list(range(24)), "off_weight": 1.0},
    "USDCAD": {"active": list(range(24)), "off_weight": 1.0},
    "XAUUSD": {"active": [14, 15, 16, 17, 18, 19], "off_weight": 0.5},
    "DOGEUSD": {"active": [14, 15, 16, 17, 18, 19, 20], "off_weight": 0.3},
    "ADAUSD": {"active": [14, 15, 16, 17, 18, 19, 20], "off_weight": 0.3},
}

# Asymmetry ratios from unified close analysis (BUY/SELL profitability ratio)
ASYMMETRY_RATIOS = {
    "BTCUSD": 3.0,    # BUY 3x more profitable
    "ETHUSD": 1.4,    # Moderate BUY bias
    "GBPUSD": 2.0,    # BUY 2x more profitable  
    "EURUSD": 1.0,    # Symmetric
    "NZDUSD": 2.0,    # BUY 2x more profitable
    "NAS100": 1.0,    # Symmetric (trend)
    "US30": 1.0,      # Symmetric (trend)
    "XAUUSD": 1.0,    # Symmetric (trend)
    "USDJPY": 1.0,    # Symmetric
    "SOLUSD": 1.5,    # Moderate BUY bias
    "XRPUSD": 1.5,    # Moderate BUY bias
    "DOGEUSD": 1.0,   # Symmetric
    "ADAUSD": 1.0,    # Symmetric
    "USDCHF": 1.0,    # Symmetric
    "USDCAD": 1.0,    # Symmetric
}

def compute_atr(symbol, timeframe, bars=100):
    """Compute ATR for a symbol on given timeframe."""
    import numpy as np
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    if rates is None or len(rates) < 15:
        return None
    highs = rates['high']
    lows = rates['low']
    closes = rates['close']
    
    # True range = max(high-low, abs(high-prev_close), abs(low-prev_close))
    tr = highs[1:] - lows[1:]
    tr = np.maximum(tr, np.abs(highs[1:] - closes[:-1]))
    tr = np.maximum(tr, np.abs(lows[1:] - closes[:-1]))
    
    # ATR(14)
    atr_14 = np.mean(tr[-14:])
    return float(atr_14)

def compute_session_weight(symbol, current_hour_utc):
    """Compute session weight for current hour."""
    window = SESSION_WINDOWS.get(symbol, {"active": list(range(24)), "off_weight": 1.0})
    if current_hour_utc in window["active"]:
        return 1.0
    return window["off_weight"]

def compute_atr_scaled_step(symbol, atr_current, regime, current_hour_utc, base_timeframe="M15"):
    """
    Compute ATR-scaled step with session and asymmetry adjustments.
    
    Returns dict with:
        step: unified step (base)
        step_buy: buy step (wider if BUY is more profitable)
        step_sell: sell step (tighter if SELL is less profitable)
        regime_coeff: regime multiplier used
        session_weight: session multiplier used
        asymmetry_ratio: BUY:SELL ratio
    """
    from datetime import datetime
    if current_hour_utc is None:
        current_hour_utc = datetime.utcnow().hour
    
    regime_coeff = REGIME_COEFFICIENTS.get(regime, 1.0)
    session_weight = compute_session_weight(symbol, current_hour_utc)
    asym_ratio = ASYMMETRY_RATIOS.get(symbol, 1.0)
    
    # Base step = ATR * regime_coeff * session_weight
    base_step = atr_current * regime_coeff * session_weight
    
    # Asymmetric steps: BUY wider if asym_ratio > 1, SELL tighter
    # Total width preserved: step_buy + step_sell = 2 * base_step
    # Ratio: step_buy / step_sell = asym_ratio
    # Solving: step_sell = 2*base / (1+ratio), step_buy = ratio * step_sell
    if asym_ratio > 0:
        step_sell = (2 * base_step) / (1 + asym_ratio)
        step_buy = asym_ratio * step_sell
    else:
        step_buy = base_step
        step_sell = base_step
    
    return {
        "symbol": symbol,
        "timeframe": base_timeframe,
        "regime": regime,
        "atr_current": atr_current,
        "session_hour_utc": current_hour_utc,
        "regime_coeff": regime_coeff,
        "session_weight": session_weight,
        "asymmetry_ratio": asym_ratio,
        "step": base_step,
        "step_buy": step_buy,
        "step_sell": step_sell,
        "raw_close_alpha": 0.5,
        "max_open_per_side": max(3, min(12, int(100 / (regime_coeff * session_weight)))),
    }

if __name__ == "__main__":
    import numpy as np
    from datetime import datetime
    
    mt5.initialize()
    
    # Define symbols and their current regimes (from regime classification)
    symbol_regimes = {
        "BTCUSD": "STRONG_TREND",
        "ETHUSD": "STRONG_TREND",
        "GBPUSD": "WEAK_TREND",
        "EURUSD": "WEAK_TREND",
        "NZDUSD": "TRANSITION",
        "USDJPY": "STRONG_TREND",
        "NAS100": "STRONG_TREND",
        "US30": "WEAK_TREND",
        "XAUUSD": "STRONG_TREND",
        "SOLUSD": "STRONG_TREND",
        "XRPUSD": "STRONG_TREND",
        "DOGEUSD": "STRONG_TREND",
        "ADAUSD": "STRONG_TREND",
        "USDCHF": "WEAK_TREND",
        "USDCAD": "WEAK_TREND",
    }
    
    timeframe_map = {
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1,
    }
    
    current_hour = datetime.utcnow().hour
    results = []
    
    for symbol, regime in symbol_regimes.items():
        atr = compute_atr(symbol, mt5.TIMEFRAME_M15)
        if atr is None or atr <= 0:
            print(f"  {symbol}: ATR unavailable")
            continue
        
        params = compute_atr_scaled_step(symbol, atr, regime, current_hour)
        results.append(params)
        print(f"  {symbol}: step={params['step']:.6f}, buy={params['step_buy']:.6f}, sell={params['step_sell']:.6f} "
              f"(ATR={atr:.5f}, regime={regime}, session={params['session_weight']:.1f}, asym={params['asymmetry_ratio']:.1f}:1)")
    
    output = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "current_hour_utc": current_hour,
        "symbols": results,
    }
    
    with open(OUTPUT, 'w') as f:
        json.dump(output, f, indent=2)
    
    print(f"\nATR-scaled step params written to {OUTPUT}")
    print(f"Symbols processed: {len(results)}")
    
    mt5.shutdown()
