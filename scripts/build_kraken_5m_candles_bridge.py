#!/usr/bin/env python3
import json
import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TICKS_PATH = ROOT / "reports" / "cache" / "kraken_spot_live_radar_ticks.json"
OUTPUT_PATH = ROOT / "reports" / "cache" / "kraken_spot_5m_candles_bridge.json"

def main():
    print("=" * 80)
    print("KRAKEN 5M CANDLE BRIDGE — Ticks to ML Features")
    print("=" * 80)

    if not TICKS_PATH.exists():
        print(f"ERROR: {TICKS_PATH} not found")
        return

    with open(TICKS_PATH, "r") as f:
        data = json.load(f)

    samples = data["samples"]
    print(f"Processing {len(samples)} products...")

    all_candles = {}

    for product, ticks in samples.items():
        if not ticks:
            continue
            
        df = pd.DataFrame(ticks)
        df["mid"] = (df["bid"] + df["ask"]) / 2.0
        df["ts_bin"] = (df["ts"] // 300) * 300
        
        # Group by 5m bins
        groups = df.groupby("ts_bin")
        
        candles = []
        for ts, group in groups:
            candles.append({
                "start": ts,
                "open": group["mid"].iloc[0],
                "high": group["mid"].max(),
                "low": group["mid"].min(),
                "close": group["mid"].iloc[-1],
                "volume": 0.0, # Volume not available in ticks cache
                "ticks": len(group)
            })
            
        all_candles[product] = candles

    with open(OUTPUT_PATH, "w") as f:
        json.dump(all_candles, f, indent=2)

    print(f"DONE! Saved {len(all_candles)} products to {OUTPUT_PATH}")
    print("=" * 80)

if __name__ == "__main__":
    main()
