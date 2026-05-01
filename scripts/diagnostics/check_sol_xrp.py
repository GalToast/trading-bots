import json
d = json.load(open('reports/execution_monitor_report.json'))
for r in d['rows']:
    lane = r.get('lane', '?')
    if any(x in lane.lower() for x in ['sol', 'xrp']):
        closes = r.get('event_trade_closes', '?')
        opens = r.get('open_count', '?')
        wd = r.get('watchdog_status', '?')
        clean = r.get('clean_forward_realized_delta_usd', '')
        clean_c = r.get('clean_forward_new_closes', '')
        print(f'{lane}: closes={closes}, open={opens}, wd={wd}, clean={clean}/{clean_c}')
