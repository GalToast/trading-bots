import json, os
out = []
for lane in ['shadow_gbpusd_m15_asym', 'shadow_nzdusd_m15_asym', 'shadow_eurusd_m15_opt', 'shadow_btcusd_m15_warp']:
    path = f'reports/penetration_lattice_{lane}_state.json'
    if os.path.exists(path):
        with open(path) as f:
            state = json.load(f)
        s = list(state.get('symbols', {}).values())[0]
        closes = s.get('realized_closes', 0)
        net = s.get('realized_net_usd', 0)
        opens = len(s.get('open_tickets', []))
        resets = s.get('anchor_resets', 0)
        resets_flat = s.get('anchor_resets_flat', 0)
        resets_risk = s.get('anchor_resets_risk', 0)
        per_close = net / closes if closes > 0 else 0
        hb = state.get('runner', {}).get('heartbeat_at', 'N/A')
        sym = list(state.get('symbols', {}).keys())[0]
        out.append(f'{lane}: {sym} {closes}c ${net:.2f} (${per_close:.2f}/c) {opens}o {resets}r ({resets_flat}f/{resets_risk}risk) hb={hb}')
    else:
        out.append(f'{lane}: NO STATE FILE')
result = '\n'.join(out)
with open('reports/lane_status_update.txt', 'w') as f:
    f.write(result + '\n')
print(result)
