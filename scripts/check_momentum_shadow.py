import json
s = json.load(open('reports/penetration_lattice_shadow_momentum_alpha50_state.json'))
symbols = s.get('symbols', {})
total_r = 0
total_c = 0
for sym in ['GBPUSD', 'EURUSD', 'NZDUSD']:
    if sym not in symbols:
        print(f'{sym}: NOT FOUND')
        continue
    sym_data = symbols[sym]
    pnl = sym_data.get('realized_net_usd', 0)
    closes = sym_data.get('realized_closes', 0)
    floating = sym_data.get('floating_net_usd', 0)
    tokens = len(sym_data.get('rearm_tokens', []))
    rearm_opens = sym_data.get('rearm_opens', 0)
    total_r += pnl
    total_c += closes
    print(f'{sym}: realized=${pnl:.2f}, closes={closes}, floating=${floating:.2f}, tokens={tokens}, rearm_opens={rearm_opens}')
print(f'TOTAL: realized=${total_r:.2f}, closes={total_c}')
