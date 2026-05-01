#!/usr/bin/env python3
"""Live Combined Scorer: Score 131 products with fresh foundry features."""
import json
import joblib
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

# Fresh foundry features
FOUNDRY_PATH = REPORTS / "coinbase_spot_live_foundry_features.json"
TEMPORAL_PATH = REPORTS / "coinbase_spot_temporal_features.json"

# Models
TAIL_MODEL_V2 = REPORTS / "models" / "coinbase_spot_high_gross_tail_predictor_v2_fixed.joblib"
FAST_GREEN_MODEL = REPORTS / "models" / "coinbase_spot_fast_green_model.joblib"

# Also load V1 tail model for comparison
TAIL_MODEL_V1 = REPORTS / "models" / "coinbase_spot_tail_predictor.joblib"

FEE_BPS_ROUND_TRIP = 240.0  # 120bps x 2 taker

def load_model(path):
    if not path.exists():
        return None
    return joblib.load(path)

def score_with_model(df, model):
    """Score a dataframe with a model. Handles categorical + numeric features."""
    cat_features = model.get("categorical", [])
    num_features = model.get("numeric", [])
    pipe = model["model"]
    
    for col in cat_features:
        df[col] = df[col].astype(str).fillna("")
    for col in num_features:
        df[col] = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    
    all_features = cat_features + num_features
    missing = [f for f in all_features if f not in df.columns]
    if missing:
        print(f"  WARNING: Missing features: {missing}")
        for f in missing:
            df[f] = 0
    
    return pipe.predict_proba(df[all_features])[:, 1]

def main():
    print("=" * 80)
    print("LIVE COMBINED SCORER: Fresh Foundry Features (131 products)")
    print("=" * 80)
    
    # 1. Load fresh foundry features
    with open(FOUNDRY_PATH) as f:
        foundry_data = json.load(f)
    print(f"Loaded {len(foundry_data)} products from foundry bridge")
    
    # 2. Load temporal features
    temporal_data = {}
    if TEMPORAL_PATH.exists():
        with open(TEMPORAL_PATH) as f:
            temporal_data = json.load(f)
        print(f"Loaded temporal features for {len(temporal_data)} products")
    else:
        print("WARNING: No temporal features found — using defaults")
    
    # 3. Build dataframe for scoring
    # Need to match the training table format
    rows = []
    for product_id, features in foundry_data.items():
        row = dict(features)
        row["product_id"] = product_id
        
        # Add temporal features if available
        if product_id in temporal_data:
            row.update(temporal_data[product_id])
        else:
            # Default temporal features (same as Kraken proxy approach)
            row["tail_hit_rate_5"] = 0.0
            row["time_since_tail"] = 1000.0
            row["prev_ret_1_bps"] = 0.0
            row["trend_3"] = 10.0
            row["trend_6"] = 10.0
            row["non_tail_streak"] = 100.0
        
        # Add categorical defaults
        row["archetype"] = "ignition"
        row["trigger"] = "momentum"
        row["confirmation"] = "none"
        row["exit"] = "trail"
        row["sizing"] = "equal"
        row["trigger_mode"] = "default"
        row["lookback"] = 12  # numeric, not categorical
        row["hour_utc"] = 22  # Current hour
        row["trigger_bps"] = 0  # Default: no trigger threshold
        row["target_pct"] = 1.0  # 1% target
        row["stop_pct"] = 2.0  # 2% stop
        row["hold_bars"] = 2  # Hold 2 bars
        
        # Fee and spread
        row["fee_bps_round_trip"] = FEE_BPS_ROUND_TRIP
        row["spread_bps_proxy"] = 13.5  # Default spread estimate
        
        # Placeholder labels (we're scoring, not training)
        row["gross_pct"] = 0.0
        row["net_pct"] = 0.0
        row["future_mfe_pct"] = 0.0
        row["future_mae_pct"] = 0.0
        
        rows.append(row)
    
    df = pd.DataFrame(rows)
    print(f"Built scoring dataframe: {len(df)} rows x {len(df.columns)} cols")
    
    # 4. Load models
    tail_v2 = load_model(TAIL_MODEL_V2)
    fg_model = load_model(FAST_GREEN_MODEL)
    tail_v1 = load_model(TAIL_MODEL_V1)
    
    if not fg_model:
        print("ERROR: Fast-green model not found!")
        return
    if not tail_v2:
        print("WARNING: V2 tail model not found, falling back to V1")
    
    # 5. Score with V2 tail model (preferred)
    print("\n--- Scoring with V2 Tail Model (AUC=0.9944) ---")
    try:
        if tail_v2:
            feature_cols = tail_v2.get("feature_cols", [])
            encoding_map = tail_v2.get("encoding_map", {})
            pipe = tail_v2["model"]
            
            # Prepare features for V2 model
            df_v2 = df.copy()
            for col in feature_cols:
                if col not in df_v2.columns:
                    df_v2[col] = 0
                df_v2[col] = pd.to_numeric(df_v2[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
            
            tail_probs_v2 = pipe.predict_proba(df_v2[feature_cols])[:, 1]
            df["tail_prob_v2"] = tail_probs_v2
            print(f"  V2 Tail mean: {tail_probs_v2.mean():.4f}, median: {np.median(tail_probs_v2):.4f}")
            print(f"  V2 Tail ≥0.90: {(tail_probs_v2 >= 0.90).sum()}")
            print(f"  V2 Tail ≥0.95: {(tail_probs_v2 >= 0.95).sum()}")
        else:
            tail_probs_v2 = None
            print("  V2 model not loaded")
    except Exception as e:
        print(f"  ERROR scoring with V2 tail: {e}")
        import traceback
        traceback.print_exc()
        tail_probs_v2 = None
    
    # 6. Score with V1 tail model (fallback)
    print("\n--- Scoring with V1 Tail Model (AUC=0.657) ---")
    try:
        tail_probs_v1 = score_with_model(df.copy(), tail_v1)
        df["tail_prob_v1"] = tail_probs_v1
        print(f"  V1 Tail mean: {tail_probs_v1.mean():.4f}, median: {np.median(tail_probs_v1):.4f}")
        print(f"  V1 Tail ≥0.90: {(tail_probs_v1 >= 0.90).sum()}")
        print(f"  V1 Tail ≥0.95: {(tail_probs_v1 >= 0.95).sum()}")
    except Exception as e:
        print(f"  ERROR scoring with V1 tail: {e}")
        tail_probs_v1 = None
    
    # 7. Score with fast-green model
    print("\n--- Scoring with Fast-Green Model (AUC=0.815) ---")
    try:
        fg_probs = score_with_model(df.copy(), fg_model)
        df["fast_green_prob"] = fg_probs
        print(f"  Fast-Green mean: {fg_probs.mean():.4f}, median: {np.median(fg_probs):.4f}")
        print(f"  Fast-Green ≥0.90: {(fg_probs >= 0.90).sum()}")
        print(f"  Fast-Green ≥0.95: {(fg_probs >= 0.95).sum()}")
    except Exception as e:
        print(f"  ERROR scoring with fast-green: {e}")
        return
    
    # 8. Combined scoring
    print("\n" + "=" * 80)
    print("COMBINED SCORER RESULTS (LIVE)")
    print("=" * 80)
    
    # Use V1 tail (the one the original backtest used)
    tail_col = "tail_prob_v1"
    if tail_probs_v2 is not None:
        print("\n--- V2 Tail + Fast-Green ---")
        for tail_t in [0.80, 0.90, 0.95]:
            for fg_t in [0.80, 0.90, 0.95]:
                mask = (df["tail_prob_v2"] >= tail_t) & (df["fast_green_prob"] >= fg_t)
                n = mask.sum()
                if n > 0:
                    products = df[mask]["product_id"].tolist()
                    avg_fg = df[mask]["fast_green_prob"].mean()
                    avg_tail = df[mask]["tail_prob_v2"].mean()
                    print(f"  Tail≥{tail_t}, FG≥{fg_t}: {n} products — {products[:5]}{'...' if n > 5 else ''}")
                    print(f"    avg tail={avg_tail:.3f}, avg fg={avg_fg:.3f}")
    
    print("\n--- V1 Tail + Fast-Green (original backtest combo) ---")
    for tail_t in [0.80, 0.90, 0.95]:
        for fg_t in [0.80, 0.90, 0.95]:
            mask = (df[tail_col] >= tail_t) & (df["fast_green_prob"] >= fg_t)
            n = mask.sum()
            if n > 0:
                products = df[mask]["product_id"].tolist()
                avg_fg = df[mask]["fast_green_prob"].mean()
                avg_tail = df[mask][tail_col].mean()
                print(f"  Tail≥{tail_t}, FG≥{fg_t}: {n} products — {products[:5]}{'...' if n > 5 else ''}")
                print(f"    avg tail={avg_tail:.3f}, avg fg={avg_fg:.3f}")
    
    # 9. Top 10 by combined score
    print("\n--- Top 10 Products by Combined Score (V1 Tail × Fast-Green) ---")
    df["combined_score"] = df[tail_col] * df["fast_green_prob"]
    top10 = df.nlargest(10, "combined_score")
    for _, r in top10.iterrows():
        print(f"  {r['product_id']:12s}: tail={r[tail_col]:.4f}, fg={r['fast_green_prob']:.4f}, combined={r['combined_score']:.4f}")
    
    # 10. Save results
    output_path = REPORTS / "live_combined_scorer_results.json"
    results = {}
    for _, r in df.iterrows():
        results[r["product_id"]] = {
            "tail_prob_v1": float(r.get("tail_prob_v1", 0)),
            "tail_prob_v2": float(r.get("tail_prob_v2", 0)),
            "fast_green_prob": float(r.get("fast_green_prob", 0)),
            "combined_score": float(r.get("combined_score", 0)),
        }
    
    with open(output_path, "w") as f:
        json.dump({"generated_at": pd.Timestamp.now("UTC").isoformat(), "products": results}, f, indent=2)
    print(f"\nSaved results to {output_path}")
    
    # 11. Verdict
    high_combo = df[df["combined_score"] >= 0.95 * 0.90]  # tail≥0.95, fg≥0.90 equivalent
    if len(high_combo) > 0:
        print(f"\n🎯 **SIGNALS FOUND!** {len(high_combo)} products pass combined threshold")
        for _, r in high_combo.iterrows():
            print(f"   {r['product_id']}: tail={r[tail_col]:.4f}, fg={r['fast_green_prob']:.4f}")
    else:
        print(f"\n❌ **NO SIGNALS** — No products pass combined threshold at current market state")
        print(f"   Best combined score: {df['combined_score'].max():.4f} ({df.loc[df['combined_score'].idxmax(), 'product_id']})")

if __name__ == "__main__":
    main()
