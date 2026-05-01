import json
with open('reports/kraken_maker_opportunity_board.json') as f:
    board = json.load(f)

for prod in ['BANANAS31-USD', 'GRASS-USD', 'ENS-USD', 'BMB-USD', 'CHIP-USD', 'DAI-USD', 'GUN-USD', 'GWEI-USD']:
    r = next((row for row in board.get('rows', []) if row['product_id'] == prod), None)
    if r:
        print(f'{prod:16s} spread={r.get("spread_bps",0):>8.1f}  mer={r.get("mer",0):>6.2f}  atr={r.get("atr_12_bps",0):>8.1f}')
    else:
        print(f'{prod:16s} NOT ON BOARD')
