import json
from pathlib import Path

state_file = Path('reports/kraken_spot_maker_machinegun_shadow_state.json')
with open(state_file) as f:
    state = json.load(f)
    
state_data = state.get('state', {})
print(f'Updated: {state.get("updated_at", "?")}')
print(f'Cash: ${state_data.get("cash_usd", 0):.4f}')
print(f'Starting cash: ${state_data.get("starting_cash_usd", 100):.4f}')
print(f'Active positions: {len(state_data.get("active_positions", []))}')
print(f'Total closes: {state_data.get("total_closes", 0)}')
print(f'Realized PnL: ${state_data.get("realized_pnl_usd", 0):.4f}')

for pos in state_data.get('active_positions', []):
    print(f'  Active: {pos.get("product_id", "?")} at {pos.get("entry_price", 0):.4f}')

# Check review
review_file = Path('reports/kraken_spot_maker_machinegun_review.json')
if review_file.exists():
    with open(review_file) as f:
        review = json.load(f)
    print(f'\nReview stats:')
    print(f'  Closes: {review.get("total_closes", 0)}')
    print(f'  Realized PnL: {review.get("realized_pnl_usd", 0):.4f}')
    print(f'  Win rate: {review.get("win_rate", 0):.1%}')
