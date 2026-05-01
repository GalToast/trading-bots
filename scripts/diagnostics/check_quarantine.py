import json
q = json.load(open('reports/watchdog/crypto_watchdog_quarantine_state.json'))
print(json.dumps(q, indent=2))
