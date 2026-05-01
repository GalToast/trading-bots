import MetaTrader5 as mt5
import numpy as np
import json
from datetime import datetime, timezone

if not mt5.initialize():
    print("MT5 init failed")
    exit(1)

symbols = ["GBPUSD", "EURUSD", "NZDUSD", "USDJPY", "BTCUSD", "ETHUSD"]
results = []

for sym in symbols:
    # Pull H4, H1, M15 data
    h4_rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H4, 0, 200)
    h1_rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 500)
    m15_rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 1000)
    
    result = {"symbol": sym}
    
    for tf_name, rates in [("h4", h4_rates), ("h1", h1_rates), ("m15", m15_rates)]:
        if rates is None or len(rates) < 56:
            result[f"{tf_name}_trend"] = "UNKNOWN"
            result[f"{tf_name}_adx"] = 0
            continue
        
        closes = rates["close"]
        
        # EMA 21 vs 55
        def ema(data, period):
            k = 2 / (period + 1)
            ema_vals = np.zeros_like(data)
            ema_vals[0] = data[0]
            for i in range(1, len(data)):
                ema_vals[i] = data[i] * k + ema_vals[i-1] * (1-k)
            return ema_vals
        
        ema21 = ema(closes, 21)
        ema55 = ema(closes, 55)
        
        # Current trend direction
        if ema21[-1] > ema55[-1]:
            trend = "BULLISH"
        else:
            trend = "BEARISH"
        
        # Count consecutive bars in trend
        in_trend = 0
        for i in range(len(ema21)-1, -1, -1):
            if (ema21[i] > ema55[i] and ema21[-1] > ema55[-1]) or (ema21[i] < ema55[i] and ema21[-1] < ema55[-1]):
                in_trend += 1
            else:
                break
        
        # ADX(14)
        def adx(rates, period=14):
            if len(rates) < period + 1:
                return 0
            plus_dm = []
            minus_dm = []
            for i in range(1, len(rates)):
                high_diff = rates[i]["high"] - rates[i-1]["high"]
                low_diff = rates[i-1]["low"] - rates[i]["low"]
                if high_diff > low_diff and high_diff > 0:
                    plus_dm.append(high_diff)
                else:
                    plus_dm.append(0)
                if low_diff > high_diff and low_diff > 0:
                    minus_dm.append(low_diff)
                else:
                    minus_dm.append(0)
            
            atr = []
            for i in range(1, len(rates)):
                atr.append(max(rates[i]["high"] - rates[i]["low"], abs(rates[i]["high"] - rates[i-1]["close"]), abs(rates[i]["low"] - rates[i-1]["close"])))
            
            # Smooth
            atr_avg = np.mean(atr[-period:])
            plus_di = 100 * np.mean(plus_dm[-period:]) / atr_avg if atr_avg > 0 else 0
            minus_di = 100 * np.mean(minus_dm[-period:]) / atr_avg if atr_avg > 0 else 0
            
            dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di) if (plus_di + minus_di) > 0 else 0
            return dx
        
        adx_val = adx(rates)
        
        result[f"{tf_name}_trend"] = trend
        result[f"{tf_name}_adx"] = round(adx_val, 1)
        result[f"{tf_name}_bars_in_trend"] = in_trend
    
    # Alignment
    trends = [result.get("h4_trend"), result.get("h1_trend"), result.get("m15_trend")]
    bullish = trends.count("BULLISH")
    bearish = trends.count("BEARISH")
    if bullish == 3:
        result["alignment"] = "ALL_BULLISH"
    elif bearish == 3:
        result["alignment"] = "ALL_BEARISH"
    elif bullish >= 2:
        result["alignment"] = "MOSTLY_BULLISH"
    elif bearish >= 2:
        result["alignment"] = "MOSTLY_BEARISH"
    else:
        result["alignment"] = "MIXED"
    
    # Flip counting for H1
    h1_ema21 = None
    h1_ema55 = None
    if h1_rates is not None and len(h1_rates) >= 56:
        def ema(data, period):
            k = 2 / (period + 1)
            ema_vals = np.zeros_like(data)
            ema_vals[0] = data[0]
            for i in range(1, len(data)):
                ema_vals[i] = data[i] * k + ema_vals[i-1] * (1-k)
            return ema_vals
        h1_closes = h1_rates["close"]
        h1_ema21 = ema(h1_closes, 21)
        h1_ema55 = ema(h1_closes, 55)
        h1_diff = h1_ema21 - h1_ema55
        flips = 0
        for i in range(1, len(h1_diff)):
            if h1_diff[i] * h1_diff[i-1] < 0:
                flips += 1
        result["h1_flip_count_500"] = flips
        
        # Average trend duration
        trend_lengths = []
        current_len = 1
        for i in range(1, len(h1_diff)):
            if (h1_diff[i] > 0 and h1_diff[i-1] > 0) or (h1_diff[i] < 0 and h1_diff[i-1] < 0):
                current_len += 1
            else:
                trend_lengths.append(current_len)
                current_len = 1
        if trend_lengths:
            result["avg_h1_trend_duration_bars"] = round(np.mean(trend_lengths), 1)
        else:
            result["avg_h1_trend_duration_bars"] = 500
    
    # Flip counting for H4
    if h4_rates is not None and len(h4_rates) >= 56:
        h4_closes = h4_rates["close"]
        h4_ema21 = ema(h4_closes, 21)
        h4_ema55 = ema(h4_closes, 55)
        h4_diff = h4_ema21 - h4_ema55
        flips = 0
        for i in range(1, len(h4_diff)):
            if h4_diff[i] * h4_diff[i-1] < 0:
                flips += 1
        result["h4_flip_count_200"] = flips
        
        trend_lengths = []
        current_len = 1
        for i in range(1, len(h4_diff)):
            if (h4_diff[i] > 0 and h4_diff[i-1] > 0) or (h4_diff[i] < 0 and h4_diff[i-1] < 0):
                current_len += 1
            else:
                trend_lengths.append(current_len)
                current_len = 1
        if trend_lengths:
            result["avg_h4_trend_duration_bars"] = round(np.mean(trend_lengths), 1)
        else:
            result["avg_h4_trend_duration_bars"] = 200
    
    results.append(result)

# Print summary table
print("\n" + "="*100)
print(f"{'SYMBOL':<10} {'H4 DIR':<10} {'H4 ADX':<8} {'H1 DIR':<10} {'H1 ADX':<8} {'M15 DIR':<10} {'M15 ADX':<8} {'ALIGNMENT':<15} {'H1 FLIPS':<10} {'H4 FLIPS':<10}")
print("="*100)
for r in results:
    print(f"{r['symbol']:<10} {r.get('h4_trend','?'):<10} {r.get('h4_adx',0):<8} {r.get('h1_trend','?'):<10} {r.get('h1_adx',0):<8} {r.get('m15_trend','?'):<10} {r.get('m15_adx',0):<8} {r.get('alignment','?'):<15} {r.get('h1_flip_count_500',0):<10} {r.get('h4_flip_count_200',0):<10}")

# Save
with open("reports/mtf_regime_flip_analysis.json", "w") as f:
    json.dump(results, f, indent=2)

print(f"\nSaved to reports/mtf_regime_flip_analysis.json")

mt5.shutdown()
