import json, psutil

# Check all watchdog reports for issues
reports = {
    'crypto': 'reports/watchdog/crypto_watchdog_report.json',
    'shadow': 'reports/watchdog/shadow_watchdog_report.json',
    'fx': 'reports/watchdog/fx_watchdog_report.json',
    'feeder_m15': 'reports/watchdog/feeder_crypto_m15_canary_report.json',
}

issues = []
for name, path in reports.items():
    try:
        r = json.load(open(path))
        for row in r.get('rows', []):
            lane = row.get('name', '?')
            status = row.get('status', '?')
            pids = row.get('process_ids', [])
            reasons = row.get('reasons', [])
            hb = row.get('heartbeat_age_seconds', '?')

            if status not in ('ok', 'paused', 'starting'):
                issues.append(f'{name}: {lane} → {status}, reasons={reasons[:2]}')
            elif status == 'ok' and not pids:
                issues.append(f'{name}: {lane} → ok but NO PROCESS, hb={hb}s')
    except Exception as e:
        issues.append(f'{name}: ERROR reading {path}: {e}')

if issues:
    print(f'Found {len(issues)} issues:')
    for i in issues:
        print(f'  {i}')
else:
    print('✅ No issues found!')
