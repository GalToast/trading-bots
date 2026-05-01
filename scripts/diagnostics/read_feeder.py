import json
r = json.load(open('reports/watchdog/feeder_crypto_m15_canary_report.json'))
print(type(r).__name__)
if isinstance(r, dict):
    print(list(r.keys()))
    rows = r.get('rows', [])
    print(f'{len(rows)} rows')
    for row in rows:
        lane = row.get('lane', '?')
        print(f'{lane}: status={row.get("status","?")}, pid={row.get("pid","?")}')
