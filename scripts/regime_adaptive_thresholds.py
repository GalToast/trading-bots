#!/usr/bin/env python3
"""Regime-Adaptive Thresholds — dynamically adjust tail threshold based on market state.

The key insight from temporal features:
- When tail_hit_rate_5 is HIGH (tail events clustering), the market is in a "hot regime"
- When tail_hit_rate_5 is LOW (drought), the market is in a "cold regime"

Instead of fixed Tail≥0.95 + FG≥0.90, we use:
- Hot regime (tail_hit_rate_5 > 0.3): Tail≥0.85, FG≥0.85 → MORE signals
- Normal regime (0.1 < tail_hit_rate_5 ≤ 0.3): Tail≥0.95, FG≥0.90 → standard
- Cold regime (tail_hit_rate_5 ≤ 0.1): Tail≥0.98, FG≥0.95 → FEWER but higher quality signals

This should capture MORE signals during tail clusters (when they're most profitable)
and FEWER during droughts (when the model is less reliable).
"""
import joblib
import numpy as np
import pandas as pd
import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
TABLE_PATH = ROOT / "reports" / "coinbase_spot_fee_survival_training_table.csv"
TAIL_V2 = REPORTS / "models" / "coinbase_spot_high_gross_tail_predictor_v2_fixed.joblib"
FG_MODEL = REPORTS / "models" / "coinbase_spot_fast_green_model.joblib"
TEMPORAL_PATH = REPORTS / "coinbase_spot_temporal_features.json"

def load_model(path):
    if not path.exists():
        return None
    return joblib.load(path)

def load_temporal():
    path = Path(TEMPORAL_PATH)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))

def score_with_model_v2(df, model):
    """Score with V2 model (needs temporal features from lookup)."""
    temporal = load_temporal()
    cat_features = list(model.get("categorical_cols") or [])
    num_features = list(model.get("feature_cols") or [])
    # Remove temporal from numeric if they're mixed
    temporal_keys = ["tail_hit_rate_5", "time_since_tail", "prev_ret_1_bps", 
                     "trend_3", "trend_6", "non_tail_streak"]
    
    # Add temporal features to dataframe
    for product_id in df["product_id"].unique():
        mask = df["product_id"] == product_id
        t = temporal.get(product_id, {})
        for key in temporal_keys:
            if key in num_features and key not in df.columns:
                df.loc[mask, key] = t.get(key, 0.0)
    
    for col in cat_features:
        df[col] = df[col].astype(str).fillna("")
    for col in num_features:
        df[col] = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    
    pipe = model["model"]
    return pipe.predict_proba(df[cat_features + num_features])[:, 1]

def score_with_model_v1(df, model):
    """Score with V1 model (no temporal features)."""
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

def get_regime_thresholds(tail_hit_rate_5):
    """Get regime-adaptive thresholds based on tail hit rate."""
    if tail_hit_rate_5 > 0.3:
        return 0.85, 0.85, "HOT"
    elif tail_hit_rate_5 > 0.1:
        return 0.95, 0.90, "NORMAL"
    else:
        return 0.98, 0.95, "COLD"

def main():
    print("=" * 80)
    print("REGIME-ADAPTIVE THRESHOLDS — Dynamic Signal Selection")
    print("=" * 80)

    df = pd.read_csv(TABLE_PATH)
    df["net_pct"] = df["gross_pct"] - 2.4

    split_at = int(len(df) * 0.75)
    test_df = df.iloc[split_at:].copy()
    print(f"Test set: {len(test_df):,} rows")

    # Load V1 models (V2 integration blocked by encoding issues)
    tail_v1 = load_model(REPORTS / "models" / "coinbase_spot_tail_predictor.joblib")
    fg_model = load_model(FG_MODEL)
    temporal = load_temporal()
    
    if not tail_v1 or not fg_model:
        print(f"ERROR: V1={tail_v1 is not None}, FG={fg_model is not None}")
        return

    print("\nScoring test set with V1 tail model...")
    test_tail = score_with_model_v1(test_df.copy(), tail_v1)
    test_fg = score_with_model_v1(test_df.copy(), fg_model)
    print(f"  Tail mean: {test_tail.mean():.4f}, FG mean: {test_fg.mean():.4f}")

    # Regime classification using lookup table
    print("\nRegime classification by product:")
    regimes = {}
    for product_id in sorted(test_df["product_id"].unique()):
        t = temporal.get(product_id, {})
        hit_rate = t.get("tail_hit_rate_5", 0.0)
        tail_thresh, fg_thresh, regime = get_regime_thresholds(hit_rate)
        regimes[product_id] = (tail_thresh, fg_thresh, regime)
        print(f"  {product_id:<12} hit_rate={hit_rate:.3f} → {regime:<6} (Tail≥{tail_thresh:.2f}, FG≥{fg_thresh:.2f})")

    # Regime-adaptive selection
    print(f"\n{'='*80}")
    print(f"REGIME-ADAPTIVE VS FIXED THRESHOLDS")
    print(f"{'='*80}")

    # Fixed thresholds (baseline)
    fixed_mask = (test_tail >= 0.95) & (test_fg >= 0.90)
    fixed_selected = test_df[fixed_mask].copy()
    fixed_selected["tail_prob"] = test_tail[fixed_mask]
    fixed_selected["fg_prob"] = test_fg[fixed_mask]
    fixed_selected["combined_score"] = fixed_selected["tail_prob"] * fixed_selected["fg_prob"]

    # Regime-adaptive: apply per-signal thresholds based on product regime
    adaptive_mask = np.zeros(len(test_df), dtype=bool)
    for i in range(len(test_df)):
        product_id = test_df.iloc[i]["product_id"]
        tail_thresh, fg_thresh, regime = regimes.get(product_id, (0.95, 0.90, "NORMAL"))
        if test_tail[i] >= tail_thresh and test_fg[i] >= fg_thresh:
            adaptive_mask[i] = True

    adaptive_selected = test_df[adaptive_mask].copy()
    adaptive_selected["tail_prob"] = test_tail[adaptive_mask]
    adaptive_selected["fg_prob"] = test_fg[adaptive_mask]
    adaptive_selected["combined_score"] = adaptive_selected["tail_prob"] * adaptive_selected["fg_prob"]

    print(f"\n{'Metric':<30} {'Fixed (0.95/0.90)':>20} {'Regime-Adaptive':>20} {'Delta':>10}")
    print("-" * 82)
    
    fixed_n = len(fixed_selected)
    adaptive_n = len(adaptive_selected)
    fixed_cum = fixed_selected["net_pct"].sum() if fixed_n > 0 else 0
    adaptive_cum = adaptive_selected["net_pct"].sum() if adaptive_n > 0 else 0
    fixed_avg = fixed_selected["net_pct"].mean() if fixed_n > 0 else 0
    adaptive_avg = adaptive_selected["net_pct"].mean() if adaptive_n > 0 else 0
    fixed_win = (fixed_selected["net_pct"] > 0).mean() if fixed_n > 0 else 0
    adaptive_win = (adaptive_selected["net_pct"] > 0).mean() if adaptive_n > 0 else 0

    print(f"{'Signals':<30} {fixed_n:>20} {adaptive_n:>20} {adaptive_n - fixed_n:>10}")
    print(f"{'Cumulative net %':<30} {fixed_cum:>20.2f} {adaptive_cum:>20.2f} {adaptive_cum - fixed_cum:>10.2f}")
    print(f"{'Avg net %':<30} {fixed_avg:>20.4f} {adaptive_avg:>20.4f} {adaptive_avg - fixed_avg:>10.4f}")
    print(f"{'Win rate %':<30} {fixed_win*100:>20.1f} {adaptive_win*100:>20.1f} {(adaptive_win - fixed_win)*100:>10.1f}")

    # Execution slot compression for both
    print(f"\n{'='*80}")
    print(f"EXECUTION SLOT COMPRESSION")
    print(f"{'='*80}")

    for label, selected in [("Fixed", fixed_selected), ("Adaptive", adaptive_selected)]:
        if len(selected) == 0:
            print(f"\n  {label}: No signals")
            continue
        
        cycles = selected.groupby("time").apply(
            lambda g: g.nlargest(3, "combined_score")
        ).reset_index(drop=True)
        
        from datetime import datetime
        unique_times = cycles["time"].nunique()
        min_t = cycles["time"].min()
        max_t = cycles["time"].max()
        span_days = (datetime.utcfromtimestamp(max_t) - datetime.utcfromtimestamp(min_t)).total_seconds() / 86400
        
        print(f"\n  {label} (Top-3 per cycle):")
        print(f"    Unique timestamps: {unique_times}")
        print(f"    Signals: {len(cycles)}")
        print(f"    Cumulative net: {cycles['net_pct'].sum():.2f}%")
        print(f"    Avg net: {cycles['net_pct'].mean():.4f}%")
        print(f"    Win rate: {(cycles['net_pct'] > 0).mean()*100:.1f}%")
        print(f"    Span: {span_days:.1f} days")
        if span_days > 0:
            daily = cycles["net_pct"].sum() / span_days
            print(f"    Daily return: {daily:.2f}%")
            days_4x = np.log(4) / np.log(1 + daily / 100) if daily > 0 else float('inf')
            print(f"    Days to 4x: {days_4x:.0f}")

    print(f"\n{'='*80}")
    print("DONE")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
