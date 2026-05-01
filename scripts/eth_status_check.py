import json
p = 'reports/penetration_lattice_live_ethusd_m15_warp_state.json'
with open(p) as f:
    data = json.load(f)
s = data['symbols']['ETHUSD']
out = []
out.append(f'closes={s["realized_closes"]}, net=${s["realized_net_usd"]:.2f}')
out.append(f'opens={len(s["open_tickets"])}, resets={s["anchor_resets"]}')
out.append(f'anchor=${s["anchor"]:.2f}, next_buy=${s["next_buy_level"]:.2f}, next_sell=${s["next_sell_level"]:.2f}')
out.append(f'heartbeat={data["runner"]["heartbeat_at"]}')
out.append(f'consecutive_exceptions={data["runner"]["consecutive_exceptions"]}')

# Check events for fresh closes
import os
events_path = 'reports/penetration_lattice_live_ethusd_m15_warp_events.jsonl'
fresh = 0
if os.path.exists(events_path):
    with open(events_path) as ef:
        for line in ef:
            line = line.strip()
            if not line: continue
            try:
                evt = json.loads(line)
            except: continue
            if evt.get('action') == 'close_ticket' and evt.get('ts_utc', '') > '2026-04-14T20:29:00':
                pnl = evt.get('pnl', evt.get('profit', 0))
                out.append(f'Fresh close: {evt["ts_utc"]} {evt.get("direction","?")} PnL=${pnl:.2f}')
                fresh += 1
if fresh == 0:
    out.append('No fresh closes since relaunch')

result = '\n'.join(out)
with open('reports/eth_m15_status_check.txt', 'w') as f:
    f.write(result + '\n')
print(result)
