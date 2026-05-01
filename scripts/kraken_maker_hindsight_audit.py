import json
from pathlib import Path
from datetime import datetime, timezone
import sys

ROOT = Path(__file__).resolve().parent.parent
EVENT_LOG = ROOT / "reports" / "kraken_spot_maker_machinegun_shadow_events.jsonl"
OUTPUT_PATH = ROOT / "reports" / "kraken_maker_hindsight_analysis.json"

def load_jsonl(path: Path):
    if not path.exists(): return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line.strip()))
            except: continue
    return rows

def main():
    print("RUNNING KRAKEN MAKER HINDSIGHT AUDIT...")
    events = load_jsonl(EVENT_LOG)
    if not events:
        print("No events found.")
        return

    misses = [e for e in events if e["action"] == "maker_entry_miss"]
    closes = [e for e in events if e["action"] == "close_maker_shadow"]
    
    total_missed_alpha = 0.0
    opportunity_loss_count = 0
    
    # Analysis logic:
    # 1. Missed Fills: If we missed a fill, did the price hit our target later?
    # (This requires historical tick data which we might not have in the log)
    # 2. Exit Efficiency: Did we leave money on the table?
    
    efficiency_stats = []
    for c in closes:
        max_favorable = c.get("max_net_pct_on_cost", 0.0)
        actual_net = c.get("net_pct", 0.0)
        
        # Capture Rate: How much of the move did we keep?
        # If max_favorable is 1% and we kept 0.5%, capture is 50%.
        if max_favorable > 0:
            capture = actual_net / max_favorable
        else:
            capture = 1.0 if actual_net >= 0 else 0.0
            
        efficiency_stats.append({
            "product_id": c["product_id"],
            "max_favorable": max_favorable,
            "actual_net": actual_net,
            "capture_rate": capture,
            "reason": c["reason"]
        })

    avg_capture = sum(e["capture_rate"] for e in efficiency_stats) / max(len(efficiency_stats), 1)
    
    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_events": len(events),
        "total_closes": len(closes),
        "total_misses": len(misses),
        "avg_capture_rate": round(avg_capture, 4),
        "efficiency_by_product": efficiency_stats
    }
    
    with open(OUTPUT_PATH, "w") as f:
        json.dump(results, f, indent=2)
        
    print(f"AUDIT COMPLETE. Avg Capture Rate: {avg_capture:.2%}")
    print(f"Results saved to {OUTPUT_PATH}")

if __name__ == "__main__":
    main()
