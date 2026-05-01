#!/usr/bin/env python3
"""BTC Short Squeeze — TRUE Missed Alpha Quantification

The M5 Warp uses PENETRATION CLOSES: SELLs close when price drops below entry.
Each close earns approximately $21.43 (current average).
Missed SELL levels = missed opportunities for $21.43/close.

The question is: during this rally, how many SELL levels did the lattice miss
because it had no SELL rearm tokens?
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def main():
    p = ROOT / "reports" / "penetration_lattice_live_btcusd_m5_warp_state.json"
    s = json.loads(p.read_text(encoding='utf-8'))
    btc = s['symbols']['BTCUSD']
    
    tickets = btc['open_tickets']
    rearm = btc.get('rearm_tokens', [])
    anchor = btc['anchor']
    step = 100
    realized = btc['realized_net_usd']
    closes = btc['realized_closes']
    sell_tokens = sum(1 for t in rearm if t['direction'] == 'SELL')
    
    sells = [t for t in tickets if t['direction'] == 'SELL']
    sell_prices = sorted([t['entry_fill_price'] for t in sells])
    highest_sell = sell_prices[-1]
    
    # Current BTC price
    current_price = 74561
    
    avg_per_close = realized / closes if closes > 0 else 0
    
    # How many SELL levels were missed during the rally?
    # The lattice would normally sell at: anchor + step, anchor + 2*step, etc.
    # But without rearm tokens, it can't.
    # The gap between highest open SELL and current price represents missed levels.
    
    levels_above = max(0, int((current_price - highest_sell) / step))
    
    # These levels are all BELOW anchor, so they WOULD profit on mean reversion
    levels_below_anchor = max(0, int((anchor - highest_sell) / step))
    levels_above_anchor = levels_above - levels_below_anchor
    
    print("=" * 70)
    print("BTC M5 WARP SHORT SQUEEZE — TRUE MISSED ALPHA")
    print("=" * 70)
    print()
    print(f"Current BTC price:    ~{current_price}")
    print(f"Anchor:               {anchor:.2f}")
    print(f"Highest open SELL:    {highest_sell:.2f}")
    print(f"SELL rearm tokens:    {sell_tokens} (ZERO = can't add SELLs!)")
    print(f"Average $/close:      ${avg_per_close:.2f}")
    print()
    print(f"Total missed SELL levels:       {levels_above}")
    print(f"  Below anchor (profitable):     {levels_below_anchor}")
    print(f"  Above anchor (risky):          {levels_above_anchor}")
    print()
    
    # Estimated missed alpha per mean-reversion cycle
    # Each level gets 1 close when price mean-reverts back down
    missed_alpha_per_cycle = levels_below_anchor * avg_per_close
    
    print(f"MISSED ALPHA per mean-reversion cycle:")
    print(f"  {levels_below_anchor} levels × ${avg_per_close:.2f}/close = ${missed_alpha_per_cycle:.2f}")
    print()
    
    # How many times has this rally happened?
    # The M5 Warp does ~34 closes/hour in backtest. With 17 open SELLs,
    # the lattice has been in this squeeze for some time.
    # Estimate: the rally from 73,000 to 74,561 took about 4-6 hours
    # At 34 closes/hour, that's ~136-204 potential closes
    # With only 17 SELLs and no new ones, the lattice captured only the existing positions
    # The 17 SELLs have closed 41 times total → ~2.4 closes per position
    # With 13 more positions, that's 13 × 2.4 × $21.43 = ~$669 missed
    
    closes_per_position = closes / len(sells) if sells else 0
    missed_alpha_total = levels_below_anchor * closes_per_position * avg_per_close
    
    print(f"ESTIMATED TOTAL MISSED ALPHA during this rally:")
    print(f"  {levels_below_anchor} missed levels × {closes_per_position:.1f} closes/position × ${avg_per_close:.2f}")
    print(f"  = ${missed_alpha_total:.2f}")
    print()
    
    print("RISK ANALYSIS:")
    print(f"  Adding {levels_below_anchor} SELLs below anchor:")
    additional_floating_at_current = sum((highest_sell + i*step - current_price) * 0.01 for i in range(1, levels_below_anchor+1))
    print(f"    Additional floating at current price: ${additional_floating_at_current:.2f}")
    max_drawdown = sum((highest_sell + i*step - 76000) * 0.01 for i in range(1, levels_below_anchor+1))
    print(f"    Worst case (BTC to $76K): ${max_drawdown:.2f}")
    print(f"    Profit on mean reversion:  ${missed_alpha_per_cycle:.2f}/cycle")
    print()
    
    print("=" * 70)
    print("VERDICT:")
    print(f"  The short squeeze is costing ~${missed_alpha_per_cycle:.2f} per mean-reversion cycle.")
    print(f"  Estimated total missed during this rally: ~${missed_alpha_total:.2f}")
    print(f"  ")
    print(f"  The fix (dynamic rearm below anchor) adds ${abs(additional_floating_at_current):.2f}")
    print(f"  floating risk but recovers ${missed_alpha_per_cycle:.2f} per cycle.")
    print(f"  Reward/risk: {missed_alpha_per_cycle/abs(additional_floating_at_current):.1f}x")
    print(f"  ")
    print(f"  RECOMMENDATION: Implement dynamic rearm for levels BELOW anchor only.")
    print(f"  This captures the missed alpha without adding risk above anchor.")
    print("=" * 70)

if __name__ == "__main__":
    main()
