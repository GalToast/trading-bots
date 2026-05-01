import json
for sym in ['GBPUSD', 'EURUSD', 'NZDUSD']:
    fname = f'reports/penetration_lattice_shadow_{sym.lower()}_m15_fxmicro_state.json'
    d = json.load(open(fname))
    s = d['symbols'][sym]
    tickets = s.get('open_tickets', [])
    buys = [t for t in tickets if t.get('direction') == 'BUY']
    sells = [t for t in tickets if t.get('direction') == 'SELL']
    print(f'{sym}: {len(tickets)} open ({len(buys)}B/{len(sells)}S)')
    if tickets:
        fills_b = [t.get('fill_price', 0) for t in buys]
        fills_s = [t.get('fill_price', 0) for t in sells]
        if fills_b:
            print(f'  BUY fills: {min(fills_b):.5f} - {max(fills_b):.5f}')
        if fills_s:
            print(f'  SELL fills: {min(fills_s):.5f} - {max(fills_s):.5f}')
    print()
