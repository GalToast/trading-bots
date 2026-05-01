"""
MSLS Forensic Backtest Tool v3
==============================
Calculates REALIZED PnL with the "NeverLose" 30-Second Rule.
1. Entry: MSLS Signal (SMC Hardened)
2. Immediate Exit: If next M1 bar is not green, exit (micro-loss).
3. Profit Target: 1:2 Risk-to-Reward.
4. Time Exit: Max 60 minutes.
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
    print(f"\n--- MSLS Realized PnL Backtest for {symbol} (Last {days} days) ---")
    
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1440 * days)
    if rates is None or len(rates) == 0:
        return
        
    bars = []
    for r in rates:
        bars.append({'t': r[0], 'o': r[1], 'h': r[2], 'l': r[3], 'c': r[4], 'v': r[5]})
        
    signals = []
    lookback = 40
    lot_size = 0.01
    
    for i in range(lookback + 5, len(bars) - 60):
        window = bars[:i+1]
        signal, conf, sl, thesis = detect_msls_signal(window, lookback=lookback)
        
        if signal:
            entry_price = bars[i]['c']
            stop_dist = abs(entry_price - sl)
            tp_price = entry_price + (stop_dist * 2) if signal == "BUY" else entry_price - (stop_dist * 2)
            
            # Rule 1: 30-Second Green Rule (Proxy: next M1 candle)
            next_bar = bars[i+1]
            is_green = (signal == "BUY" and next_bar['c'] > entry_price) or (signal == "SELL" and next_bar['c'] < entry_price)
            
            realized_pnl = 0
            if not is_green:
                # Immediate Exit (Micro-loss)
                realized_pnl = (next_bar['c'] - entry_price) if signal == "BUY" else (entry_price - next_bar['c'])
            else:
                # Management: Hold for 1:2 R:R or 60 mins
                for j in range(i + 1, min(i + 61, len(bars))):
                    if signal == "BUY":
                        if bars[j]['l'] <= sl: # Stopped
                            realized_pnl = sl - entry_price
                            break
                        if bars[j]['h'] >= tp_price: # TP Hit
                            realized_pnl = tp_price - entry_price
                            break
                    else:
                        if bars[j]['h'] >= sl: # Stopped
                            realized_pnl = entry_price - sl
                            break
                        if bars[j]['l'] <= tp_price: # TP Hit
                            realized_pnl = entry_price - tp_price
                            break
                    # End of 60m time exit
                    if j == i + 60:
                        realized_pnl = (bars[j]['c'] - entry_price) if signal == "BUY" else (entry_price - bars[j]['c'])

            # Normalize to dollar PnL for 0.01 lot (approx)
            multiplier = 100000 if "JPY" not in symbol else 1000 # Standard lot scaling
            if any(idx in symbol for idx in ["NAS100", "US30", "GER30"]):
                multiplier = 10 # Approx for indices
                
            signals.append({
                'pnl_usd': realized_pnl * multiplier * lot_size,
                'win': realized_pnl > 0
            })
            
    if signals:
        df = pd.DataFrame(signals)
        total_pnl = df['pnl_usd'].sum()
        win_rate = df['win'].mean() * 100
        print(f"Signals: {len(df)} | Total PnL: ${total_pnl:+.2f} | WR: {win_rate:.1f}% | Avg Trade: ${df['pnl_usd'].mean():+.2f}")

if __name__ == "__main__":
    if not mt5.initialize(): sys.exit(1)
    for sym in ["US30", "AUDCHF", "NAS100", "USDCHF", "GBPUSD"]:
        run_backtest(sym, days=30)
    mt5.shutdown()
