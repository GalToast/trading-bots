#!/usr/bin/env python3
"""Mad Scientist Combined Simulation — ALL interventions together.

Combines:
1. Spread admission gate (spread>=50, MER>=2.5)
2. Tighter stops (max_loss=1.5%, no_mfe=0.5%)
3. Trailing winners (1.5x multiplier on rent >0.5%)
4. Position count increase (estimate: 1.5x more trades)
"""
import json
from pathlib import Path

EVENT_LOG = Path("reports/kraken_spot_maker_machinegun_shadow_events.jsonl")

def load_events():
    events = []
    with open(EVENT_LOG) as f:
        for line in f:
            try:
                events.append(json.loads(line.strip()))
            except:
                pass
    return events

def main():
    events = load_events()
    closes = [e for e in events if "close" in e.get("action", "")]
    
    print("=" * 80)
    print("COMBINED INTERVENTION SIMULATION — ALL CEILINGS SHATTERED")
    print("=" * 80)
    print(f"Analyzing {len(closes)} historical closes\n")
    
    # Simulate each intervention
    baseline_total = sum(e.get("net_pct", 0) for e in closes)
    baseline_wins = sum(1 for e in closes if e.get("net_pct", 0) > 0)
    
    # Step 1: Spread filter — removes low-spread products
    spread_admitted = 0
    spread_total = 0
    spread_wins = 0
    for e in closes:
        # Approximate spread based on product (from earlier analysis)
        prod = e.get("product_id", "")
        spread_map = {
            "HOUSE-USD": 659, "BTR-USD": 98, "FOLKS-USD": 182, "BANANAS31-USD": 44,
            "GRASS-USD": 39, "KSM-USD": 21, "AUD-USD": 1, "DASH-USD": 3,
            "CRV-USD": 9, "AERO-USD": 21, "ENS-USD": 32,
        }
        spread = spread_map.get(prod, 50)  # Default: assume passes
        mer_map = {
            "HOUSE-USD": 4.06, "BTR-USD": 3.52, "FOLKS-USD": 12.63, "BANANAS31-USD": 5.46,
            "GRASS-USD": 3.89, "KSM-USD": 2.42, "AUD-USD": 0.43, "DASH-USD": 0.22,
            "CRV-USD": 0.36, "AERO-USD": 1.17, "ENS-USD": 6.07,
        }
        mer = mer_map.get(prod, 3.0)  # Default: assume passes
        
        if spread >= 50 and mer >= 2.5:
            spread_admitted += 1
            spread_total += e.get("net_pct", 0)
            if e.get("net_pct", 0) > 0:
                spread_wins += 1
    
    # Step 2: Tighter stops
    tighter_total = 0
    for e in closes:
        net = e.get("net_pct", 0)
        reason = e.get("reason", "")
        if "no_mfe_adverse_stop" in reason and net < -0.50:
            net = -0.50
        elif "emergency_stop" in reason and net < -1.5:
            net = -1.5
        tighter_total += net
    
    # Step 3: Trailing winners
    trailing_total = 0
    for e in closes:
        net = e.get("net_pct", 0)
        reason = e.get("reason", "")
        if "rent_harvest" in reason and net > 0.50:
            net = net * 1.5
        trailing_total += net
    
    # Step 4: Combined (spread + tighter + trailing)
    combined_total = 0
    combined_wins = 0
    for e in closes:
        prod = e.get("product_id", "")
        spread = spread_map.get(prod, 50)
        mer = mer_map.get(prod, 3.0)
        
        if spread < 50 or mer < 2.5:
            continue  # Not admitted
        
        net = e.get("net_pct", 0)
        reason = e.get("reason", "")
        
        # Tighter stops
        if "no_mfe_adverse_stop" in reason and net < -0.50:
            net = -0.50
        elif "emergency_stop" in reason and net < -1.5:
            net = -1.5
        
        # Trailing winners
        if "rent_harvest" in reason and net > 0.50:
            net = net * 1.5
        
        combined_total += net
        if net > 0:
            combined_wins += 1
    
    # Print results
    print(f"{'='*80}")
    print(f"RESULTS TABLE:")
    print(f"{'='*80}")
    print(f"{'Metric':<25} {'Baseline':>12} {'Spread':>12} {'Tighter':>12} {'Trailing':>12} {'COMBINED':>12}")
    print("-" * 80)
    print(f"{'Total Net %':<25} {baseline_total:>12.2f} {spread_total:>12.2f} {tighter_total:>12.2f} {trailing_total:>12.2f} {combined_total:>12.2f}")
    print(f"{'Closes':<25} {len(closes):>12} {spread_admitted:>12} {'~':>12} {'~':>12} {combined_wins + (spread_admitted - combined_wins):>12}")
    print(f"{'Win Rate':<25} {baseline_wins/len(closes)*100:>11.1f}% {spread_wins/spread_admitted*100 if spread_admitted else 0:>11.1f}% {'~':>12} {'~':>12} {'~':>12}")
    
    # Projections
    print(f"\n{'='*80}")
    print(f"MONEY PROJECTIONS:")
    print(f"{'='*80}")
    
    # Current: 29 closes over ~2 days = ~14.5 closes/day
    closes_per_day = len(closes) / 2
    
    # Baseline: 14.5 closes/day * 0.059% avg = 0.85%/day
    baseline_daily = baseline_total / 2
    combined_daily = combined_total / 2
    
    # With position count increase (1.5x more closes)
    combined_daily_with_volume = combined_daily * 1.5
    
    print(f"\nBaseline (current): {baseline_daily:+.2f}%/day → {baseline_daily * 30:+.2f}%/month → ${100 * (1 + baseline_daily/100)**30 - 100:.2f}/mo on $100")
    print(f"Combined: {combined_daily:+.2f}%/day → {combined_daily * 30:+.2f}%/month → ${100 * (1 + combined_daily/100)**30 - 100:.2f}/mo on $100")
    print(f"Combined + 1.5x volume: {combined_daily_with_volume:+.2f}%/day → {combined_daily_with_volume * 30:+.2f}%/month → ${100 * (1 + combined_daily_with_volume/100)**30 - 100:.2f}/mo on $100")
    
    # The multiplier
    if baseline_total != 0:
        mult = combined_total / baseline_total
        print(f"\nCombined vs Baseline multiplier: {mult:.1f}x")
    print(f"Combined + volume vs Baseline: {combined_total * 1.5 / baseline_total:.1f}x")

if __name__ == "__main__":
    main()
