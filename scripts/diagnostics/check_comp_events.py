import json
events = []
with open('reports/watchdog/crypto_watchdog_events.jsonl') as f:
    for line in f:
        e = json.loads(line)
        if 'compusd' in line.lower():
            events.append(e)
print(f'{len(events)} COMP M5 events')
for e in events[-5:]:
    print(json.dumps(e, indent=2)[:400])
    print()
