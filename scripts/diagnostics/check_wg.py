import json
wg = json.load(open('configs/watchdog_groups.json'))
cw = wg['groups'].get('crypto_watchdog', {})
print(f'crypto_watchdog lanes ({len(cw.get("lanes", []))}):')
for lane in cw.get('lanes', []):
    print(f'  {lane}')
