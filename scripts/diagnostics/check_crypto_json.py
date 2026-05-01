import json
r = json.load(open('reports/watchdog/crypto_watchdog_report.json'))
for row in r.get('rows', []):
    name = row.get('name', '?')
    if 'sol' in name.lower() or 'xrp' in name.lower() or 'comp' in name.lower():
        print(f'{name}:')
        print(f'  status: {row.get("status")}')
        print(f'  process_ids: {row.get("process_ids")}')
        print(f'  matching_process_id: {row.get("matching_process_id")}')
        print(f'  heartbeat_age_seconds: {row.get("heartbeat_age_seconds")}')
        print(f'  event_trade_closes: {row.get("event_trade_closes")}')
        print(f'  open_count: {row.get("open_count")}')
        print(f'  enabled: {row.get("enabled")}')
        print(f'  reasons: {row.get("reasons", [])}')
        print()
