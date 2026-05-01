import json
out = []
for lane in ['shadow_gbpjpy_m15_warp', 'shadow_eurjpy_m15_warp', 'shadow_xagusd_m15_warp', 'shadow_nas100_m15_warp', 'shadow_us30_m15_warp']:
    path = f'reports/penetration_lattice_{lane}_state.json'
    try:
        with open(path) as f:
            state = json.load(f)
        pid = state.get('runner', {}).get('pid', 'N/A')
        script = state.get('runner', {}).get('script', 'N/A')
        started = state.get('runner', {}).get('started_at', 'N/A')
        heartbeat = state.get('runner', {}).get('heartbeat_at', 'N/A')
        exceptions = state.get('runner', {}).get('consecutive_exceptions', 'N/A')
        out.append(f'{lane}: PID={pid}, script={script}, started={started}, hb={heartbeat}, exc={exceptions}')
    except Exception as e:
        out.append(f'{lane}: ERROR - {e}')
with open('reports/cross_pair_pids.txt', 'w') as f:
    f.write('\n'.join(out) + '\n')
for line in out:
    print(line)
