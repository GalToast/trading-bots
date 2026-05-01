import json
reg = json.load(open('configs/penetration_lattice_runner_registry.json'))
for r in reg['lanes']:
    name = r.get('name', '')
    if 'shadow_btcusd_m5_warp' == name:
        print(json.dumps(r, indent=2))
        break
