#!/usr/bin/env python3
"""Analyze structure shapeshifter event log for burst patterns and escape timing."""
import json
from collections import Counter, defaultdict

def main():
    path = r"reports\penetration_lattice_shadow_ethusd_m5_structure_shapeshifter_events.jsonl"
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    print(f"Total events: {len(events)}")

    # Count by action type
    actions = Counter(e.get("action", "unknown") for e in events)
    print(f"\nAction distribution:")
    for k, v in actions.most_common():
        print(f"  {k}: {v}")

    # Analyze open bursts — group by timestamp
    opens = [e for e in events if e.get("action") == "open_ticket"]
    print(f"\nTotal opens: {len(opens)}")
    bursts = defaultdict(list)
    for o in opens:
        bursts[o["ts_utc"]].append(o)
    print(f"Distinct open timestamps (bursts): {len(bursts)}")
    for ts, group in sorted(bursts.items()):
        directions = Counter(g["direction"] for g in group)
        entries = [g["entry_price"] for g in group]
        fills = [g["fill_price"] for g in group]
        print(f"\n  Burst at {ts}: {len(group)} opens, directions={dict(directions)}")
        print(f"    Entry range: {min(entries):.2f} - {max(entries):.2f}")
        print(f"    All filled at: {fills[0]:.2f}")

    # Analyze escapes
    escapes = [e for e in events if "escape" in e.get("action", "")]
    print(f"\n\nTotal escapes: {len(escapes)}")
    if escapes:
        total_escape_pnl = sum(e.get("realized_pnl", 0) for e in escapes)
        print(f"Total escape PNL: {total_escape_pnl:.2f}")
        escape_bursts = defaultdict(list)
        for e in escapes:
            escape_bursts[e["ts_utc"]].append(e)
        print(f"Distinct escape timestamps: {len(escape_bursts)}")
        for ts, group in sorted(escape_bursts.items()):
            pnl = sum(g.get("realized_pnl", 0) for g in group)
            directions = Counter(g.get("direction", "?") for g in group)
            print(f"  {ts}: {len(group)} escapes, PNL={pnl:.2f}, dirs={dict(directions)}")

    # Analyze closes (natural, not escape)
    closes = [e for e in events if e.get("action") == "close_position"]
    print(f"\nNatural closes: {len(closes)}")
    for c in closes[:10]:
        print(f"  PNL={c.get('realized_pnl', 'N/A')}, dir={c.get('direction', '?')}")

    # Correlate bursts with escapes
    print(f"\n\n--- BURST → ESCAPE CORRELATION ---")
    for ts, group in sorted(bursts.items()):
        # Find escapes within 1 hour of this burst
        from datetime import datetime, timedelta
        burst_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        nearby_escapes = []
        for e in escapes:
            escape_time = datetime.fromisoformat(e["ts_utc"].replace("Z", "+00:00"))
            if timedelta(0) <= (escape_time - burst_time) <= timedelta(hours=1):
                nearby_escapes.append(e)
        if nearby_escapes:
            escape_pnl = sum(e.get("realized_pnl", 0) for e in nearby_escapes)
            print(f"  Burst at {ts} ({len(group)} opens) → {len(nearby_escapes)} escapes within 1h, PNL={escape_pnl:.2f}")
        else:
            print(f"  Burst at {ts} ({len(group)} opens) → NO escapes within 1h (oscillation worked?)")

    # Oscillation evidence: did price ever return to entry levels?
    print(f"\n\n--- PRICE TRAJECTORY (tick history fallback events) ---")
    ticks = [e for e in events if e.get("action") == "tick_history_fallback"]
    if ticks:
        for t in ticks[:10]:
            bid, ask = t.get("bid", 0), t.get("ask", 0)
            mid = (bid + ask) / 2 if bid and ask else 0
            print(f"  {t['ts_utc']}: bid={bid}, ask={ask}, mid={mid:.2f}")

if __name__ == "__main__":
    main()
