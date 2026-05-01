import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CAL_EVENTS = ROOT / "reports" / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_microfill_calibrated_v2_ab_events.jsonl"

def find_the_ugly_truth():
    if not CAL_EVENTS.exists():
        print(f"File not found: {CAL_EVENTS}")
        return
    
    print(f"--- THE 'DIRTY LAUNDRY' AUDIT (CALIBRATED REALITY) ---")
    
    with open(CAL_EVENTS, "r", encoding="utf-8-sig") as f:
        for line in f:
            try:
                e = json.loads(line)
                if e.get("action") == "close_maker_shadow":
                    net = float(e.get("net", 0.0))
                    mae = float(e.get("min_net_pct_on_cost", 0.0))
                    pid = e.get("product_id")
                    
                    # Be brutal: Show everything that wasn't a perfect clean win
                    if net < 0:
                        print(f"🚨 [LOSS] {pid}: Net {net:+.2f}, dipped {mae:.2f}% before failing. Reason: {e.get('reason')}")
                    elif mae < -1.0:
                        print(f"⚠️ [UGLY WIN] {pid}: Net +{net:.2f}, but dipped {mae:.2f}% first. (Too close!)")
            except:
                continue

if __name__ == "__main__":
    find_the_ugly_truth()
