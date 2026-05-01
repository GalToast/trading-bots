#!/usr/bin/env python3
"""
Top Edges Module — Quick access to the best strategies from 320 tested.

Usage:
    from top_edges import get_top_n, get_by_category, get_by_hit_rate, get_strategy
    
    # Get top 10 by PnL
    top10 = get_top_n(10, by="pnl")
    
    # Get all volume strategies
    vol = get_by_category("volume")
    
    # Get strategies with >50% hit rate
    high_wr = get_by_hit_rate(min_hit_rate=50)
    
    # Get a specific strategy
    strat = get_strategy("time_decay_signal")
"""

import json
from pathlib import Path

REPORTS_DIR = Path(__file__).parent.parent / "reports"
SYNTHESIS_PATH = REPORTS_DIR / "synthesis_report_final.json"

_cache = None


def _load():
    global _cache
    if _cache is None:
        with open(SYNTHESIS_PATH) as f:
            _cache = json.load(f)
    return _cache


def get_top_n(n=10, by="pnl"):
    """Get top N strategies by PnL or hit rate."""
    data = _load()
    edges = data["top_20_edges"]
    if by == "pnl":
        return edges[:n]
    elif by == "hit_rate":
        sorted_edges = sorted(edges, key=lambda x: float(x["hit_rate"].replace("%", "")), reverse=True)
        return sorted_edges[:n]
    return edges[:n]


def get_by_category(category):
    """Get all strategies in a category, sorted by PnL."""
    data = _load()
    return [e for e in data["top_20_edges"] if e["category"] == category]


def get_by_hit_rate(min_hit_rate=50):
    """Get strategies with hit rate >= min_hit_rate."""
    data = _load()
    results = []
    for e in data["top_10_hit_rate"]:
        hit = float(e["hit_rate"].replace("%", ""))
        if hit >= min_hit_rate:
            results.append(e)
    return results


def get_strategy(name):
    """Get a specific strategy by name."""
    data = _load()
    for e in data["top_20_edges"]:
        if e["strategy"] == name:
            return e
    return None


def get_category_summary():
    """Get summary of all categories."""
    data = _load()
    return data["category_summary"]


def get_recommendations():
    """Get research recommendations."""
    data = _load()
    return data["recommendations"]


def print_summary():
    """Print a human-readable summary."""
    data = _load()
    print(f"\n{'='*70}")
    print(f"  TOP EDGES SUMMARY — {data['total_tested']} strategies tested")
    print(f"{'='*70}\n")
    print(f"  {'Strategy':<28} {'Category':<16} {'PnL':<10} {'Hit%':<7}")
    print(f"  {'-'*65}")
    for e in data["top_20_edges"][:15]:
        hr = e["hit_rate"]
        if isinstance(hr, str):
            hr = hr.replace("%", "")
        try:
            hr = f"{float(hr):.1f}%"
        except:
            hr = str(hr)
        print(f"  {e['strategy']:<28} {e['category']:<16} ${e['total_net_pnl']:>8.0f}  {hr:>5}")
    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    print_summary()
