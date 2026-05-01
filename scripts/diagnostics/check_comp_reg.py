import json
reg = json.load(open('configs/penetration_lattice_runner_registry.json'))
print(f'Total lanes: {len(reg["lanes"])}')
for lane in reg['lanes']:
    if 'compusd' in lane.get('name', '').lower():
        print(f'FOUND: {lane["name"]}')
        print(f'  kind: {lane.get("kind")}')
        print(f'  has restart_args: {"restart_args" in lane}')
        break
else:
    print('NOT FOUND')
