#!/usr/bin/env python3
"""
Adversarial Robustness Auditor (Fixed Verification)
Calculates the 'Survival Rate' with correct loss detection.
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

def audit_robustness(slippage_bps: float = 1.0):
    events = load_jsonl(EVENTS_PATH)
    closes = [e for e in events if e.get("action") == "close_maker_shadow"]
    
    total = len(closes)
    original_wins = 0
    stress_wins = 0
    
    for c in closes:
        net = float(c.get("net", 0))
        net_pct = float(c.get("net_pct", 0))
        
        if net > 0:
            original_wins += 1
            
        stress_net_pct = net_pct - (slippage_bps * 2) / 100.0
        if stress_net_pct > 0:
            stress_wins += 1
            
    print(f"--- ADVERSARIAL AUDIT: {total} Historical Trades ---")
    print(f"Original WR: {(original_wins/total*100):.1f}% ({original_wins}/{total})")
    print(f"Stress WR:   {(stress_wins/total*100):.1f}% ({stress_wins}/{total})")
    print(f"Survival:    {(stress_wins/original_wins*100 if original_wins > 0 else 0):.1f}% of wins survived stress.")

if __name__ == "__main__":
    audit_robustness(slippage_bps=1.0)
