import json
lines = []
for alpha in ['alpha0', 'alpha50']:
    s = json.load(open(f'reports/penetration_lattice_shadow_{alpha}_raw_state.json'))
    total_r = 0
    total_c = 0
    for sym in ['GBPUSD', 'EURUSD', 'NZDUSD']:
        if sym in s['symbols']:
            total_r += s['symbols'][sym].get('realized_net_usd', 0)
            total_c += s['symbols'][sym].get('realized_closes', 0)
    lines.append(f'{alpha}: closes={total_c}, realized=${total_r:.2f}')
with open('reports/shadow_status.txt', 'w') as f:
    f.write('\n'.join(lines) + '\n')
print('\n'.join(lines))
