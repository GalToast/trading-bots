"""
MSLS Forensic Backtest Tool v2.1
=================================
Refined initialization and error reporting.
Analyzes 30 days of M1 data.
"""
import MetaTrader5 as mt5
import os
import sys
import pandas as pd
from datetime import datetime, timedelta, timezone

# Add current directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bot.gemini_v2 import detect_msls_signal

def run_backtest(symbol, days=30):
    print(f"\n--- MSLS Deep Backtest for {symbol} (Last {days} days) ---")
    
    # Pull M1 data
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1440 * days)
    if rates is None or len(rates) == 0:
        print(f"No rates found for {symbol}. Error: {mt5.last_error()}")
        return
        
    bars = []
    for r in rates:
        bars.append({
            't': r[0], 'o': r[1], 'h': r[2], 'l': r[3], 'c': r[4], 'v': r[5]
        })
        
    signals = []
    lookback = 40
    # Process bars (limit to avoid extreme runtimes in backtest)
    for i in range(lookback + 5, len(bars) - 60):
        window = bars[:i+1]
        signal, conf, sl, thesis = detect_msls_signal(window, lookback=lookback)
        
        if signal:
            entry_price = bars[i]['c']
            
            # Proxy for 30-Second Rule
            next_bar = bars[i+1]
            green_by_next_bar = False
            if signal == "BUY" and next_bar['h'] > entry_price: green_by_next_bar = True
            if signal == "SELL" and next_bar['l'] < entry_price: green_by_next_bar = True

            max_future_profit = 0
            stopped_out = False
            
            for j in range(i + 1, min(i + 61, len(bars))):
                if signal == "BUY":
                    profit = bars[j]['h'] - entry_price
                    if bars[j]['l'] <= sl:
                        stopped_out = True
                        break
                else:
                    profit = entry_price - bars[j]['l']
                    if bars[j]['h'] >= sl:
                        stopped_out = True
                        break
                max_future_profit = max(max_future_profit, profit)
            
            multiplier = 10000
            if "JPY" in symbol: multiplier = 100
            if any(idx in symbol for idx in ["NAS100", "US30", "GER30", "FRA40", "ESP35", "JPN225"]):
                multiplier = 1
                
            signals.append({
                'signal': signal,
                'stopped': stopped_out,
                'green_next': green_by_next_bar,
                'max_points': max_future_profit * multiplier
            })
            
    if not signals:
        print("No MSLS signals detected.")
    else:
        df = pd.DataFrame(signals)
        win_count = len(df[df['stopped'] == False])
        green_rate = (df['green_next'].mean() * 100)
        win_rate = (win_count/len(df)*100)
        avg_points = df['max_points'].mean()
        
        print(f"Signals: {len(df)} | Green Next: {green_rate:.1f}% | WR: {win_rate:.1f}% | Avg Max Pts: {avg_points:.2f}")

if __name__ == "__main__":
    if not mt5.initialize():
        print(f"MT5 initialize failed. Error: {mt5.last_error()}")
        sys.exit(1)
        
    symbols = ["USDCHF", "NAS100", "GBPUSD", "AUDCHF", "EURUSD", "US30", "XAUUSD", "USDJPY", "EURJPY", "GER30"]
    for sym in symbols:
        run_backtest(sym, days=30)
    mt5.shutdown()
