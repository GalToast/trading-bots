import json, psutil

# Check BTC M15 shadow process
print("=== BTC M15 Shadow ($15 step) ===")
r = json.load(open('reports/watchdog/feeder_crypto_m15_canary_report.json'))
for row in r.get('rows', []):
    name = row.get('name', '')
    if 'btcusd_m15_warp' in name.lower():
        print(f'{name}:')
        print(f'  status: {row.get("status")}')
        print(f'  process_ids: {row.get("process_ids")}')
        print(f'  heartbeat_age: {row.get("heartbeat_age_seconds")}')
        print(f'  closes: {row.get("event_trade_closes")}')
        print(f'  open: {row.get("open_count")}')
        print()

# Check if processes are alive
print("=== BTC M15 Shadow Processes ===")
for p in psutil.process_iter(['pid', 'cmdline']):
    cmd = ' '.join(p.info['cmdline'] or [])
    if 'btcusd_m15_warp' in cmd.lower():
        print(f'PID {p.info["pid"]}: {cmd[:120]}')
