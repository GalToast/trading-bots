#!/usr/bin/env python3
"""Quick audit of all key lane states."""
import json, os
from pathlib import Path

ROOT = Path(__file__).parent.parent
REPORTS = ROOT / "reports"

def read_json(path):
    p = REPORTS / path
    if not p.exists():
        return None
    return json.loads(p.read_text())

def count_events(path):
    p = REPORTS / path
    if not p.exists():
        return 0
    with open(p) as f:
        return sum(1 for line in f if line.strip())

def analyze_events(path, action_filter=None):
    p = REPORTS / path
    if not p.exists():
        return []
    results = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            if action_filter is None or e.get('action') == action_filter:
                results.append(e)
    return results

print("=" * 80)
print("LIVE LANE AUDIT — 2026-04-13 ~22:42 UTC")
print("=" * 80)

# 1. FX M15 Micro (forward validation)
print("\n--- FX M15 Micro Forward Validation ---")
for sym in ['gbpusd', 'eurusd', 'nzdusd']:
    state = read_json(f"shadow_fx_m15_micro_{sym}_bar_state.json")
    if state:
        print(f"  {sym.upper()}: {state['realized_closes']} closes, ${state['realized_net_usd']:+.2f}, "
              f"{len(state['open_tickets'])} open, bars={state.get('bars_processed','?')}/{state.get('total_bars','?')}, "
              f"updated={state.get('updated_at','?')}")
    else:
        print(f"  {sym.upper()}: STATE NOT FOUND")

# 2. GBPUSD tick-forward
print("\n--- GBPUSD Tick-Forward Shadow ---")
state = read_json("shadow_gbpusd_tick_forward_state.json")
if state:
    closes = analyze_events("shadow_gbpusd_tick_forward_events.jsonl", "close")
    durable_net = sum(c.get('net_usd', 0) for c in closes)
    last_event = analyze_events("shadow_gbpusd_tick_forward_events.jsonl")[-1] if closes else None
    print(f"  Durable closes: {len(closes)}, Net: ${durable_net:+.2f}")
    print(f"  Snapshot: {len(state.get('open_tickets',[]))} open, updated={state.get('updated_at','?')}")
    if last_event:
        print(f"  Last event: {last_event.get('ts_utc','?')} action={last_event.get('action','?')}")
else:
    print("  STATE NOT FOUND")

# 3. Live BTC M5 Warp
print("\n--- Live BTC M5 Warp (941780) ---")
state = read_json("penetration_lattice_live_btcusd_m5_warp_state.json")
if state:
    print(f"  {state.get('realized_closes','?')} closes, ${state.get('realized_net_usd',0):+.2f}, "
          f"{len(state.get('open_tickets',[]))} open, updated={state.get('updated_at','?')}")
else:
    print("  STATE NOT FOUND")

# 4. Live FX Rearm
print("\n--- Live FX Rearm (941777) ---")
state = read_json("penetration_lattice_live_source_state.json")
if state:
    tickets = state.get('open_tickets', [])
    sym_counts = {}
    for t in tickets:
        sym = t.get('symbol', 'unknown')
        sym_counts[sym] = sym_counts.get(sym, 0) + 1
    print(f"  {state.get('realized_closes','?')} closes, ${state.get('realized_net_usd',0):+.2f}, "
          f"{len(tickets)} open {sym_counts}, updated={state.get('updated_at','?')}")
else:
    print("  STATE NOT FOUND")

# 5. BTC M15 Warp shadow
print("\n--- BTC M15 Warp Shadow ---")
state = read_json("penetration_lattice_shadow_btcusd_m15_warp_state.json")
if state:
    print(f"  {state.get('realized_closes','?')} closes, ${state.get('realized_net_usd',0):+.2f}, "
          f"{len(state.get('open_tickets',[]))} open, updated={state.get('updated_at','?')}")
else:
    print("  STATE NOT FOUND")

# 6. Kelly shadow
print("\n--- Kelly Shadow ---")
state = read_json("kelly_shadow_state.json")
if state:
    print(f"  Cycle: {state.get('cycle','?')}, Equity: ${state.get('equity',0):.2f}, "
          f"Closes: {state.get('total_closes','?')}, "
          f"updated={state.get('updated_at','?')}")
else:
    print("  STATE NOT FOUND")

# 7. CFG/ETH ratio sleeve
print("\n--- CFG/ETH Ratio Sleeve ---")
state = read_json("cfg_eth_synthetic_sleeve_shadow_state.json")
if state:
    print(f"  Closes: {state.get('realized_closes','?')}, Opens: {state.get('open_count','?')}, "
          f"updated={state.get('updated_at','?')}")
else:
    print("  STATE NOT FOUND")

# 8. CFG/BTC ratio sleeve
print("\n--- CFG/BTC Ratio Sleeve ---")
state = read_json("cfg_btc_synthetic_sleeve_shadow_state.json")
if state:
    print(f"  Closes: {state.get('realized_closes','?')}, Opens: {state.get('open_count','?')}, "
          f"updated={state.get('updated_at','?')}")
else:
    print("  STATE NOT FOUND")

print("\n" + "=" * 80)
print("AUDIT COMPLETE")
print("=" * 80)
