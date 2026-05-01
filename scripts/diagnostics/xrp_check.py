import json
try:
    baseline = json.load(open('reports/clean_forward_baselines.json'))
    xrp = baseline.get('shadow_xrpusd_m5_warp', {})
    print(f'XRP M5 baseline: {json.dumps(xrp, indent=2)[:500]}')
except Exception as e:
    print(f'No baseline: {e}')

try:
    state = json.load(open('reports/penetration_lattice_shadow_xrpusd_m5_warp_state.json'))
    print(f'close_count: {state.get("close_count", 0)}')
    print(f'anchor_reset_count: {state.get("anchor_reset_count", 0)}')
    open_pos = state.get('open_positions', {})
    print(f'open positions: {len(open_pos)}')
except Exception as e:
    print(f'State error: {e}')
