#!/usr/bin/env python3
"""
Deep Adversarial Auditor (Stress-to-Break)
Finds the 'Breaking Point' of the current alpha under extreme market conditions.
"""

import json
import random
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

def run_stress_test(
    slippage_multiplier: float = 1.0, 
    latency_penalty_bps: float = 2.0, 
    taker_fee_bps: float = 60.0
):
    events = load_jsonl(EVENTS_PATH)
    closes = [e for e in events if e.get("action") == "close_maker_shadow"]
    
    total = len(closes)
    stress_wins = 0
    total_stress_net = 0.0
    
    for c in closes:
        net_pct = float(c.get("net_pct", 0))
        cost = float(c.get("cost_usd", 1.0))
        
        # 1. Dynamic Size Slippage (Simulated impact of moving Titan blocks)
        # We assume $10 was 10% of depth, so scaling to $100 is 100% of depth.
        # Impact = (Order_Size / Depth) * Penalty
        impact_bps = (cost / 10.0) * 0.5 * slippage_multiplier
        
        # 2. Latency Jitter (Random Front-Run)
        jitter = random.uniform(0.5, 2.0) * latency_penalty_bps
        
        # 3. Fee Churn (User specified Taker Fee)
        # Original fee was 40bps. We add the delta.
        fee_delta_bps = (taker_fee_bps - 40.0) if c.get("exit_type") == "taker_insurance" else 0.0
        
        total_penalty_bps = impact_bps + jitter + fee_delta_bps
        stress_net_pct = net_pct - (total_penalty_bps / 100.0)
        
        if stress_net_pct > 0:
            stress_wins += 1
        
        total_stress_net += (stress_net_pct / 100.0) * cost
        
    return {
        "total": total,
        "wins": stress_wins,
        "wr": (stress_wins / total * 100) if total > 0 else 0,
        "net": total_stress_net
    }

def main():
    print("=== DEEP ADVERSARIAL AUDIT (563 TRADES) ===")
    
    # Baseline
    res0 = run_stress_test(0, 0, 40)
    print(f"BASELINE: WR {res0['wr']:.1f}% | Net ${res0['net']:.2f}")
    
    # STAGE 1: Standard Friction (2bps latency + 1bps slippage)
    res1 = run_stress_test(1.0, 2.0, 40.0)
    print(f"STAGE 1 (Friction): WR {res1['wr']:.1f}% | Net ${res1['net']:.2f}")
    
    # STAGE 2: Titan Scale ($100 sizing + 60bps fees)
    res2 = run_stress_test(5.0, 3.0, 60.0)
    print(f"STAGE 2 (Titan):    WR {res2['wr']:.1f}% | Net ${res2['net']:.2f}")
    
    # STAGE 3: STRESS-TO-BREAK (The Edge of the Cliff)
    print("\n--- FINDING THE EDGE OF THE CLIFF ---")
    for s in range(5, 50, 5):
        res = run_stress_test(float(s), 5.0, 80.0)
        print(f"Stress Multiplier {s:2}: WR {res['wr']:.1f}% | Net ${res['net']:+.2f}")
        if res['wr'] < 90:
            print(f"!!! BREAKING POINT REACHED AT {s}x SLIPPAGE !!!")
            break

if __name__ == "__main__":
    main()
