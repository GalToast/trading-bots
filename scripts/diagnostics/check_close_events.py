import json
from pathlib import Path

results = []

for name in ['shadow_btcusd_m15_step15', 'shadow_btcusd_m15_step20', 'shadow_gbpusd_m15_btc_tight15', 'shadow_eurusd_m15_btc_tight15', 'shadow_usdjpy_m15_btc_tight15', 'shadow_audusd_m15_btc_tight15', 'shadow_nzdusd_m15_btc_tight15', 'shadow_usdcad_m15_btc_tight15', 'live_btcusd_m15_warp']:
    ev_path = Path(f'reports/penetration_lattice_{name}_events.jsonl')
    if not ev_path.exists():
        results.append(f'{name}: no events file')
        continue
    lines = [l.strip() for l in ev_path.read_text().split('\n') if l.strip()]
    events = []
    for l in lines:
        try:
            events.append(json.loads(l))
        except:
            pass
    closes = [e for e in events if e.get('action') == 'close_ticket' or e.get('event') == 'close_ticket']
    opens = [e for e in events if e.get('action') == 'open_ticket' or e.get('event') == 'open_ticket']
    
    line = f'{name}: {len(opens)} opens, {len(closes)} closes'
    if closes:
        last = closes[-1]
        pnl = last.get('realized_pnl', '?')
        entry = last.get('entry_fill_price', '?')
        exit_px = last.get('exit_fill_price', '?')
        line += f' | Last close: pnl=${pnl}, entry={entry}, exit={exit_px}'
    if opens:
        last_open = opens[-1]
        line += f' | Last open: {last_open.get("direction","?")}@{last_open.get("trigger_level","?")}'
    results.append(line)

with open('reports/close_audit.txt', 'w') as f:
    f.write('\n'.join(results))
    f.write('\n')

for r in results:
    print(r)
