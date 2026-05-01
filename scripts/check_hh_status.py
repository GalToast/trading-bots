import json, os
out = []
for path in [
    'reports/penetration_lattice_shadow_gbpusd_m15_hungry_hippo_v1_state.json',
    'reports/penetration_lattice_shadow_nas100_m15_hungry_hippo_v1_state.json',
]:
    if os.path.exists(path):
        with open(path) as f:
            state = json.load(f)
        s = list(state['symbols'].values())[0]
        closes = s.get('realized_closes', 0)
        net = s.get('realized_net_usd', 0)
        opens = len(s.get('open_tickets', []))
        resets = s.get('anchor_resets', 0)
        resets_f = s.get('anchor_resets_flat', 0)
        hb = state['runner'].get('heartbeat_at', 'N/A')
        started = state['runner'].get('started_at', 'N/A')
        sym = list(state['symbols'].keys())[0]
        out.append(f'{sym} HH: {closes}c, ${net:.2f}, {opens}o, {resets}r ({resets_f}f), started={started}, hb={hb}')
    else:
        out.append(f'{os.path.basename(path)}: NO STATE FILE')
result = '\n'.join(out)
with open('reports/hh_status.txt', 'w') as f:
    f.write(result + '\n')
print(result)
