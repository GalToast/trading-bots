"""
MSLS Forensic Backtest Tool
===========================
Scans historical M1 data for Micro-Structure Liquidity Sweep (MSLS) signals.
Verifies if the engine would have saved us from recent losses or caught winners.
"""
import MetaTrader5 as mt5
import os
import sys
import pandas as pd
from datetime import datetime, timedelta, timezone

# Add current directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bot.gemini_v2 import detect_msls_signal

def run_backtest(symbol, days=1):
    if not mt5.initialize():
        print("MT5 initialize failed")
        return
        
    print(f"--- MSLS Backtest for {symbol} (Last {days} days) ---")
    
    # Pull M1 data
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1440 * days)
    if rates is None or len(rates) == 0:
        print(f"No rates found for {symbol}")
        return
        
    bars = []
    for r in rates:
        bars.append({
            't': r[0],
            'o': r[1],
            'h': r[2],
            'l': r[3],
            'c': r[4],
            'v': r[5]
        })
        
    signals = []
    # Scan with a sliding window
    lookback = 40
    for i in range(lookback + 5, len(bars)):
        window = bars[:i+1]
        signal, conf, sl, thesis = detect_msls_signal(window, lookback=lookback)
        
        if signal:
            entry_time = datetime.fromtimestamp(bars[i]['t'], tz=timezone.utc)
            entry_price = bars[i]['c']
            
            # Simple result check: what happened in the next 20 bars?
            max_future_profit = 0
            stopped_out = False
            
            for j in range(i + 1, min(i + 21, len(bars))):
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
                
            signals.append({
                'time': entry_time,
                'signal': signal,
                'price': entry_price,
                'sl': sl,
                'thesis': thesis,
                'stopped': stopped_out,
                'max_profit_pips': max_future_profit * 10000 if "JPY" not in symbol else max_future_profit * 100
            })
            
    if not signals:
        print("No MSLS signals detected.")
    else:
        print(f"Detected {len(signals)} signals.")
        df = pd.DataFrame(signals)
        print(df)
        
        win_count = len(df[df['stopped'] == False])
        print(f"\nSummary: {win_count}/{len(df)} avoided stop-out in 20-bar window ({(win_count/len(df)*100):.1f}%)")

if __name__ == "__main__":
    # Test on USDCHF and NAS100
    for sym in ["USDCHF", "NAS100", "GBPUSD", "AUDCHF"]:
        run_backtest(sym, days=1)
    mt5.shutdown()
