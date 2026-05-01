#!/usr/bin/env python3
"""Build temporal features for the V2 tail predictor model.

These features were added by opencode's V2 model and are required for scoring:
- tail_hit_rate_5: 5-period rolling hit rate of high-gross trades
- time_since_tail: periods since last high-gross trade
- prev_ret_1_bps: previous candle's return
- trend_3: 3-period trend direction
- trend_6: 6-period trend direction  
- non_tail_streak: consecutive non-tail periods
"""
import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLE_PATH = ROOT / "reports" / "coinbase_spot_fee_survival_training_table.csv"
TAIL_THRESHOLD = 2.5  # gross > 2.5% = high-gross

def build_temporal_features(df):
    """Add temporal features to the training table, sorted chronologically per product."""
    df = df.sort_values(["product_id", "time"]).copy()
    
    # High-gross flag
    df["is_tail"] = (df["gross_pct"] > TAIL_THRESHOLD).astype(int)
    
    # Group by product for per-product temporal features
    groups = df.groupby("product_id")
    
    # 1. tail_hit_rate_5: rolling 5-period hit rate
    df["tail_hit_rate_5"] = groups["is_tail"].transform(
        lambda x: x.shift(1).rolling(5, min_periods=1).mean()
    )
    
    # 2. time_since_tail: periods since last tail
    def periods_since_tail(group):
        result = []
        last_tail_idx = -999
        for i, (idx, val) in enumerate(group.items()):
            if val == 1:
                last_tail_idx = i
            result.append(i - last_tail_idx if last_tail_idx >= 0 else 999)
        return pd.Series(result, index=group.index)
    
    df["time_since_tail"] = groups["is_tail"].transform(periods_since_tail)
    
    # 3. prev_ret_1_bps: lag the ret_1_bps feature
    df["prev_ret_1_bps"] = groups["ret_1_bps"].transform(lambda x: x.shift(1))
    
    # 4. trend_3: direction of last 3-period return sum
    df["trend_3"] = groups["ret_1_bps"].transform(
        lambda x: x.shift(1).rolling(3, min_periods=1).mean()
    )
    
    # 5. trend_6: direction of last 6-period return sum
    df["trend_6"] = groups["ret_1_bps"].transform(
        lambda x: x.shift(1).rolling(6, min_periods=1).mean()
    )
    
    # 6. non_tail_streak: consecutive non-tail count
    def non_tail_streak(group):
        result = []
        streak = 0
        for val in group:
            if val == 0:
                streak += 1
            else:
                streak = 0
            result.append(streak)
        return pd.Series(result, index=group.index)
    
    df["non_tail_streak"] = groups["is_tail"].transform(non_tail_streak)
    
    # Fill NaN from shifts
    temporal_cols = ["tail_hit_rate_5", "time_since_tail", "prev_ret_1_bps", 
                     "trend_3", "trend_6", "non_tail_streak"]
    df[temporal_cols] = df[temporal_cols].fillna(0)
    
    return df

def main():
    print("Loading training table...")
    df = pd.read_csv(TABLE_PATH)
    print(f"  Rows: {len(df):,}, Products: {df['product_id'].nunique()}")
    
    print("\nBuilding temporal features...")
    df = build_temporal_features(df)
    
    temporal_cols = ["tail_hit_rate_5", "time_since_tail", "prev_ret_1_bps",
                     "trend_3", "trend_6", "non_tail_streak"]
    
    print("\nTemporal feature stats:")
    for col in temporal_cols:
        print(f"  {col:<25} mean={df[col].mean():>10.4f}  std={df[col].std():>10.4f}  "
              f"min={df[col].min():>10.4f}  max={df[col].max():>10.4f}")
    
    # Save augmented table
    output_path = ROOT / "reports" / "coinbase_spot_fee_survival_training_table_v2.csv"
    df.to_csv(output_path, index=False)
    print(f"\nSaved to: {output_path}")
    print(f"  New columns: {temporal_cols}")
    
    # Quick check: does the temporal data help separation?
    print("\nTail vs Non-Tail temporal feature comparison:")
    tail_df = df[df["gross_pct"] > TAIL_THRESHOLD]
    non_tail = df[df["gross_pct"] <= TAIL_THRESHOLD]
    
    print(f"  {'Feature':<25} {'Non-Tail Mean':>15} {'Tail Mean':>15} {'Ratio':>8}")
    print(f"  {'-'*65}")
    for col in temporal_cols:
        nt_mean = non_tail[col].mean()
        t_mean = tail_df[col].mean()
        ratio = t_mean / nt_mean if nt_mean != 0 else float('inf')
        print(f"  {col:<25} {nt_mean:>15.4f} {t_mean:>15.4f} {ratio:>8.2f}x")

if __name__ == "__main__":
    main()
