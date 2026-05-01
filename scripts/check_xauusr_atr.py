import MetaTrader5 as mt5
mt5.initialize()
rates = mt5.copy_rates_from_pos('XAUUSD', mt5.TIMEFRAME_M15, 0, 100)
with open('reports/xauusr_atr_output.txt', 'w') as f:
    n = 0 if rates is None else len(rates)
    f.write(f'Got {n} bars\n')
    f.write(f'Fields: {rates.dtype.names if rates is not None else "none"}\n')
    if n > 0:
        # Use field names for numpy structured array
        highs = rates['high']
        lows = rates['low']
        closes = rates['close']
        ranges = highs - lows  # numpy array subtraction
        avg_range = ranges.mean()
        atr_14 = ranges[-14:].mean()
        f.write(f'ATR(14): {atr_14:.2f}\n')
        f.write(f'Avg range: {avg_range:.2f}\n')
        f.write(f'Recommended 1.5x ATR: {atr_14*1.5:.2f}\n')
        f.write(f'Recommended 1.0x ATR: {atr_14*1.0:.2f}\n')
        f.write(f'Ratio avg_range/step(4.8): {avg_range/4.8:.2f}x\n')
        f.write(f'Current price (last close): {closes[-1]:.2f}\n')
        f.write(f'Current bid/ask spread sample: {ranges[:5]}\n')
mt5.shutdown()
