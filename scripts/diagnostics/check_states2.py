import json
for name in ['shadow_solusd_m5_warp', 'shadow_xrpusd_m5_warp']:
    state = json.load(open(f'reports/penetration_lattice_{name}_state.json'))
    print(f'{name}:')
    print(f'  updated_at: {state.get("updated_at", "?")}')
    print(f'  close_count: {state.get("close_count", 0)}')
    open_pos = state.get("open_positions", {})
    print(f'  open_positions: {len(open_pos)}')
    for side, positions in open_pos.items():
        for p in positions[:3]:
            print(f'    {side}: {p}')
    print(f'  anchor: {state.get("anchor_price", 0)}')
    print()
