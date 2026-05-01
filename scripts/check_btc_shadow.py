import json, datetime
s = json.load(open('reports/penetration_lattice_shadow_btcusd_h1_state.json'))
sym = s['symbols'].get('BTCUSD', {})
with open('reports/btc_shadow_status.txt', 'w') as f:
    last_bar = sym.get('last_bar_time', 0)
    if last_bar:
        dt = datetime.datetime.fromtimestamp(last_bar, tz=datetime.timezone.utc)
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        f.write(f'Last bar time: {dt}\n')
        f.write(f'Current time: {now}\n')
        f.write(f'Time since last bar: {now - dt}\n')
        f.write(f'Anchor: {sym.get("anchor", 0)}\n')
        f.write(f'Open tickets: {len(sym.get("open_tickets", []))}\n')
        f.write(f'Realized: {sym.get("realized_net_usd", 0)}\n')
        f.write(f'Closes: {sym.get("realized_closes", 0)}\n')
    else:
        f.write('No last bar time\n')

with open('reports/btc_shadow_status.txt') as f:
    print(f.read())
