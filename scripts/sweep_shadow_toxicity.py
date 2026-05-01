#!/usr/bin/env python3
"""Quick shadow lane toxicity sweep."""
import json, os

registry_data = json.load(open('configs/penetration_lattice_runner_registry.json'))
registry = registry_data.get('lanes', [])
results = []
for entry in registry:
    if not entry.get('enabled', True):
        continue
    name = entry['name']
    state_path = entry.get('state_path', '')
    if not state_path:
        continue
    if not os.path.exists(state_path):
        results.append((name, 0, 0, 0, 0, 'NO_STATE_FILE'))
        continue
    try:
        state = json.load(open(state_path))
        symbols = state.get('symbols', {})
        for sym, data in symbols.items():
            closes = data.get('realized_closes', 0)
            net = data.get('realized_net_usd', 0)
            opens = len(data.get('open_tickets', []))
            resets = data.get('anchor_resets', 0)
            per_close = net / closes if closes > 0 else 0
            results.append((name, closes, net, per_close, opens, resets))
    except Exception as e:
        results.append((name, 0, 0, 0, 0, f'ERROR: {e}'))

# Sort by per_close ascending (most toxic first)
for name, closes, net, per_close, opens, resets in sorted(results, key=lambda x: x[3]):
    if isinstance(resets, str):
        print(f"{name}: {resets}")
    elif closes > 0:
        flag = "TOXIC" if per_close < 0 else "OK"
        print(f"{name}: {closes}c net={net:.2f} per_close={per_close:.2f} opens={opens} resets={resets} [{flag}]")
    else:
        print(f"{name}: {closes}c opens={opens} resets={resets} [bootstrap]")
