#!/usr/bin/env python3
import json
from pathlib import Path

REPORTS = Path(__file__).parent.parent / "reports"
EXEC_EVENTS = REPORTS / "penetration_lattice_live_btcusd_exc2_tight_exec_events.jsonl"

events = [json.loads(l) for l in EXEC_EVENTS.read_text().splitlines() if l.strip()]
closes = [e for e in events if "close" in e.get("action", "")]

# Show full structure of first close attempt result
print("=== First close full event ===")
print(json.dumps(closes[0], indent=2))
print()
print("=== Trades 11-16 (zero PnL events) ===")
for c in closes[10:16]:
    print(json.dumps(c, indent=2)[:600])
    print("---")
