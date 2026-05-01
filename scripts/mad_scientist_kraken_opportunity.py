#!/usr/bin/env python3
"""Mad Scientist: Kraken Maker Opportunity Analysis

The hindsight audit shows 6.81% avg capture rate — but that's skewed by losses.
Let me find the REAL pattern: which products are PROFITABLE vs which bleed?
"""
import json
from pathlib import Path

REPORT_PATH = Path("reports/kraken_maker_hindsight_analysis.json")

def main():
    with open(REPORT_PATH) as f:
        data = json.load(f)
    
    closes = data["efficiency_by_product"]
    
    # Group by product
    product_stats = {}
    for c in closes:
        prod = c["product_id"]
        if prod not in product_stats:
            product_stats[prod] = {"wins": 0, "losses": 0, "total_net": 0.0, "count": 0, "max_favorable_sum": 0.0}
        
        product_stats[prod]["count"] += 1
        product_stats[prod]["total_net"] += c["actual_net"]
        product_stats[prod]["max_favorable_sum"] += c["max_favorable"]
        
        if c["actual_net"] > 0:
            product_stats[prod]["wins"] += 1
        else:
            product_stats[prod]["losses"] += 1
    
    print("=" * 80)
    print("KRAKEN MAKER — PRODUCT-LEVEL PROFITABILITY ANALYSIS")
    print("=" * 80)
    print(f"{'Product':<12} {'Trades':>6} {'Wins':>5} {'Losses':>6} {'Win%':>6} {'TotalNet%':>10} {'AvgNet%':>9} {'AvgMaxFav%':>11}")
    print("-" * 80)
    
    for prod, stats in sorted(product_stats.items(), key=lambda x: x[1]["total_net"], reverse=True):
        win_pct = stats["wins"] / stats["count"] * 100
        avg_net = stats["total_net"] / stats["count"]
        avg_max_fav = stats["max_favorable_sum"] / stats["count"]
        print(f"{prod:<12} {stats['count']:>6} {stats['wins']:>5} {stats['losses']:>6} {win_pct:>5.1f}% "
              f"{stats['total_net']:>10.4f} {avg_net:>9.4f} {avg_max_fav:>11.4f}")
    
    # The KEY insight: what's the pattern in winners vs losers?
    print(f"\n{'='*80}")
    print("PATTERN ANALYSIS:")
    print(f"{'='*80}")
    
    winners = {k: v for k, v in product_stats.items() if v["total_net"] > 0}
    losers = {k: v for k, v in product_stats.items() if v["total_net"] <= 0}
    
    print(f"\nWINNERS ({len(winners)} products, {sum(v['count'] for v in winners.values())} trades):")
    for prod, stats in sorted(winners.items(), key=lambda x: x[1]["total_net"], reverse=True):
        print(f"  {prod}: {stats['count']} trades, {stats['wins']}/{stats['count']} wins, "
              f"{stats['total_net']:.2f}% total, {stats['total_net']/stats['count']:.2f}% avg")
    
    print(f"\nLOSERS ({len(losers)} products, {sum(v['count'] for v in losers.values())} trades):")
    for prod, stats in sorted(losers.items(), key=lambda x: x[1]["total_net"]):
        print(f"  {prod}: {stats['count']} trades, {stats['wins']}/{stats['count']} wins, "
              f"{stats['total_net']:.2f}% total, {stats['total_net']/stats['count']:.2f}% avg")
    
    # What's the capture rate on WINNERS?
    winner_captures = [c["capture_rate"] for c in closes if c["product_id"] in winners and c["actual_net"] > 0]
    if winner_captures:
        print(f"\nAvg capture rate on winning trades: {sum(winner_captures)/len(winner_captures):.1%}")
    
    # The MAD SCIENTIST QUESTION: If we ONLY traded the winners, what would happen?
    total_winner_net = sum(v["total_net"] for v in winners.values())
    total_loser_net = sum(v["total_net"] for v in losers.values())
    total_net = total_winner_net + total_loser_net
    
    print(f"\n{'='*80}")
    print("COUNTERFACTUAL: What if we only traded the winning products?")
    print(f"{'='*80}")
    print(f"Winner products net: {total_winner_net:.4f}%")
    print(f"Loser products net: {total_loser_net:.4f}%")
    print(f"Actual total net: {total_net:.4f}%")
    print(f"\nIf we filtered to only winners: {total_winner_net:.4f}% over {sum(v['count'] for v in winners.values())} trades")
    print(f"  Avg per trade: {total_winner_net / sum(v['count'] for v in winners.values()):.4f}%")

if __name__ == "__main__":
    main()
