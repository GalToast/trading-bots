import json
r=json.load(open('reports/watchdog/feeder_crypto_m15_canary_report.json'))
for row in r['rows']:
    name = row.get('name', '?')
    pid = row.get('matching_process_id')
    status = row.get('status', '?')
    hb = row.get('heartbeat_age_seconds', '?')
    resets = row.get('anchor_reset_count', 0)
    closes = row.get('event_trade_closes', 0)
    opens = row.get('event_trade_opens', 0)
    print(f'{name}: status={status}, pid={pid}, hb={hb}s, closes={closes}, opens={opens}, resets={resets}')
