import json
wd = json.load(open('reports/watchdog/crypto_watchdog_loop_state.json'))
print(f'Status: {wd.get("status")}')
print(f'Lanes ({len(wd.get("lanes", []))}):')
for lane in wd.get('lanes', []):
    print(f'  {lane}')
