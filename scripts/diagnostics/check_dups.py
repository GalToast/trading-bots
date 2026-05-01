import json
r = json.load(open('reports/watchdog/crypto_watchdog_report.json'))
rows = r.get('rows', [])
print(f'Total rows: {len(rows)}')
names = [row.get('name', '?') for row in rows]
print(f'Unique names: {len(set(names))}')
from collections import Counter
counts = Counter(names)
for name, count in counts.items():
    if count > 1:
        print(f'DUPLICATE: {name} appears {count} times')
for row in rows:
    name = row.get('name', '?')
    status = row.get('status', '?')
    pids = row.get('process_ids', [])
    print(f'  {name}: status={status}, pids={pids}')
