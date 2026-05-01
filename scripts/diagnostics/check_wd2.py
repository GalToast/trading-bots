import json
wd = json.load(open('reports/watchdog/crypto_watchdog_loop_state.json'))
print(f'Status: {wd.get("status")}')
print(f'Updated: {wd.get("updated_at", "?")}')
print(f'Lanes: {len(wd.get("lanes", []))}')
print(f'Rows: {wd.get("rows_total", 0)}')
print(f'Counts: {wd.get("status_counts", {})}')
