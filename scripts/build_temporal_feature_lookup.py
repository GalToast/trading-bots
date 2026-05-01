#!/usr/bin/env python3
"""Build temporal feature lookup table for V2 tail model.

Reads the training table and computes per-product temporal features that can be
looked up by product_id during live scoring. This populates:
- tail_hit_rate_5: rolling 5-signal hit rate of high-gross trades
- time_since_tail: signals since last high-gross trade
- prev_ret_1_bps: previous signal's 1-bar return
- trend_3: 3-signal rolling mean of ret_1_bps
- trend_6: 6-signal rolling mean of ret_1_bps
- non_tail_streak: consecutive non-tail signal count

Output: JSON lookup file keyed by product_id with the LATEST temporal values.
"""
import json
import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLE_PATH = ROOT / "reports" / "coinbase_spot_fee_survival_training_table.csv"
OUTPUT_PATH = ROOT / "reports" / "coinbase_spot_temporal_features.json"
TAIL_THRESHOLD = 2.5  # gross > 2.5% = high-gross

def build_temporal_lookup():
    df = pd.read_csv(TABLE_PATH)
    df = df.sort_values(["product_id", "time"]).copy()
    df["is_tail"] = (df["gross_pct"] > TAIL_THRESHOLD).astype(int)

    lookup = {}

    for product_id, group in df.groupby("product_id"):
        group = group.reset_index(drop=True)
        n = len(group)
        if n == 0:
            continue

        # tail_hit_rate_5: rolling 5-signal hit rate (shifted by 1 to avoid lookahead)
        hit_rate = group["is_tail"].shift(1).rolling(5, min_periods=1).mean()

        # time_since_tail: signals since last tail
        time_since = []
        last_tail = -999
        for i in range(n):
            if group.iloc[i]["is_tail"] == 1:
                last_tail = i
            time_since.append(i - last_tail if last_tail >= 0 else 999)

        # prev_ret_1_bps: lagged ret_1_bps
        prev_ret = group["ret_1_bps"].shift(1)

        # trend_3: 3-signal rolling mean of ret_1_bps (shifted)
        trend_3 = group["ret_1_bps"].shift(1).rolling(3, min_periods=1).mean()

        # trend_6: 6-signal rolling mean of ret_1_bps (shifted)
        trend_6 = group["ret_1_bps"].shift(1).rolling(6, min_periods=1).mean()

        # non_tail_streak: consecutive non-tail count
        streak = []
        count = 0
        for i in range(n):
            if group.iloc[i]["is_tail"] == 0:
                count += 1
            else:
                count = 0
            streak.append(count)

        # Get the LAST values for each product (most recent temporal state)
        last_idx = n - 1
        lookup[product_id] = {
            "tail_hit_rate_5": round(float(hit_rate.iloc[last_idx]), 6) if last_idx < len(hit_rate) else 0.0,
            "time_since_tail": float(time_since[last_idx]) if last_idx < len(time_since) else 0.0,
            "prev_ret_1_bps": round(float(prev_ret.iloc[last_idx]), 6) if last_idx < len(prev_ret) else 0.0,
            "trend_3": round(float(trend_3.iloc[last_idx]), 6) if last_idx < len(trend_3) else 0.0,
            "trend_6": round(float(trend_6.iloc[last_idx]), 6) if last_idx < len(trend_6) else 0.0,
            "non_tail_streak": float(streak[last_idx]) if last_idx < len(streak) else 0.0,
        }

    return lookup

def main():
    print("Building temporal feature lookup table...")
    lookup = build_temporal_lookup()

    print(f"Products: {len(lookup)}")
    print("\nTemporal features by product:")
    for product, features in sorted(lookup.items()):
        print(f"  {product}:")
        for k, v in features.items():
            print(f"    {k}: {v}")

    # Save to JSON
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(lookup, indent=2), encoding="utf-8")
    print(f"\nSaved to: {OUTPUT_PATH}")

if __name__ == "__main__":
    main()
