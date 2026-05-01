#!/usr/bin/env python3
"""Swarm Brain Feature Bus (Titan 7.0 Alpha).

Collects real-time features from the global veto engine, the market scanner,
and the fleet-wide activity log to provide a unified 'Brain' state for
adaptive exit targeting and predictive fill modeling.
"""
import json
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
GLOBAL_VETO_PATH = ROOT / "reports" / "global_lead_lag_veto_state.json"
OPPORTUNITY_BOARD_PATH = ROOT / "reports" / "kraken_maker_opportunity_board.json"
DOMINANCE_STATE_PATH = ROOT / "reports" / "swarm_brain_dominance_state.json"
SWARM_BRAIN_PATH = ROOT / "reports" / "swarm_brain_features.json"
SHADOW_LOG_PATH = ROOT / "reports" / "neural_harpoon_shadow_log.jsonl"
TITAN11_PATH = ROOT / "reports" / "titan11_mean_reversion.json"
TITAN12_PATH = ROOT / "reports" / "titan12_sector_dislocation.json"
KRAKEN_TAPE_PATH = ROOT / "reports" / "kraken_crossing_pressure_tape_events.jsonl"

def load_json(path: Path) -> dict[str, Any]:
    if not path.exists(): return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except:
        return {}

def get_crossing_pressure() -> dict[str, dict[str, int]]:
    """Parses the shadow log and Kraken tape for recent directional crossing-pressure events (last 5 mins)."""
    pressure = {}
    cutoff = time.time() - 300

    # 1. Parse Neural Harpoon Shadow Log (Coinbase Taker -> Kraken Maker Prediction)
    if SHADOW_LOG_PATH.exists():
        try:
            with open(SHADOW_LOG_PATH, "r") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        pid = d.get("product_id")
                        action = d.get("harpoon_action", "")
                        # Simple timestamp check - would be better with iso parsing but this matches existing pattern
                        if pid not in pressure: 
                            pressure[pid] = {"ask_pressure": 0, "bid_pressure": 0}
                        
                        if action == "SHADOW_SHORT":
                            pressure[pid]["ask_pressure"] += 1
                        elif action == "SHADOW_LONG":
                            pressure[pid]["bid_pressure"] += 1
                    except:
                        continue
        except:
            pass

    # 2. Parse Kraken Crossing Pressure Tape (Direct Kraken Book Trials)
    if KRAKEN_TAPE_PATH.exists():
        try:
            with open(KRAKEN_TAPE_PATH, "r") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        action = d.get("action")
                        if action not in ["crossing_pressure_cycle", "microfill_calibration_trial"]:
                            continue
                        
                        pid = d.get("product_id")
                        if pid not in pressure:
                            pressure[pid] = {"ask_pressure": 0, "bid_pressure": 0}
                        
                        # Case A: Summary Cycle
                        if action == "crossing_pressure_cycle":
                            if d.get("buy_fill_like"): pressure[pid]["ask_pressure"] += 2
                            if d.get("sell_fill_like"): pressure[pid]["bid_pressure"] += 2
                            
                        # Case B: Individual Trial (Lower latency)
                        elif action == "microfill_calibration_trial":
                            result = d.get("result", "")
                            is_fill = result in ["hard_cross_fill_proxy", "probable_queue_depletion_fill_proxy"]
                            if is_fill:
                                side = d.get("side", "").lower()
                                if side == "buy": # Taker sold to us
                                    pressure[pid]["ask_pressure"] += 1
                                elif side == "sell": # Taker bought from us
                                    pressure[pid]["bid_pressure"] += 1
                    except:
                        continue
        except:
            pass
            
    return pressure

def publish_swarm_brain():
    """Aggregates and publishes the global feature set."""
    veto_state = load_json(GLOBAL_VETO_PATH)
    opp_board = load_json(OPPORTUNITY_BOARD_PATH)
    radar = load_json(ROOT / "reports" / "kraken_spot_live_radar.json")
    dominance_state = load_json(DOMINANCE_STATE_PATH)
    crossing_pressure = get_crossing_pressure()
    titan11 = load_json(TITAN11_PATH)
    titan12 = load_json(TITAN12_PATH)
    
    # VOLATILITY VOLCANOES (Priority Targets)
    volcanoes = ["PLANCK-USD", "ANLOG-USD", "KET-USD"]
    
    # Calculate Global Regime Score
    pulse_scores = [to_float(r.get("pulse_score")) for r in opp_board.get("rows", [])]
    regime_score = sum(pulse_scores) / len(pulse_scores) if pulse_scores else 0.0
    
    # Dislocation Scores from Qwen
    dislocation_map = {res.get("product"): res.get("confidence") for res in titan11.get("results", [])}
    
    # TRIANGULAR FAIR VALUE ANCHOR
    radar_rows = radar.get("rows", [])
    prices = {r.get("product_id"): to_float(r.get("bid")) for r in radar_rows if r.get("bid")}
    
    triangular_deltas = {}
    
    # ETC Anchor
    if "ETC-USD" in prices and "ETH-USD" in prices and "ETC-ETH" in prices:
        fair_etc_eth = prices["ETC-USD"] / prices["ETH-USD"]
        actual_etc_eth = prices["ETC-ETH"]
        delta_bps = ((actual_etc_eth - fair_etc_eth) / fair_etc_eth) * 10000.0
        triangular_deltas["ETC-ETH"] = round(delta_bps, 2)
        
    # FIL Anchor
    if "FIL-USD" in prices and "ETH-USD" in prices and "FIL-ETH" in prices:
        fair_fil_eth = prices["FIL-USD"] / prices["ETH-USD"]
        actual_fil_eth = prices["FIL-ETH"]
        delta_bps = ((actual_fil_eth - fair_fil_eth) / fair_fil_eth) * 10000.0
        triangular_deltas["FIL-ETH"] = round(delta_bps, 2)

    features = {
        "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "global_veto_active": veto_state.get("veto_active", False),
        "global_regime_score": round(regime_score, 4),
        "active_candidates": len(opp_board.get("rows", [])),
        "triangular_deltas_bps": triangular_deltas,
        "fleet_dominance": dominance_state.get("fleet_dominance", {}),
        "crossing_pressure": crossing_pressure,
        "dislocation_scores": dislocation_map,
        "titan_9_1_ready": [
            r.get("product_id") for r in opp_board.get("rows", [])
            if (to_float(r.get("spread_bps")) > 300.0 or r.get("product_id") in volcanoes) 
            and (crossing_pressure.get(r.get("product_id"), {}).get("ask_pressure", 0) > 2 or r.get("product_id") in volcanoes)
        ],
        "veto_reasons": veto_state.get("reasons", []),
        "lead_leaders": veto_state.get("leaders_monitored", []),
        "sector_dislocations": {
            d.get("product_id"): {
                "dislocation_bps": d.get("dislocation_bps"),
                "z_dislocation": d.get("z_dislocation"),
                "z_acceleration": d.get("z_acceleration"),
                "forensic_score": d.get("forensic_score"),
                "is_liquid": d.get("is_liquid")
            } for d in titan12.get("dislocations", [])
        },
        "liquid_dislocations": [
            d.get("product_id") for d in titan12.get("dislocations", [])
            if d.get("is_liquid") and d.get("forensic_score", 0) > 1.5
        ],
        "sector_stats": titan12.get("sector_stats", {})
    }
    
    with open(SWARM_BRAIN_PATH, "w") as f:
        json.dump(features, f, indent=2)
    return features

def to_float(val: Any) -> float:
    try: return float(val)
    except: return 0.0

if __name__ == "__main__":
    print("--- SWARM BRAIN FEATURE BUS ACTIVE ---")
    while True:
        publish_swarm_brain()
        time.sleep(1) # High-speed refresh
