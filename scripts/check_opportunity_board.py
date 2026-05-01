import json

with open('reports/kraken_maker_opportunity_board.json') as f:
    board = json.load(f)

print(f'Updated: {board.get("updated_at", "?")}')
print(f'Products on board: {len(board.get("rows", []))}')
print()
for r in board.get('rows', [])[:20]:
    print(f'{r["product_id"]:12s} combined={r.get("combined_score", "?"):>8}  tail={r.get("tail_prob", "?"):>8}  fg={r.get("fast_green_prob", "?"):>8}  mer={r.get("mer", "?"):>6}')
