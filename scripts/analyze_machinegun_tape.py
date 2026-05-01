#!/usr/bin/env python3
"""Analyze the machinegun opportunity tape trend across all scans."""
import json
from pathlib import Path
from collections import defaultdict, Counter

TAPE_PATH = Path(__file__).parent.parent / "reports" / "coinbase_spot_machinegun_opportunity_tape.jsonl"

def main():
    scans = []
    with open(TAPE_PATH, "r") as f:
        for line in f:
            if line.strip():
                scans.append(json.loads(line))

    print(f"Total scans: {len(scans)}")
    print()

    # PnL trajectory
    print("=== CHIP-USD PnL TRAJECTORY ===")
    for s in scans:
        pos = s["current_position_mark"]
        dec = s["decision"]
        ts = s["ts_utc"][-13:-5]  # Just HH:MM:SS
        print(f"  {ts} | bid={pos['bid']:.5f} | net_pnl=${pos['net_pnl']:.4f} | net%={pos['net_pct_on_cost']:.2f}% | decision={dec['decision']}")

    print()

    # Decision counts
    decisions = Counter(s["decision"]["decision"] for s in scans)
    print(f"Decision counts: {dict(decisions)}")
    print()

    # Product rank frequency - which products consistently rank high?
    rank_counts = defaultdict(lambda: {"appearances": 0, "total_score": 0, "count_by_rank": Counter()})
    for s in scans:
        for c in s["top_candidates"]:
            key = c["product_id"]
            rank_counts[key]["appearances"] += 1
            rank_counts[key]["total_score"] += c["machinegun_score"]
            rank_counts[key]["count_by_rank"][c["rank"]] += 1

    print("=== PRODUCT CONSISTENCY (appearing in top candidates across scans) ===")
    print(f"{'Product':<15} {'Scans':<6} {'Avg Score':<10} {'R1':<4} {'R2':<4} {'R3':<4} {'R4':<4} {'R5':<4}")
    print("-" * 60)
    for prod in sorted(rank_counts, key=lambda p: rank_counts[p]["total_score"], reverse=True):
        d = rank_counts[prod]
        avg_score = d["total_score"] / d["appearances"]
        ranks = d["count_by_rank"]
        print(f"{prod:<15} {d['appearances']:<6} {avg_score:<10.2f} {ranks[1]:<4} {ranks[2]:<4} {ranks[3]:<4} {ranks[4]:<4} {ranks[5]:<4}")

    print()

    # Momentum consistency - which products have stable positive 15m+60m returns?
    print("=== MOMENTUM STABILITY ===")
    momentum_data = defaultdict(list)
    for s in scans:
        for c in s["top_candidates"]:
            momentum_data[c["product_id"]].append({
                "ret_15m": c["ret_15m_pct"],
                "ret_60m": c["ret_60m_pct"],
                "spread": c["spread_bps"],
            })

    print(f"{'Product':<15} {'15m mean':<10} {'15m std':<10} {'60m mean':<10} {'60m std':<10} {'spread mean':<12}")
    print("-" * 80)
    for prod in sorted(momentum_data, key=lambda p: sum(d["ret_15m"] for d in momentum_data[p]) / len(momentum_data[p]), reverse=True):
        vals = momentum_data[prod]
        n = len(vals)
        mean_15m = sum(v["ret_15m"] for v in vals) / n
        std_15m = (sum((v["ret_15m"] - mean_15m)**2 for v in vals) / n) ** 0.5 if n > 1 else 0
        mean_60m = sum(v["ret_60m"] for v in vals) / n
        std_60m = (sum((v["ret_60m"] - mean_60m)**2 for v in vals) / n) ** 0.5 if n > 1 else 0
        mean_spread = sum(v["spread"] for v in vals) / n
        print(f"{prod:<15} {mean_15m:<10.2f} {std_15m:<10.2f} {mean_60m:<10.2f} {std_60m:<10.2f} {mean_spread:<12.2f}")


if __name__ == "__main__":
    main()
