import json
reg = json.load(open('configs/penetration_lattice_runner_registry.json'))
lanes = reg.get('lanes', [])
for lane in lanes:
    name = lane.get('name', '')
    if any(x in name for x in ['fxmicro', 'rearm_941777', 'momentum']):
        args = lane.get('extra_args', [])
        print(f'{name}:')
        print(f'  script: {lane.get("script", "?")}')
        print(f'  args: {" ".join(args[:10])}')
        symbols = lane.get('symbols', [])
        print(f'  symbols: {symbols}')
        print(f'  state: {lane.get("state_path", "?")}')
        heartbeat = lane.get('watchdog_heartbeat_check_seconds', 'N/A')
        print(f'  watchdog hb: {heartbeat}')
        print()
