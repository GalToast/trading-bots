import json

print("UNIFIED SHADOW RUNNER - ALL SYMBOLS:")
print("=" * 80)

total_realized = 0
total_closes = 0
total_rearm = 0

for sym in ['BTCUSD', 'ETHUSD', 'XRPUSD', 'SOLUSD', 'DOGEUSD', 'ADAUSD', 'DOTUSD', 'GBPUSD', 'EURUSD', 'NZDUSD']:
    path = f'reports/unified_shadow_{sym.lower()}_state.json'
    try:
        s = json.load(open(path))
        sym_data = s.get('symbols', {}).get(sym, {})
        realized = sym_data.get('realized_net_usd', 0)
        closes = sym_data.get('realized_closes', 0)
        rearm = sym_data.get('rearm_opens', 0)
        open_count = len(sym_data.get('open_tickets', []))
        anchor = sym_data.get('anchor', 0)
        next_sell = sym_data.get('next_sell_level', 0)
        next_buy = sym_data.get('next_buy_level', 0)
        total_realized += realized
        total_closes += closes
        total_rearm += rearm
        print(f'{sym:10s}: open={open_count:4d}, realized=${realized:>12,.2f}, closes={closes:>5d}, rearm={rearm:>5d}, anchor={anchor:.2f}')
    except Exception as e:
        print(f'{sym:10s}: ERROR - {e}')

print("=" * 80)
print(f'TOTAL     : open=N/A,   realized=${total_realized:>12,.2f}, closes={total_closes:>5d}, rearm={total_rearm:>5d}')
