#!/usr/bin/env python3
"""
Live Trade Performance Tracker — Aggregates live trades and compares to backtest.

Reads the events log and state file to produce:
- Trade-by-trade breakdown
- Cumulative PnL curve
- Live vs backtest comparison
- Win rate convergence plot

Usage:
    python scripts/live_trade_tracker.py
    python scripts/live_trade_tracker.py --watch
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "live_proof_state.json"
EVENT_PATH = ROOT / "reports" / "live_proof_events.jsonl"
BACKTEST_PATH = ROOT / "reports/definitive_30d_validations.json"
OUTPUT_PATH = ROOT / "reports" / "live_trade_performance.json"


def utc_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def load_events():
    """Load all events from the event log."""
    if not EVENT_PATH.exists():
        return []
    with open(EVENT_PATH) as f:
        return [json.loads(line) for line in f if line.strip()]


def load_state():
    """Load current runner state."""
    if not STATE_PATH.exists():
        return None
    with open(STATE_PATH) as f:
        return json.load(f)


def load_backtest():
    """Load backtest predictions."""
    if not BACKTEST_PATH.exists():
        return {}
    with open(BACKTEST_PATH) as f:
        return json.load(f)


def analyze_trades(events):
    """Analyze trade history from events."""
    trades = []
    open_positions = {}
    
    for evt in events:
        action = evt.get('action')
        coin = evt.get('coin')
        
        if action == 'open':
            open_positions[coin] = {
                'entry_price': evt.get('entry_price'),
                'tp': evt.get('tp'),
                'sl': evt.get('sl'),
                'deploy': evt.get('deploy'),
                'strategy': evt.get('strategy'),
                'open_ts': evt.get('ts_utc'),
            }
        elif action == 'close' and coin in open_positions:
            opened = open_positions.pop(coin)
            trade = {
                'coin': coin,
                'strategy': opened.get('strategy'),
                'entry_price': opened.get('entry_price'),
                'exit_price': evt.get('exit_price'),
                'tp': opened.get('tp'),
                'sl': opened.get('sl'),
                'deploy': opened.get('deploy'),
                'net': evt.get('net', 0),
                'fees': evt.get('fees', 0),
                'hold_bars': evt.get('hold_bars'),
                'reason': evt.get('reason'),
                'open_ts': opened.get('open_ts'),
                'close_ts': evt.get('ts_utc'),
                'result': 'win' if evt.get('net', 0) > 0 else 'loss',
            }
            trades.append(trade)
    
    return trades, open_positions


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    
    while True:
        events = load_events()
        state = load_state()
        backtest = load_backtest()
        
        trades, open_positions = analyze_trades(events)
        
        # Calculate stats
        total_trades = len(trades)
        wins = sum(1 for t in trades if t['result'] == 'win')
        losses = total_trades - wins
        win_rate = wins / max(total_trades, 1) * 100
        total_pnl = sum(t['net'] for t in trades)
        total_fees = sum(t['fees'] for t in trades)
        
        # Current equity
        current_equity = state.get('total_equity', 48.0) if state else 48.0
        total_return = (current_equity - 48.0) / 48.0 * 100
        
        print(f"\n{'='*70}")
        print(f"  LIVE TRADE PERFORMANCE — {utc_now()}")
        print(f"{'='*70}")
        print(f"  Current Equity: ${current_equity:.2f} ({total_return:+.1f}%)")
        print(f"  Closed Trades: {total_trades} ({wins}W / {losses}L, WR={win_rate:.1f}%)")
        print(f"  Total PnL: ${total_pnl:+.2f} (fees: ${total_fees:.2f})")
        print(f"  Active Positions: {len(open_positions)}")
        
        if trades:
            print(f"\n  Trade History:")
            print(f"  {'#':>3} {'Coin':<8} {'Strategy':<12} {'Entry':>8} {'Exit':>8} {'Net':>7} {'Hold':>5} {'Reason':<8}")
            print(f"  {'-'*3} {'-'*8} {'-'*12} {'-'*8} {'-'*8} {'-'*7} {'-'*5} {'-'*8}")
            for i, t in enumerate(trades, 1):
                status = "✅" if t['result'] == 'win' else "❌"
                print(f"  {i:>3} {t['coin']:<8} {t['strategy']:<12} ${t['entry_price']:>7.4f} ${t['exit_price']:>7.4f} ${t['net']:>+6.2f} {t['hold_bars']:>5}b {t['reason']:<8} {status}")
        
        if open_positions:
            print(f"\n  Active Positions:")
            for coin, pos in open_positions.items():
                print(f"    {coin} ({pos['strategy']}): entry=${pos['entry_price']:.4f}, TP=${pos['tp']:.4f}, SL=${pos['sl']:.4f}, deployed=${pos['deploy']:.2f}")
        
        # Save report
        report = {
            'timestamp': utc_now(),
            'equity': current_equity,
            'total_return_pct': round(total_return, 2),
            'closed_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'win_rate': round(win_rate, 1),
            'total_pnl': round(total_pnl, 2),
            'total_fees': round(total_fees, 2),
            'active_positions': len(open_positions),
            'trades': trades,
            'open_positions': open_positions,
        }
        
        OUTPUT_PATH.write_text(json.dumps(report, indent=2), encoding='utf-8')
        
        if not args.watch:
            break
        
        import time
        time.sleep(30)
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
