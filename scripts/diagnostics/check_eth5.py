import json
state = json.load(open('reports/penetration_lattice_shadow_ethusd_m5_warp_5_state.json'))
print('ETH M5 $5 state:')
print(f'  close_count: {state.get("close_count", 0)}')
print(f'  anchor: {state.get("anchor_price", 0)}')
open_pos = state.get('open_positions', {})
print(f'  open_positions: {len(open_pos)}')
for side, positions in open_pos.items():
    for p in positions[:3]:
        print(f'    {side}: {p}')
