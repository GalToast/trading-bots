#!/usr/bin/env python3
"""BTC Short Squeeze Missed Alpha Quantification

Analyzes the M5 Warp and exc2_tight lanes to quantify:
1. How many SELL triggers missed during the rally
2. Estimated alpha lost per missed trigger
3. Simulation of dynamic rearm injection
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
M5_STATE = ROOT / "reports" / "penetration_lattice_live_btcusd_m5_warp_state.json"
EXC2_STATE = ROOT / "reports" / "penetration_lattice_shadow_btcusd_exc2_tight_state.json"

def analyze_short_squeeze(state_path, name, current_price, step):
    """Analyze a single lane for short squeeze pattern."""
    s = json.loads(state_path.read_text())
    btc = s.get('symbols', {}).get('BTCUSD', s)
    tickets = btc.get('open_tickets', [])
    rearm = btc.get('rearm_tokens', [])
    anchor = btc.get('anchor', 0)
    
    buys = [t for t in tickets if t.get('direction') == 'BUY']
    sells = [t for t in tickets if t.get('direction') == 'SELL']
    
    if not sells:
        print(f"\n{name}: No SELL positions (not short-squeezed)")
        return
    
    sell_prices = sorted([t['entry_fill_price'] for t in sells])
    highest_sell = sell_prices[-1]
    lowest_sell = sell_prices[0]
    
    levels_above = max(0, int((current_price - highest_sell) / step))
    
    print(f"\n{'='*60}")
    print(f"{name} — SHORT SQUEEZE ANALYSIS")
    print(f"{'='*60}")
    print(f"  Current price:    ~{current_price:.2f}")
    print(f"  Anchor:           {anchor:.2f}")
    print(f"  Step:             ${step}")
    print(f"  SELL positions:   {len(sells)} (range: {lowest_sell:.2f} - {highest_sell:.2f})")
    print(f"  BUY positions:    {len(buys)}")
    print(f"  Rearm tokens:     {len(rearm)}")
    
    # Count rearm tokens by direction
    buy_tokens = sum(1 for t in rearm if t.get('direction') == 'BUY')
    sell_tokens = sum(1 for t in rearm if t.get('direction') == 'SELL')
    print(f"    BUY tokens:  {buy_tokens}")
    print(f"    SELL tokens: {sell_tokens}")
    
    print(f"\n  Distance above highest SELL: {current_price - highest_sell:.2f}")
    print(f"  Missed SELL levels: {levels_above}")
    
    if levels_above > 0:
        print(f"\n  Missed SELL triggers (dynamic rearm would fire these):")
        total_additional_floating = 0
        total_profit_on_reversion = 0
        for i in range(1, levels_above + 1):
            entry = highest_sell + i * step
            floating_pnl = (entry - current_price) * 0.01
            reversion_pnl = (entry - anchor) * 0.01
            total_additional_floating += floating_pnl
            total_profit_on_reversion += reversion_pnl
            print(f"    SELL @ {entry:.2f}: floating={floating_pnl:+.2f}, profit on reversion={reversion_pnl:+.2f}")
        
        print(f"\n  IMPACT OF DYNAMIC REARM:")
        print(f"    Additional floating risk: ${total_additional_floating:.2f}")
        print(f"    Profit on mean reversion to anchor: ${total_profit_on_reversion:.2f}")
        
        realized = btc.get('realized_net_usd', 0)
        print(f"    Current realized: ${realized:.2f}")
        print(f"    Net after reversion (with dynamic): ${realized + total_profit_on_reversion:.2f}")

def main():
    # Current BTC price from latest trade-firing alerts
    # Latest SELL trigger was @74561.01, so price is around 74,550
    current_price = 74550
    
    print(f"BTC SHORT SQUEEZE MISSED ALPHA QUANTIFICATION")
    print(f"Analysis time: 02:26 UTC 2026-04-14")
    print(f"Current BTC price estimate: ~{current_price}")
    
    # M5 Warp analysis
    if M5_STATE.exists():
        analyze_short_squeeze(M5_STATE, "M5 WARP (step=$100)", current_price, step=100)
    
    # exc2_tight analysis
    if EXC2_STATE.exists():
        analyze_short_squeeze(EXC2_STATE, "EXC2 TIGHT (step=$45)", current_price, step=45)
    
    print(f"\n{'='*60}")
    print(f"KEY FINDING: SELL rearm tokens are ONLY generated when SELLs close profitably.")
    print(f"During a rally, SELLs are underwater -> no closes -> no rearm tokens -> can't add SELLs.")
    print(f"This is the SHORT SQUEEZE pattern: the lattice stops capturing alpha during trends.")
    print(f"")
    print(f"FIX: Dynamic rearm regeneration — inject SELL tokens when price moves N steps")
    print(f"     beyond highest open SELL, allowing the lattice to keep adding positions.")
    print(f"     Risk: more underwater positions during extended trends.")
    print(f"     Reward: captures mean-reversion alpha on the way back down.")

if __name__ == "__main__":
    main()
