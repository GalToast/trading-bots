#!/usr/bin/env python3
"""
Runner Monitor — Checks multi-coin momentum runner state every 30s.
Posts summary to stdout. Can be run as background watcher.
"""
import json
import time
import sys
from pathlib import Path

STATE_PATH = Path("reports/multi_coin_momentum_state.json")
EVENTS_PATH = Path("reports/multi_coin_momentum_events.jsonl")

last_event_count = 0
last_cycle = 0

def check():
    global last_event_count, last_cycle
    
    try:
        state = json.loads(STATE_PATH.read_text())
    except Exception as e:
        print(f"[MONITOR] State file error: {e}")
        return
    
    cycle = state.get('cycle', 0)
    equity = state.get('total_equity', 0)
    cash = state.get('cash', 0)
    pos_val = state.get('position_value', 0)
    
    # Check for new events
    try:
        with open(EVENTS_PATH) as f:
            event_count = sum(1 for _ in f)
    except:
        event_count = 0
    
    if cycle != last_cycle or event_count != last_event_count:
        print(f"[{time.strftime('%H:%M:%S')}] Cycle {cycle} | Equity ${equity:.2f} | Cash ${cash:.2f} | Pos ${pos_val:.2f} | Events {event_count}")
        last_cycle = cycle
        last_event_count = event_count
    
    # Show active positions
    for coin, info in state.get('coins', {}).items():
        if info.get('position') == 'active':
            entry = info.get('position_entry', '?')
            hold = info.get('position_hold', '?')
            print(f"  🟢 {coin}: ACTIVE, entry=${entry}, hold={hold} bars")
        elif info.get('closes', 0) > 0:
            wr = info.get('win_rate', 0)
            print(f"  {coin}: {info['closes']} closes, {info['wins']}W/{info['losses']}L, WR={wr:.0f}%")
    
    # Show new events
    try:
        with open(EVENTS_PATH) as f:
            events = [json.loads(line) for line in f if line.strip()]
        new_events = events[-5:] if len(events) > 5 else events
        for evt in new_events:
            if evt.get('action') == 'open':
                print(f"  📈 OPEN: {evt['coin']} @ ${evt.get('entry_price', '?')} TP=${evt.get('tp', '?')} SL=${evt.get('sl', '?')} deploy=${evt.get('deploy', '?')}")
            elif evt.get('action') == 'close':
                print(f"  📉 CLOSE: {evt['coin']} entry=${evt.get('entry_price', '?')} exit=${evt.get('exit_price', '?')} net=${evt.get('net', 0):.2f} reason={evt.get('reason', '?')}")
    except:
        pass

if __name__ == "__main__":
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    print(f"[MONITOR] Watching runner every {interval}s. Press Ctrl+C to stop.")
    try:
        while True:
            check()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[MONITOR] Stopped.")
