import json

with open('configs/penetration_lattice_runner_registry.json') as f:
    data = json.load(f)

disabled_count = 0
for lane in data['lanes']:
    if lane['name'] == 'shadow_btcusd_m15_warp':
        lane['enabled'] = False
        lane['pause_note'] = 'KILLED 2026-04-14: 105 resets, -$242, step=$15 way too tight for BTC M15 (ATR=$283)'
        print(f'Disabled: {lane["name"]}')
        disabled_count += 1

if disabled_count == 0:
    print('BTC M15 warp entry not found in registry')

with open('configs/penetration_lattice_runner_registry.json', 'w') as f:
    json.dump(data, f, indent=2)
print('Registry updated')
