import json
reg = json.load(open('configs/penetration_lattice_runner_registry.json'))
for r in reg.get('lanes', []):
    name = r.get('name', '') or r.get('registry_key', '')
    if any(x in name.lower() for x in ['ethusd_m5', 'xrpusd_m5', 'solusd_m5', 'eth_m5', 'xrp_m5', 'sol_m5']):
        wd = r.get('watchdog_group', 'NOT_SET')
        state = r.get('state', '?')
        pid = r.get('pid', '?')
        step = r.get('step', '?')
        print(f'{name}: watchdog={wd}, state={state}, pid={pid}, step={step}')
