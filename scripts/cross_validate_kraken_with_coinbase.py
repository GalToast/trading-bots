#!/usr/bin/env python3
"""Cross-validate Coinbase Tail/FastGreen models against Kraken bid/ask cache.

Reads Kraken tick cache (bid/ask timestamps), aggregates into 5-minute candles,
computes the same features as the Coinbase training table, and scores with
Tail+FastGreen models to find if the Coinbase signal works on Kraken data.

This is PROXY-ONLY — no live orders, no shadow execution.
"""
import json
import joblib
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
KRAKEN_CACHE = ROOT / "reports" / "cache" / "kraken_spot_live_radar_ticks.json"
TAIL_MODEL = ROOT / "reports" / "models" / "coinbase_spot_tail_predictor.joblib"
FG_MODEL = ROOT / "reports" / "models" / "coinbase_spot_fast_green_model.joblib"

CANDLE_INTERVAL = 300  # 5 minutes in seconds

def load_kraken_cache():
    if not KRAKEN_CACHE.exists():
        return None
    data = json.loads(KRAKEN_CACHE.read_text(encoding="utf-8"))
    return data.get("samples", {})

def ticks_to_candles(ticks, interval=CANDLE_INTERVAL):
    """Aggregate bid/ask ticks into OHLCV candles."""
    if not ticks:
        return []
    
    # Sort by timestamp
    ticks = sorted(ticks, key=lambda x: x["ts"])
    
    candles = []
    current_candle = None
    
    for tick in ticks:
        ts = tick["ts"]
        bid = tick["bid"]
        ask = tick["ask"]
        mid = (bid + ask) / 2
        
        # Determine candle boundary
        candle_start = int(ts // interval) * interval
        
        if current_candle is None or current_candle["time"] != candle_start:
            if current_candle is not None:
                candles.append(current_candle)
            current_candle = {
                "time": candle_start,
                "open": mid,
                "high": mid,
                "low": mid,
                "close": mid,
                "volume": 1,
            }
        else:
            current_candle["high"] = max(current_candle["high"], mid)
            current_candle["low"] = min(current_candle["low"], mid)
            current_candle["close"] = mid
            current_candle["volume"] = current_candle.get("volume", 0) + 1
    
    if current_candle is not None:
        candles.append(current_candle)
    
    return candles

def compute_candle_features(candles):
    """Compute features compatible with Coinbase models from candle list."""
    if len(candles) < 12:
        return []
    
    df = pd.DataFrame(candles)
    df = df.sort_values("time").reset_index(drop=True)
    
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    opens = df["open"].values
    volumes = df["volume"].values
    
    features = []
    # We need 12 candles to calculate the first ret_12
    for i in range(12, len(df)):
        close_now = closes[i]
        high_now = highs[i]
        low_now = lows[i]
        open_now = opens[i]
        volume_now = volumes[i]
        
        # Returns
        ret_1 = ((close_now / closes[i-1]) - 1) * 10000 if closes[i-1] > 0 else 0
        ret_3 = ((close_now / closes[i-3]) - 1) * 10000 if closes[i-3] > 0 else 0
        ret_6 = ((close_now / closes[i-6]) - 1) * 10000 if closes[i-6] > 0 else 0
        ret_12 = ((close_now / closes[i-12]) - 1) * 10000 if closes[i-12] > 0 else 0
        
        # Range and body
        range_bps = ((high_now / low_now) - 1) * 10000 if low_now > 0 else 0
        body_bps = ((close_now / open_now) - 1) * 10000 if open_now > 0 else 0
        
        # Close location
        candle_range = high_now - low_now
        close_location = ((close_now - low_now) / candle_range) if candle_range > 0 else 0.5
        
        # Volume multiplier
        vol_mean = np.mean(volumes[i-12:i]) if i >= 12 else volume_now
        volume_mult = volume_now / vol_mean if vol_mean > 0 else 1.0
        
        # Volatility
        abs_rets = np.abs(np.diff(closes[max(0, i-12):i+1]) / closes[max(0, i-12):i])
        volatility = np.std(abs_rets) * 10000 if len(abs_rets) > 1 else 0
        
        # Acceleration vs median
        median_abs = np.median(abs_rets) if len(abs_rets) > 0 else 0
        accel = abs(ret_1 / 10000) / (median_abs + 1e-9) if median_abs > 0 else 0
        
        # Distance from high/low
        high_12 = np.max(highs[max(0, i-12):i+1])
        low_12 = np.min(lows[max(0, i-12):i+1])
        range_12 = high_12 - low_12
        dist_high = ((close_now / high_12) - 1) * 10000 if high_12 > 0 else 0
        dist_low = ((close_now / low_12) - 1) * 10000 if low_12 > 0 else 0
        position = ((close_now - low_12) / range_12) if range_12 > 0 else 0.5
        
        features.append({
            "ret_1_bps": ret_1,
            "ret_3_bps": ret_3,
            "ret_6_bps": ret_6,
            "ret_12_bps": ret_12,
            "range_bps": range_bps,
            "body_bps": body_bps,
            "close_location": close_location,
            "volume_mult_12": volume_mult,
            "volatility_12_bps": volatility,
            "accel_vs_median_abs_12": accel,
            "dist_from_12_high_bps": dist_high,
            "dist_from_12_low_bps": dist_low,
            "position_in_12_range": position,
            "time": int(df.iloc[i]["time"]),
        })
    
    return features

def score_with_model(features, model):
    """Score features with a Coinbase model."""
    if not features:
        return [], []
    
    if "categorical" in model:
        cat_features = model["categorical"]
        num_features = model["numeric"]
    elif "categorical_cols" in model:
        cat_features = model["categorical_cols"]
        num_features = [c for c in model["feature_cols"] if c not in cat_features]
    else:
        raise ValueError(f"Unknown model format: {list(model.keys())}")
    
    df = pd.DataFrame(features)
    # Add dummy categorical columns
    for col in cat_features:
        df[col] = "unknown"
    
    for col in num_features:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    
    pipe = model["model"]
    probs = pipe.predict_proba(df[cat_features + num_features])[:, 1]
    return probs, features

def main():
    print("=" * 80)
    print("CROSS-VALIDATION: Coinbase Tail/FastGreen vs Kraken Cache")
    print("=" * 80)
    
    # Load Kraken cache
    print("\nLoading Kraken cache...")
    samples = load_kraken_cache()
    if not samples:
        print("ERROR: No Kraken cache found")
        return
    
    print(f"  Products: {len(samples)}")
    total_ticks = sum(len(ticks) for ticks in samples.values())
    print(f"  Total ticks: {total_ticks:,}")
    
    # Load models
    print("\nLoading models...")
    tail_model = joblib.load(TAIL_MODEL)
    fg_model = joblib.load(FG_MODEL)
    
    # Process each product
    print("\nProcessing products...")
    all_results = []
    max_tail_p = 0.0
    max_fg_p = 0.0
    
    for product_id, ticks in sorted(samples.items()):
        if len(ticks) < 15:
            continue
        
        candles = ticks_to_candles(ticks)
        if len(candles) < 13:
            continue
        
        features = compute_candle_features(candles)
        if not features:
            continue
        
        tail_probs, _ = score_with_model(features, tail_model)
        fg_probs, _ = score_with_model(features, fg_model)
        
        if len(tail_probs) == 0 or len(fg_probs) == 0:
            continue
            
        max_tail_p = max(max_tail_p, np.max(tail_probs))
        max_fg_p = max(max_fg_p, np.max(fg_probs))
        
        for i, (tail_p, fg_p) in enumerate(zip(tail_probs, fg_probs)):
            if tail_p >= 0.95 and fg_p >= 0.90:
                f = features[i]
                all_results.append({
                    "product_id": product_id,
                    "time": f["time"],
                    "tail_prob": tail_p,
                    "fg_prob": fg_p,
                    "ret_1_bps": f["ret_1_bps"],
                    "ret_12_bps": f["ret_12_bps"],
                })
    
    print(f"\n{'='*80}")
    print(f"RESULTS")
    print(f"{ '='*80}")
    print(f"  Max Tail Prob Found: {max_tail_p:.4f}")
    print(f"  Max FG Prob Found  : {max_fg_p:.4f}")
    
    if not all_results:
        print(f"  No signals found at Tail>=0.95 + FG>=0.90")
    else:
        print(f"  Signals found: {len(all_results)}")
        for r in sorted(all_results, key=lambda x: x["tail_prob"] * x["fg_prob"], reverse=True)[:10]:
            print(f"  {r['product_id']:<12} {datetime.utcfromtimestamp(r['time'])}  tail={r['tail_prob']:.4f}  fg={r['fg_prob']:.4f}")
    
    print(f"\n{'='*80}")
    print("DONE")
    print(f"{ '='*80}")

if __name__ == "__main__":
    main()
