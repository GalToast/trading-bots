import json, os
groups = ['crypto_watchdog', 'shadow_watchdog', 'fx_watchdog', 'feeder_crypto_m15_canary', 'm5_warp_comparison']
for g in groups:
    path = f'reports/watchdog/{g}_loop_state.json'
    if os.path.exists(path):
        wd = json.load(open(path))
        status = wd.get('status', '?')
        updated = wd.get('updated_at', '?')
        pid = wd.get('pid', '?')
        print(f'{g}: status={status}, updated={updated}, pid={pid}')
    else:
        print(f'{g}: NO STATE FILE')
