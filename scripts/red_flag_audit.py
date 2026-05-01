import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
# Auditing the CALIBRATED lane specifically for the most adversarial data
CAL_EVENTS = ROOT / "reports" / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_microfill_calibrated_v2_ab_events.jsonl"
PRIMARY_EVENTS = ROOT / "reports" / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_ab_events.jsonl"

def find_red_flags(path: Path, label: str):
    if not path.exists():
        return
    
    print(f"\n--- AUDIT: {label} ---")
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            try:
                e = json.loads(line)
                if e.get("action") == "close_maker_shadow":
                    net = float(e.get("net", 0.0))
                    mae = float(e.get("min_net_pct_on_cost", 0.0))
                    pid = e.get("product_id")
                    
                    # FLAG 1: High Drawdown wins
                    if net > 0 and mae < -1.5:
                        print(f"[UGLY WIN] {pid}: Net +{net:.2f}, but dipped {mae:.2f}% first.")
                    
                    # FLAG 2: Total Losses
                    if net < 0:
                        print(f"[TOTAL LOSS] {pid}: Net {net:.2f}, MAE {mae:.2f}%. Reason: {e.get('reason')}")
            except:
                continue

if __name__ == "__main__":
    find_red_flags(CAL_EVENTS, "Calibrated Reality Lane")
    find_red_flags(PRIMARY_EVENTS, "Primary Titan Lane")
