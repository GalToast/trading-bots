#!/usr/bin/env python3
"""
First Trades Monitor — Watches for first signals and closes from the live runner.
Posts alerts when trades happen and builds a report after the first N closes.

Usage:
    python scripts/first_trades_monitor.py --max-closes 10
    python scripts/first_trades_monitor.py --watch
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "multi_coin_isolated_state.json"
EVENT_PATH = ROOT / "reports" / "multi_coin_isolated_events.jsonl"
REPORT_PATH = ROOT / "reports" / "first_trades_report.json"


def utc_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def check_state():
    """Check runner state file."""
    if not STATE_PATH.exists():
        return None
    try:
        return json.loads(STATE_PATH.read_text(encoding='utf-8'))
    except:
        return None


def check_events(last_count):
    """Check for new events."""
    if not EVENT_PATH.exists():
        return last_count, []
    
    with open(EVENT_PATH) as f:
        events = [json.loads(line) for line in f if line.strip()]
    
    new_events = events[last_count:]
    return len(events), new_events


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--max-closes', type=int, default=10)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=int, default=15)
    args = parser.parse_args()
    
    print(f"[{utc_now()}] First Trades Monitor started")
    print(f"  Watching: {EVENT_PATH}")
    print(f"  Target: {args.max_closes} closes")
    print()
    
    last_event_count = 0
    close_count = 0
    open_count = 0
    first_trades = []
    
    try:
        while True:
            state = check_state()
            event_count, new_events = check_events(last_event_count)
            
            for evt in new_events:
                action = evt.get('action', '?')
                coin = evt.get('coin', '?')
                ts = evt.get('ts_utc', '?')
                
                if action == 'open':
                    open_count += 1
                    print(f"  📈 [{ts[:19]}] OPEN: {coin} @ ${evt.get('entry_price', '?')} TP=${evt.get('tp', '?')} SL=${evt.get('sl', '?')}")
                    first_trades.append({'action': 'open', 'coin': coin, 'ts': ts, 'details': evt})
                elif action == 'close':
                    close_count += 1
                    net = evt.get('net', 0)
                    reason = evt.get('reason', '?')
                    status = "✅ WIN" if net > 0 else "❌ LOSS"
                    print(f"  📉 [{ts[:19]}] CLOSE: {coin} net=${net:+.2f} reason={reason} {status}")
                    first_trades.append({'action': 'close', 'coin': coin, 'ts': ts, 'net': net, 'reason': reason})
                    
                    if close_count >= args.max_closes:
                        print(f"\n  Target reached: {close_count} closes")
                        break
                elif action == 'runner_start_isolated':
                    coins = evt.get('coins', [])
                    print(f"  🔄 [{ts[:19]}] RUNNER START: {len(coins)} coins, ${evt.get('total_cash', 0):.2f}")
            
            last_event_count = event_count
            
            if state:
                cycle = state.get('cycle', 0)
                equity = state.get('total_equity', 0)
                pnl = state.get('total_pnl', 0)
                updated = state.get('updated_at', '?')
                print(f"  [Cycle {cycle}] Equity: ${equity:.2f} PnL: ${pnl:+.2f} Closes: {close_count}/{args.max_closes} (updated {updated[:19]})")
            
            if close_count >= args.max_closes:
                break
            
            if not args.watch:
                break
            
            time.sleep(args.interval)
    
    except KeyboardInterrupt:
        print(f"\n  Monitor stopped by user")
    
    # Save report
    wins = sum(1 for t in first_trades if t.get('net', 0) > 0)
    losses = sum(1 for t in first_trades if t.get('action') == 'close' and t.get('net', 0) <= 0)
    
    report = {
        'timestamp': utc_now(),
        'total_opens': open_count,
        'total_closes': close_count,
        'wins': wins,
        'losses': losses,
        'win_rate': round(wins / max(close_count, 1) * 100, 1),
        'first_trades': first_trades,
    }
    
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f"\n  Report saved: {REPORT_PATH}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
