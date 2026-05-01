#!/usr/bin/env python3
import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLE_PATH = ROOT / "reports" / "coinbase_spot_fee_survival_training_table.csv"

def main():
    print("=" * 80)
    print("CLUSTER ANCHOR ANALYSIS — Multi-Asset Topological Ranking")
    print("=" * 80)

    df = pd.read_csv(TABLE_PATH)
    # Use Coinbase fees
    df["net_pct"] = df["gross_pct"] - 2.4
    
    # Filter for the "Combined Scorer" qualified signals (simulated thresholds)
    # Based on the team's messages, they used Tail >= 0.95 and FG >= 0.90
    # Since I don't have the tail_prob in the CSV, I'll use the 'selected' mask logic
    # from my previous turn's simulation if possible. 
    # Actually, I'll just use the full table and rank EVERYTHING per timestamp 
    # to see if 'Lead' products perform better in general.
    
    print(f"Total rows: {len(df)}")
    print(f"Total execution cycles (5m): {df['time'].nunique()}")
    
    # Ranking metrics
    metrics = ["volume_mult_12", "ret_12_bps", "volatility_12_bps"]
    
    for metric in metrics:
        print(f"\n--- Ranking by {metric} ---")
        df[f"{metric}_rank"] = df.groupby("time")[metric].rank(ascending=False, method="first")
        
        # Look at the performance of Top 5 ranks
        for r in range(1, 6):
            rank_df = df[df[f"{metric}_rank"] == r]
            win_rate = (rank_df["net_pct"] > 0).mean() * 100
            avg_net = rank_df["net_pct"].mean()
            print(f"  Rank {r}: {len(rank_df):>5} signals, Win Rate {win_rate:>5.1f}%, Avg Net {avg_net:>7.4f}%")

    # Now, let's look at the "Cluster Size" effect.
    # Are signals in "Lone" cycles better than "Crowded" cycles?
    print("\n" + "=" * 80)
    print("CLUSTER SIZE EFFECT")
    print("=" * 80)
    
    cycle_counts = df.groupby("time").size().to_frame("cluster_size")
    df = df.merge(cycle_counts, on="time")
    
    # Group by cluster size bins
    df["size_bin"] = pd.cut(df["cluster_size"], bins=[0, 10, 50, 100, 300])
    
    summary = df.groupby("size_bin")["net_pct"].agg(["count", "mean", lambda x: (x > 0).mean() * 100])
    summary.columns = ["Signals", "Avg Net %", "Win Rate %"]
    print(summary)

    print("\n" + "=" * 80)
    print("OUTSIDE THE BOX CONCLUSION")
    print("=" * 80)
    
    # Find the "Sweet Spot"
    best_bin = summary["Avg Net %"].idxmax()
    print(f"The 'Sweet Spot' for execution is cycles with size {best_bin}.")
    print("This suggests the edge is REGIME-DEPENDENT.")
    
if __name__ == "__main__":
    main()
