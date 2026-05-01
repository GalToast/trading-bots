import json
reg = json.load(open('configs/penetration_lattice_runner_registry.json'))
for r in reg['lanes']:
    name = r.get('name', '')
    if any(x in name for x in ['ethusd_m5', 'xrpusd_m5', 'solusd_m5']):
        print(json.dumps(r, indent=2))
        print('---')
