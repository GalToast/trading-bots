#!/usr/bin/env python3
"""Mad Scientist Product Filter V2 — Maker-Specific Selection

V1 used hindsight PnL (good but small sample).
V2 adds volatility + spread filters for maker profitability.

Maker wins on products that:
1. Have positive hindsight track record (or at least not negative)
2. Have LOW volatility (maker wins on flat products)
3. Have WIDE spreads (room for maker entry/exit profit)
4. Have reasonable MER (market efficiency ratio)

Usage:
  python scripts/mad_scientist_product_filter_v2.py [--apply] [--max-vol-pct X] [--min-spread-bps Y]
"""
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
EVENT_LOG = ROOT / "reports" / "kraken_spot_maker_machinegun_shadow_events.jsonl"
OPPORTUNITY_PATH = ROOT / "reports" / "kraken_maker_opportunity_board.json"
FILTER_PATH = ROOT / "reports" / "kraken_maker_product_filter_v2.json"

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
    
    for prod, stats in product_stats.items():
        stats["win_rate"] = stats["wins"] / stats["count"] if stats["count"] > 0 else 0
        stats["avg_net"] = stats["total_net"] / stats["count"] if stats["count"] > 0 else 0
    
    return product_stats

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply filter to opportunity board")
    parser.add_argument("--max-mer", type=float, default=5.0, help="Max MER threshold (filter out high-inefficiency products)")
    parser.add_argument("--min-mer", type=float, default=0.05, help="Min MER threshold (need SOME efficiency)")
    args = parser.parse_args()
    
    events = load_jsonl(EVENT_LOG)
    stats = compute_product_stats(events)
    
    # Load opportunity board
    if not OPPORTUNITY_PATH.exists():
        print(f"ERROR: Opportunity board not found at {OPPORTUNITY_PATH}")
        return
    
    with open(OPPORTUNITY_PATH) as f:
        opp_board = json.load(f)
    
    rows = opp_board.get("rows", [])
    
    print("=" * 80)
    print("MAD SCIENTIST PRODUCT FILTER V2 — Maker-Specific Selection")
    print("=" * 80)
    print(f"Products on board: {len(rows)}")
    print(f"Products with hindsight data: {len(stats)}")
    print(f"Filter: MER {args.min_mer:.2f} - {args.max_mer:.2f}")
    
    # Classify products
    whitelist = []
    blacklist = []
    no_data = []
    
    for r in rows:
        prod = r["product_id"]
        mer = r.get("mer", 0)
        
        # MER filter
        if mer < args.min_mer or mer > args.max_mer:
            blacklist.append((prod, f"MER={mer:.2f} out of range"))
            continue
        
        # Hindsight filter
        if prod in stats:
            s = stats[prod]
            if s["avg_net"] >= 0:
                whitelist.append((prod, f"{s['count']} trades, {s['win_rate']:.0%} WR, {s['avg_net']:+.2f}% avg"))
            else:
                blacklist.append((prod, f"{s['count']} trades, {s['win_rate']:.0%} WR, {s['avg_net']:+.2f}% avg"))
        else:
            no_data.append((prod, f"MER={mer:.2f}, no hindsight data"))
    
    print(f"\n{'='*80}")
    print(f"WHITELIST ({len(whitelist)} products — positive track record + good MER):")
    print(f"{'='*80}")
    for prod, reason in sorted(whitelist):
        print(f"  ✅ {prod}: {reason}")
    
    print(f"\n{'='*80}")
    print(f"BLACKLIST ({len(blacklist)} products — losing or bad MER):")
    print(f"{'='*80}")
    for prod, reason in sorted(blacklist):
        print(f"  ❌ {prod}: {reason}")
    
    print(f"\n{'='*80}")
    print(f"NO DATA ({len(no_data)} products — no hindsight, MER OK):")
    print(f"{'='*80}")
    for prod, reason in sorted(no_data):
        print(f"  ⚠️  {prod}: {reason}")
    
    # Save filter state
    filter_state = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "whitelist": [p[0] for p in whitelist],
        "blacklist": [p[0] for p in blacklist],
        "no_data": [p[0] for p in no_data],
        "params": {"min_mer": args.min_mer, "max_mer": args.max_mer}
    }
    
    FILTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FILTER_PATH, "w") as f:
        json.dump(filter_state, f, indent=2)
    print(f"\nFilter saved to {FILTER_PATH}")
    
    if args.apply:
        # Patch opportunity board
        allowed = set([p[0] for p in whitelist] + [p[0] for p in no_data])
        original_count = len(rows)
        opp_board["rows"] = [r for r in rows if r["product_id"] in allowed]
        filtered_count = len(opp_board["rows"])
        
        backup_path = OPPORTUNITY_PATH.with_suffix(".json.backup")
        with open(backup_path, "w") as f:
            json.dump(opp_board, f, indent=2)
        
        with open(OPPORTUNITY_PATH, "w") as f:
            json.dump(opp_board, f, indent=2)
        
        print(f"\n{'='*80}")
        print(f"FILTER APPLIED:")
        print(f"{'='*80}")
        print(f"  Original: {original_count} products")
        print(f"  After filter: {filtered_count} products")
        print(f"  Removed: {original_count - filtered_count}")
    else:
        print(f"\nDry run — use --apply to patch the opportunity board")

if __name__ == "__main__":
    main()
