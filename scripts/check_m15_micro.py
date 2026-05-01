import json
for sym in ['gbpusd', 'eurusd', 'nzdusd']:
    path = 'reports/shadow_fx_m15_micro_%s_bar_state.json' % sym
    d = json.load(open(path))
    closes = d.get('realized_closes', '?')
    net = d.get('realized_net_usd', '?')
    opens = len(d.get('open_tickets', []))
    updated = d.get('updated_at', '?')
    bars = d.get('bars_processed', '?')
    print('%s: closes=%s, net=$%s, open=%d, updated=%s, bars=%s' % (sym.upper(), closes, net, opens, updated, bars))
