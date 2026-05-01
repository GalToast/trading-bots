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

tight_products = {'HOUSE-USD', 'FOLKS-USD', 'BTR-USD'}
tight_closes = [e for e in closes if e.get('product_id') in tight_products]

print(f'Tight gate closes: {len(tight_closes)}')
total = sum(e.get('net_pct', 0) for e in tight_closes)
wins = sum(1 for e in tight_closes if e.get('net_pct', 0) > 0)
losses = sum(1 for e in tight_closes if e.get('net_pct', 0) <= 0)
print(f'Wins: {wins}, Losses: {losses}, WR: {wins/len(tight_closes)*100:.1f}%')
print(f'Total net: {total:+.4f}%')
print(f'Avg per close: {total/len(tight_closes):+.4f}%')

baseline_pnl = sum(e.get('net_pct', 0) * 8 / 100 for e in tight_closes)
test_pnl = sum(e.get('net_pct', 0) * 15 / 100 for e in tight_closes)
print(f'\nBaseline (8%): ${baseline_pnl:.4f}')
print(f'Test (15%): ${test_pnl:.4f}')
print(f'Multiplier: {test_pnl/baseline_pnl:.2f}x')

if losses == 0:
    print(f'\nZERO LOSSES on tight gate - size increase is SAFE')
else:
    print(f'\n{losses} losses on tight gate')
