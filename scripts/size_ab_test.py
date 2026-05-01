#!/usr/bin/env python3
"""Position Size A/B Shadow Test — Prove bigger positions = more money.

Replays historical closes at different position sizes to prove:
- 15% deploy = 2x money per trade, same win rate
- Kill condition: ANY loss at 15% → revert to 8%

Usage:
  python scripts/size_ab_test.py
  python scripts/size_ab_test.py --baseline 8 --test 15
"""
import json
import argparse
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=float, default=8.0, help="Baseline deploy %")
    parser.add_argument("--test", type=float, default=15.0, help="Test deploy %")
    args = parser.parse_args()
    
    events = load_events()
    closes = [e for e in events if "close" in e.get("action", "")]
    
    if not closes:
        print("No closes found!")
        return
    
    print("=" * 80)
    print(f"POSITION SIZE A/B TEST — Baseline {args.baseline}% vs Test {args.test}%")
    print("=" * 80)
    
    baseline_pnl = 0
    test_pnl = 0
    test_losses = 0
    test_wins = 0
    
    print(f"\n{'#':>4} {'Product':<14} {'Net%':>8} {'Baseline$':>10} {'Test$':>10} {'Result':>10}")
    print("-" * 80)
    
    for i, e in enumerate(closes, 1):
        net_pct = e.get("net_pct", 0)
        prod = e.get("product_id", "?")
        
        # PnL = net_pct/100 * deploy_pct/100 * cash
        # Assuming $100 starting cash, simplified: PnL = net_pct * deploy_pct / 100
        baseline_trade_pnl = net_pct * args.baseline / 100
        test_trade_pnl = net_pct * args.test / 100
        
        baseline_pnl += baseline_trade_pnl
        test_pnl += test_trade_pnl
        
        if net_pct < 0:
            test_losses += 1
            result = "LOSS"
        else:
            test_wins += 1
            result = "WIN"
        
        print(f"{i:>4} {prod:<14} {net_pct:>7.4f}% ${baseline_trade_pnl:>9.4f} ${test_trade_pnl:>9.4f} {result:>10}")
    
    print(f"\n{'='*80}")
    print(f"RESULTS:")
    print(f"{'='*80}")
    print(f"Closes: {len(closes)}")
    print(f"Win rate: {test_wins}/{len(closes)} ({test_wins/len(closes)*100:.1f}%)")
    print(f"Losses: {test_losses}")
    print(f"\nBaseline ({args.baseline}%): ${baseline_pnl:.4f}")
    print(f"Test ({args.test}%): ${test_pnl:.4f}")
    print(f"Multiplier: {test_pnl/baseline_pnl:.2f}x" if baseline_pnl != 0 else "N/A")
    print(f"Improvement: ${test_pnl - baseline_pnl:+.4f}")
    
    if test_losses > 0:
        print(f"\n⚠️  KILL CONDITION MET: {test_losses} loss(es) at {args.test}% deploy")
        print(f"  → DO NOT increase position size")
        print(f"  → More tape needed to prove the gate")
    else:
        print(f"\n✅ NO LOSSES at {args.test}% deploy")
        print(f"  → Position size increase is SAFE (but needs 50+ closes for confidence)")
        print(f"  → Current: {len(closes)} closes. Need 50+ for statistical confidence.")

if __name__ == "__main__":
    main()
