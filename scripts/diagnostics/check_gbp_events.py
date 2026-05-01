import json
lines = open('reports/penetration_lattice_shadow_gbpusd_m5_warp_events.jsonl').readlines()
actions = {}
for line in lines:
    e = json.loads(line)
    action = e.get('action', 'unknown')
    actions[action] = actions.get(action, 0) + 1
print(f'Total events: {len(lines)}')
for action, count in sorted(actions.items()):
    print(f'  {action}: {count}')

# Check closes
closes = [json.loads(l) for l in lines if 'close_ticket' in l]
total_pnl = sum(c.get('realized_pnl', 0) for c in closes)
print(f'\nCloses: {len(closes)}, Total PnL: ${total_pnl:.2f}')
if closes:
    print(f'Avg $/close: ${total_pnl/len(closes):.2f}')
