#!/usr/bin/env python3
"""Dynamic Rearm Tighter-Step Simulation — CAPPED Token Injection

The uncapped version injected 65 SELLs → poor R/R (0.3x).
This version caps injected tokens to N (3, 5, 7, 10) to limit floating risk.

Key question: What's the optimal N that maximizes EV while maintaining acceptable R/R?
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def main():
    p = ROOT / "reports" / "penetration_lattice_live_btcusd_m5_warp_state.json"
    s = json.loads(p.read_text(encoding='utf-8'))
    btc = s['symbols']['BTCUSD']
    
    tickets = btc['open_tickets']
    anchor = btc['anchor']
    realized = btc['realized_net_usd']
    closes = btc['realized_closes']
    avg_per_close = realized / closes if closes > 0 else 0
    
    sells = [t for t in tickets if t['direction'] == 'SELL']
    sell_prices = sorted([t['entry_fill_price'] for t in sells])
    highest_sell = sell_prices[-1]
    
    current_price = 74561
    
    # Reversion scenarios
    reversion_scenarios = [
        ("Current (no reversion)", current_price, 0.00),
        ("Shallow pullback (-0.2%)", current_price * 0.998, 0.15),
        ("Pullback to anchor (-0.3%)", anchor, 0.25),
        ("Moderate reversion (-0.8%)", current_price * 0.992, 0.25),
        ("Deep reversion (-1.5%)", current_price * 0.985, 0.15),
        ("Very deep (-2.5%)", current_price * 0.975, 0.10),
        ("Trend continuation (+1.0%)", current_price * 1.01, 0.05),
        ("Trend continuation (+2.0%)", current_price * 1.02, 0.05),
    ]
    
    # Test different M values, step sizes, and token caps
    m_values = [1, 2, 3, 4, 5]
    step_sizes = [10, 20, 30, 50, 75]
    cap_values = [3, 5, 7, 10, 15, 20]
    
    print("=" * 90)
    print("DYNAMIC REARM — CAPPED TOKEN INJECTION SIMULATION")
    print("=" * 90)
    print()
    print(f"Current BTC price:    ~{current_price}")
    print(f"Anchor:               {anchor:.2f}")
    print(f"Highest open SELL:    {highest_sell:.2f}")
    print(f"Existing SELLs:       {len(sells)}")
    print(f"Average $/close:      ${avg_per_close:.2f}")
    print()
    
    results = []
    
    for cap in cap_values:
        for m in m_values:
            for step in step_sizes:
                # Calculate injected entries (capped at N tokens)
                injected = []
                entry = highest_sell + m * step
                while entry < current_price and len(injected) < cap:
                    injected.append(entry)
                    entry += step
                
                if not injected:
                    continue
                
                # Calculate scenario outcomes
                scenario_outcomes = []
                for label, rev_price, weight in reversion_scenarios:
                    floating = sum((e - current_price) * 0.01 for e in injected)
                    profit = sum((e - rev_price) * 0.01 for e in injected)
                    levels_crossed = sum(1 for e in injected if rev_price < e < current_price)
                    alpha = levels_crossed * avg_per_close
                    total = profit + alpha
                    
                    scenario_outcomes.append({
                        'label': label,
                        'weight': weight,
                        'floating': floating,
                        'profit': profit,
                        'alpha': alpha,
                        'total': total,
                    })
                
                # Expected value
                ev = sum(w * o['total'] for o, w in [(o, s[2]) for s, o in zip(reversion_scenarios, scenario_outcomes)])
                max_dd = min(o['total'] for o in scenario_outcomes)
                rr = ev / abs(max_dd) if max_dd != 0 else float('inf')
                
                results.append({
                    'cap': cap,
                    'm': m,
                    'step': step,
                    'num_injected': len(injected),
                    'ev': ev,
                    'max_dd': max_dd,
                    'rr': rr,
                    'scenarios': scenario_outcomes,
                    'entries': injected,
                })
    
    # Filter for positive EV and sort by R/R (prefer quality over quantity)
    positive_ev = [r for r in results if r['ev'] > 0]
    positive_ev.sort(key=lambda x: -x['rr'])
    
    print("TOP 15 COMBINATIONS (positive EV, sorted by Reward/Risk):")
    print(f"{'Rank':>4} {'Cap':>4} {'M':>3} {'Step':>6} {'Inject':>7} {'EV':>10} {'MaxDD':>10} {'R/R':>6}")
    print("-" * 90)
    
    for rank, r in enumerate(positive_ev[:15], 1):
        print(f"{rank:>4}  {r['cap']:>3}  {r['m']:>3}  ${r['step']:>4}  {r['num_injected']:>6}  "
              f"${r['ev']:>8.2f}  ${r['max_dd']:>8.2f}  {r['rr']:>5.1f}x")
    
    print()
    
    # Also show best EV (regardless of R/R)
    best_ev = max(results, key=lambda x: x['ev'])
    best_rr = max(positive_ev, key=lambda x: x['rr']) if positive_ev else None
    
    print("=" * 90)
    print(f"BALANCED OPTIMAL (best R/R with EV > $100):")
    # Find best R/R with at least $100 EV
    balanced = [r for r in positive_ev if r['ev'] > 100]
    if balanced:
        best_balanced = max(balanced, key=lambda x: x['rr'])
        print(f"  Cap={best_balanced['cap']}, M={best_balanced['m']}, Step=${best_balanced['step']}")
        print(f"  Injected: {best_balanced['num_injected']} SELLs")
        print(f"  Entry range: ${best_balanced['entries'][0]:.2f} - ${best_balanced['entries'][-1]:.2f}")
        print(f"  EV: ${best_balanced['ev']:.2f}/cycle")
        print(f"  Max Drawdown: ${best_balanced['max_dd']:.2f}")
        print(f"  Reward/Risk: {best_balanced['rr']:.1f}x")
        print()
        
        # Scenario breakdown
        print(f"  SCENARIO BREAKDOWN:")
        print(f"  {'Scenario':>35} {'Weight':>7} {'Floating':>10} {'Profit':>10} {'Alpha':>8} {'Total':>10}")
        print(f"  {'-'*85}")
        for o in best_balanced['scenarios']:
            print(f"  {o['label']:>35} {o['weight']:>6.0%}  ${o['floating']:>8.2f}  ${o['profit']:>8.2f}  "
                  f"${o['alpha']:>6.2f}  ${o['total']:>8.2f}")
    
    print()
    print("=" * 90)
    print("KEY FINDINGS:")
    print(f"  1. Capping injected tokens dramatically improves R/R vs uncapped (65 SELLs → 3-10 SELLs)")
    print(f"  2. The optimal balance: Cap={best_balanced['cap']}, M={best_balanced['m']}, Step=${best_balanced['step']}")
    print(f"     → EV=${best_balanced['ev']:.2f}/cycle, R/R={best_balanced['rr']:.1f}x")
    print(f"  3. This is {best_balanced['ev']/236*100:.0f}% of the naive below-anchor EV ($236/cycle)")
    print(f"     but with {best_balanced['rr']/2.7*100:.0f}% of the R/R (2.7x naive)")
    print()
    print(f"  RECOMMENDATION: Implement capped dynamic rearm with")
    print(f"  → Max {best_balanced['cap']} tokens, M={best_balanced['m']} steps above highest SELL, ${best_balanced['step']} step")
    print(f"  → This captures pullback alpha without excessive floating risk")
    print("=" * 90)

if __name__ == "__main__":
    main()
