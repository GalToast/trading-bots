#!/usr/bin/env python3
"""Mad Scientist Money Velocity Simulator.

Simulates what would have happened with different parameter choices
on the existing close data.

Interventions:
1. Tighter stops (1.5% max loss, 0.50% no-MFE stop)
2. Trailing winners (let positions >0.50% run 2x longer)
3. More positions (15 vs 10 max)
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

def simulate_baseline(closes):
    """What actually happened."""
    total = sum(e.get("net_pct", 0) for e in closes)
    wins = [e for e in closes if e.get("net_pct", 0) > 0]
    losses = [e for e in closes if e.get("net_pct", 0) <= 0]
    return {
        "name": "Baseline (what happened)",
        "closes": len(closes),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(closes) if closes else 0,
        "total_net": total,
        "avg_win": sum(e.get("net_pct", 0) for e in wins) / len(wins) if wins else 0,
        "avg_loss": sum(e.get("net_pct", 0) for e in losses) / len(losses) if losses else 0,
        "best_trade": max((e.get("net_pct", 0) for e in closes), default=0),
        "worst_trade": min((e.get("net_pct", 0) for e in closes), default=0),
    }

def simulate_tighter_stops(closes, max_loss_pct=1.5, no_mfe_stop_pct=0.50):
    """What if we had tighter stops?"""
    adjusted = []
    for e in closes:
        net = e.get("net_pct", 0)
        reason = e.get("reason", "")
        
        # If the close was a big loss from adverse stop, cap it
        if "no_mfe_adverse_stop" in reason and net < -no_mfe_stop_pct:
            net = -no_mfe_stop_pct  # Would have stopped earlier
        elif "emergency_stop" in reason and net < -max_loss_pct:
            net = -max_loss_pct  # Would have stopped at max loss
        
        adjusted_e = e.copy()
        adjusted_e["net_pct"] = net
        adjusted.append(adjusted_e)
    
    return simulate_baseline(adjusted)

def simulate_trailing_winners(closes, trail_threshold=0.50, trail_multiplier=1.5):
    """What if we let winners above threshold run longer?"""
    adjusted = []
    for e in closes:
        net = e.get("net_pct", 0)
        reason = e.get("reason", "")
        
        # If it was a rent harvest above threshold, let it run
        if "rent_harvest" in reason and net > trail_threshold:
            net = net * trail_multiplier  # Capture more of the move
        
        adjusted_e = e.copy()
        adjusted_e["net_pct"] = net
        adjusted.append(adjusted_e)
    
    return simulate_baseline(adjusted)

def simulate_combined(closes, **kwargs):
    """Both interventions together."""
    adjusted = []
    for e in closes:
        net = e.get("net_pct", 0)
        reason = e.get("reason", "")
        
        # Tighter stops
        if "no_mfe_adverse_stop" in reason and net < -kwargs.get("no_mfe_stop", 0.50):
            net = -kwargs.get("no_mfe_stop", 0.50)
        elif "emergency_stop" in reason and net < -kwargs.get("max_loss", 1.5):
            net = -kwargs.get("max_loss", 1.5)
        
        # Trailing winners
        if "rent_harvest" in reason and net > kwargs.get("trail_threshold", 0.50):
            net = net * kwargs.get("trail_multiplier", 1.5)
        
        adjusted_e = e.copy()
        adjusted_e["net_pct"] = net
        adjusted.append(adjusted_e)
    
    return simulate_baseline(adjusted)

def main():
    events = load_events()
    closes = [e for e in events if "close" in e.get("action", "")]
    
    if not closes:
        print("No closes found!")
        return
    
    print("=" * 80)
    print("MAD SCIENTIST MONEY VELOCITY SIMULATION")
    print("=" * 80)
    print(f"Analyzing {len(closes)} historical closes\n")
    
    # Run simulations
    baseline = simulate_baseline(closes)
    tighter = simulate_tighter_stops(closes)
    trailing = simulate_trailing_winners(closes)
    combined = simulate_combined(closes)
    
    # Print results
    header = f"{'Metric':<20} {'Baseline':>12} {'Tighter':>12} {'Trailing':>12} {'Combined':>12}"
    print(header)
    print("-" * 80)
    
    print(f"{'Total Net %':<20} {baseline['total_net']:>12.2f} {tighter['total_net']:>12.2f} {trailing['total_net']:>12.2f} {combined['total_net']:>12.2f}")
    print(f"{'Win Rate':<20} {baseline['win_rate']:>11.1%} {tighter['win_rate']:>11.1%} {trailing['win_rate']:>11.1%} {combined['win_rate']:>11.1%}")
    print(f"{'Avg Win':<20} {baseline['avg_win']:>12.4f} {tighter['avg_win']:>12.4f} {trailing['avg_win']:>12.4f} {combined['avg_win']:>12.4f}")
    print(f"{'Avg Loss':<20} {baseline['avg_loss']:>12.4f} {tighter['avg_loss']:>12.4f} {trailing['avg_loss']:>12.4f} {combined['avg_loss']:>12.4f}")
    print(f"{'Best Trade':<20} {baseline['best_trade']:>12.4f} {tighter['best_trade']:>12.4f} {trailing['best_trade']:>12.4f} {combined['best_trade']:>12.4f}")
    print(f"{'Worst Trade':<20} {baseline['worst_trade']:>12.4f} {tighter['worst_trade']:>12.4f} {trailing['worst_trade']:>12.4f} {combined['worst_trade']:>12.4f}")
    
    print(f"\n{'='*80}")
    print("IMPACT ANALYSIS:")
    print(f"{'='*80}")
    print(f"Tighter stops alone: {tighter['total_net'] - baseline['total_net']:+.2f}% improvement")
    print(f"Trailing winners alone: {trailing['total_net'] - baseline['total_net']:+.2f}% improvement")
    print(f"Combined: {combined['total_net'] - baseline['total_net']:+.2f}% improvement ({(combined['total_net'] / baseline['total_net'] - 1) * 100:+.0f}% multiplier)")
    
    if combined['total_net'] > 0:
        closes_per_day = len(closes) / 2  # Approximate 2 days of data
        daily_net = combined['total_net'] / 2
        print(f"\nProjected daily return with combined interventions: {daily_net:+.2f}%")
        print(f"Projected monthly return (30 days): {daily_net * 30:+.2f}%")
        print(f"Projected monthly return on $100: ${100 * (1 + daily_net/100)**30 - 100:.2f}")

if __name__ == "__main__":
    main()
