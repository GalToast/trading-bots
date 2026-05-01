#!/usr/bin/env python3
"""Mad Scientist Product Filter: Kraken Maker Whitelist Builder.

Uses hindsight audit results to build a product whitelist/blacklist.
Only products with positive net track record are whitelisted.

Usage:
  python scripts/mad_scientist_product_filter.py [--apply]

Without --apply: shows what products would be filtered
With --apply: patches the Maker Opportunity Board to exclude bleeders
"""
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
EVENT_LOG = ROOT / "reports" / "kraken_spot_maker_machinegun_shadow_events.jsonl"
OPPORTUNITY_PATH = ROOT / "reports" / "kraken_maker_opportunity_board.json"
FILTER_PATH = ROOT / "reports" / "kraken_maker_product_filter.json"

def load_jsonl(path: Path):
    if not path.exists():
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line.strip()))
            except:
                continue
    return rows

def compute_product_stats(events):
    """Compute per-product profitability stats from hindsight events."""
    closes = [e for e in events if e["action"] == "close_maker_shadow"]
    
    product_stats = {}
    for c in closes:
        prod = c["product_id"]
        if prod not in product_stats:
            product_stats[prod] = {"wins": 0, "losses": 0, "total_net": 0.0, "count": 0}
        
        product_stats[prod]["count"] += 1
        product_stats[prod]["total_net"] += c.get("net_pct", 0.0)
        
        if c.get("net_pct", 0.0) > 0:
            product_stats[prod]["wins"] += 1
        else:
            product_stats[prod]["losses"] += 1
    
    # Compute win rate and avg net
    for prod, stats in product_stats.items():
        stats["win_rate"] = stats["wins"] / stats["count"] if stats["count"] > 0 else 0
        stats["avg_net"] = stats["total_net"] / stats["count"] if stats["count"] > 0 else 0
    
    return product_stats

def build_filter(stats, min_trades=1, min_avg_net=0.0):
    """Build whitelist: products with >= min_trades and avg_net >= min_avg_net."""
    whitelist = []
    blacklist = []
    
    for prod, s in stats.items():
        if s["count"] >= min_trades and s["avg_net"] >= min_avg_net:
            whitelist.append(prod)
        else:
            blacklist.append(prod)
    
    return sorted(whitelist), sorted(blacklist)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply filter to opportunity board")
    parser.add_argument("--min-trades", type=int, default=1, help="Min trades to consider")
    parser.add_argument("--min-avg-net", type=float, default=0.0, help="Min avg net % to whitelist")
    args = parser.parse_args()
    
    events = load_jsonl(EVENT_LOG)
    if not events:
        print("No events found in shadow log")
        return
    
    stats = compute_product_stats(events)
    
    print("=" * 80)
    print("MAD SCIENTIST PRODUCT FILTER — Kraken Maker Whitelist Builder")
    print("=" * 80)
    print(f"\nTotal closes analyzed: {sum(s['count'] for s in stats.values())}")
    print(f"Products tracked: {len(stats)}")
    
    whitelist, blacklist = build_filter(stats, args.min_trades, args.min_avg_net)
    
    print(f"\n{'='*80}")
    print(f"WHITELIST ({len(whitelist)} products — positive track record):")
    print(f"{'='*80}")
    for prod in whitelist:
        s = stats[prod]
        print(f"  ✅ {prod}: {s['count']} trades, {s['win_rate']:.0%} WR, {s['avg_net']:+.2f}% avg net")
    
    print(f"\n{'='*80}")
    print(f"BLACKLIST ({len(blacklist)} products — bleeding capital):")
    print(f"{'='*80}")
    for prod in blacklist:
        s = stats[prod]
        print(f"  ❌ {prod}: {s['count']} trades, {s['win_rate']:.0%} WR, {s['avg_net']:+.2f}% avg net")
    
    # Save filter state
    filter_state = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "whitelist": whitelist,
        "blacklist": blacklist,
        "product_stats": stats,
        "params": {
            "min_trades": args.min_trades,
            "min_avg_net": args.min_avg_net
        }
    }
    
    FILTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FILTER_PATH, "w") as f:
        json.dump(filter_state, f, indent=2)
    print(f"\nFilter state saved to {FILTER_PATH}")
    
    if args.apply:
        # Patch the opportunity board
        if not OPPORTUNITY_PATH.exists():
            print(f"\nERROR: Opportunity board not found at {OPPORTUNITY_PATH}")
            return
        
        with open(OPPORTUNITY_PATH) as f:
            opp_board = json.load(f)
        
        original_count = len(opp_board.get("rows", []))
        opp_board["rows"] = [r for r in opp_board.get("rows", []) if r["product_id"] in whitelist]
        filtered_count = len(opp_board["rows"])
        
        # Backup original
        backup_path = OPPORTUNITY_PATH.with_suffix(".json.backup")
        with open(backup_path, "w") as f:
            json.dump(opp_board, f, indent=2)
        
        with open(OPPORTUNITY_PATH, "w") as f:
            json.dump(opp_board, f, indent=2)
        
        print(f"\n{'='*80}")
        print(f"FILTER APPLIED:")
        print(f"{'='*80}")
        print(f"  Original opportunities: {original_count}")
        print(f"  After filter: {filtered_count}")
        print(f"  Removed: {original_count - filtered_count}")
        print(f"  Backup saved to: {backup_path}")
        
        if filtered_count == 0:
            print(f"\n⚠️  WARNING: No products pass the filter!")
            print(f"   The maker runner will have NO opportunities to trade.")
            print(f"   Consider relaxing --min-trades or --min-avg-net parameters.")
    else:
        print(f"\n{'='*80}")
        print(f"DRY RUN — no changes made")
        print(f"{'='*80}")
        print(f"Run with --apply to patch the opportunity board")

if __name__ == "__main__":
    main()
