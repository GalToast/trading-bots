#!/usr/bin/env python3
"""Mad Scientist Admission Rule Analysis for @codex-spot-runtime.

Goal: Concrete fields/thresholds that distinguish:
- HOUSE-USD (+6.67%), BTR-USD (+2.07%) ← winners we want to admit
- GRASS-USD (-0.96%), ENS open ← losers we want to block

Analyze what these products looked like BEFORE entry using:
- Maker Opportunity Board (MER, tail, fg scores)
- Hindsight audit data
- Volatility/spread characteristics
"""
import json
from pathlib import Path

OPPORTUNITY_PATH = Path("reports/kraken_maker_opportunity_board.json")
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
    # Load opportunity board
    with open(OPPORTUNITY_PATH) as f:
        opp_board = json.load(f)
    board = {r["product_id"]: r for r in opp_board.get("rows", [])}
    
    # Load events
    events = load_events()
    closes = [e for e in events if "close" in e.get("action", "")]
    
    print("=" * 80)
    print("ADMISSION RULE ANALYSIS — HOUSE/BTR vs GRASS/ENS Pattern")
    print("=" * 80)
    
    # Winners
    winners = ["HOUSE-USD", "BTR-USD", "FOLKS-USD", "BANANAS31-USD"]
    losers = ["GRASS-USD", "ENS-USD", "KSM-USD", "AUD-USD"]
    
    print(f"\n{'='*80}")
    print(f"WINNERS (products we want to admit):")
    print(f"{'='*80}")
    print(f"{'Product':<14} {'MER':>8} {'Tail':>8} {'FG':>10} {'Net%':>8} {'Reason':<30}")
    print("-" * 80)
    for prod in winners:
        r = board.get(prod, {})
        mer = r.get("mer", "N/A")
        tail = r.get("tail_prob", "N/A")
        fg = r.get("fast_green_prob", "N/A")
        
        # Find close event
        close_net = "N/A"
        close_reason = "N/A"
        for e in closes:
            if e.get("product_id") == prod and "close" in e.get("action", ""):
                close_net = f"{e.get('net_pct', 0):+.2f}"
                close_reason = e.get("reason", "?")
        
        if isinstance(mer, float):
            print(f"{prod:<14} {mer:>8.2f} {tail:>8.4f} {fg:>10.6f} {close_net:>8} {close_reason:<30}")
        else:
            print(f"{prod:<14} {str(mer):>8} {str(tail):>8} {str(fg):>10} {close_net:>8} {close_reason:<30}")
    
    print(f"\n{'='*80}")
    print(f"LOSERS (products we want to block):")
    print(f"{'='*80}")
    print(f"{'Product':<14} {'MER':>8} {'Tail':>8} {'FG':>10} {'Net%':>8} {'Reason':<30}")
    print("-" * 80)
    for prod in losers:
        r = board.get(prod, {})
        mer = r.get("mer", "N/A")
        tail = r.get("tail_prob", "N/A")
        fg = r.get("fast_green_prob", "N/A")
        
        close_net = "N/A"
        close_reason = "N/A"
        for e in closes:
            if e.get("product_id") == prod and "close" in e.get("action", ""):
                close_net = f"{e.get('net_pct', 0):+.2f}"
                close_reason = e.get("reason", "?")
        
        if isinstance(mer, float):
            print(f"{prod:<14} {mer:>8.2f} {tail:>8.4f} {fg:>10.6f} {close_net:>8} {close_reason:<30}")
        else:
            print(f"{prod:<14} {str(mer):>8} {str(tail):>8} {str(fg):>10} {close_net:>8} {close_reason:<30}")
    
    # Pattern analysis
    print(f"\n{'='*80}")
    print(f"PATTERN DISCOVERY:")
    print(f"{'='*80}")
    
    win_mers = [board[p]["mer"] for p in winners if p in board and isinstance(board[p].get("mer"), (int, float))]
    lose_mers = [board[p]["mer"] for p in losers if p in board and isinstance(board[p].get("mer"), (int, float))]
    
    if win_mers and lose_mers:
        print(f"Winner MER range: {min(win_mers):.2f} - {max(win_mers):.2f} (mean: {sum(win_mers)/len(win_mers):.2f})")
        print(f"Loser MER range: {min(lose_mers):.2f} - {max(lose_mers):.2f} (mean: {sum(lose_mers)/len(lose_mers):.2f})")
        
        # Find the admission threshold
        # Everything above min(winner MER) and below max(loser MER) is ambiguous
        min_win_mer = min(win_mers)
        max_lose_mer = max(lose_mers)
        
        print(f"\nAdmission threshold candidates:")
        print(f"  MER >= {min_win_mer:.2f}: admits {sum(1 for m in win_mers if m >= min_win_mer)}/{len(win_mers)} winners, blocks {sum(1 for m in lose_mers if m >= min_win_mer)}/{len(lose_mers)} losers")
        print(f"  MER < {max_lose_mer:.2f}: blocks {sum(1 for m in lose_mers if m < max_lose_mer)}/{len(lose_mers)} losers, admits {sum(1 for m in win_mers if m < max_lose_mer)}/{len(win_mers)} winners")
        
        # Best threshold
        best_threshold = None
        best_score = 0
        for threshold in [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 15.0, 20.0]:
            win_admitted = sum(1 for m in win_mers if m >= threshold)
            lose_blocked = sum(1 for m in lose_mers if m < threshold)
            score = win_admitted + lose_blocked
            if score > best_score:
                best_score = score
                best_threshold = threshold
        
        print(f"\n  Best single threshold: MER >= {best_threshold}")
        win_admitted = sum(1 for m in win_mers if m >= best_threshold)
        lose_blocked = sum(1 for m in lose_mers if m < best_threshold)
        print(f"    Admits {win_admitted}/{len(win_mers)} winners ({win_admitted/len(win_mers):.0%})")
        print(f"    Blocks {lose_blocked}/{len(lose_mers)} losers ({lose_blocked/len(lose_mers):.0%})")
    
    # Now check ALL products on the board with this threshold
    print(f"\n{'='*80}")
    print(f"BOARD-WIDE ADMISSION RULE:")
    print(f"{'='*80}")
    if best_threshold:
        admitted = []
        blocked = []
        for r in opp_board.get("rows", []):
            mer = r.get("mer", 0)
            if mer >= best_threshold:
                admitted.append(r["product_id"])
            else:
                blocked.append(r["product_id"])
        
        print(f"MER >= {best_threshold} rule:")
        print(f"  Admitted: {len(admitted)} products")
        for p in sorted(admitted)[:20]:
            mer = board.get(p, {}).get("mer", 0)
            print(f"    {p}: MER={mer:.2f}")
        if len(admitted) > 20:
            print(f"    ... and {len(admitted)-20} more")
        
        print(f"\n  Blocked: {len(blocked)} products")
        for p in sorted(blocked)[:20]:
            mer = board.get(p, {}).get("mer", 0)
            print(f"    {p}: MER={mer:.2f}")
        if len(blocked) > 20:
            print(f"    ... and {len(blocked)-20} more")

if __name__ == "__main__":
    main()
