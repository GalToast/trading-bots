#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

REPORTS = ROOT / "reports"
CB_BOARD_PATH = REPORTS / "coinbase_spot_machinegun_strategy_board.json"
KR_BOARD_PATH = REPORTS / "kraken_spot_frontier_strategy_board.json"
OUTPUT_PATH = REPORTS / "venue_handoff_bridge.json"
MD_PATH = REPORTS / "venue_handoff_bridge.md"

def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def main():
    print("VENUE HANDOFF BRIDGE: Coinbase -> Kraken")
    
    cb_board = load_json(CB_BOARD_PATH)
    kr_board = load_json(KR_BOARD_PATH)
    
    # Map Coinbase signals
    cb_signals = {
        r["product_id"]: r for r in cb_board.get("rows", [])
        if r.get("rank") <= 20 # Expanded to top 20
    }
    
    # Map Kraken liquidity/opportunity
    kr_rows = {r["product_id"]: r for r in kr_board.get("rows", [])}
    
    handoffs = []
    for pid, cb_row in cb_signals.items():
        if pid in kr_rows:
            kr_row = kr_rows[pid]
            
            # Handoff Logic:
            # High-fidelity Coinbase momentum signal + Kraken low-fee structure
            cb_edge = float(cb_row.get("edge_over_hurdle_pct", 0))
            # Fee savings: CB 240bps round trip vs Kraken 80bps (taker) or 25bps (maker)
            fee_alpha_bps = 160.0 # Taker savings
            
            handoffs.append({
                "product_id": pid,
                "venue_source": "COINBASE",
                "venue_target": "KRAKEN",
                "signal_type": "high_fidelity_momentum",
                "cb_rank": cb_row["rank"],
                "cb_edge_pct": cb_edge,
                "kr_playbook": kr_row.get("playbook", "frontier_machinegun"),
                "kr_mer": kr_row.get("mer", 0.0),
                "estimated_fee_alpha_bps": fee_alpha_bps,
                "verdict": "EXECUTE_ON_KRAKEN"
            })
            
    payload = {
        "generated_at": utc_now_iso(),
        "handoffs": handoffs,
        "leadership_read": [
            "This bridge maps high-fidelity Coinbase signals to low-fee Kraken execution.",
            "Logic: If a top-5 Coinbase momentum signal exists on Kraken, route to Kraken maker/taker.",
            "Advantage: Bypasses the 240bps Coinbase fee-wall while keeping 1s radar fidelity."
        ]
    }
    
    with open(OUTPUT_PATH, "w") as f:
        json.dump(payload, f, indent=2)
        
    # Write MD
    lines = ["# Venue Handoff Bridge", "", "## Active Handoff Recommendations", ""]
    lines.append("| Product | CB Rank | CB Edge % | KR Playbook | MER | Est Fee Alpha | Verdict |")
    lines.append("| --- | ---: | ---: | --- | ---: | ---: | --- |")
    for h in handoffs:
        lines.append(f"| {h['product_id']} | {h['cb_rank']} | {h['cb_edge_pct']:.2f}% | {h['kr_playbook']} | {h['kr_mer']:.4f} | {h['estimated_fee_alpha_bps']} bps | **{h['verdict']}** |")
        
    with open(MD_PATH, "w") as f:
        f.write("\n".join(lines))
        
    print(f"DONE! Saved {len(handoffs)} handoffs to {OUTPUT_PATH}")

if __name__ == "__main__":
    main()
