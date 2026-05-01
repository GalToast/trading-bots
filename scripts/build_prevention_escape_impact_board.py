#!/usr/bin/env python3
"""Combined prevention + escape impact analysis.

Quantifies the total max-profit potential when BOTH interventions are applied:
1. Spread-based entry prevention (don't open when spread > Nx normal)
2. Cluster-aware escape (escape as cluster, not individually)

Usage:
    python scripts/build_prevention_escape_impact_board.py
"""
import json
import os
import glob
from collections import defaultdict

def analyze_lane(event_path):
    """Analyze a single lane for prevention + escape impact."""
    try:
        events = []
        with open(event_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    except (json.JSONDecodeError, IOError):
        return None

    opens = [e for e in events if e.get("action") == "open_ticket"]
    escapes = [e for e in events if "escape" in e.get("action", "")]

    if not opens:
        return None

    # Analyze spreads at open
    spreads_at_open = []
    for o in opens:
        spread = o.get("spread_px", o.get("spread_at_entry"))
        if spread is not None and spread > 0:
            spreads_at_open.append(spread)

    if not spreads_at_open:
        return None

    median_spread = sorted(spreads_at_open)[len(spreads_at_open) // 2]

    # Analyze escapes
    escape_total_pnl = sum(e.get("realized_pnl", 0) for e in escapes)

    # Group escapes by spread (high spread vs normal spread)
    high_spread_escapes = []
    normal_spread_escapes = []
    for e in escapes:
        # Find corresponding open to get spread
        entry_fill = e.get("entry_fill_price")
        if entry_fill:
            matching_open = None
            for o in opens:
                if abs(o.get("fill_price", 0) - entry_fill) < 0.01:
                    matching_open = o
                    break
            if matching_open:
                spread = matching_open.get("spread_px", matching_open.get("spread_at_entry", 0))
                if spread > median_spread * 2:
                    high_spread_escapes.append(e)
                else:
                    normal_spread_escapes.append(e)

    high_spread_escape_pnl = sum(e.get("realized_pnl", 0) for e in high_spread_escapes)
    normal_spread_escape_pnl = sum(e.get("realized_pnl", 0) for e in normal_spread_escapes)

    # Cluster analysis
    from collections import Counter
    escape_timestamps = Counter(e.get("ts_utc") for e in escapes)
    cluster_escapes = sum(count for ts, count in escape_timestamps.items() if count > 1)
    cluster_escape_pnl = sum(
        e.get("realized_pnl", 0)
        for e in escapes
        if escape_timestamps.get(e.get("ts_utc"), 0) > 1
    )

    return {
        "lane": os.path.basename(event_path),
        "total_opens": len(opens),
        "total_escapes": len(escapes),
        "escape_total_pnl": round(escape_total_pnl, 2),
        "median_spread": round(median_spread, 4),
        "high_spread_escapes": len(high_spread_escapes),
        "high_spread_escape_pnl": round(high_spread_escape_pnl, 2),
        "normal_spread_escapes": len(normal_spread_escapes),
        "normal_spread_escape_pnl": round(normal_spread_escape_pnl, 2),
        "cluster_escapes": cluster_escapes,
        "cluster_escape_pnl": round(cluster_escape_pnl, 2),
        "prevention_savable": round(high_spread_escape_pnl, 2),
        "escape_optimization_savable": round(cluster_escape_pnl, 2),
    }


def main():
    reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports")
    event_files = glob.glob(os.path.join(reports_dir, "*_events.jsonl"))

    print(f"Scanning {len(event_files)} event files for combined prevention + escape impact...")

    results = []
    for ef in sorted(event_files):
        result = analyze_lane(ef)
        if result and result["total_escapes"] > 0:
            results.append(result)

    print(f"\nFound {len(results)} lanes with escape activity:")

    total_escape_pnl = sum(r["escape_total_pnl"] for r in results)
    total_prevention_savable = sum(r["prevention_savable"] for r in results)
    total_cluster_savable = sum(r["escape_optimization_savable"] for r in results)

    print(f"\n{'='*80}")
    print(f"COMBINED PREVENTION + ESCAPE IMPACT")
    print(f"{'='*80}")
    print(f"Total escape losses: ${total_escape_pnl:+.2f}")
    print(f"Prevention-savable (high-spread entries): ${total_prevention_savable:+.2f}")
    print(f"Cluster-aware escape savable: ${total_cluster_savable:+.2f}")
    print(f"Combined potential savings: ${total_prevention_savable + total_cluster_savable:+.2f}")

    print(f"\n{'='*80}")
    print(f"PER-LANE BREAKDOWN")
    print(f"{'='*80}")

    for r in sorted(results, key=lambda x: x["escape_total_pnl"]):
        prevention_pct = abs(r["prevention_savable"] / r["escape_total_pnl"] * 100) if r["escape_total_pnl"] != 0 else 0
        cluster_pct = abs(r["escape_optimization_savable"] / r["escape_total_pnl"] * 100) if r["escape_total_pnl"] != 0 else 0

        print(f"\n--- {r['lane']} ---")
        print(f"  Opens: {r['total_opens']}, Escapes: {r['total_escapes']}, Total PNL: ${r['escape_total_pnl']:+.2f}")
        print(f"  Median spread: {r['median_spread']:.4f}")
        print(f"  High-spread escapes: {r['high_spread_escapes']} (${r['high_spread_escape_pnl']:+.2f}, {prevention_pct:.0f}%)")
        print(f"  Cluster escapes: {r['cluster_escapes']} (${r['cluster_escape_pnl']:+.2f}, {cluster_pct:.0f}%)")

    # Write JSON output
    output_path = os.path.join(reports_dir, "prevention_escape_impact_board.json")
    with open(output_path, "w") as f:
        json.dump({
            "generated_at": "2026-04-16T15:36:00+00:00",
            "total_escape_pnl": round(total_escape_pnl, 2),
            "total_prevention_savable": round(total_prevention_savable, 2),
            "total_cluster_savable": round(total_cluster_savable, 2),
            "combined_potential_savings": round(total_prevention_savable + total_cluster_savable, 2),
            "lanes": results,
        }, f, indent=2)

    print(f"\nWrote {output_path}")


if __name__ == "__main__":
    main()
