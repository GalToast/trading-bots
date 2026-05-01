#!/usr/bin/env python3
"""Spread-Based Admission Filter for Kraken Maker Machinegun.

Filters the Maker Opportunity Board to only include products where
the spread is wide enough to survive maker fees AND the MER indicates
market inefficiency (maker opportunity).

Rule: spread_bps >= min_spread AND mer >= min_mer AND mer <= max_mer

Usage:
  python scripts/spread_admission_filter.py [--apply] [--min-spread 50] [--min-mer 2.5] [--max-mer 20]
"""
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
OPPORTUNITY_PATH = ROOT / "reports" / "kraken_maker_opportunity_board.json"
FILTER_PATH = ROOT / "reports" / "spread_admission_filter.json"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply filter to opportunity board")
    parser.add_argument("--min-spread", type=float, default=50.0, help="Min spread in bps (default: 50)")
    parser.add_argument("--min-mer", type=float, default=2.5, help="Min MER (default: 2.5)")
    parser.add_argument("--max-mer", type=float, default=20.0, help="Max MER (default: 20)")
    args = parser.parse_args()
    
    if not OPPORTUNITY_PATH.exists():
        print(f"ERROR: Opportunity board not found at {OPPORTUNITY_PATH}")
        return
    
    with open(OPPORTUNITY_PATH) as f:
        opp_board = json.load(f)
    
    rows = opp_board.get("rows", [])
    
    print("=" * 80)
    print(f"SPREAD ADMISSION FILTER — spread>={args.min_spread}bps, MER={args.min_mer}-{args.max_mer}")
    print("=" * 80)
    print(f"Total products on board: {len(rows)}")
    
    admitted = []
    blocked = []
    
    for r in rows:
        prod = r["product_id"]
        spread = r.get("spread_bps", 0)
        mer = r.get("mer", 0)
        
        passes_spread = spread >= args.min_spread
        passes_mer = args.min_mer <= mer <= args.max_mer
        
        if passes_spread and passes_mer:
            admitted.append(r)
        else:
            blocked.append({
                "product_id": prod,
                "spread_bps": spread,
                "mer": mer,
                "fail_spread": not passes_spread,
                "fail_mer": not passes_mer,
            })
    
    print(f"\n{'='*80}")
    print(f"ADMITTED ({len(admitted)} products — wide spread + inefficient):")
    print(f"{'='*80}")
    print(f"{'Product':<14} {'Spread':>8} {'MER':>8} {'ATR':>10} {'Playbook':<14}")
    print("-" * 80)
    for r in sorted(admitted, key=lambda x: x.get("spread_bps", 0), reverse=True):
        print(f"{r['product_id']:<14} {r.get('spread_bps', 0):>7.1f}bps {r.get('mer', 0):>8.2f} "
              f"{r.get('atr_12_bps', 0):>9.1f}bps {r.get('playbook', '?'):<14}")
    
    print(f"\n{'='*80}")
    print(f"BLOCKED ({len(blocked)} products):")
    print(f"{'='*80}")
    
    # Group by failure reason
    spread_fails = [b for b in blocked if b["fail_spread"]]
    mer_fails = [b for b in blocked if b["fail_mer"]]
    both_fails = [b for b in blocked if b["fail_spread"] and b["fail_mer"]]
    
    print(f"  Failed spread only: {len(spread_fails) - len(both_fails)}")
    print(f"  Failed MER only: {len(mer_fails) - len(both_fails)}")
    print(f"  Failed both: {len(both_fails)}")
    
    # Save filter state
    filter_state = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {"min_spread": args.min_spread, "min_mer": args.min_mer, "max_mer": args.max_mer},
        "admitted": [r["product_id"] for r in admitted],
        "admitted_count": len(admitted),
        "blocked_count": len(blocked),
    }
    
    FILTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FILTER_PATH, "w") as f:
        json.dump(filter_state, f, indent=2)
    print(f"\nFilter saved to {FILTER_PATH}")
    
    if args.apply:
        # Patch opportunity board
        backup_path = OPPORTUNITY_PATH.with_suffix(".json.backup")
        with open(backup_path, "w") as f:
            json.dump(opp_board, f, indent=2)
        
        opp_board["rows"] = admitted
        opp_board["filtered_at"] = datetime.now(timezone.utc).isoformat()
        opp_board["filter_params"] = {"min_spread": args.min_spread, "min_mer": args.min_mer, "max_mer": args.max_mer}
        
        with open(OPPORTUNITY_PATH, "w") as f:
            json.dump(opp_board, f, indent=2)
        
        print(f"\n{'='*80}")
        print(f"FILTER APPLIED:")
        print(f"{'='*80}")
        print(f"  Before: {len(rows)} products")
        print(f"  After: {len(admitted)} products")
        print(f"  Blocked: {len(blocked)}")
        print(f"  Backup: {backup_path}")
    else:
        print(f"\nDRY RUN — use --apply to patch the opportunity board")

if __name__ == "__main__":
    main()
