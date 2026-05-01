#!/usr/bin/env python3
import json
d = json.load(open('reports/kelly_shadow_state.json'))
print(f'Cycle: {d["cycle"]}')
ledgers = d.get('ledgers', {})
total_equity = 0
total_closes = 0
for coin, ledger in sorted(ledgers.items()):
    eq = ledger.get('equity', 0)
    closes = ledger.get('closes', 0)
    pos = ledger.get('position', '?')
    strategy = ledger.get('strategy', '?')
    pnl = ledger.get('pnl', 0)
    hold = ledger.get('position_hold', '?')
    max_hold = ledger.get('position_max_hold', '?')
    total_equity += eq
    total_closes += closes
    pos_info = ''
    if pos == 'active':
        tp = ledger.get('position_tp', '?')
        pos_info = f' hold={hold}/{max_hold} entry={ledger.get("position_entry","?")} tp={tp}'
    print(f'  {coin}: equity=${eq:.2f} closes={closes} {pos} strategy={strategy} pnl=${pnl:.2f}{pos_info}')
print(f'TOTAL: equity=${total_equity:.2f} closes={total_closes}')
