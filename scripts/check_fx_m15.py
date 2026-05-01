import json, os, sys
m15_lanes = ['shadow_gbpusd_m15_warp', 'shadow_usdjpy_m15_warp', 'shadow_xauusd_m15_warp', 'shadow_audusd_m15_warp', 'shadow_eurusd_m15_warp', 'shadow_nzdusd_m15_warp', 'shadow_usdcad_m15_warp']
out = []
for lane in m15_lanes:
    state_path = f'reports/penetration_lattice_{lane}_state.json'
    if os.path.exists(state_path):
        with open(state_path) as f:
            state = json.load(f)
        sym = state['symbols']
        s = list(sym.values())[0]
        closes = s.get('realized_closes', 0)
        net = s.get('realized_net_usd', 0)
        opens = len(s.get('open_tickets', []))
        anchor = s.get('anchor', 0)
        resets = s.get('anchor_resets', 0)
        out.append(f'{lane}: {closes}c, ${net:.2f}, {opens} opens, anchor ${anchor:.5f}, {resets} resets')
    else:
        out.append(f'{lane}: NO STATE FILE')
with open('reports/fx_m15_status.txt', 'w') as f:
    f.write('\n'.join(out) + '\n')
print('\n'.join(out))
