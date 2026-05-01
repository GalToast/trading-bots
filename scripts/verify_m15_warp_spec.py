import json
d = json.load(open('reports/penetration_lattice_shadow_btcusd_m15_warp_state.json'))
btc = d.get('symbols', {}).get('BTCUSD', {})
runner = d.get('runner', {})
closes = btc.get('realized_closes', '?')
net = btc.get('realized_net_usd', '?')
opens = len(btc.get('open_tickets', []))
rearm = btc.get('rearm_opens', '?')
resets = btc.get('anchor_resets', '?')
started = runner.get('started_at', '?')
heartbeat = runner.get('heartbeat_at', '?')
max_open = btc.get('max_open_total', '?')
print('BTC M15 Warp Shadow - Current Verification:')
print('  closes:', closes)
print('  realized_net_usd: $%s' % net)
print('  open_tickets: %d' % opens)
print('  rearm_opens:', rearm)
print('  anchor_resets:', resets)
print('  max_open_total:', max_open)
print('  started_at:', started)
print('  heartbeat:', heartbeat)
if isinstance(net, (int, float)) and isinstance(closes, (int, float)) and closes > 0:
    print('  $/close: $%.2f' % (net / closes))
# Compute floating risk
total_float = 0.0
for t in btc.get('open_tickets', []):
    entry = t.get('entry_price', 0) or 0
    direction = t.get('direction', '')
    # Approximate: current price is near anchor
    anchor = btc.get('anchor', entry)
    if direction == 'BUY':
        diff = anchor - entry
    else:
        diff = entry - anchor
    # Each position is 0.01 lots, ~$1 per $1 move
    total_float += diff * 0.01
print('  approx_total_float: $%.2f' % total_float)
# Floating ratio
if isinstance(net, (int, float)) and isinstance(total_float, (int, float)) and net > 0:
    print('  floating/realized ratio: %.2f' % (abs(total_float) / net * 100))
