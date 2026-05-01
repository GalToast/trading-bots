import json
from pathlib import Path

state_file = Path('reports/kraken_spot_maker_machinegun_shadow_state.json')
if state_file.exists():
    with open(state_file) as f:
        state = json.load(f)
    state_data = state.get('state', {})
    print(f'Updated: {state.get("updated_at", "?")}')
    print(f'Cash: ${state_data.get("cash_usd", 0):.4f}')
    print(f'Active positions: {len(state_data.get("active_positions", []))}')
    print(f'Total closes: {state_data.get("total_closes", 0)}')
    
    for prod, pos in state_data.get('active_positions', {}).items():
        if isinstance(pos, dict):
            print(f'  {prod}: entry={pos.get("entry_price", 0):.4f}, pnl={pos.get("max_net_pnl", 0):+.4f}')
        else:
            print(f'  {prod}: {pos}')

event_log = Path('reports/kraken_spot_maker_machinegun_shadow_events.jsonl')
if event_log.exists():
    events = []
    with open(event_log) as f:
        for line in f:
            try:
                events.append(json.loads(line.strip()))
            except:
                pass
    house_events = [e for e in events if e.get('product_id') == 'HOUSE-USD']
    print(f'\nHOUSE-USD events: {len(house_events)}')
    for e in house_events[-5:]:
        print(f'  {e.get("action", "?")}: net={e.get("net_pct", 0):+.4f}%  reason={e.get("reason", "?")}')
