import json
d = json.load(open('reports/execution_monitor_report.json'))
results = []
for r in d['rows']:
    lane = r['lane']
    if any(x in lane for x in ['m15_warp', 'm5_warp', 'gbpusd_tick', 'solusd']):
        results.append({
            'lane': lane,
            'closes': r['event_trade_closes'],
            'open': r['open_count'],
            'wd': r['watchdog_status'],
            'clean_usd': r['clean_forward_realized_delta_usd'],
            'clean_c': r['clean_forward_new_closes'],
            'notes': r.get('notes', '')[:100]
        })
json.dump(results, open('reports/lane_check_output.json', 'w'), indent=2)
print(f"Wrote {len(results)} lanes")
