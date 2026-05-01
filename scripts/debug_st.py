#!/usr/bin/env python3
"""
Debug supertrend signals on ZEC data.
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CANDLE_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports", "candle_cache")

with open(os.path.join(CANDLE_CACHE, "ZEC_USD_FIVE_MINUTE_7d.json")) as f:
    raw = json.load(f)

if isinstance(raw, dict):
    for k in ("candles", "data", "result"):
        if k in raw:
            candles = raw[k]
            break
    else:
        candles = list(raw.values())[0]
else:
    candles = raw

print(f"Total candles: {len(candles)}")
print(f"First: {candles[0]}")
print(f"Last:  {candles[-1]}")

def compute_supertrend_all(candles, period=10, multiplier=3.0):
    n = len(candles)
    if n < period + 2:
        return [(None, None)] * n

    trs = []
    for i in range(1, n):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        c_prev = float(candles[i - 1]["close"])
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)

    atrs = []
    for i in range(len(trs)):
        if i < period - 1:
            atrs.append(None)
        else:
            atrs.append(sum(trs[i - period + 1:i + 1]) / period)

    results = [(None, None)]
    trend = "bullish"
    final_upper = 0.0
    final_lower = 0.0

    for i in range(len(trs)):
        if atrs[i] is None:
            results.append((None, None))
            continue

        atr = atrs[i]
        mid = (float(candles[i + 1]["high"]) + float(candles[i + 1]["low"])) / 2
        basic_upper = mid + multiplier * atr
        basic_lower = mid - multiplier * atr

        if i > 0 and results[i][0] is not None:
            prev_upper = final_upper
            prev_lower = final_lower
        else:
            prev_upper = basic_upper
            prev_lower = basic_lower

        final_upper = min(basic_upper, prev_upper) if basic_upper < prev_upper else basic_upper
        final_lower = max(basic_lower, prev_lower) if basic_lower > prev_lower else basic_lower

        close = float(candles[i + 1]["close"])

        if trend == "bearish" and close > final_upper:
            trend = "bullish"
            final_lower = basic_lower
        elif trend == "bullish" and close < final_lower:
            trend = "bearish"
            final_upper = basic_upper

        trend_line = final_lower if trend == "bullish" else final_upper
        results.append((trend_line, trend))

    return results

# Test different params
for mult in [2.0, 2.5, 3.0]:
    st = compute_supertrend_all(candles, period=10, multiplier=mult)
    trends = [s[1] for s in st if s[1] is not None]
    bullish = sum(1 for t in trends if t == "bullish")
    bearish = sum(1 for t in trends if t == "bearish")
    
    # Count flips
    flips = 0
    for j in range(1, len(trends)):
        if trends[j] != trends[j-1]:
            flips += 1
    b2f = sum(1 for j in range(1, len(trends)) if trends[j-1] == "bearish" and trends[j] == "bullish")
    
    print(f"\n  mult={mult}: {len(trends)} bars, bullish={bullish}, bearish={bearish}, flips={flips}, bear->bull={b2f}")
    
    # Show first few trend values
    for j in range(min(15, len(st))):
        if st[j][1] is not None:
            ts = int(candles[j].get("start", candles[j].get("time", 0)))
            t_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%m-%d %H:%M')
            print(f"    [{j}] {t_str} close={float(candles[j]['close']):.2f} -> {st[j][1]} line={st[j][0]:.2f}")
