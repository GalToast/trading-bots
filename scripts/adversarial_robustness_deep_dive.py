#!/usr/bin/env python3
"""
Adversarial Robustness Auditor (Deep Dive)
Identifies specific trades that fail under stress.
"""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
EVENTS_PATH = ROOT / "reports" / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_ab_events.jsonl"

def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists(): return []
    rows = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: rows.append(json.loads(line))
            except: continue
    return rows

def audit_deep_dive(slippage_bps: float = 1.0):
    events = load_jsonl(EVENTS_PATH)
    closes = [e for e in events if e.get("action") == "close_maker_shadow"]
    
    print(f"--- STRESS TEST FAILURES (Penalty: {slippage_bps}bps/leg) ---")
    
    fail_count = 0
    for c in closes:
        net_pct = float(c.get("net_pct", 0))
        stress_net_pct = net_pct - (slippage_bps * 2) / 100.0
        
        if stress_net_pct <= 0:
            fail_count += 1
            print(f"FAIL: {c.get('product_id')} | Original: {net_pct:+.4f}% | Stress: {stress_net_pct:+.4f}% | Reason: {c.get('reason')}")
            
    if fail_count == 0:
        print("No failures found under these conditions.")

if __name__ == "__main__":
    audit_deep_dive(slippage_bps=1.0)
