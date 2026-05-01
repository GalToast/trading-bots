#!/usr/bin/env python3
"""Middle Rule Analysis — Find the optimal gate that admits winners without GRASS.

Codex found tight gate (100/3.5) = +$1.48, loose gate (50/2.5) = +$1.40.
The loose gate is worse because it admits GRASS.

Find the middle rule that:
- Admits ALL 4 winners (HOUSE, FOLKS, BTR, BANANAS31)
- Blocks GRASS and other losers
- Maximizes net PnL
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
    
    if not closes:
        print("No closes found!")
        return
    
    # Board data for spread/MER
    import pandas as pd
    from pathlib import Path
    
    # We need to map products to their spread/MER at the time of trade
    # Use current board as approximation
    opp_path = Path("reports/kraken_maker_opportunity_board.json")
    if opp_path.exists():
        with open(opp_path) as f:
            board = {r["product_id"]: r for r in json.load(f).get("rows", [])}
    else:
        board = {}
    
    print("=" * 80)
    print("MIDDLE RULE SEARCH — Optimal admission gate")
    print("=" * 80)
    
    # Spread thresholds to test
    spread_thresholds = [30, 40, 50, 60, 75, 80, 90, 100, 110, 125, 150]
    mer_thresholds = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
    
    print(f"\n{'='*80}")
    print(f"GATE SCAN RESULTS:")
    print(f"{'='*80}")
    print(f"{'Spread':>8} {'MER':>6} {'Closes':>8} {'Wins':>5} {'WR':>6} {'Net%':>10} {'Admits':>25} {'Blocks':>25}")
    print("-" * 80)
    
    # For each gate, compute what it would admit/block
    for spread_thresh in spread_thresholds:
        for mer_thresh in mer_thresholds:
            admitted = []
            blocked = []
            total_net = 0
            wins = 0
            
            for e in closes:
                prod = e.get("product_id", "")
                net = e.get("net_pct", 0)
                
                # Get spread/MER from board (approximation)
                r = board.get(prod, {})
                spread = r.get("spread_bps", 50)  # default: assume passes
                mer = r.get("mer", 3.0)
                
                if spread >= spread_thresh and mer >= mer_thresh:
                    admitted.append(prod)
                    total_net += net
                    if net > 0:
                        wins += 1
                else:
                    blocked.append(prod)
            
            n = len(admitted)
            wr = wins / n if n > 0 else 0
            
            # Only print interesting gates
            if n > 0 and (
                (spread_thresh == 75 and mer_thresh == 2.5) or
                (spread_thresh == 50 and mer_thresh == 2.5) or
                (spread_thresh == 100 and mer_thresh == 3.5) or
                (spread_thresh == 75 and mer_thresh == 3.0) or
                (spread_thresh == 60 and mer_thresh == 2.5) or
                (spread_thresh == 80 and mer_thresh == 2.5) or
                abs(total_net) > 0.1
            ):
                admitted_unique = sorted(set(admitted))
                blocked_unique = sorted(set(blocked))
                
                admitted_str = ", ".join(admitted_unique[:5])
                if len(admitted_unique) > 5:
                    admitted_str += f" +{len(admitted_unique)-5}"
                blocked_str = ", ".join(blocked_unique[:5])
                if len(blocked_unique) > 5:
                    blocked_str += f" +{len(blocked_unique)-5}"
                
                print(f"{spread_thresh:>8} {mer_thresh:>6.1f} {n:>8} {wins:>5} {wr:>5.0%} "
                      f"{total_net:>10.4f} {admitted_str:>25} {blocked_str:>25}")
    
    # Highlight the best gates
    print(f"\n{'='*80}")
    print(f"TOP GATES BY NET PnL:")
    print(f"{'='*80}")
    
    results = []
    for spread_thresh in spread_thresholds:
        for mer_thresh in mer_thresholds:
            admitted = []
            total_net = 0
            wins = 0
            
            for e in closes:
                prod = e.get("product_id", "")
                net = e.get("net_pct", 0)
                r = board.get(prod, {})
                spread = r.get("spread_bps", 50)
                mer = r.get("mer", 3.0)
                
                if spread >= spread_thresh and mer >= mer_thresh:
                    admitted.append(prod)
                    total_net += net
                    if net > 0:
                        wins += 0
            
            n = len(admitted)
            wr = wins / n if n > 0 else 0
            results.append((spread_thresh, mer_thresh, n, wins, wr, total_net, admitted))
    
    # Sort by net PnL
    for spread, mer, n, wins, wr, net, admitted in sorted(results, key=lambda x: x[5], reverse=True)[:10]:
        if n > 0:
            admitted_unique = sorted(set(admitted))
            print(f"  spread>={spread}, MER>={mer}: {n} trades, {wins}/{n} ({wr:.0%}), net={net:+.4f}%")
            print(f"    Products: {', '.join(admitted_unique)}")

if __name__ == "__main__":
    main()
