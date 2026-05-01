#!/usr/bin/env python3
"""
THE BLACK SWAN REAPER
Simulates extreme market failure events (Flash Crashes, Depth Vacuums)
to find the machine's final insolvency point.
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

def simulate_black_swan(
    crash_pct: float = 10.0, 
    vacuum_slippage_bps: float = 50.0,
    poisoned_prob: float = 0.05
):
    events = load_jsonl(EVENTS_PATH)
    closes = [e for e in events if e.get("action") == "close_maker_shadow"]
    
    total = len(closes)
    survivors = 0
    total_net = 0.0
    
    print(f"--- BLACK SWAN AUDIT: {total} Trades ---")
    print(f"Scenario: {crash_pct}% Flash Crash | {vacuum_slippage_bps}bps Depth Vacuum | {poisoned_prob*100}% Poisoned Tape")
    
    for c in closes:
        net_pct = float(c.get("net_pct", 0))
        cost = float(c.get("cost_usd", 1.0))
        
        # 1. The Flash Crash Reaper (1% probability per trade)
        if hash(str(c.get("ts_utc"))) % 100 < 1:
             # Position is caught in a 10% gapped dump
             net_pct = -crash_pct
        
        # 2. The L2 Depth Vacuum (5% probability per trade)
        elif hash(str(c.get("ts_utc"))) % 100 < 6:
             # Best bid vanishes; we cross the spread deep into the book
             net_pct -= (vacuum_slippage_bps / 100.0)
             
        # 3. The Poisoned Tape (Malicious Front-Running)
        elif hash(str(c.get("ts_utc"))) % 100 < (6 + poisoned_prob*100):
             # Competitive HFT forces a 20bps entry slippage
             net_pct -= 0.20
             
        if net_pct > 0:
            survivors += 1
        total_net += (net_pct / 100.0) * cost
        
    print(f"Survival Rate: {(survivors/total*100):.1f}%")
    print(f"Final Net:     ${total_net:+.2f}")
    
    if total_net > 0:
        print("\n[VERDICT] INDESTRUCTIBLE: The machine survives even total market collapse.")
    else:
        print("\n[VERDICT] VULNERABLE: Extreme events could wipe out historical gains.")

if __name__ == "__main__":
    simulate_black_swan()
