import json
from pathlib import Path

opp = Path('reports/kraken_maker_opportunity_board.json')
if opp.exists():
    import os
    mtime = opp.stat().st_mtime
    from datetime import datetime, timezone
    print(f'Opportunity board file modified: {datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()}')
    
    with open(opp) as f:
        board = json.load(f)
    
    print(f'Products: {len(board.get("rows", []))}')
    
    # Check if any of our historical winners are on the board
    kraken_winners = ['CHIP-USD', 'DAI-USD', 'GUN-USD', 'GWEI-USD']
    kraken_losers = ['H-USD', 'CRV-USD', 'BASED-USD', 'AERO-USD', 'DASH-USD']
    
    board_products = {r['product_id'] for r in board.get('rows', [])}
    
    print(f'\nKraken winners on board:')
    for prod in kraken_winners:
        status = '✅ ON BOARD' if prod in board_products else '❌ MISSING'
        print(f'  {prod}: {status}')
    
    print(f'\nKraken losers on board:')
    for prod in kraken_losers:
        status = '⚠️ STILL HERE' if prod in board_products else '✅ GONE'
        print(f'  {prod}: {status}')
    
    # Check tail score variance
    tails = [r.get('tail_prob', 0) for r in board.get('rows', [])]
    fgs = [r.get('fast_green_prob', 0) for r in board.get('rows', [])]
    print(f'\nTail score range: {min(tails):.6f} to {max(tails):.6f} (variance: {max(tails) - min(tails):.6f})')
    print(f'FG score range: {min(fgs):.6f} to {max(fgs):.6f} (variance: {max(fgs) - min(fgs):.6f})')
    print(f'\n⚠️  If variance is near-zero, the scores are STALE/DEFAULT values')
