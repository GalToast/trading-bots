import json

with open('reports/kraken_maker_opportunity_board.json') as f:
    board = json.load(f)

rows = board.get('rows', [])
board_dict = {r['product_id']: r for r in rows}

kraken_winners = ['CHIP-USD', 'DAI-USD', 'GUN-USD', 'GWEI-USD']
kraken_losers = ['H-USD', 'CRV-USD', 'BASED-USD', 'AERO-USD', 'DASH-USD']

print('PRODUCT SCORES ON CURRENT BOARD:')
print('=' * 80)
print(f'{"Product":12s} {"Tail":>10} {"FG":>10} {"MER":>10} {"Status":>10}')
print('-' * 80)

for prod in kraken_winners + kraken_losers:
    r = board_dict.get(prod, {})
    tail = r.get('tail_prob', 'N/A')
    fg = r.get('fast_green_prob', 'N/A')
    mer = r.get('mer', 'N/A')
    status = 'WINNER' if prod in kraken_winners else 'LOSER'
    if isinstance(tail, float):
        print(f'{prod:12s} {tail:>10.4f} {fg:>10.6f} {mer:>10.4f} {status:>10}')
    else:
        print(f'{prod:12s} {tail:>10} {fg:>10} {mer:>10} {status:>10}')

# Top 10 by tail score
print(f'\n\nTOP 10 BY TAIL SCORE:')
sorted_by_tail = sorted(rows, key=lambda r: r.get('tail_prob', 0), reverse=True)
for r in sorted_by_tail[:10]:
    print(f'{r["product_id"]:12s} tail={r.get("tail_prob", 0):.4f}  fg={r.get("fast_green_prob", 0):.6f}  mer={r.get("mer", 0):.4f}')
