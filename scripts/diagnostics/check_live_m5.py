import json
for name in ['live_solusd_m5_warp', 'live_ethusd_m5_warp']:
    try:
        state = json.load(open(f'reports/penetration_lattice_{name}_state.json'))
        print(f'{name}:')
        print(f'  close_count: {state.get("close_count", 0)}')
        print(f'  anchor: {state.get("anchor_price", 0)}')
        open_pos = state.get('open_positions', {})
        total = sum(len(v) for v in open_pos.values())
        print(f'  open: {total}')
        updated = state.get('updated_at', '?')
        print(f'  updated_at: {updated}')
    except Exception as e:
        print(f'{name}: ERROR - {e}')
    print()
