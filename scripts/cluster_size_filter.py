#!/usr/bin/env python3
"""Cluster Size Filter for Coinbase Spot Machinegun Shadow.

Implements Gemini's "Solitary Mycelium" filter:
- Small clusters (<10 concurrent signals) = idiosyncratic demand = 2× win rate
- Large clusters (>50 concurrent signals) = systemic noise = fakeout reclaims

This script:
1. Loads the strategy board (live radar output)
2. Computes cluster sizes for each row's timestamp
3. Filters rows by max cluster size
4. Reports the impact on signal count and quality

Usage: python scripts/cluster_size_filter.py [--max-cluster-size 20] [--dry-run]
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STRATEGY_BOARD_PATH = ROOT / "reports" / "coinbase_spot_machinegun_strategy_board.json"


def load_board():
    with open(STRATEGY_BOARD_PATH) as f:
        return json.load(f)


def compute_cluster_sizes(rows, time_col="last_updated"):
    """Group rows by timestamp and compute cluster sizes.
    
    Returns a dict mapping each row index to its cluster size.
    """
    # Group by timestamp
    from collections import defaultdict
    time_groups = defaultdict(list)
    
    for idx, row in enumerate(rows):
        ts = row.get(time_col, row.get("updated_at", "unknown"))
        time_groups[ts].append(idx)
    
    # Assign cluster size to each row
    cluster_sizes = {}
    for ts, indices in time_groups.items():
        for idx in indices:
            cluster_sizes[idx] = len(indices)
    
    return cluster_sizes


def analyze_cluster_distribution(rows, cluster_sizes):
    """Analyze the distribution of cluster sizes."""
    sizes = list(cluster_sizes.values())
    
    print(f"\nCluster Size Distribution:")
    print(f"  Total rows: {len(rows)}")
    print(f"  Unique timestamps: {len(set(cluster_sizes.values()))}")
    print(f"  Min cluster size: {min(sizes)}")
    print(f"  Max cluster size: {max(sizes)}")
    print(f"  Mean cluster size: {sum(sizes)/len(sizes):.1f}")
    print(f"  Median cluster size: {sorted(sizes)[len(sizes)//2]}")
    
    # Buckets
    buckets = {
        "solitary_1": 0,
        "tiny_2_5": 0,
        "small_6_10": 0,
        "moderate_11_20": 0,
        "large_21_50": 0,
        "huge_51_plus": 0,
    }
    
    for size in sizes:
        if size == 1:
            buckets["solitary_1"] += 1
        elif size <= 5:
            buckets["tiny_2_5"] += 1
        elif size <= 10:
            buckets["small_6_10"] += 1
        elif size <= 20:
            buckets["moderate_11_20"] += 1
        elif size <= 50:
            buckets["large_21_50"] += 1
        else:
            buckets["huge_51_plus"] += 1
    
    print(f"\n  Buckets:")
    for name, count in buckets.items():
        pct = count / len(rows) * 100
        print(f"    {name:>20}: {count:>5} ({pct:>5.1f}%)")
    
    return buckets


def filter_by_cluster_size(rows, cluster_sizes, max_size):
    """Filter rows to only include those with cluster size <= max_size."""
    filtered = []
    for idx, row in enumerate(rows):
        if cluster_sizes.get(idx, 999) <= max_size:
            filtered.append(row)
    return filtered


def compare_signal_quality(original_rows, filtered_rows, label="filtered"):
    """Compare signal quality between original and filtered sets."""
    def avg_prob(rows, key):
        probs = [float(r.get(key, 0) or 0) for r in rows if r.get(key) is not None]
        return sum(probs) / len(probs) if probs else 0
    
    def avg_score(rows):
        scores = [float(r.get("machinegun_score", 0) or 0) for r in rows]
        return sum(scores) / len(scores) if scores else 0
    
    def avg_edge(rows):
        edges = [float(r.get("edge_over_hurdle_pct", 0) or 0) for r in rows]
        return sum(edges) / len(edges) if edges else 0
    
    print(f"\nSignal Quality Comparison:")
    print(f"  {'Metric':<30} {'Original':>12} {label:>12}")
    print(f"  {'-'*30} {'-'*12} {'-'*12}")
    print(f"  {'Rows':<30} {len(original_rows):>12} {len(filtered_rows):>12}")
    print(f"  {'Avg ML Survival Prob':<30} {avg_prob(original_rows, 'ml_survival_prob'):>12.4f} {avg_prob(filtered_rows, 'ml_survival_prob'):>12.4f}")
    print(f"  {'Avg Fast-Green Prob':<30} {avg_prob(original_rows, 'fast_green_prob'):>12.4f} {avg_prob(filtered_rows, 'fast_green_prob'):>12.4f}")
    print(f"  {'Avg Tail Prob':<30} {avg_prob(original_rows, 'tail_prob'):>12.4f} {avg_prob(filtered_rows, 'tail_prob'):>12.4f}")
    print(f"  {'Avg Machinegun Score':<30} {avg_score(original_rows):>12.4f} {avg_score(filtered_rows):>12.4f}")
    print(f"  {'Avg Edge Over Hurdle %':<30} {avg_edge(original_rows):>12.4f} {avg_edge(filtered_rows):>12.4f}")


def main():
    parser = argparse.ArgumentParser(description="Cluster size filter for Coinbase spot machinegun")
    parser.add_argument("--max-cluster-size", type=int, default=20, help="Maximum cluster size to include (default: 20)")
    parser.add_argument("--board-path", type=str, default=str(STRATEGY_BOARD_PATH))
    parser.add_argument("--dry-run", action="store_true", help="Only analyze, don't filter")
    args = parser.parse_args()
    
    print("=" * 80)
    print("CLUSTER SIZE FILTER ANALYSIS")
    print("=" * 80)
    
    board = load_board()
    rows = [r for r in (board.get("rows") or []) if str(r.get("playbook") or "") != "watch_only"]
    
    if not rows:
        print("No rows found in strategy board.")
        return
    
    # Compute cluster sizes
    cluster_sizes = compute_cluster_sizes(rows)
    
    # Analyze distribution
    buckets = analyze_cluster_distribution(rows, cluster_sizes)
    
    if args.dry_run:
        print(f"\nDry run complete. Use --max-cluster-size to filter.")
        return
    
    # Filter
    filtered = filter_by_cluster_size(rows, cluster_sizes, args.max_cluster_size)
    
    if not filtered:
        print(f"\nNo rows pass the cluster size filter (max={args.max_cluster_size}).")
        return
    
    # Compare quality
    compare_signal_quality(rows, filtered, label=f"cluster≤{args.max_cluster_size}")
    
    # Show top candidates after filtering
    print(f"\nTop 5 Candidates After Filter (cluster size ≤ {args.max_cluster_size}):")
    print(f"  {'Rank':<6} {'Product':<20} {'Playbook':<25} {'Score':>8} {'Edge%':>8} {'Cluster':>8}")
    print(f"  {'-'*6} {'-'*20} {'-'*25} {'-'*8} {'-'*8} {'-'*8}")
    
    for i, row in enumerate(filtered[:5]):
        idx = rows.index(row)
        print(f"  {i+1:<6} {row.get('product_id', 'N/A'):<20} {row.get('playbook', 'N/A'):<25} {float(row.get('machinegun_score', 0) or 0):>8.4f} {float(row.get('edge_over_hurdle_pct', 0) or 0):>8.4f} {cluster_sizes.get(idx, 999):>8}")
    
    # Save filtered board
    filtered_board = dict(board)
    filtered_board["rows"] = filtered + [r for r in board.get("rows", []) if str(r.get("playbook") or "") == "watch_only"]
    filtered_board["cluster_filter_applied"] = args.max_cluster_size
    filtered_board["cluster_filter_timestamp"] = "2026-04-24T07:12:00Z"
    
    output_path = ROOT / "reports" / "coinbase_spot_machinegun_strategy_board_cluster_filtered.json"
    with open(output_path, "w") as f:
        json.dump(filtered_board, f, indent=2)
    
    print(f"\nFiltered board saved to: {output_path}")
    print(f"Original rows: {len(rows)} → Filtered rows: {len(filtered)}")
    print(f"Reduction: {(1 - len(filtered)/len(rows))*100:.1f}%")


if __name__ == "__main__":
    main()
