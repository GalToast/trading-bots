#!/usr/bin/env python3
import json
import joblib
import pandas as pd
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
KRAKEN_FEATURES_PATH = REPORTS / "kraken_spot_live_foundry_features.json"
TAIL_MODEL_PATH = REPORTS / "models" / "coinbase_spot_high_gross_tail_predictor_v2_onehot.joblib"
FAST_GREEN_MODEL_PATH = REPORTS / "models" / "coinbase_spot_fast_green_model.joblib"

def main():
    if not KRAKEN_FEATURES_PATH.exists():
        print("Kraken features not found.")
        return
    
    with open(KRAKEN_FEATURES_PATH, "r") as f:
        kraken_features = json.load(f)
        
    print(f"Loaded {len(kraken_features)} Kraken products.")
    
    # Load Tail Model
    if not TAIL_MODEL_PATH.exists():
        print("Tail model not found.")
        return
    tail_payload = joblib.load(TAIL_MODEL_PATH)
    print(f"Tail Payload Keys: {list(tail_payload.keys())}")
    tail_model = tail_payload["model"]
    categorical = tail_payload.get("categorical_cols", [])
    numeric = tail_payload.get("numeric_cols", [])
    encoders = tail_payload.get("encoders", {})
    
    # Load Fast Green Model
    if not FAST_GREEN_MODEL_PATH.exists():
        print("Fast Green model not found.")
        return
    fg_payload = joblib.load(FAST_GREEN_MODEL_PATH)
    fg_model = fg_payload["model"]
    fg_categorical = fg_payload.get("categorical", [])
    fg_numeric = fg_payload.get("numeric", [])
    
    rows = []
    for product_id, features in kraken_features.items():
        # Prepare feature row (mocking some fields not in bridge but needed by model)
        row = {
            **features,
            "product_id": product_id,
            "archetype": "bubble_ignition_reclaim",
            "trigger": "live_bid_burst",
            "confirmation": "one_poll_hot",
            "exit": "wide_bubble_trail",
            "sizing": "standard_50",
            "trigger_mode": "impulse",
            "hour_utc": 12, # Mock
            "lookback": 1,
            "trigger_bps": 25.0,
            "target_pct": 7.0,
            "stop_pct": 2.5,
            "hold_bars": 12,
            "spread_bps_proxy": 10.0,
            "fee_bps_round_trip": 80.0,
            # Temporal features (mocking zeros)
            "tail_hit_rate_5": 0.0,
            "time_since_tail": 1000.0,
            "prev_ret_1_bps": 0.0,
            "trend_3": 0.0,
            "trend_6": 0.0,
            "non_tail_streak": 10.0
        }
        rows.append(row)
        
    df = pd.DataFrame(rows)
    
    # Encode Tail
    df_tail = df.copy()
    for col in categorical:
        if col in encoders:
            df_tail[col] = encoders[col].transform(df_tail[col].astype(str))
        else:
            df_tail[col] = 0
            
    # Score Tail
    print(f"Tail Model Categorical: {categorical}")
    print(f"Tail Model Numeric: {numeric}")
    print(f"DataFrame Columns: {list(df_tail.columns)}")
    
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore")
        tail_probs = tail_model.predict_proba(df_tail[categorical + numeric])[:, 1]
        
    # Score Fast Green
    df_fg = df.copy()
    for col in fg_categorical:
        df_fg[col] = df_fg[col].astype(str)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore")
        fg_probs = fg_model.predict_proba(df_fg[fg_categorical + fg_numeric])[:, 1]
        
    for i, product_id in enumerate(kraken_features.keys()):
        print(f"Product: {product_id} | Tail Prob: {tail_probs[i]:.4f} | Fast Green Prob: {fg_probs[i]:.4f}")

if __name__ == "__main__":
    main()
