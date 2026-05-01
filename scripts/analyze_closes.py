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
print(f'Total closes in event log: {len(closes)}')
if closes:
    total_pnl = sum(e.get('net_pct', 0) for e in closes)
    wins = sum(1 for e in closes if e.get('net_pct', 0) > 0)
    print(f'Total net %: {total_pnl:.4f}%')
    print(f'Win rate: {wins}/{len(closes)} = {wins/len(closes):.1%}')
    print(f'Avg per close: {total_pnl/len(closes):.4f}%')
    
    print(f'\nRecent closes:')
    for e in closes[-10:]:
        print(f'  {e.get("product_id", "?"):12s} net={e.get("net_pct", 0):+.4f}%  reason={e.get("reason", "?")}')
