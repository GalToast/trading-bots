#!/usr/bin/env python3
"""
Structural Alpha Performance Auditor (Mad Scientist Edition)
Calculates per-product 'Confidence Boosts' based on real shadow-trade MFE capture.
This feedback loop allows the machine to learn which venues actually deliver the alpha.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MFE_TRACKER_PATH = ROOT / "reports" / "kraken_spot_frontier_mfe_tracker.json"
FEEDBACK_PATH = ROOT / "reports" / "structural_alpha_performance_feedback.json"

def load_json(path: Path) -> list:
    if not path.exists(): return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except: return []

def main():
    print("AUDITING SHADOW PERFORMANCE FEEDBACK...")
    trades = load_json(MFE_TRACKER_PATH)
    
    performance = {}
    
    for t in trades:
        pid = t["product_id"]
        if pid not in performance:
            performance[pid] = {"wins": 0, "losses": 0, "total_net_pct": 0.0, "avg_capture_rate": 0.0, "samples": 0}
            
        status = t.get("status")
        if status == "closed":
            net = t.get("net_pct", 0.0)
            capture = t.get("capture_rate", 0.0)
            
            performance[pid]["samples"] += 1
            performance[pid]["total_net_pct"] += net
            performance[pid]["avg_capture_rate"] += capture
            if net > 0:
                performance[pid]["wins"] += 1
            else:
                performance[pid]["losses"] += 1
                
    feedback = {}
    for pid, stats in performance.items():
        if stats["samples"] == 0: continue
        
        avg_capture = stats["avg_capture_rate"] / stats["samples"]
        win_rate = stats["wins"] / stats["samples"]
        
        # Confidence Score: 0.0 to 2.0
        # Start at 1.0 (Neutral)
        confidence = 1.0
        
        # Boost for capture quality
        if avg_capture > 0.5: confidence += 0.5
        if avg_capture > 0.8: confidence += 0.3
        
        # Penalize for failure
        if win_rate < 0.4: confidence -= 0.5
        if avg_capture < 0: confidence -= 0.5 # Toxic discretization
        
        feedback[pid] = {
            "confidence_mult": round(max(0.1, confidence), 2),
            "win_rate": round(win_rate, 4),
            "avg_capture": round(avg_capture, 4),
            "samples": stats["samples"],
            "verdict": "PERFORMANCE_LEADER" if confidence > 1.2 else ("PERFORMANCE_DRAG" if confidence < 0.8 else "NEUTRAL")
        }
        
    with open(FEEDBACK_PATH, "w") as f:
        json.dump(feedback, f, indent=2)
        
    print(f"DONE! Audited {len(feedback)} products. Feedback saved to {FEEDBACK_PATH}")

if __name__ == "__main__":
    main()
