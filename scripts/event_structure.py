import json
from pathlib import Path
from collections import Counter

event_log = Path('reports/kraken_spot_maker_machinegun_shadow_events.jsonl')
events = []
with open(event_log) as f:
    for line in f:
        try:
            events.append(json.loads(line.strip()))
        except:
            pass

actions = Counter(e.get('action', '?') for e in events)
print('Action counts:')
for action, count in actions.most_common():
    print(f'  {action}: {count}')

print(f'\nTotal events: {len(events)}')

opens = [e for e in events if 'open' in e.get('action', '').lower()]
closes = [e for e in events if 'close' in e.get('action', '').lower()]
nonzero_closes = [e for e in closes if e.get('net_pct', 0) != 0]
print(f'\nOpens: {len(opens)}')
print(f'Closes (all): {len(closes)}')
print(f'Closes (nonzero): {len(nonzero_closes)}')

print(f'\nFirst 5 opens:')
for e in opens[:5]:
    print(f'  {e.get("timestamp", "?"):>30s} {e.get("action", "?"):30s} {e.get("product_id", "?"):14s} net={e.get("net_pct", 0):+.4f}%')

print(f'\nLast 5 closes:')
for e in closes[-5:]:
    print(f'  {e.get("timestamp", "?"):>30s} {e.get("action", "?"):30s} {e.get("product_id", "?"):14s} net={e.get("net_pct", 0):+.4f}%  reason={e.get("reason", "?")}')

open_products = set(e.get('product_id') for e in opens)
close_products = set(e.get('product_id') for e in nonzero_closes)
print(f'\nProducts with opens: {sorted(open_products)}')
print(f'Products with nonzero closes: {sorted(close_products)}')
