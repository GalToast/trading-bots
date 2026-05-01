import json
r = json.load(open('reports/watchdog/crypto_watchdog_report.json'))
for row in r.get('rows', []):
    name = row.get('name', '?')
    status = row.get('status', '?')
    pid = row.get('matching_process_id')
    hb = row.get('heartbeat_age_seconds', '?')
    closes = row.get('event_trade_closes', 0)
    opens = row.get('open_count', 0)
    if 'sol' in name.lower() or 'xrp' in name.lower() or 'comp' in name.lower():
        print(f'{name}: status={status}, pid={pid}, hb={hb}s, closes={closes}, open={opens}')
