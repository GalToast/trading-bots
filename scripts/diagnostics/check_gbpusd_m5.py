import json
lines = open('reports/penetration_lattice_shadow_gbpusd_m5_warp_events.jsonl').readlines()
closes = [json.loads(l) for l in lines if 'close_ticket' in l]
print(f'Closes: {len(closes)}')
total_pnl = sum(c.get('realized_pnl', 0) for c in closes)
print(f'Total PnL: ${total_pnl:.2f}')
if closes:
    print(f'Avg $/close: ${total_pnl/len(closes):.2f}')
    for c in closes:
        print(f"  {c.get('direction')}: pnl=${c.get('realized_pnl', 0):.2f}, level={c.get('level_idx')}, time={c.get('ts_utc', '?')[:19]}")

# Check state
s = json.load(open('reports/penetration_lattice_shadow_gbpusd_m5_warp_state.json'))
print(f'\nState:')
print(f'  anchor: {s.get("anchor_price", 0)}')
open_pos = s.get('open_positions', {})
total_open = sum(len(v) for v in open_pos.values())
print(f'  open positions: {total_open}')
for side, positions in open_pos.items():
    for p in positions[:3]:
        print(f'    {side}: entry={p.get("open_price", "?")}')
print(f'  closes (state): {s.get("close_count", 0)}')
