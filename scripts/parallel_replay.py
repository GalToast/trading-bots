#!/usr/bin/env python3
"""Parallel Position Shadow Replay — Prove or disprove the max=3 thesis.

Replays the historical tape with max_positions=3 instead of 1.
Tracks:
- Total net at 3x concurrent
- Worst concurrent drawdown
- Correlation of returns between products
- Win rate at 3x vs 1x
- Whether parallel positions are safe

Usage:
  python scripts/parallel_replay.py
  python scripts/parallel_replay.py --max-positions 3
  python scripts/parallel_replay.py --max-positions 5
"""
import json
import argparse
from pathlib import Path
from collections import defaultdict
import math

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

def replay_tape(events, max_positions=3, cooldown_polls=60):
    """Replay the tape with N max positions and cooldown.
    
    Returns detailed metrics about the parallel run.
    """
    # Build chronological list of opens and closes
    opens = []
    closes = []
    for e in events:
        action = e.get("action", "")
        if "open" in action.lower():
            opens.append(e)
        elif "close" in action.lower() and e.get("net_pct", 0) != 0:
            closes.append(e)
    
    # Sort by timestamp (they should already be chronological)
    # We'll process events in order
    all_events = sorted(opens + closes, key=lambda e: e.get("timestamp", ""))
    
    # Replay state
    active_positions = {}  # product_id -> position data
    product_cooldowns = {}  # product_id -> polls remaining
    total_net = 0
    total_net_baseline = 0  # What we'd have gotten with max=1
    wins = 0
    losses = 0
    concurrent_pnl_history = []  # Track PnL when multiple positions open
    max_concurrent = 0
    worst_concurrent_drawdown = 0
    product_returns = defaultdict(list)  # For correlation
    
    # Track per-product stats
    product_stats = defaultdict(lambda: {"opens": 0, "closes": 0, "net": 0, "wins": 0, "losses": 0})
    
    for e in all_events:
        action = e.get("action", "")
        prod = e.get("product_id", "")
        net = e.get("net_pct", 0)
        
        # Update cooldowns
        for p in list(product_cooldowns.keys()):
            product_cooldowns[p] -= 1
            if product_cooldowns[p] <= 0:
                del product_cooldowns[p]
        
        if "open" in action.lower():
            # Try to open position
            if prod in product_cooldowns:
                continue  # Still in cooldown
            if prod in active_positions:
                continue  # Already open
            
            if len(active_positions) >= max_positions:
                continue  # At capacity
            
            # Open the position
            active_positions[prod] = {"net_pct": 0, "opened_at": e.get("timestamp", "")}
            product_stats[prod]["opens"] += 1
            if len(active_positions) > max_concurrent:
                max_concurrent = len(active_positions)
        
        elif "close" in action.lower() and net != 0:
            # Check if this product has an open position in our replay
            if prod in active_positions:
                # Close it
                del active_positions[prod]
                product_cooldowns[prod] = cooldown_polls
                
                total_net += net
                product_stats[prod]["closes"] += 1
                product_stats[prod]["net"] += net
                product_returns[prod].append(net)
                
                if net > 0:
                    wins += 1
                    product_stats[prod]["wins"] += 1
                else:
                    losses += 1
                    product_stats[prod]["losses"] += 1
                    if abs(net) > worst_concurrent_drawdown:
                        worst_concurrent_drawdown = abs(net)
                
                # Track concurrent PnL
                if len(active_positions) > 0:
                    concurrent_pnl_history.append({
                        "product": prod,
                        "net": net,
                        "concurrent_positions": len(active_positions) + 1,  # +1 because we just closed one
                    })
    
    # Compute correlation matrix
    correlation_matrix = {}
    products = list(product_returns.keys())
    for i, p1 in enumerate(products):
        for j, p2 in enumerate(products):
            if i >= j:
                continue
            r1 = product_returns[p1]
            r2 = product_returns[p2]
            # Simple correlation: only if both have returns
            if len(r1) > 1 and len(r2) > 1:
                # Pearson correlation (simplified)
                min_len = min(len(r1), len(r2))
                r1_short = r1[:min_len]
                r2_short = r2[:min_len]
                mean1 = sum(r1_short) / len(r1_short)
                mean2 = sum(r2_short) / len(r2_short)
                cov = sum((a - mean1) * (b - mean2) for a, b in zip(r1_short, r2_short)) / min_len
                std1 = math.sqrt(sum((a - mean1) ** 2 for a in r1_short) / min_len)
                std2 = math.sqrt(sum((b - mean2) ** 2 for b in r2_short) / min_len)
                if std1 > 0 and std2 > 0:
                    corr = cov / (std1 * std2)
                    correlation_matrix[f"{p1} vs {p2}"] = corr
    
    return {
        "max_positions": max_positions,
        "cooldown_polls": cooldown_polls,
        "total_net": total_net,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / (wins + losses) if (wins + losses) > 0 else 0,
        "max_concurrent": max_concurrent,
        "worst_drawdown": worst_concurrent_drawdown,
        "concurrent_pnl_events": len(concurrent_pnl_history),
        "product_stats": dict(product_stats),
        "correlation_matrix": correlation_matrix,
        "product_returns": dict(product_returns),
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-positions", type=int, default=3, help="Max concurrent positions")
    parser.add_argument("--cooldown", type=int, default=60, help="Cooldown polls")
    args = parser.parse_args()
    
    events = load_events()
    
    print("=" * 80)
    print(f"PARALLEL POSITION SHADOW REPLAY — max={args.max_positions}, cooldown={args.cooldown}")
    print("=" * 80)
    
    # Run baseline (max=1)
    baseline = replay_tape(events, max_positions=1, cooldown_polls=args.cooldown)
    
    # Run test (max=N)
    test = replay_tape(events, max_positions=args.max_positions, cooldown_polls=args.cooldown)
    
    print(f"\n{'='*80}")
    print(f"{'Metric':<30} {'Baseline (max=1)':>18} {'Test (max=' + str(args.max_positions) + ')':>18}")
    print("-" * 80)
    print(f"{'Total net %':<30} {baseline['total_net']:>17.4f}% {test['total_net']:>17.4f}%")
    print(f"{'Wins':<30} {baseline['wins']:>18} {test['wins']:>18}")
    print(f"{'Losses':<30} {baseline['losses']:>18} {test['losses']:>18}")
    print(f"{'Win rate':<30} {baseline['win_rate']:>17.1%} {test['win_rate']:>17.1%}")
    print(f"{'Max concurrent positions':<30} {baseline['max_concurrent']:>18} {test['max_concurrent']:>18}")
    print(f"{'Worst single drawdown':<30} {baseline['worst_drawdown']:>17.4f}% {test['worst_drawdown']:>17.4f}%")
    print(f"{'Concurrent PnL events':<30} {baseline['concurrent_pnl_events']:>18} {test['concurrent_pnl_events']:>18}")
    
    # Product breakdown
    print(f"\n{'='*80}")
    print(f"PRODUCT BREAKDOWN (test run):")
    print(f"{'='*80}")
    for prod, stats in sorted(test['product_stats'].items(), key=lambda x: -x[1]['net']):
        wr = stats['wins'] / (stats['wins'] + stats['losses']) if (stats['wins'] + stats['losses']) > 0 else 0
        print(f"  {prod:14s}: {stats['closes']} closes, {stats['wins']}W/{stats['losses']}L ({wr:.0%}), net={stats['net']:+.4f}%")
    
    # Correlation
    print(f"\n{'='*80}")
    print(f"RETURN CORRELATION (test run):")
    print(f"{'='*80}")
    if test['correlation_matrix']:
        for pair, corr in sorted(test['correlation_matrix'].items(), key=lambda x: -abs(x[1])):
            print(f"  {pair:<25s}: {corr:+.4f}")
    else:
        print("  Not enough data for correlation")
    
    # Verdict
    print(f"\n{'='*80}")
    print(f"VERDICT:")
    print(f"{'='*80}")
    improvement = test['total_net'] - baseline['total_net']
    if baseline['total_net'] != 0:
        multiplier = test['total_net'] / baseline['total_net']
    else:
        multiplier = 0
    
    print(f"  Improvement: {improvement:+.4f}%")
    print(f"  Multiplier: {multiplier:.2f}x")
    
    if test['losses'] > baseline['losses']:
        print(f"  ⚠️  More losses at max={args.max_positions} ({test['losses']} vs {baseline['losses']})")
        print(f"  → Parallel positions INCREASE risk")
    else:
        print(f"  ✅ Same or fewer losses at max={args.max_positions}")
        print(f"  → Parallel positions are SAFE (based on historical tape)")
    
    if test['correlation_matrix']:
        max_corr = max(abs(v) for v in test['correlation_matrix'].values())
        if max_corr > 0.7:
            print(f"  ⚠️  High correlation detected (max |r| = {max_corr:.2f})")
            print(f"  → Parallel positions may amplify losses")
        elif max_corr > 0.3:
            print(f"  ⚡ Moderate correlation (max |r| = {max_corr:.2f})")
            print(f"  → Some diversification benefit")
        else:
            print(f"  ✅ Low correlation (max |r| = {max_corr:.2f})")
            print(f"  → Parallel positions provide diversification")

if __name__ == "__main__":
    main()
