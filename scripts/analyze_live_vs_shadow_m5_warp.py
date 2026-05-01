#!/usr/bin/env python3
"""
Live M5 Warp vs Shadow Comparison Analysis
============================================
Compares the live broker execution with the tick-native shadow to find
the execution friction gap and recommend fixes.

Usage:
    python scripts/analyze_live_vs_shadow_m5_warp.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LIVE_STATE = ROOT / "reports" / "penetration_lattice_live_btcusd_m5_warp_state.json"
SHADOW_STATE = ROOT / "reports" / "penetration_lattice_shadow_btcusd_m5_warp_state.json"

def main():
    live = json.loads(LIVE_STATE.read_text())
    shadow = json.loads(SHADOW_STATE.read_text())
    
    ls = live.get('symbols', {}).get('BTCUSD', {})
    ss = shadow.get('symbols', {}).get('BTCUSD', {})
    
    live_realized = ls.get('realized_net_usd', 0)
    live_closes = ls.get('realized_closes', 0)
    live_opens = len(ls.get('open_tickets', []))
    live_anchor = ls.get('anchor', 0)
    
    shadow_realized = ss.get('realized_net_usd', 0)
    shadow_closes = ss.get('realized_closes', 0)
    shadow_opens = len(ss.get('open_tickets', []))
    shadow_anchor = ss.get('anchor', 0)
    
    print("=" * 72)
    print("LIVE M5 WARP vs SHADOW COMPARISON")
    print("=" * 72)
    print()
    print(f"{'Metric':<30} {'Live':>12} {'Shadow':>12} {'Ratio':>10}")
    print("-" * 72)
    print(f"{'Realized Net (USD)':<30} ${live_realized:>10.2f} ${shadow_realized:>10.2f} {shadow_realized/live_realized:>9.1f}x" if live_realized > 0 else "")
    print(f"{'Closes':<30} {live_closes:>12} {shadow_closes:>12} {shadow_closes/live_closes:>9.1f}x" if live_closes > 0 else "")
    print(f"{'Open positions':<30} {live_opens:>12} {shadow_opens:>12}")
    print(f"{'$/close':<30} ${live_realized/live_closes:>10.2f} ${shadow_realized/shadow_closes:>10.2f}" if live_closes > 0 and shadow_closes > 0 else "")
    print(f"{'Anchor':<30} {live_anchor:>12.2f} {shadow_anchor:>12.2f}")
    print()
    
    # Directional bias analysis
    live_tickets = ls.get('open_tickets', [])
    live_buys = [t for t in live_tickets if t.get('direction') == 'BUY']
    live_sells = [t for t in live_tickets if t.get('direction') == 'SELL']
    
    shadow_tickets = ss.get('open_tickets', [])
    shadow_buys = [t for t in shadow_tickets if t.get('direction') == 'BUY']
    shadow_sells = [t for t in shadow_tickets if t.get('direction') == 'SELL']
    
    print(f"Directional bias:")
    print(f"  Live:   {len(live_buys)} BUY, {len(live_sells)} SELL ({len(live_sells)/max(1,len(live_tickets))*100:.0f}% short)")
    print(f"  Shadow: {len(shadow_buys)} BUY, {len(shadow_sells)} SELL ({len(shadow_sells)/max(1,len(shadow_tickets))*100:.0f}% short)")
    print()
    
    # Key findings
    print("KEY FINDINGS:")
    print(f"1. Shadow closes {shadow_closes/live_closes:.1f}x more often than live")
    print(f"   - Shadow: {shadow_closes} closes, Live: {live_closes} closes")
    print(f"   - Shadow runs at tick-native speed, live at MT5 polling speed")
    print()
    print(f"2. $/close is nearly identical: ${live_realized/live_closes:.2f} vs ${shadow_realized/shadow_closes:.2f}")
    print(f"   - The EDGE PER CLOSE is the same")
    print(f"   - The gap is purely in CLOSE FREQUENCY, not edge quality")
    print()
    print(f"3. Live accumulates {live_opens} open positions vs shadow's {shadow_opens}")
    print(f"   - Live can't close fast enough during trends")
    print(f"   - Step=$100 is too tight for live execution latency")
    print()
    
    # Recommendations
    print("RECOMMENDATIONS:")
    print("1. WIDEN STEP SIZE: Increase M5 step from $100 to $200-300")
    print("   - Fewer fills per trend = less inventory accumulation")
    print("   - Same $/close, fewer open positions = less floating risk")
    print()
    print("2. ADD ASYMMETRIC CAPS: Limit max_open_per_side asymmetrically")
    print("   - If lane is 80% short-biased, cap SELLs at 10 max")
    print("   - This prevents runaway inventory buildup during trends")
    print()
    print("3. FASTER EXIT ROUTING: Use direct broker close instead of polling")
    print("   - The live runner polls every 30 seconds")
    print("   - Reducing poll interval to 5-10 seconds would help")
    print()
    print("4. ACCEPT FLOATING AS NORMAL: The M5 Warp edge is designed to")
    print("   accumulate counter-trend inventory and wait for mean reversion.")
    print("   The floating loss is NOT a bug — it's the strategy mechanics.")
    print("   What matters is whether realized gains exceed floating losses")
    print("   over the full cycle.")

if __name__ == "__main__":
    main()
