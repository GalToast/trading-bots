#!/usr/bin/env python3
"""Audit escape timing across all lattice lanes.

Answers: How often does the escape fire before oscillation has time to work?
This identifies lanes where cluster-aware or patient escape would have saved money.

Usage:
    python scripts/build_escape_timing_audit.py
"""
import json
import os
import glob
from collections import defaultdict

def analyze_escape_lane(event_path):
    """Analyze a single lane's escape timing."""
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

    if not opens or not escapes:
        return None

    # Group opens by timestamp (burst detection)
    open_bursts = defaultdict(list)
    for o in opens:
        open_bursts[o["ts_utc"]].append(o)

    # Group escapes by timestamp
    escape_bursts = defaultdict(list)
    for e in escapes:
        escape_bursts[e["ts_utc"]].append(e)

    # Analyze each escape burst
    escape_analysis = []
    for esc_ts, esc_group in sorted(escape_bursts.items()):
        total_pnl = sum(e.get("realized_pnl", 0) for e in esc_group)
        directions = defaultdict(int)
        for e in esc_group:
            directions[e.get("direction", "?")] += 1

        # Find corresponding open burst (most recent one before this escape)
        matching_opens = []
        for open_ts, open_group in sorted(open_bursts.items(), reverse=True):
            if open_ts < esc_ts:
                matching_opens = open_group
                open_ts_match = open_ts
                break

        # Check if opens were clustered (same fill price ± small tolerance)
        if matching_opens:
            fill_prices = [o.get("fill_price", 0) for o in matching_opens]
            if fill_prices:
                fill_spread = max(fill_prices) - min(fill_prices)
                same_fill = fill_spread < 0.01  # Within 1 cent = same fill cluster
            else:
                fill_spread = 0
                same_fill = False
        else:
            fill_spread = 0
            same_fill = False

        escape_analysis.append({
            "escape_ts": esc_ts,
            "escape_count": len(esc_group),
            "total_pnl": round(total_pnl, 2),
            "directions": dict(directions),
            "action": esc_group[0].get("action", "unknown"),
            "matching_opens": len(matching_opens),
            "fill_spread": round(fill_spread, 4),
            "same_fill_cluster": same_fill,
        })

    return {
        "lane": os.path.basename(event_path),
        "total_opens": len(opens),
        "total_escapes": len(escapes),
        "open_bursts": len(open_bursts),
        "escape_bursts": len(escape_bursts),
        "escape_details": escape_analysis,
    }


def main():
    reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports")
    event_files = glob.glob(os.path.join(reports_dir, "*_events.jsonl"))

    print(f"Scanning {len(event_files)} event files for escape timing analysis...")

    results = []
    for ef in sorted(event_files):
        result = analyze_escape_lane(ef)
        if result and result["total_escapes"] > 0:
            results.append(result)

    print(f"\nFound {len(results)} lanes with escape activity:")

    for r in sorted(results, key=lambda x: sum(d["total_pnl"] for d in x["escape_details"])):
        total_escape_pnl = sum(d["total_pnl"] for d in r["escape_details"])
        cluster_escapes = [d for d in r["escape_details"] if d["same_fill_cluster"]]
        cluster_pnl = sum(d["total_pnl"] for d in cluster_escapes)

        print(f"\n--- {r['lane']} ---")
        print(f"  Opens: {r['total_opens']} in {r['open_bursts']} bursts")
        print(f"  Escapes: {r['total_escapes']} in {r['escape_bursts']} bursts, total PNL: ${total_escape_pnl:+.2f}")
        if cluster_escapes:
            print(f"  ⚠️  CLUSTER ESCAPES: {len(cluster_escapes)} bursts, ${cluster_pnl:+.2f} (same-fill positions escaped together)")
        for d in r["escape_details"]:
            cluster_flag = " ⚠️ SAME-FILL" if d["same_fill_cluster"] else ""
            print(f"    {d['action']}: {d['escape_count']} pos, ${d['total_pnl']:+.2f}, "
                  f"dirs={d['directions']}, opens_matched={d['matching_opens']}, "
                  f"fill_spread={d['fill_spread']:.4f}{cluster_flag}")


if __name__ == "__main__":
    main()
