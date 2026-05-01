import json
from pathlib import Path

event_log = Path('reports/kraken_spot_maker_machinegun_shadow_events.jsonl')
if event_log.exists():
    events = []
    with open(event_log) as f:
        for line in f:
            try:
                events.append(json.loads(line.strip()))
            except:
                pass
    print(f'Total events: {len(events)}')
    if events:
        print(f'\nLast 5 events:')
        for e in events[-5:]:
            print(f'  {e.get("timestamp", "?")} | {e.get("action", "?")} | {e.get("product_id", "?")} | net={e.get("net_pct", "?")}')
else:
    print('Event log not found')

# Also check the state file
state_file = Path('reports/kraken_spot_maker_machinegun_shadow_state.json')
if state_file.exists():
    with open(state_file) as f:
        state = json.load(f)
    print(f'\nState file updated_at: {state.get("updated_at", "?")}')
    state_data = state.get("state", {})
    print(f'Cash: ${state_data.get("cash_usd", "?")}')
    print(f'Positions: {len(state_data.get("active_positions", []))}')
else:
    print('State file not found')
