import json
symbols = ['btcusd','ethusd','xrpusd','solusd','dogeusd','adausd','dotusd','gbpusd','eurusd','nzdusd']
total = 0
for sym in symbols:
    with open(f'reports/unified_shadow_{sym}_state.json') as f:
        data = json.load(f)
    key = list(data['symbols'].keys())[0]
    s = data['symbols'][key]
    r = s['realized_net_usd']
    c = s['realized_closes']
    total += r
    print(f'{sym:>12}: realized=${r:>14,.2f}  closes={c:>6}')
print(f"{'TOTAL':>12}: realized=${total:>14,.2f}")
