#!/usr/bin/env python3
"""Analyze cluster size distribution in the 709 combined-scorer signals.

Answers: How many signals are in small vs large clusters?
Does the Solitary Mycelium filter (<20 concurrent signals) add value
to the combined scorer, or is it redundant?
"""
import joblib
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
TABLE_PATH = ROOT / "reports" / "coinbase_spot_fee_survival_training_table.csv"
TAIL_MODEL = ROOT / "reports" / "models" / "coinbase_spot_tail_predictor.joblib"
FG_MODEL = ROOT / "reports" / "models" / "coinbase_spot_fast_green_model.joblib"

def load_model(path):
    if not path.exists():
        return None
    return joblib.load(path)

def score_with_model(df, model):
    if "categorical" in model:
        cat_features = model["categorical"]
        num_features = model["numeric"]
    elif "categorical_cols" in model:
        cat_features = model["categorical_cols"]
        num_features = [c for c in model["feature_cols"] if c not in cat_features]
    else:
        raise ValueError(f"Unknown model format: {list(model.keys())}")
    
    pipe = model["model"]
    for col in cat_features:
        df[col] = df[col].astype(str).fillna("")
    for col in num_features:
        df[col] = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return pipe.predict_proba(df[cat_features + num_features])[:, 1]

def main():
    print("=" * 80)
    print("CLUSTER SIZE ANALYSIS — Combined Scorer Signals")
    print("=" * 80)

    df = pd.read_csv(TABLE_PATH)
    df["net_pct"] = df["gross_pct"] - 2.4

    split_at = int(len(df) * 0.75)
    test_df = df.iloc[split_at:].copy()
    print(f"Test set: {len(test_df):,} rows")

    tail_model = load_model(TAIL_MODEL)
    fg_model = load_model(FG_MODEL)
    
    if not tail_model or not fg_model:
        print("ERROR: Models not found")
        return

    print("\nScoring test set...")
    test_tail = score_with_model(test_df, tail_model)
    test_fg = score_with_model(test_df, fg_model)

    # Count concurrent signals per timestamp
    time_counts = test_df.groupby("time").size().to_dict()
    test_df["cluster_size"] = test_df["time"].map(time_counts)

    # Filter to combined scorer signals
    mask = (test_tail >= 0.95) & (test_fg >= 0.90)
    signals = test_df[mask].copy()
    signals["tail_prob"] = test_tail[mask]
    signals["fg_prob"] = test_fg[mask]

    print(f"\nCombined scorer signals: {len(signals):,}")
    print(f"Unique timestamps: {signals['time'].nunique():,}")
    print(f"Avg cluster size: {signals['cluster_size'].mean():.1f}")

    # Cluster size distribution
    print(f"\n{'='*60}")
    print(f"CLUSTER SIZE DISTRIBUTION")
    print(f"{'='*60}")
    
    size_buckets = [
        ("Tiny (1-5)", 1, 5),
        ("Small (6-10)", 6, 10),
        ("Medium (11-20)", 11, 20),
        ("Large (21-50)", 21, 50),
        ("Huge (51+)", 51, 999999),
    ]
    
    for label, low, high in size_buckets:
        bucket = signals[(signals["cluster_size"] >= low) & (signals["cluster_size"] <= high)]
        if len(bucket) == 0:
            continue
        
        avg_net = bucket["net_pct"].mean()
        win_rate = (bucket["net_pct"] > 0).mean()
        cum_net = bucket["net_pct"].sum()
        
        print(f"  {label:<15} {len(bucket):>4} signals  avg_net={avg_net:>7.4f}%  win={win_rate:>6.1%}  cum={cum_net:>8.2f}%")

    # Solitary Mycelium filter analysis
    print(f"\n{'='*60}")
    print(f"SOLITARY MYCELIUM FILTER ANALYSIS")
    print(f"{'='*60}")
    
    for threshold in [10, 15, 20, 30, 50]:
        filtered = signals[signals["cluster_size"] < threshold]
        if len(filtered) == 0:
            continue
        
        avg_net = filtered["net_pct"].mean()
        win_rate = (filtered["net_pct"] > 0).mean()
        cum_net = filtered["net_pct"].sum()
        unique_times = filtered["time"].nunique()
        
        print(f"  Cluster < {threshold:>3}: {len(filtered):>4} signals, {unique_times:>3} cycles, "
              f"avg_net={avg_net:>7.4f}%, win={win_rate:>6.1%}, cum={cum_net:>8.2f}%")

    # Compare small vs large clusters
    small = signals[signals["cluster_size"] < 10]
    large = signals[signals["cluster_size"] >= 50]
    
    print(f"\n{'='*60}")
    print(f"SMALL vs LARGE CLUSTERS")
    print(f"{'='*60}")
    
    if len(small) > 0:
        print(f"  Small (<10): {len(small)} signals, avg_net={small['net_pct'].mean():.4f}%, "
              f"win={(small['net_pct']>0).mean()*100:.1f}%, cum={small['net_pct'].sum():.2f}%")
    
    if len(large) > 0:
        print(f"  Large (≥50): {len(large)} signals, avg_net={large['net_pct'].mean():.4f}%, "
              f"win={(large['net_pct']>0).mean()*100:.1f}%, cum={large['net_pct'].sum():.2f}%")

    # Execution slot compression with cluster filter
    print(f"\n{'='*60}")
    print(f"EXECUTION COMPRESSION WITH CLUSTER FILTER")
    print(f"{'='*60}")
    
    for threshold in [10, 20, 50]:
        filtered = signals[signals["cluster_size"] < threshold]
        if len(filtered) == 0:
            continue
        
        cycles = filtered.groupby("time").apply(
            lambda g: g.nlargest(3, lambda x: x["tail_prob"] * x["fg_prob"])
        ).reset_index(drop=True)
        
        if len(cycles) == 0:
            continue
        
        from datetime import datetime
        unique_times = cycles["time"].nunique()
        min_t = cycles["time"].min()
        max_t = cycles["time"].max()
        span_days = (datetime.utcfromtimestamp(max_t) - datetime.utcfromtimestamp(min_t)).total_seconds() / 86400
        
        cum_net = cycles["net_pct"].sum()
        avg_net = cycles["net_pct"].mean()
        win_rate = (cycles["net_pct"] > 0).mean()
        
        print(f"  Cluster < {threshold}: {len(cycles)} signals, {unique_times} cycles, "
              f"cum={cum_net:.2f}%, avg={avg_net:.4f}%, win={win_rate*100:.1f}%")
        if span_days > 0:
            daily = cum_net / span_days
            print(f"    Span: {span_days:.1f} days, daily={daily:.2f}%")

    print(f"\n{'='*80}")
    print("DONE")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
