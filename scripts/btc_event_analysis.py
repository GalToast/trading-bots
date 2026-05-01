import json

p = 'reports/penetration_lattice_live_btcusd_exc2_tight_exec_events.jsonl'
lines = open(p, encoding='utf-8').readlines()

for i, line in enumerate(lines):
    try:
        ev = json.loads(line.strip())
        action = ev.get('action', '')
        
        if action == 'close_attempt':
            print(f'\n=== Close attempt #{i} ===')
            result = ev.get('result', {})
            print(f'  Reason: {result.get("reason", ev.get("reason", ""))}')
            print(f'  Direction: {ev.get("direction", "?")}')
            print(f'  Result keys: {list(result.keys())}')
            
            bf = result.get('broker_fill', result.get('broker_fills', None))
            if bf:
                print(f'  Broker fill: {json.dumps(bf, indent=2)[:500]}')
            else:
                print(f'  No broker fill found')
                print(f'  Result: {json.dumps(result, indent=2)[:500]}')
    except Exception as e:
        print(f'Error on line {i}: {e}')
