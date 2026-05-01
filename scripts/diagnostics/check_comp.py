import json

# Check COMP M5 quarantine details
print("=== COMP M5 Quarantine ===")
r = json.load(open('reports/watchdog/crypto_watchdog_report.json'))
for row in r.get('rows', []):
    name = row.get('name', '')
    if 'comp' in name.lower():
        print(json.dumps(row, indent=2)[:1000])
        break

# Check quarantine state
print("\n=== Crypto Watchdog Quarantine State ===")
q = json.load(open('reports/watchdog/crypto_watchdog_quarantine_state.json'))
print(json.dumps(q, indent=2)[:1000])

# Check registry entry
print("\n=== COMP M5 Registry Entry ===")
reg = json.load(open('configs/penetration_lattice_runner_registry.json'))
for lane in reg.get('lanes', []):
    if 'comp' in lane.get('name', '').lower():
        print(json.dumps(lane, indent=2))
        break
