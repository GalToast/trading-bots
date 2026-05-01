import json
from pathlib import Path

event_log = Path('reports/kraken_spot_maker_machinegun_shadow_events.jsonl')
events = []
with open(event_log) as f:
    for line in f:
        try:
            events.append(json.loads(line.strip()))
        except:
            pass

closes = [e for e in events if 'close' in e.get('action', '')]
print(f'Total closes: {len(closes)}')
print(f'\nRecent closes (last 10):')
for e in closes[-10:]:
    print(f'  {e.get("product_id", "?"):14s} net={e.get("net_pct", 0):+.4f}%  reason={e.get("reason", "?")}')

total = sum(e.get('net_pct', 0) for e in closes)
wins = sum(1 for e in closes if e.get('net_pct', 0) > 0)
print(f'\nTotal net: {total:+.4f}%')
print(f'Win rate: {wins}/{len(closes)} = {wins/len(closes)*100:.1f}%')

# HOUSE closes
house = [e for e in closes if e.get('product_id') == 'HOUSE-USD']
print(f'\nHOUSE-USD closes: {len(house)}')
for e in house:
    print(f'  net={e.get("net_pct", 0):+.4f}%')
