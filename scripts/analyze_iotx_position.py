#!/usr/bin/env python3
"""
IOTX Position Analysis
=======================
Analyze the live IOTX-USD position using candle history from state file.
Determines if TP is reachable before timeout and recommends optimal action.

Usage:
  python scripts/analyze_iotx_position.py
"""
import json
from pathlib import Path
import sys

def main():
    state_path = Path(__file__).parent.parent / "reports" / "multi_coin_isolated_state.json"
    
    print("=" * 70)
    print("IOTX-USD POSITION ANALYSIS (LIVE RUNNER)")
    print("=" * 70)
    
    with open(state_path) as f:
        state = json.load(f)
    
    iotx = state['ledgers']['IOTX-USD']
    
    entry = float(iotx['position_entry'])
    tp = float(iotx['position_tp'])
    sl = float(iotx['position_sl'])
    hold = int(iotx['position_hold'])
    max_hold = int(iotx['position_max_hold'])
    equity = float(iotx['equity'])
    starting = float(iotx['starting_cash'])
    units = float(iotx['position_units'])
    closes = int(iotx['closes'])
    losses = int(iotx['losses'])
    wins = int(iotx['wins'])
    
    tp_pct = ((tp - entry) / entry) * 100
    sl_pct = ((sl - entry) / entry) * 100
    bars_remaining = max_hold - hold
    
    print(f"\nPosition Details:")
    print(f"  Entry: ${entry:.6f}")
    print(f"  TP:    ${tp:.6f} (+{tp_pct:.2f}%)")
    print(f"  SL:    ${sl:.6f} ({sl_pct:.2f}%)")
    print(f"  Hold:  {hold}/{max_hold} ({hold/max_hold*100:.1f}%)")
    print(f"  Bars remaining: {bars_remaining}")
    print(f"  Units: {units:.2f}")
    print(f"  Equity: ${equity:.4f} (from ${starting:.4f}, +{((equity-starting)/starting)*100:.2f}%)")
    print(f"  Track record: {closes} closes, {wins}W/{losses}L")
    
    # Analyze candle history for price action
    history_len = int(iotx.get('history_len', 0))
    print(f"\nCandle History: {history_len} bars")
    
    # Check if the runner has recent candle data
    last_candle_time = int(iotx.get('last_candle_time', 0))
    print(f"  Last candle time: {last_candle_time}")
    
    # Calculate what price movement is needed per remaining bar
    if bars_remaining > 0:
        price_needed_per_bar = tp_pct / bars_remaining
        print(f"\nTP Reachability:")
        print(f"  Need +{tp_pct:.2f}% in {bars_remaining} bars")
        print(f"  That's +{price_needed_per_bar:.2f}% per bar average")
        
        if tp_pct > 5:
            print(f"  ⚠️ Large move needed ({tp_pct:.2f}%) - timeout likely unless strong trend")
        elif tp_pct > 2:
            print(f"  ⏳ Moderate move needed - possible if trending")
        else:
            print(f"  ✅ Small move needed - likely achievable")
    
    # Full organism context
    print(f"\n=== FULL ORGANISM ===")
    print(f"Total equity: ${state['total_equity']:.2f}")
    print(f"Total PnL: ${state['total_pnl']:.2f}")
    print(f"Return: {state['return_pct']:.2f}%")
    
    for coin, data in state['ledgers'].items():
        pos = data.get('position', '?')
        eq = float(data.get('equity', 0))
        start = float(data.get('starting_cash', 0))
        ret = float(data.get('return_pct', 0))
        print(f"  {coin}: {pos}, ${eq:.2f} ({ret:+.2f}%)")
    
    print("\n" + "=" * 70)
    
    # Recommendations
    print("\nRECOMMENDATIONS:")
    if hold > 40:
        print(f"  ⚠️ Position is {hold}/{max_hold} - timeout imminent in {bars_remaining} bars")
        print(f"  Consider: Let it ride if price is trending toward TP, else prepare for timeout close")
    elif tp_pct > 10:
        print(f"  ⚠️ TP is +{tp_pct:.2f}% away - aggressive target")
        print(f"  Consider: Lower TP to +5-8% for more frequent closes")
    else:
        print(f"  ✅ Position has {bars_remaining} bars remaining, TP is +{tp_pct:.2f}% away")
        print(f"  Status: Monitor price action")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
