#!/usr/bin/env python3
"""
Neural Harpoon Phase 2: Enhanced Toxicity Propagation Model
Trains an XGBoost model using MER, Spread, and Temporal features
to predict if a Coinbase toxicity event will propagate to Kraken.
"""

import json
import os
import joblib
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier
from sklearn.metrics import roc_auc_score, classification_report
from sklearn.preprocessing import OneHotEncoder

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
EVENTS_PATH = os.path.join(PROJECT_ROOT, "reports", "predatory_shadow_monitor_events.jsonl")
MER_PATH = os.path.join(PROJECT_ROOT, "reports", "kraken_maker_opportunity_board.json")
MODELS_DIR = os.path.join(PROJECT_ROOT, "reports", "models")
MODEL_OUTPUT = os.path.join(MODELS_DIR, "kraken_toxicity_harpoon_v2.joblib")

def parse_ts(ts_str):
    try:
        return datetime.fromisoformat(ts_str)
    except:
        return datetime.min.replace(tzinfo=timezone.utc)

def main():
    if not os.path.exists(EVENTS_PATH):
        print(f"Events file not found: {EVENTS_PATH}")
        return

    print("Loading predatory events...")
    events = []
    with open(EVENTS_PATH, "r") as f:
        for line in f:
            if not line.strip(): continue
            try:
                events.append(json.loads(line))
            except: pass

    # Load MER data
    mer_map = {}
    if os.path.exists(MER_PATH):
        try:
            with open(MER_PATH, "r") as f:
                mer_data = json.load(f)
                for row in mer_data.get("rows", []):
                    mer_map[row["product_id"]] = {
                        "mer": row.get("mer", 0.0),
                        "spread_bps": row.get("spread_bps", 0.0)
                    }
        except Exception as e:
            print(f"Error loading MER data: {e}")

    # Separate toxic events from warp events
    toxic_actions = [
        "fake_floor_pull_detected", 
        "iceberg_buy_reload_detected", 
        "iceberg_sell_reload_detected", 
        "magnetic_wall_touch_detected"
    ]
    warp_actions = ["kraken_warp_flush_detected", "kraken_warp_surge_detected"]

    toxic_events = []
    warp_events = []

    for ev in events:
        action = ev.get("action")
        if action in toxic_actions:
            toxic_events.append(ev)
        elif action in warp_actions:
            warp_events.append(ev)

    print(f"Found {len(toxic_events)} toxic events and {len(warp_events)} warp events.")

    # Pair toxic events with forward warp events
    rows = []
    for tev in toxic_events:
        ts = parse_ts(tev.get("ts_utc"))
        pid = tev.get("product_id")
        
        # Target: 1 if a warp event occurs for the same product_id within 30 seconds
        has_warp = 0
        for wev in warp_events:
            if wev.get("product_id") == pid:
                wts = parse_ts(wev.get("ts_utc"))
                delta = (wts - ts).total_seconds()
                if 0 < delta <= 30:
                    has_warp = 1
                    break
        
        # Build features
        current_bid_size = tev.get("current_bid_size", 0.0)
        previous_bid_size = tev.get("previous_bid_size", 0.0)
        current_ask_size = tev.get("current_ask_size", 0.0)
        previous_ask_size = tev.get("previous_ask_size", 0.0)
        
        size_delta_pct = 0.0
        if previous_bid_size > 0:
            size_delta_pct = (current_bid_size - previous_bid_size) / previous_bid_size
        elif previous_ask_size > 0:
            size_delta_pct = (current_ask_size - previous_ask_size) / previous_ask_size
            
        mdata = mer_map.get(pid, {"mer": 0.0, "spread_bps": 0.0})
        
        rows.append({
            "action": tev.get("action"),
            "product_id": pid,
            "price": tev.get("price", 0.0),
            "reload_multiple": tev.get("reload_multiple", 0.0),
            "vol_24h": tev.get("vol_24h", 0.0),
            "size_delta_pct": size_delta_pct,
            "mag_level": tev.get("mag_level", 0.0),
            "mer": mdata["mer"],
            "spread_bps": mdata["spread_bps"],
            "hour_of_day": ts.hour,
            "target": has_warp
        })

    df = pd.DataFrame(rows)
    print(f"Constructed DataFrame with {len(df)} samples. Target mean: {df['target'].mean():.4f}")

    if len(df) < 50:
        print("Not enough data to train V2 model. Falling back to simple fit.")
    
    # One-hot encode action
    encoder = OneHotEncoder(sparse_output=False, handle_unknown='ignore')
    action_encoded = encoder.fit_transform(df[['action']])
    action_cols = encoder.get_feature_names_out(['action'])
    df_action = pd.DataFrame(action_encoded, columns=action_cols, index=df.index)
    df = pd.concat([df, df_action], axis=1)

    features = [
        "price", "reload_multiple", "vol_24h", "size_delta_pct", 
        "mag_level", "mer", "spread_bps", "hour_of_day"
    ] + list(action_cols)
    
    X = df[features]
    y = df["target"]

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    print("Training Enhanced XGBoost Classifier...")
    model = XGBClassifier(
        n_estimators=150,
        max_depth=5,
        learning_rate=0.05,
        random_state=42,
        eval_metric="auc"
    )
    
    try:
        model.fit(X_train, y_train)
        if len(np.unique(y_test)) > 1:
            preds = model.predict_proba(X_test)[:, 1]
            auc = roc_auc_score(y_test, preds)
            print(f"Validation AUC: {auc:.4f}")
            print("\nClassification Report:")
            print(classification_report(y_test, model.predict(X_test)))
    except Exception as e:
        print(f"Warning during evaluation: {e}")
        model.fit(X, y)

    os.makedirs(MODELS_DIR, exist_ok=True)
    payload = {
        "model": model,
        "features": features,
        "encoder": encoder,
        "version": "2.0",
        "trained_at": datetime.now(timezone.utc).isoformat()
    }
    joblib.dump(payload, MODEL_OUTPUT)
    print(f"V2 Model saved to {MODEL_OUTPUT}")

if __name__ == "__main__":
    main()
