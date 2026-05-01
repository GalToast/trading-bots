#!/usr/bin/env python3
"""Swarm Brain Book Dominance Pulse (Titan 7.0 Alpha).

Audits the Kraken orderbook depth for the top systemic candidates 
to see if our $15 orders are "Big Fish" or "Invisible Ghosts."
"""
import json
import time
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from kraken_spot_client import KrakenSpotClient
from swarm_brain_feature_bus import to_float

MANIFEST_PATH = ROOT / "reports" / "structural_alpha_manifest.json"
DOMINANCE_STATE_PATH = ROOT / "reports" / "swarm_brain_dominance_state.json"
ORDER_SIZE_USD = 15.0

def load_json(path: Path) -> dict[str, Any]:
    if not path.exists(): return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except:
        return {}

def get_rest_pair(product_id: str) -> str:
    rest_pair = product_id.replace("-", "").upper()
    if rest_pair == "XBTUSD": return "XXBTZUSD"
    if rest_pair == "ETCETH": return "XETCXETH"
    if rest_pair == "ETHXBT": return "XETHXXBT"
    if rest_pair == "XRPXBT": return "XXRPXXBT"
    return rest_pair

def audit_fleet_dominance(last_depth_cache: dict[str, float]):
    client = KrakenSpotClient()
    manifest = load_json(MANIFEST_PATH)
    candidates = manifest.get("manifest", [])[:5] # Top 5 only for speed
    
    dominance_map = {}
    
    for cand in candidates:
        pid = cand.get("product_id")
        rest_pair = get_rest_pair(pid)
        
        try:
            depth = client.depth(rest_pair, count=5)
            bids = depth.get(rest_pair, {}).get("bids", [])
            if not bids: continue
            
            # L1 Dominance
            l1_price = float(bids[0][0])
            l1_vol = float(bids[0][1])
            l1_depth_usd = l1_price * l1_vol
            l1_dominance = (ORDER_SIZE_USD / (l1_depth_usd + ORDER_SIZE_USD)) * 100.0
            
            # Depth Delta (Change in L1 depth)
            last_depth = last_depth_cache.get(pid, l1_depth_usd)
            depth_delta_usd = l1_depth_usd - last_depth
            last_depth_cache[pid] = l1_depth_usd
            
            dominance_map[pid] = {
                "l1_dominance_pct": round(l1_dominance, 2),
                "l1_depth_usd": round(l1_depth_usd, 2),
                "depth_delta_usd": round(depth_delta_usd, 2),
                "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            }
        except:
            continue
            
    state = {
        "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "order_size_usd": ORDER_SIZE_USD,
        "fleet_dominance": dominance_map
    }
    
    with open(DOMINANCE_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
    return state

if __name__ == "__main__":
    print("--- SWARM BRAIN BOOK DOMINANCE PULSE ACTIVE ---")
    depth_cache = {}
    while True:
        audit_fleet_dominance(depth_cache)
        time.sleep(10) # 10s refresh to avoid rate limits
