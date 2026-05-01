"""M5 Warp FX Analysis — Does the crypto lattice edge apply to FX?"""
import MetaTrader5 as mt5, json

mt5.initialize()

# Get all FX pairs available
symbols = mt5.symbols_get()
fx_pairs_config = {
    'EURUSD': {'digits': 5, 'point': 0.00001},
    'GBPUSD': {'digits': 5, 'point': 0.00001},
    'USDJPY': {'digits': 3, 'point': 0.001},
    'NZDUSD': {'digits': 5, 'point': 0.00001},
    'AUDUSD': {'digits': 5, 'point': 0.00001},
    'USDCAD': {'digits': 5, 'point': 0.00001},
}

results = {}
for sym_name, cfg in fx_pairs_config.items():
    info = mt5.symbol_info(sym_name)
    if info is None:
        print(f"  {sym_name}: NOT AVAILABLE")
        continue
    
    spread = info.spread * info.point
    rates = mt5.copy_rates_from_pos(sym_name, mt5.TIMEFRAME_M5, 0, 500)
    if rates is None or len(rates) < 100:
        print(f"  {sym_name}: insufficient data")
        continue
    
    highs = rates['high']
    lows = rates['low']
    closes = rates['close']
    
    # ATR(14)
    atr_values = []
    for i in range(14, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        atr_values.append(tr)
    atr = sum(atr_values[-14:]) / 14
    
    # Average Range (last 100 bars)
    ranges = [(highs[i] - lows[i]) for i in range(-100, 0)]
    avg_range = sum(ranges) / len(ranges)
    
    price = closes[-1]
    
    # Recommended M5 Warp steps at different ATR multiples
    steps = {}
    for k_atr in [1.0, 1.5, 2.0]:
        step = atr * k_atr
        step_rounded = round(step, cfg['digits'])
        steps[f"{k_atr}x_atr"] = step_rounded
    
    # Spread comparison
    spread_pct_of_1x = spread / atr * 100
    spread_pct_of_1_5x = spread / (atr * 1.5) * 100
    
    result = {
        'symbol': sym_name,
        'price': round(price, cfg['digits']),
        'atr': round(atr, cfg['digits']),
        'atr_pct': round(atr/price*100, 4),
        'range': round(avg_range, cfg['digits']),
        'range_pct': round(avg_range/price*100, 4),
        'range_atr_ratio': round(avg_range/atr, 2),
        'spread': round(spread, cfg['digits']),
        'spread_pct': round(spread/price*100, 4),
        'spread_vs_1x_atr': round(spread_pct_of_1x, 1),
        'spread_vs_1_5x_atr': round(spread_pct_of_1_5x, 1),
        'step_1x': steps['1.0x_atr'],
        'step_1_5x': steps['1.5x_atr'],
        'step_2x': steps['2.0x_atr'],
    }
    results[sym_name] = result
    
    print(f"{sym_name}:")
    print(f"  Price: {price:.{cfg['digits']}f}")
    print(f"  ATR: {atr:.{cfg['digits']}f} ({atr/price*100:.4f}%)")
    print(f"  Range: {avg_range:.{cfg['digits']}f} ({avg_range/price*100:.4f}%)")
    print(f"  Range/ATR: {avg_range/atr:.2f}x")
    print(f"  Spread: {spread:.{cfg['digits']}f} ({spread/price*100:.4f}%)")
    print(f"  Spread vs 1.0x ATR: {spread_pct_of_1x:.1f}%")
    print(f"  Spread vs 1.5x ATR: {spread_pct_of_1_5x:.1f}%")
    print(f"  Recommended steps: 1.0x={steps['1.0x_atr']}, 1.5x={steps['1.5x_atr']}, 2.0x={steps['2.0x_atr']}")
    print()

# Save for later use
json.dump(results, open('reports/fx_m5_atr_analysis.json', 'w'), indent=2)
print(f"\nSaved to reports/fx_m5_atr_analysis.json")

# Now compare to crypto
print("\n=== CRYPTO vs FX Comparison ===")
try:
    crypto = json.load(open('reports/sol_atr_analysis.json'))
    for sym, data in crypto.items():
        print(f"  {sym}: ATR={data.get('ATR_M15', '?')}, Range/ATR={data.get('Range_ATR_ratio', '?')}x")
except:
    pass

for sym, data in results.items():
    print(f"  {sym}: ATR={data['atr']}, Range/ATR={data['range_atr_ratio']}x, Spread/1.5xATR={data['spread_vs_1_5x_atr']}%")

mt5.shutdown()
