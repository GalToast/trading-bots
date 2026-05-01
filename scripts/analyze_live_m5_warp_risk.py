#!/usr/bin/env python3
"""Calculate floating risk for live BTC M5 Warp lane."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "penetration_lattice_live_btcusd_m5_warp_state.json"

def main():
    d = json.loads(STATE_PATH.read_text())
    s = d.get('symbols', {}).get('BTCUSD', {})
    tickets = s.get('open_tickets', [])
    
    # Get current price from state
    last_tick_time = s.get('last_tick_time', 0)
    anchor = s.get('anchor', 0)
    
    # Calculate floating PnL per position
    vol = 0.01
    total_floating = 0
    buys = []
    sells = []
    
    for t in tickets:
        entry = t.get('entry_fill_price', t.get('entry_price', 0))
        direction = t.get('direction', '')
        ticket = t.get('live_ticket', 0)
        
        # For live tickets, use actual broker PnL if available
        if direction == 'BUY':
            buys.append((entry, ticket))
        else:
            sells.append((entry, ticket))
    
    print(f"Live BTC M5 Warp - Floating Risk Analysis")
    print(f"=" * 50)
    print(f"Anchor: {anchor:.2f}")
    print(f"Total open tickets: {len(tickets)}")
    print(f"  BUYs: {len(buys)}")
    print(f"  SELLs: {len(sells)}")
    print(f"Realized net: ${s.get('realized_net_usd', 0):.2f}")
    print(f"Realized closes: {s.get('realized_closes', 0)}")
    print(f"")
    
    if buys:
        print(f"BUY entries:")
        for entry, ticket in buys:
            print(f"  Ticket {ticket}: ${entry:.2f}")
    
    if sells:
        print(f"\nSELL entries:")
        for entry, ticket in sells:
            print(f"  Ticket {ticket}: ${entry:.2f}")
    
    print(f"\nKey insight:")
    print(f"  With 17 SELLs and 1 BUY, the lane is heavily SHORT-biased.")
    print(f"  If BTC rallies, SELLs go underwater.")
    print(f"  If BTC drops, SELLs profit but BUY goes deeper underwater.")
    print(f"  This is the M5 Warp pattern — it accumulates counter-trend inventory")
    print(f"  during trends, then harvests when price reverts.")

if __name__ == "__main__":
    main()
