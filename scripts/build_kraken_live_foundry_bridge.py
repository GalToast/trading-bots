#!/usr/bin/env python3
import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
PULSE_CANDLES_PATH = ROOT / "reports" / "cache" / "kraken_spot_pulse_candles.json"
OUTPUT_PATH = ROOT / "reports" / "kraken_spot_live_foundry_features.json"

def main():
    print("=" * 80)
    print("KRAKEN LIVE FOUNDRY BRIDGE (CANDLE-BACKED)")
    print("=" * 80)

    live_features = {}

    # 1. Load Pulse Candles (1m)
    if PULSE_CANDLES_PATH.exists():
        print(f"Loading pulse candles from {PULSE_CANDLES_PATH}...")
        with open(PULSE_CANDLES_PATH, "r") as f:
            data = json.load(f)
        
        entries = data.get("entries", {})
        print(f"Found {len(entries)} candle entries.")
        
        for key, entry in entries.items():
            product_id = key.split("|")[0]
            candles_1m = entry.get("candles", [])
            if len(candles_1m) < 60: # Need at least 60m for 12x 5m features
                continue
                
            df_1m = pd.DataFrame(candles_1m)
            # Aggregate 1m to 5m
            df_1m["ts_bin"] = (df_1m["start"] // 300) * 300
            candles_5m = df_1m.groupby("ts_bin").agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum"
            }).sort_index()
            
            if len(candles_5m) < 13:
                continue
                
            closes = candles_5m["close"].values
            highs = candles_5m["high"].values
            lows = candles_5m["low"].values
            opens = candles_5m["open"].values
            volumes = candles_5m["volume"].values
            
            i = len(closes) - 1
            
            # Calculate features (matching Coinbase bridge logic)
            ret_1 = ((closes[i] / closes[i-1]) - 1) * 10000
            ret_3 = ((closes[i] / closes[i-3]) - 1) * 10000
            ret_6 = ((closes[i] / closes[i-6]) - 1) * 10000
            ret_12 = ((closes[i] / closes[i-12]) - 1) * 10000
            
            range_bps = ((highs[i] / lows[i]) - 1) * 10000 if lows[i] > 0 else 0
            body_bps = ((closes[i] / opens[i]) - 1) * 10000 if opens[i] > 0 else 0
            
            c_range = highs[i] - lows[i]
            close_loc = (closes[i] - lows[i]) / c_range if c_range > 0 else 0.5
            
            vol_mean = volumes[max(0, i-12):i].mean()
            vol_mult = volumes[i] / vol_mean if vol_mean > 0 else 1.0
            
            # ATR (Average True Range) - 12 bar
            tr_list = []
            for j in range(max(1, i-11), i+1):
                high = highs[j]
                low = lows[j]
                prev_close = closes[j-1]
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                tr_list.append(tr)
            atr_12 = np.mean(tr_list) if tr_list else (highs[i] - lows[i])
            atr_12_pct = (atr_12 / closes[i]) * 100.0 if closes[i] > 0 else 0.0

            abs_rets = np.abs(np.diff(closes[max(0, i-12):i+1]) / closes[max(0, i-12):i])
            volat = np.std(abs_rets) * 10000 if len(abs_rets) > 1 else 1.0
            
            median_abs = np.median(abs_rets) if len(abs_rets) > 0 else 0.0001
            accel = abs(ret_1 / 10000) / (median_abs + 1e-9)
            
            h12 = np.max(highs[max(0, i-12):i+1])
            l12 = np.min(lows[max(0, i-12):i+1])
            r12 = h12 - l12
            dist_h = ((closes[i] / h12) - 1) * 10000 if h12 > 0 else 0
            dist_l = ((closes[i] / l12) - 1) * 10000 if l12 > 0 else 0
            pos12 = (closes[i] - l12) / r12 if r12 > 0 else 0.5
            
            live_features[product_id] = {
                "ret_1_bps": round(ret_1, 6),
                "ret_3_bps": round(ret_3, 6),
                "ret_6_bps": round(ret_6, 6),
                "ret_12_bps": round(ret_12, 6),
                "range_bps": round(range_bps, 6),
                "body_bps": round(body_bps, 6),
                "close_location": round(close_loc, 4),
                "volume_mult_12": round(vol_mult, 4),
                "atr_12_pct": round(atr_12_pct, 6),
                "volatility_12_bps": round(volat, 6),
                "accel_vs_median_abs_12": round(accel, 6),
                "dist_from_12_high_bps": round(dist_h, 6),
                "dist_from_12_low_bps": round(dist_l, 6),
                "position_in_12_range": round(pos12, 4),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "source": "pulse_candles"
            }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(live_features, f, indent=2)

    print(f"DONE! Saved {len(live_features)} products to {OUTPUT_PATH}")
    print("=" * 80)

if __name__ == "__main__":
    main()
