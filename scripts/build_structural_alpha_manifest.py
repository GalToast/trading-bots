#!/usr/bin/env python3
"""
Structural Alpha Manifest Builder (Mad Scientist Edition)
Synthesizes MER, Handoffs, and Neural Warp Probes into a single 'Heat Score'.
This manifest drives the dynamic mutation of the shadow fleet.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
MER_PATH = REPORTS / "kraken_maker_opportunity_board.json"
HANDOFF_PATH = REPORTS / "venue_handoff_bridge.json"
HARPOON_LOG_PATH = REPORTS / "neural_harpoon_shadow_log.jsonl"
FEEDBACK_PATH = REPORTS / "structural_alpha_performance_feedback.json"
OUTPUT_PATH = REPORTS / "structural_alpha_manifest.json"
MD_PATH = REPORTS / "structural_alpha_manifest.md"

def load_json(path: Path) -> dict:
    if not path.exists(): return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except: return {}

def load_jsonl_last(path: Path, count: int = 100) -> list:
    if not path.exists(): return []
    try:
        with open(path, "r") as f:
            lines = f.readlines()
            return [json.loads(l) for l in lines[-count:]]
    except: return []

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def main():
    print("BUILDING STRUCTURAL ALPHA MANIFEST...")
    
    mer_data = load_json(MER_PATH)
    handoff_data = load_json(HANDOFF_PATH)
    harpoon_events = load_jsonl_last(HARPOON_LOG_PATH)
    performance_feedback = load_json(FEEDBACK_PATH)
    
    manifest = {}
    
    # 1. Start with MER (Structural Rent)
    for row in mer_data.get("rows", []):
        pid = row["product_id"]
        manifest[pid] = {
            "mer": row["mer"],
            "base_score": row["mer"] * 10.0, # MER is a strong base
            "components": ["maker_rent"],
            "verdict": "RENT_HARVEST",
            "suggested_mode": "MAKER",
            "suggested_trail": max(2.5, row["atr_12_bps"] / 100.0 * 1.5)
        }
        
    # 2. Add Handoff Alpha (Coinbase Predictive Signal)
    for h in handoff_data.get("handoffs", []):
        pid = h["product_id"]
        if pid not in manifest:
            manifest[pid] = {
                "mer": h["kr_mer"],
                "base_score": 0.0,
                "components": [],
                "verdict": "MOMENTUM_HANDOFF",
                "suggested_mode": "TAKER",
                "suggested_trail": 1.5
            }
        
        manifest[pid]["base_score"] += (h["cb_edge_pct"] * 20.0) # Boost for CB edge
        manifest[pid]["components"].append("cb_momentum")
        if h["cb_edge_pct"] > 3.0:
            manifest[pid]["verdict"] = "EXPLOSIVE_HANDOFF"
            
    # 3. Layer on Neural Warp Probes (Microstructure Heat)
    warp_counts = {}
    for e in harpoon_events:
        pid = e.get("product_id")
        prob = float(e.get("warp_probability", 0.0))
        if prob > 0.60:
            warp_counts[pid] = warp_counts.get(pid, 0) + 1
            
    for pid, count in warp_counts.items():
        if pid in manifest:
            manifest[pid]["base_score"] += (count * 5.0) # Massive boost for recurring warp events
            manifest[pid]["components"].append("neural_warp")
            
    # 4. Final Scoring & Normalization (including Performance Feedback)
    final_rows = []
    for pid, data in manifest.items():
        feedback = performance_feedback.get(pid, {"confidence_mult": 1.0})
        conf_mult = feedback["confidence_mult"]
        
        # Scale score by performance confidence
        score = data["base_score"] * conf_mult
        
        # Scale sizing based on score
        suggested_size_mult = min(2.0, 1.0 + (score / 100.0))
        # Further scale by confidence (Sentient Sizing)
        suggested_size_mult *= conf_mult
        
        final_rows.append({
            "product_id": pid,
            "heat_score": round(score, 4),
            "performance_conf": conf_mult,
            "suggested_mode": data["suggested_mode"],
            "suggested_size_mult": round(min(3.0, suggested_size_mult), 2),
            "suggested_trail_pct": round(data["suggested_trail"], 4),
            "components": data["components"],
            "verdict": data["verdict"]
        })
        
    final_rows.sort(key=lambda x: x["heat_score"], reverse=True)
    
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "generative_structural_alpha",
        "leadership_read": [
            "This manifest drive dynamic mutation of sizing and trailing stops.",
            "Heat Score = (MER * 10) + (CB_Edge * 20) + (Warp_Count * 5).",
            "Shadow runners should scale exposure by 'suggested_size_mult'."
        ],
        "manifest": final_rows
    }
    
    with open(OUTPUT_PATH, "w") as f:
        json.dump(payload, f, indent=2)
        
    # Write MD
    lines = ["# Structural Alpha Manifest", "", "## Heat Rankings (Shadow Fleet 4.0)", ""]
    lines.append("| Product | Heat | Mode | Size Mult | Trail % | Verdict | Components |")
    lines.append("| --- | ---: | --- | ---: | ---: | --- | --- |")
    for r in final_rows[:25]:
        comp_str = ", ".join(r["components"])
        lines.append(f"| {r['product_id']} | {r['heat_score']} | {r['suggested_mode']} | {r['suggested_size_mult']}x | {r['suggested_trail_pct']}% | **{r['verdict']}** | {comp_str} |")
        
    with open(MD_PATH, "w") as f:
        f.write("\n".join(lines))
        
    print(f"DONE! Saved {len(final_rows)} targets to {OUTPUT_PATH}")

if __name__ == "__main__":
    main()
