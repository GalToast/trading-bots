import json
import subprocess

# Get all Python processes with command lines
result = subprocess.run(
    ['powershell', '-Command',
     'Get-CimInstance Win32_Process -Filter "Name=\'python.exe\'" | Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress'],
    capture_output=True, text=True
)

try:
    procs = json.loads(result.stdout)
    if isinstance(procs, dict):
        procs = [procs]
except:
    print("Failed to parse process list")
    exit(1)

# Categorize
categories = {
    'live_lanes': [],
    'shadow_crypto': [],
    'shadow_fx': [],
    'coinbase_bundle': [],
    'coinbase_rsi': [],
    'coinbase_piranha': [],
    'coinbase_rotation': [],
    'coinbase_other': [],
    'kelly': [],
    'watchdog': [],
    'comms': [],
    'fx_m15_micro': [],
    'gbpusd_forward': [],
    'other': [],
}

for p in procs:
    cmd = p.get('CommandLine', '') or ''
    pid = p.get('ProcessId', '?')
    label = '%s (%s)' % (cmd[:80].replace('\n', ' '), pid)

    if 'multi_coin_isolated' in cmd or 'kelly_shadow_runner' in cmd:
        categories['kelly'].append(label)
    elif 'live_penetration' in cmd and 'direct-live' in cmd:
        categories['live_lanes'].append(label)
    elif 'live_penetration' in cmd and 'shadow' in cmd and 'crypto' in cmd:
        categories['shadow_crypto'].append(label)
    elif 'live_penetration' in cmd and 'shadow' in cmd:
        categories['shadow_fx'].append(label)
    elif 'coinbase_rsi_bundle' in cmd:
        categories['coinbase_bundle'].append(label)
    elif 'live_coinbase_rsi_shadow' in cmd:
        categories['coinbase_rsi'].append(label)
    elif 'live_coinbase_spot_piranha' in cmd:
        categories['coinbase_piranha'].append(label)
    elif 'live_rotation' in cmd:
        categories['coinbase_rotation'].append(label)
    elif 'live_coinbase' in cmd or 'coinbase_futures' in cmd:
        categories['coinbase_other'].append(label)
    elif 'watch_penetration' in cmd:
        categories['watchdog'].append(label)
    elif 'comms_server' in cmd:
        categories['comms'].append(label)
    elif 'fx_m15_micro' in cmd:
        categories['fx_m15_micro'].append(label)
    elif 'shadow_gbpusd_tick_forward' in cmd:
        categories['gbpusd_forward'].append(label)
    else:
        categories['other'].append(label)

print('=' * 70)
print('PYTHON PROCESS FLEET AUDIT')
print('=' * 70)
print('Total: %d processes' % len(procs))

for cat, items in categories.items():
    if items:
        print('\n%s (%d):' % (cat.upper(), len(items)))
        for item in items:
            print('  - %s' % item)
