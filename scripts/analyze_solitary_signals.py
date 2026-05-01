#!/usr/bin/env python3
import joblib
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLE_PATH = ROOT / "reports" / "coinbase_spot_fee_survival_training_table.csv"
TAIL_MODEL = ROOT / "reports" / "models" / "coinbase_spot_tail_predictor.joblib"
FG_MODEL = ROOT / "reports" / "models" / "coinbase_spot_fast_green_model.joblib"

def score_with_model(df, model):
    cat_features = model.get("categorical", model.get("categorical_cols", []))
    num_features = model.get("numeric", [c for c in model.get("feature_cols", []) if c not in cat_features])
    pipe = model["model"]
    for col in cat_features: df[col] = df[col].astype(str).fillna("")
    for col in num_features: df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return pipe.predict_proba(df[cat_features + num_features])[:, 1]

def main():
    df = pd.read_csv(TABLE_PATH)
    # Only test set (25%)
    split_at = int(len(df) * 0.75)
    test_df = df.iloc[split_at:].copy()
    
    tail_m = joblib.load(TAIL_MODEL)
    fg_m = joblib.load(FG_MODEL)
    
    test_df["tail_p"] = score_with_model(test_df.copy(), tail_m)
    test_df["fg_p"] = score_with_model(test_df.copy(), fg_m)
    
    # The "Combined Scorer" filter
    qualified = test_df[(test_df["tail_p"] >= 0.95) & (test_df["fg_p"] >= 0.90)].copy()
    
    # Cluster sizes (from original table to represent market breadth)
    cycle_counts = df.groupby("time").size().to_frame("cluster_size")
    qualified = qualified.merge(cycle_counts, on="time")
    
    print("=" * 80)
    print("DISTRIBUTION OF QUALIFIED SIGNALS BY CLUSTER SIZE")
    print("=" * 80)
    
    bins = [0, 10, 20, 50, 100, 300]
    qualified["size_bin"] = pd.cut(qualified["cluster_size"], bins=bins)
    
    summary = qualified.groupby("size_bin").size().to_frame("Signals")
    summary["Win Rate %"] = qualified.groupby("size_bin").apply(lambda x: (x["gross_pct"] > 2.4).mean() * 100)
    
    print(summary)
    
    print("\nTotal Qualified: ", len(qualified))
    print("Small Cluster (<20) Count: ", len(qualified[qualified["cluster_size"] < 20]))

if __name__ == "__main__":
    main()
