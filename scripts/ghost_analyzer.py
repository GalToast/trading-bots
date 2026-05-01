#!/usr/bin/env python3
"""Ghost Horizon Analyzer — Read and analyze ghost horizon data.

Once ghost horizons are live in the runner, this tool reads the data
and produces the key metrics:
- ghost_upside: max ghost minus exit net
- ghost_time_to_peak: which horizon had the max?
- ghost_direction: rising, falling, or choppy
- Decision rule: should we implement trailing?

Usage:
  python scripts/ghost_analyzer.py
  python scripts/ghost_analyzer.py --summary
  python scripts/ghost_analyzer.py --by-product
"""
import json
from pathlib import Path
from collections import defaultdict

GHOST_LOG = Path("reports/kraken_spot_maker_machinegun_ghost_horizons.jsonl")

def load_ghosts():
    if not GHOST_LOG.exists():
        return []
    ghosts = []
    with open(GHOST_LOG) as f:
        for line in f:
            try:
                ghosts.append(json.loads(line.strip()))
            except:
                pass
    return ghosts

def analyze_ghost(ghost):
    """Analyze a single ghost horizon."""
    exit_net = ghost.get("exit_net", 0)
    horizons = {
        "30s": ghost.get("ghost_30s"),
        "60s": ghost.get("ghost_60s"),
        "180s": ghost.get("ghost_180s"),
        "300s": ghost.get("ghost_300s"),
    }
    
    # Filter out None/stale values
    valid = {k: v for k, v in horizons.items() if v is not None and v != "stale"}
    
    if not valid:
        return {"product": ghost.get("product", "?"), "status": "no_data", "exit_net": exit_net}
    
    max_val = max(valid.values())
    max_key = max(valid, key=lambda k: valid[k])
    min_val = min(valid.values())
    
    ghost_upside = max_val - exit_net
    
    # Direction
    values = list(valid.values())
    if len(values) >= 2:
        if values[-1] > values[0]:
            direction = "rising"
        elif values[-1] < values[0]:
            direction = "falling"
        else:
            direction = "choppy"
    else:
        direction = "single"
    
    return {
        "product": ghost.get("product", "?"),
        "exit_net": exit_net,
        "ghost_upside": ghost_upside,
        "max_ghost": max_val,
        "max_at": max_key,
        "min_ghost": min_val,
        "direction": direction,
        "valid_horizons": len(valid),
        "status": "ok",
    }

def main():
    ghosts = load_ghosts()
    
    if not ghosts:
        print("No ghost data found yet!")
        print(f"Expected at: {GHOST_LOG}")
        print("Ghost horizons will be logged once the runner starts marking them.")
        return
    
    print("=" * 80)
    print(f"GHOST HORIZON ANALYSIS — {len(ghosts)} ghosts")
    print("=" * 80)
    
    analyses = [analyze_ghost(g) for g in ghosts]
    ok = [a for a in analyses if a["status"] == "ok"]
    
    if not ok:
        print("No valid ghost data yet (all ghosts incomplete or stale)")
        return
    
    # Summary stats
    upsides = [a["ghost_upside"] for a in ok]
    directions = defaultdict(int)
    for a in ok:
        directions[a["direction"]] += 1
    
    print(f"\nValid ghosts: {len(ok)}/{len(ghosts)}")
    print(f"\nGhost Upside (max ghost - exit net):")
    print(f"  Mean: {sum(upsides)/len(upsides):+.4f}%")
    print(f"  Median: {sorted(upsides)[len(upsides)//2]:+.4f}%")
    print(f"  Max: {max(upsides):+.4f}%")
    print(f"  Min: {min(upsides):+.4f}%")
    print(f"  Positive: {sum(1 for u in upsides if u > 0)}/{len(upsides)} ({sum(1 for u in upsides if u > 0)/len(upsides)*100:.0f}%)")
    
    print(f"\nGhost Direction:")
    for direction, count in sorted(directions.items()):
        print(f"  {direction}: {count} ({count/len(ok)*100:.0f}%)")
    
    # Decision rule
    median_upside = sorted(upsides)[len(upsides)//2]
    print(f"\n{'='*80}")
    print(f"DECISION RULE (after 50+ winners):")
    print(f"{'='*80}")
    if len(ok) < 50:
        print(f"  Not enough data yet: {len(ok)}/50 ghosts")
        print(f"  Current median upside: {median_upside:+.4f}%")
        if median_upside > 0.10:
            print(f"  → TRENDING: Trailing WOULD help (median > 0.10%)")
        elif median_upside < 0.05:
            print(f"  → TRENDING: Current exits are optimal (median < 0.05%)")
        else:
            print(f"  → TRENDING: Inconclusive (median 0.05-0.10%)")
    else:
        if median_upside > 0.10:
            print(f"  ✅ IMPLEMENT TRAILING — median upside {median_upside:+.4f}% > 0.10%")
        elif median_upside < 0.05:
            print(f"  ✅ CURRENT EXITS OPTIMAL — median upside {median_upside:+.4f}% < 0.05%")
        else:
            print(f"  ⚠️ INCONCLUSIVE — median upside {median_upside:+.4f}% in gray zone")
    
    # Per-product breakdown
    print(f"\n{'='*80}")
    print(f"BY PRODUCT:")
    print(f"{'='*80}")
    by_product = defaultdict(list)
    for a in ok:
        by_product[a["product"]].append(a)
    
    for prod, analyses_list in sorted(by_product.items(), key=lambda x: -sum(a["ghost_upside"] for a in x)):
        avg_upside = sum(a["ghost_upside"] for a in analyses_list) / len(analyses_list)
        print(f"\n  {prod}: {len(analyses_list)} ghosts, avg upside: {avg_upside:+.4f}%")
        for a in analyses_list[:3]:
            print(f"    exit={a['exit_net']:+.4f}%  ghost_max={a['max_ghost']:+.4f}%  upside={a['ghost_upside']:+.4f}%  at={a['max_at']}  dir={a['direction']}")

if __name__ == "__main__":
    main()
