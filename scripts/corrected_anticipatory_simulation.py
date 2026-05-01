#!/usr/bin/env python3
import json
from pathlib import Path

def main():
    current_price = 74561
    anchor = 74054
    avg_per_close = 15.55
    
    # Corrected scenarios: pullback is relative to the PEAK, not the starting price.
    scenarios = [
        ("No rally, no pullback", current_price, current_price, 0.10),
        ("Small rally (+0.3%), full reversion", current_price * 1.003, current_price, 0.15),
        ("Small rally (+0.3%), shallow pullback (-0.1% from peak)", current_price * 1.003, current_price * 1.003 * 0.999, 0.10),
        ("Moderate rally (+0.8%), full reversion", current_price * 1.008, current_price, 0.15),
        ("Moderate rally (+0.8%), shallow pullback (-0.2% from peak)", current_price * 1.008, current_price * 1.008 * 0.998, 0.15),
        ("Moderate rally (+0.8%), deep pullback (-0.5% from peak)", current_price * 1.008, current_price * 1.008 * 0.995, 0.10),
        ("Big rally (+1.5%), shallow pullback (-0.2% from peak)", current_price * 1.015, current_price * 1.015 * 0.998, 0.10),
        ("Big rally (+1.5%), moderate pullback (-0.5% from peak)", current_price * 1.015, current_price * 1.015 * 0.995, 0.10),
        ("Massive rally (+2.5%), sustained (no pullback)", current_price * 1.025, current_price * 1.025, 0.05),
    ]
    
    # Test the "optimal" config from the original script
    n = 10
    m = 1
    step = 50
    injected = [current_price + (m + i) * step for i in range(n)]
    
    print("CORRECTED ADVERSARIAL SIMULATION")
    print(f"Injection levels: {', '.join([f'${e:.2f}' for e in injected])}\n")
    print(f"{ 'Scenario':>55} {'Peak':>10} {'Reversion':>10} {'Fired':>5} {'Floating':>10} {'Profit':>10}")
    print("-" * 110)
    
    scenario_outcomes = []
    for label, peak, rev, prob in scenarios:
        fired = [e for e in injected if e <= peak]
        
        if not fired:
            floating = 0
            profit = 0
        else:
            floating = sum((e - peak) * 0.01 for e in fired)
            profit = sum((e - rev) * 0.01 for e in fired)
            
        print(f"{label:>55} ${peak:>8.2f} ${rev:>8.2f}  {len(fired):>4}  ${floating:>8.2f}  ${profit:>8.2f}")
        scenario_outcomes.append({
            'label': label,
            'prob': prob,
            'fired': len(fired),
            'floating': floating,
            'profit': profit,
        })
        
    ev = sum(o['prob'] * o['profit'] for o in scenario_outcomes)
    max_dd = min(o['profit'] for o in scenario_outcomes) # Realized loss if forced to close, or persistent float
    print("\nCorrected Expected Value: ${:.2f}".format(ev))
    print("Corrected Max Drawdown: ${:.2f}".format(max_dd))

if __name__ == "__main__":
    main()
