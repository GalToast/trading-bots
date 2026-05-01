#!/usr/bin/env python3
"""Dynamic Rearm — ANTICIPATORY Above-Price Injection Simulation

Instead of injecting SELL tokens below current price (which lose on shallow reversions),
inject tokens ABOVE current price. These only fire if BTC continues rallying,
then ANY pullback below entry is profitable.

This is "anticipatory" rearm — pre-placing tokens at levels price might reach.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def main():
    p = ROOT / "reports" / "penetration_lattice_live_btcusd_m5_warp_state.json"
    s = json.loads(p.read_text(encoding='utf-8'))
    btc = s['symbols']['BTCUSD']
    
    anchor = btc['anchor']
    realized = btc['realized_net_usd']
    closes = btc['realized_closes']
    avg_per_close = realized / closes if closes > 0 else 0
    
    sells = [t for t in btc['open_tickets'] if t['direction'] == 'SELL']
    sell_prices = sorted([t['entry_fill_price'] for t in sells])
    highest_sell = sell_prices[-1]
    
    current_price = 74561
    
    # Price path scenarios during the rally:
    # Each scenario has: (label, peak_price, reversion_price, probability)
    # peak_price = how high BTC rallies before pulling back
    # reversion_price = where BTC pulls back to
    scenarios = [
        ("No rally, no pullback", current_price, current_price, 0.10),
        ("Small rally (+0.3%), shallow pullback (-0.1%)", current_price * 1.003, current_price * 0.999, 0.15),
        ("Small rally (+0.3%), deep pullback (-0.8%)", current_price * 1.003, current_price * 0.992, 0.10),
        ("Moderate rally (+0.8%), shallow pullback (-0.2%)", current_price * 1.008, current_price * 0.998, 0.15),
        ("Moderate rally (+0.8%), anchor reversion", current_price * 1.008, anchor, 0.15),
        ("Moderate rally (+0.8%), deep pullback (-1.5%)", current_price * 1.008, current_price * 0.985, 0.10),
        ("Big rally (+1.5%), moderate pullback (-0.5%)", current_price * 1.015, current_price * 0.995, 0.10),
        ("Big rally (+1.5%), deep pullback (-1.5%)", current_price * 1.015, current_price * 0.985, 0.10),
        ("Big rally (+2.0%), deep pullback (-2.0%)", current_price * 1.02, current_price * 0.98, 0.05),
    ]
    
    # Test different anticipatory injection configs
    # Inject N tokens starting at current_price + M*step, with step size S
    m_values = [1, 2, 3]  # How many steps above current price to start
    step_sizes = [50, 75, 100, 150, 200]  # Step size for token placement
    n_tokens = [3, 5, 7, 10]  # Number of tokens to inject
    
    print("=" * 90)
    print("ANTICIPATORY DYNAMIC REARM — ABOVE-PRICE INJECTION")
    print("=" * 90)
    print()
    print(f"Current BTC price:    ~{current_price}")
    print(f"Anchor:               {anchor:.2f}")
    print(f"Highest open SELL:    {highest_sell:.2f}")
    print(f"Average $/close:      ${avg_per_close:.2f}")
    print()
    
    results = []
    
    for n in n_tokens:
        for m in m_values:
            for step in step_sizes:
                # Calculate injection levels: current_price + m*step, + (m+1)*step, ...
                injected = [current_price + (m + i) * step for i in range(n)]
                
                # For each scenario, calculate outcome
                scenario_outcomes = []
                for label, peak, rev, prob in scenarios:
                    # Which tokens fire? (only those below peak price)
                    fired = [e for e in injected if e <= peak]
                    
                    if not fired:
                        # No tokens fired → no additional risk, no additional alpha
                        floating = 0
                        profit = 0
                        levels_crossed = 0
                        alpha = 0
                    else:
                        # Fired tokens: calculate floating at peak, profit on reversion
                        floating = sum((e - peak) * 0.01 for e in fired)
                        profit = sum((e - rev) * 0.01 for e in fired)
                        levels_crossed = sum(1 for e in fired if rev < e <= peak)
                        alpha = levels_crossed * avg_per_close
                    
                    total = profit + alpha
                    scenario_outcomes.append({
                        'label': label,
                        'prob': prob,
                        'fired': len(fired),
                        'floating': floating,
                        'profit': profit,
                        'alpha': alpha,
                        'total': total,
                    })
                
                # Expected value
                ev = sum(o['prob'] * o['total'] for o in scenario_outcomes)
                max_dd = min(o['total'] for o in scenario_outcomes)
                rr = ev / abs(max_dd) if max_dd != 0 else float('inf')
                
                results.append({
                    'n': n,
                    'm': m,
                    'step': step,
                    'entries': injected,
                    'ev': ev,
                    'max_dd': max_dd,
                    'rr': rr,
                    'scenarios': scenario_outcomes,
                })
    
    # Sort by EV descending
    results.sort(key=lambda x: -x['ev'])
    
    print("TOP 15 COMBINATIONS (by Expected Value):")
    print(f"{'Rank':>4} {'N':>3} {'M':>3} {'Step':>6} {'Entries':>45} {'EV':>10} {'MaxDD':>10} {'R/R':>6}")
    print("-" * 90)
    
    for rank, r in enumerate(results[:15], 1):
        entries_str = ", ".join([f"${e:.0f}" for e in r['entries'][:3]])
        if len(r['entries']) > 3:
            entries_str += "..."
        print(f"{rank:>4}  {r['n']:>3}  {r['m']:>3}  ${r['step']:>4}  {entries_str:>45}  "
              f"${r['ev']:>8.2f}  ${r['max_dd']:>8.2f}  {r['rr']:>5.1f}x")
    
    print()
    
    # Find best balanced (high EV with acceptable R/R)
    balanced = [r for r in results if r['ev'] > 20 and r['rr'] > 0.5]
    if balanced:
        best_balanced = max(balanced, key=lambda x: x['rr'])
        
        print("=" * 90)
        print(f"BALANCED OPTIMAL (EV > $20, R/R > 0.5x):")
        print(f"  N={best_balanced['n']}, M={best_balanced['m']}, Step=${best_balanced['step']}")
        print(f"  Injection levels: {', '.join([f'${e:.2f}' for e in best_balanced['entries']])}")
        print(f"  EV: ${best_balanced['ev']:.2f}/cycle")
        print(f"  Max Drawdown: ${best_balanced['max_dd']:.2f}")
        print(f"  Reward/Risk: {best_balanced['rr']:.1f}x")
        print()
        
        print(f"  SCENARIO BREAKDOWN:")
        print(f"  {'Scenario':>50} {'Prob':>6} {'Fire':>5} {'Floating':>10} {'Profit':>10} {'Alpha':>8} {'Total':>10}")
        print(f"  {'-'*99}")
        for o in best_balanced['scenarios']:
            print(f"  {o['label']:>50} {o['prob']:>5.0%}  {o['fired']:>4}  "
                  f"${o['floating']:>8.2f}  ${o['profit']:>8.2f}  ${o['alpha']:>6.2f}  ${o['total']:>8.2f}")
    
    print()
    print("=" * 90)
    print("KEY INSIGHT:")
    print("  Anticipatory injection ABOVE current price avoids the shallow-reversion trap.")
    print("  Tokens only fire if BTC rallies further → entries are above current price.")
    print("  ANY pullback below entry → profit. No deep reversion required.")
    print()
    print(f"  Best anticipatory config: N={best_balanced['n']}, M={best_balanced['m']}, Step=${best_balanced['step']}")
    print(f"  → EV: ${best_balanced['ev']:.2f}/cycle, R/R: {best_balanced['rr']:.1f}x")
    print(f"  → Compared to naive below-anchor ($236/cycle, 2.7x R/R)")
    print(f"  → This is {best_balanced['ev']/236*100:.0f}% of naive EV, {best_balanced['rr']/2.7*100:.0f}% of naive R/R")
    print("=" * 90)

if __name__ == "__main__":
    main()
