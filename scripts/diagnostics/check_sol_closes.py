"""Check SOL M5 close history — which are H1-era vs M5-era?"""
import json
from datetime import datetime, timezone

events = []
with open('reports/penetration_lattice_shadow_solusd_m5_warp_events.jsonl') as f:
    for line in f:
        e = json.loads(line)
        if 'close_ticket' in e.get('action', ''):
            events.append(e)

print(f"Total close events: {len(events)}")
print()

# The H1→M5 fix was applied at ~17:19 UTC
# Before that, the lane was running with default H1 timeframe
# After the fix, it was restarted with correct M5

# Group by time
for e in events[-15:]:
    pnl = e.get('realized_pnl', '?')
    ts = e.get('ts_utc', e.get('time', '?'))
    side = e.get('direction', '?')
    print(f"  {side}: pnl=${pnl}, time={ts}")

# Count by hour
from collections import Counter
hours = Counter()
for e in events:
    ts = e.get('ts_utc', '')
    if ts:
        try:
            dt = datetime.fromisoformat(ts)
            hours[dt.strftime('%H:00')] += 1
        except:
            pass

print(f"\nCloses by hour: {dict(sorted(hours.items()))}")
