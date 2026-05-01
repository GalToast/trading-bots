#!/usr/bin/env python3
"""
Titan 12.0 — Sector Dislocation Intelligence (The Rubber Band).

Identifies relative strength and weakness by comparing an asset's move
to its sector median. 

Thesis: If an entire sector (e.g. SOL-Eco) is up 50bps, but HONEY is up 150bps,
HONEY is "stretched" and likely to snap back to the sector mean.
Conversely, if HONEY is flat while the sector is up, it's a "lagging" candidate.

Output: reports/titan12_sector_dislocation.json
"""

import json
import time
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RADAR_PATH = ROOT / "reports" / "kraken_spot_live_radar.json"
OUTPUT_PATH = ROOT / "reports" / "titan12_sector_dislocation.json"

# SECTOR DEFINITIONS
SECTORS = {
    "SOL-Eco": ["HONEY-USD", "SHAPE-USD", "JUP-USD", "PYTH-USD", "SOL-USD", "RENDER-USD"],
    "DePIN-Infra": ["CQT-USD", "AKT-USD", "HNT-USD", "TRAC-USD", "GLMR-USD", "ANLOG-USD"],
    "Polkadot": ["ACA-USD", "DOT-USD", "GLMR-USD", "ASTR-USD"],
    "AI-Compute": ["TAO-USD", "NEAR-USD", "RENDER-USD", "FET-USD", "AI3-USD"],
    "Meme-Vol": ["PENGU-USD", "GOAT-USD", "BERT-USD", "KOBAN-USD", "SPX-USD", "DOGE-USD", "DUCK-USD", "FORTH-USD"],
    "Infra-DeFi": ["BLUR-USD", "ENS-USD", "KSM-USD", "UNI-USD", "AAVE-USD", "CHEX-USD"],
    "Goldilocks-Alpha": ["KEY-USD", "L3-USD", "PLANCK-USD", "IDEX-USD"]
}

# EXCLUSIONS (Texas-Safe)
EXCLUSIONS = ["FOLKS-USD"]

def to_float(val: Any, default: float = 0.0) -> float:
    try: return float(val)
    except: return default

def calculate_median(values: list[float]) -> float:
    if not values: return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 1:
        return sorted_vals[n // 2]
    else:
        return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0

def scan_sector_dislocation():
    if not RADAR_PATH.exists():
        print(f"Waiting for {RADAR_PATH}...")
        return

    try:
        with open(RADAR_PATH, "r") as f:
            radar = json.load(f)
    except Exception as e:
        print(f"Error loading radar: {e}")
        return

    rows = radar.get("rows", [])
    price_map = {r.get("product_id"): r for r in rows}
    
    sector_stats = {}
    dislocations = []

    for sector_name, products in SECTORS.items():
        ret_1m_list = []
        ret_5m_list = []
        velocity_list = []
        acceleration_list = []
        imbalance_list = []
        
        for pid in products:
            if pid in EXCLUSIONS: continue
            row = price_map.get(pid)
            if not row: continue
            
            ret_1m = to_float(row.get("ret_60s_bps"))
            ret_30s = to_float(row.get("ret_30s_bps"))
            ret_5m = to_float(row.get("ret_5m_bps"))
            
            # Velocity: Last 30s move
            velocity = ret_30s
            # Acceleration: Change in velocity (Last 30s vs Prior 30s)
            acceleration = ret_30s - (ret_1m - ret_30s)
            
            imbalance = to_float(row.get("imbalance_pct"), 50.0)
            
            ret_1m_list.append(ret_1m)
            ret_5m_list.append(ret_5m)
            velocity_list.append(velocity)
            acceleration_list.append(acceleration)
            imbalance_list.append(imbalance)
            
        if not ret_1m_list: continue
        
        median_1m = calculate_median(ret_1m_list)
        median_5m = calculate_median(ret_5m_list)
        median_velocity = calculate_median(velocity_list)
        median_acceleration = calculate_median(acceleration_list)
        median_imbalance = calculate_median(imbalance_list)
        
        # Calculate Sector Volatility
        mean_1m = sum(ret_1m_list) / len(ret_1m_list)
        variance_1m = sum((x - mean_1m)**2 for x in ret_1m_list) / len(ret_1m_list)
        stdev_1m = max(1.0, variance_1m**0.5)
        
        mean_accel = sum(acceleration_list) / len(acceleration_list)
        var_accel = sum((x - mean_accel)**2 for x in acceleration_list) / len(acceleration_list)
        stdev_accel = max(1.0, var_accel**0.5)
        
        sector_stats[sector_name] = {
            "median_1m_bps": round(median_1m, 2),
            "median_velocity_bps": round(median_velocity, 2),
            "median_acceleration_bps": round(median_acceleration, 2),
            "stdev_1m_bps": round(stdev_1m, 2),
            "stdev_acceleration_bps": round(stdev_accel, 2),
            "sample_count": len(ret_1m_list)
        }
        
        for pid in products:
            if pid in EXCLUSIONS: continue
            row = price_map.get(pid)
            if not row: continue
            
            ret_1m = to_float(row.get("ret_60s_bps"))
            ret_30s = to_float(row.get("ret_30s_bps"))
            acceleration = ret_30s - (ret_1m - ret_30s)
            imbalance = to_float(row.get("imbalance_pct"), 50.0)
            
            dislocation_bps = ret_1m - median_1m
            accel_dislocation = acceleration - median_acceleration
            imbalance_dislocation = imbalance - median_imbalance
            
            # Standard Deviation Normalization
            z_dislocation = dislocation_bps / stdev_1m
            z_acceleration = accel_dislocation / stdev_accel
            
            # Economics Gate (Strategist Filters)
            spread_bps = to_float(row.get("spread_bps"))
            bid_size_usd = to_float(row.get("bid_size")) * to_float(row.get("bid"))
            is_liquid = spread_bps >= 150 and bid_size_usd >= 15
            
            # Forensic Score: Combines Stretch + Breakaway (Acceleration) + Resistance (Imbalance)
            # High forensic score = asset is stretching AND accelerating away AND book is piling up against it.
            forensic_score = (abs(z_dislocation) * 0.4) + (abs(z_acceleration) * 0.4)
            if (dislocation_bps > 0 and imbalance < 40) or (dislocation_bps < 0 and imbalance > 60):
                forensic_score += 0.2 # Resistance bonus
            
            dislocations.append({
                "product_id": pid,
                "sector": sector_name,
                "dislocation_bps": round(dislocation_bps, 2),
                "z_dislocation": round(z_dislocation, 2),
                "acceleration_bps": round(acceleration, 2),
                "z_acceleration": round(z_acceleration, 2),
                "imbalance_pct": round(imbalance, 2),
                "is_liquid": is_liquid,
                "forensic_score": round(forensic_score, 2),
                "confidence": min(1.0, forensic_score / 3.0)
            })

    dislocations.sort(key=lambda x: abs(x["dislocation_bps"]), reverse=True)

    output = {
        "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sector_stats": sector_stats,
        "dislocations": dislocations[:100], # Expanded limit for all sectors
        "status": "active"
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)
        
    # Console output for visibility
    print(f"--- Sector Dislocation Update ({output['ts_utc']}) ---")
    for d in dislocations[:10]:
        liquid_marker = "[LIQ]" if d["is_liquid"] else "[ILL]"
        marker = "[HOT]" if d["forensic_score"] > 1.5 else "[WARM]" if d["forensic_score"] > 1.0 else "[COOL]"
        print(f"  {liquid_marker} {marker} {d['product_id']} ({d['sector']}): {d['dislocation_bps']:+g} bps | Score:{d['forensic_score']} | Z_Acc:{d['z_acceleration']:+.2f}")

if __name__ == "__main__":
    print("Starting Titan 12.0 Sector Dislocation Intelligence...")
    while True:
        scan_sector_dislocation()
        time.sleep(2) # Sync with radar refresh
