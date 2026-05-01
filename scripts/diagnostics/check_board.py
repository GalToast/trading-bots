import json
d = json.load(open('reports/execution_monitor_report.json'))
key_lanes = [
    'live_btcusd_m15_warp_941781', 'live_btcusd_m5_warp_probation_941780',
    'live_btcusd_exc2_tight_941779', 'shadow_ethusd_m15_warp',
    'shadow_ethusd_m5_warp_5', 'shadow_ethusd_m5_warp_wide',
    'shadow_solusd_m5_warp', 'shadow_xrpusd_m5_warp',
    'shadow_gbpusd_tick_forward', 'shadow_btcusd_m5_warp',
    'shadow_btcusd_m15_warp', 'shadow_btcusd_m15_warp_on20',
    'shadow_ethusd_m5_warp'
]
for r in d['rows']:
    lane = r.get('lane', '')
    if lane in key_lanes:
        closes = r.get('event_trade_closes', 0)
        opens = r.get('open_count', 0)
        wd = r.get('watchdog_status', '?')
        clean = r.get('clean_forward_realized_delta_usd', '')
        clean_c = r.get('clean_forward_new_closes', '')
        notes = r.get('notes', '')[:100]
        print(f"{lane}")
        print(f"  closes={closes}, open={opens}, wd={wd}, clean={clean}/{clean_c}")
        if notes: print(f"  notes={notes}")
