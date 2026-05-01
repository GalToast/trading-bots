import json
for name in ['bsxusd_1s_dislocation_tape.json', 'billyusd_1s_dislocation_tape.json']:
    import os
    path = f'reports/cache/{name}'
    if os.path.exists(path):
        d = json.load(open(path))
        print(f'{name}: {d["total_ticks"]} ticks, {d["triggered_count"]} triggers')
        for t in d['triggered_events'][:5]:
            print(f'  t={t["s"]}s: spread={t["spread_bps"]}bps bid=${t["bid_depth_usd"]} ask=${t["ask_depth_usd"]} ask_down={t["ask_down_bps"]}bps bid_up={t["bid_up_bps"]}bps')
    else:
        print(f'{name}: NOT YET READY')
