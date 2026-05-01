import json
d = json.load(open('reports/penetration_lattice_runner_watchdog.json'))
rows = d.get('rows', [])
print(f"Total rows: {len(rows)}")
for r in rows:
    lane = r.get('lane', '')
    if any(x in lane.lower() for x in ['m5_warp', 'solusd', 'xrpusd']):
        print(f"{lane}: status={r.get('status','?')}, heartbeat={r.get('heartbeat_at','?')}")
