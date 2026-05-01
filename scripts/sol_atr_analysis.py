"""SOL ATR/Range analysis for M15/M5 step optimization."""
import MetaTrader5 as mt5
import json
from datetime import datetime, timezone

mt5.initialize()

symbols = ["SOLUSD", "ETHUSD", "BTCUSD", "XRPUSD"]
results = {}

for sym in symbols:
    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 500)
    if rates is None or len(rates) == 0:
        print(f"{sym}: NO DATA")
        continue

    prices = rates['close']
    highs = rates['high']
    lows = rates['low']

    # ATR(14)
    atr_values = []
    for i in range(14, len(prices)):
        tr = max(highs[i] - lows[i], abs(highs[i] - prices[i-1]), abs(lows[i] - prices[i-1]))
        if i < 14 + 13:
            atr_values.append(tr)
        else:
            atr_values.append(tr)
    current_atr = sum(atr_values[-14:]) / 14 if len(atr_values) >= 14 else 0

    # Range: avg of (high-low) over last 100 bars
    ranges = [(highs[i] - lows[i]) for i in range(-100, 0)]
    avg_range = sum(ranges) / len(ranges)

    price = prices[-1]
    step_pct = current_atr / price * 100 if price else 0
    range_pct = avg_range / price * 100 if price else 0

    # Current step from registry
    steps = {"SOLUSD": {"M15": 0.20, "M5": 0.12}, "ETHUSD": {"M15": 5.0, "M5": 3.0},
             "BTCUSD": {"M15": 75.0, "M5": 100.0}, "XRPUSD": {"M15": 0.02, "M5": 0.0016}}

    result = {
        "symbol": sym,
        "price": round(price, 4),
        "ATR_M15": round(current_atr, 4),
        "Range_M15": round(avg_range, 4),
        "ATR_x": round(current_atr / price, 4) if price else 0,
        "Range_x": round(avg_range / price, 4) if price else 0,
        "Range_ATR_ratio": round(avg_range / current_atr, 2) if current_atr else 0,
    }
    results[sym] = result
    print(f"{sym}: price={price:.4f}, ATR={current_atr:.4f} ({current_atr/price*100:.2f}%), Range={avg_range:.4f} ({avg_range/price*100:.2f}%), Range/ATR={avg_range/current_atr:.2f}x")

    # Recommended steps
    for tf, atr_mult in [("M15", 1.2), ("M5", 1.5)]:
        recommended = current_atr * atr_mult
        current = steps.get(sym, {}).get(tf, 0)
        print(f"  {tf} @ {atr_mult}x ATR: recommended=${recommended:.4f}, current=${current}, ratio={current/recommended:.2f}x" if recommended else f"  {tf}: N/A")

mt5.shutdown()
json.dump(results, open('reports/sol_atr_analysis.json', 'w'), indent=2)
print("\nSaved to reports/sol_atr_analysis.json")
