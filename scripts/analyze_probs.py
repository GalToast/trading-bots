import json
import os

try:
    with open('reports/kraken_spot_frontier_strategy_board.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    rows = data.get('rows', [])
    
    # Top Tail Probs
    tail_sorted = sorted(rows, key=lambda x: float(x.get('tail_prob') or 0.0), reverse=True)
    print("--- Top 10 Tail Probs ---")
    for r in tail_sorted[:10]:
        print(f"{r['product_id']}: {r.get('tail_prob')} (FG: {r.get('fast_green_prob')})")
        
    # Top Fast Green Probs
    fg_sorted = sorted(rows, key=lambda x: float(x.get('fast_green_prob') or 0.0), reverse=True)
    print("\n--- Top 10 Fast Green Probs ---")
    for r in fg_sorted[:10]:
        print(f"{r['product_id']}: {r.get('fast_green_prob')} (Tail: {r.get('tail_prob')})")
        
except Exception as e:
    print(f"Error: {e}")
