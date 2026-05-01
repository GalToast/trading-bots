#!/usr/bin/env python3
"""
Post-reload performance comparison script.

Compares trade behavior before vs after each reload timestamp.
Outputs a JSON report with key metrics for validation.

Usage:
    python scripts/reload_comparison.py [--trades-before 50] [--trades-after 50]
"""

import json
import os
import argparse
from datetime import datetime, timezone
from collections import defaultdict

RELOAD_TIMESTAMPS = [
    # Format: (timestamp_utc, description)
    ("2026-04-09T14:44:18+00:00", "7-patch bundle: SHOTGUN cap, conf bump, sub-ATR trail, NAS100/GBPAUD blocklist"),
    # Add future reloads here as they happen
]

def load_trades(log_path):
    """Load trade behavior log entries."""
    trades = []
    if not os.path.exists(log_path):
        print(f"ERROR: {log_path} not found")
        return trades
    
    with open(log_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trade = json.loads(line)
                # Parse timestamps
                for ts_field in ['entry_time_utc', 'exit_time_utc', 'recorded_at_utc']:
                    if ts_field in trade and trade[ts_field]:
                        try:
                            trade[f'{ts_field}_dt'] = datetime.fromisoformat(
                                str(trade[ts_field]).replace('Z', '+00:00')
                            )
                        except Exception:
                            pass
                trades.append(trade)
            except json.JSONDecodeError:
                continue
    
    # Sort by recorded_at_utc descending (most recent first)
    trades.sort(key=lambda t: t.get('recorded_at_utc', ''), reverse=True)
    return trades

def compute_metrics(trades):
    """Compute performance metrics for a set of trades."""
    if not trades:
        return {
            'count': 0,
            'net_pnl': 0.0,
            'avg_pnl': 0.0,
            'win_rate': 0.0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'payoff_ratio': 0.0,
            'max_win': 0.0,
            'max_loss': 0.0,
            'avg_hold_sec': 0.0,
            'early_fail_count': 0,
            'first_green_count': 0,
            'by_mode': {},
            'by_symbol': {},
            'loss_distribution': {'<1': 0, '1-5': 0, '5-20': 0, '20-50': 0, '50+': 0},
            'win_distribution': {'<0.5': 0, '0.5-1': 0, '1-3': 0, '3-5': 0, '5+': 0},
        }
    
    realized_pnls = [float(t.get('realized_pnl', 0.0) or 0.0) for t in trades]
    wins = [p for p in realized_pnls if p > 0]
    losses = [p for p in realized_pnls if p <= 0]
    hold_secs = [float(t.get('hold_seconds', 0.0) or 0.0) for t in trades]
    
    # Loss distribution
    loss_dist = {'<1': 0, '1-5': 0, '5-20': 0, '20-50': 0, '50+': 0}
    for l in losses:
        abs_l = abs(l)
        if abs_l < 1: loss_dist['<1'] += 1
        elif abs_l < 5: loss_dist['1-5'] += 1
        elif abs_l < 20: loss_dist['5-20'] += 1
        elif abs_l < 50: loss_dist['20-50'] += 1
        else: loss_dist['50+'] += 1
    
    # Win distribution
    win_dist = {'<0.5': 0, '0.5-1': 0, '1-3': 0, '3-5': 0, '5+': 0}
    for w in wins:
        if w < 0.5: win_dist['<0.5'] += 1
        elif w < 1: win_dist['0.5-1'] += 1
        elif w < 3: win_dist['1-3'] += 1
        elif w < 5: win_dist['3-5'] += 1
        else: win_dist['5+'] += 1
    
    # By mode
    by_mode = defaultdict(lambda: {'count': 0, 'net_pnl': 0.0, 'wins': 0, 'losses': 0})
    for t in trades:
        mode = str(t.get('entry_mode', 'UNKNOWN')).upper()
        pnl = float(t.get('realized_pnl', 0.0) or 0.0)
        by_mode[mode]['count'] += 1
        by_mode[mode]['net_pnl'] += pnl
        if pnl > 0: by_mode[mode]['wins'] += 1
        else: by_mode[mode]['losses'] += 1
    
    # By symbol
    by_symbol = defaultdict(lambda: {'count': 0, 'net_pnl': 0.0, 'wins': 0})
    for t in trades:
        sym = str(t.get('symbol', 'UNKNOWN')).upper()
        pnl = float(t.get('realized_pnl', 0.0) or 0.0)
        by_symbol[sym]['count'] += 1
        by_symbol[sym]['net_pnl'] += pnl
        if pnl > 0: by_symbol[sym]['wins'] += 1
    
    return {
        'count': len(trades),
        'net_pnl': round(sum(realized_pnls), 2),
        'avg_pnl': round(sum(realized_pnls) / len(realized_pnls), 2) if realized_pnls else 0.0,
        'win_rate': round(len(wins) / len(realized_pnls) * 100, 1) if realized_pnls else 0.0,
        'avg_win': round(sum(wins) / len(wins), 2) if wins else 0.0,
        'avg_loss': round(sum(losses) / len(losses), 2) if losses else 0.0,
        'payoff_ratio': round((sum(wins) / len(wins)) / (abs(sum(losses) / len(losses))), 2) if wins and losses else 0.0,
        'max_win': round(max(wins), 2) if wins else 0.0,
        'max_loss': round(min(losses), 2) if losses else 0.0,
        'avg_hold_sec': round(sum(hold_secs) / len(hold_secs), 0) if hold_secs else 0.0,
        'early_fail_count': sum(1 for t in trades if str(t.get('exit_reason', '')).startswith('EARLY_FAIL')),
        'first_green_count': sum(1 for t in trades if t.get('first_green_before_fail')),
        'by_mode': {k: {'count': v['count'], 'net_pnl': round(v['net_pnl'], 2), 
                        'wins': v['wins'], 'losses': v['losses']} 
                    for k, v in by_mode.items()},
        'by_symbol': {k: {'count': v['count'], 'net_pnl': round(v['net_pnl'], 2), 
                          'wins': v['wins']} 
                      for k, v in sorted(by_symbol.items(), key=lambda x: x[1]['net_pnl'])},
        'loss_distribution': loss_dist,
        'win_distribution': win_dist,
    }

def main():
    parser = argparse.ArgumentParser(description='Compare trade performance before/after reloads')
    parser.add_argument('--trades-before', type=int, default=50, help='Number of trades before each reload to compare')
    parser.add_argument('--trades-after', type=int, default=50, help='Number of trades after each reload to compare')
    parser.add_argument('--log-path', type=str, default=None, help='Path to trade_behavior_log.jsonl')
    args = parser.parse_args()
    
    # Find log path
    log_path = args.log_path
    if not log_path:
        # Try common locations
        script_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(script_dir)
        for candidate in ['trade_behavior_log.jsonl', os.path.join(parent_dir, 'trade_behavior_log.jsonl')]:
            if os.path.exists(candidate):
                log_path = candidate
                break
    
    if not log_path or not os.path.exists(log_path):
        print(f"ERROR: Cannot find trade_behavior_log.jsonl")
        return
    
    trades = load_trades(log_path)
    if not trades:
        print("ERROR: No trades found in log")
        return
    
    # Sort chronologically for split analysis
    trades_chrono = sorted(trades, key=lambda t: t.get('recorded_at_utc', ''))
    
    report = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'total_trades_in_log': len(trades_chrono),
        'comparisons': [],
    }
    
    for reload_ts, description in RELOAD_TIMESTAMPS:
        reload_dt = datetime.fromisoformat(reload_ts.replace('Z', '+00:00'))
        
        # Split trades
        before_trades = [t for t in trades_chrono if t.get('recorded_at_utc_dt') and t['recorded_at_utc_dt'] < reload_dt]
        after_trades = [t for t in trades_chrono if t.get('recorded_at_utc_dt') and t['recorded_at_utc_dt'] >= reload_dt]
        
        # Take the most recent N before and after
        before_sample = before_trades[-args.trades_before:] if len(before_trades) > args.trades_before else before_trades
        after_sample = after_trades[:args.trades_after]
        
        before_metrics = compute_metrics(before_sample)
        after_metrics = compute_metrics(after_sample)
        
        comparison = {
            'reload_timestamp': reload_ts,
            'description': description,
            'before': {
                'trade_count': len(before_sample),
                'metrics': before_metrics,
            },
            'after': {
                'trade_count': len(after_sample),
                'metrics': after_metrics,
            },
            'delta': {
                'net_pnl': round(after_metrics['net_pnl'] - before_metrics['net_pnl'], 2),
                'avg_pnl': round(after_metrics['avg_pnl'] - before_metrics['avg_pnl'], 2),
                'win_rate': round(after_metrics['win_rate'] - before_metrics['win_rate'], 1),
                'avg_win': round(after_metrics['avg_win'] - before_metrics['avg_win'], 2),
                'avg_loss': round(after_metrics['avg_loss'] - before_metrics['avg_loss'], 2),
                'payoff_ratio': round(after_metrics['payoff_ratio'] - before_metrics['payoff_ratio'], 2),
                'max_loss': round(after_metrics['max_loss'] - before_metrics['max_loss'], 2),
                'loss_under_1_pct': round(
                    after_metrics['loss_distribution']['<1'] / max(1, after_metrics['count'] - len([p for p in [float(t.get('realized_pnl', 0.0) or 0.0) for t in after_sample] if p > 0])) * 100, 1
                ) if after_sample else 0.0,
            }
        }
        report['comparisons'].append(comparison)
    
    # Output
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'reports')
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    output_path = os.path.join(output_dir, f'reload-comparison-{ts}.json')
    
    with open(output_path, 'w') as f:
        json.dump(report, f, indent=2)
    
    # Print summary
    print(f"\n{'='*60}")
    print(f"RELOAD PERFORMANCE COMPARISON")
    print(f"{'='*60}")
    print(f"Total trades in log: {len(trades_chrono)}")
    print(f"Report saved to: {output_path}")
    
    for comp in report['comparisons']:
        print(f"\n--- {comp['description']} ---")
        print(f"Reload at: {comp['reload_timestamp']}")
        
        before = comp['before']
        after = comp['after']
        delta = comp['delta']
        
        print(f"\n  BEFORE ({before['trade_count']} trades):")
        print(f"    Net P/L: ${before['metrics']['net_pnl']:+.2f}")
        print(f"    Avg P/L: ${before['metrics']['avg_pnl']:+.2f}")
        print(f"    Win Rate: {before['metrics']['win_rate']:.1f}%")
        print(f"    Avg Win: ${before['metrics']['avg_win']:.2f} | Avg Loss: ${before['metrics']['avg_loss']:.2f}")
        print(f"    Payoff Ratio: {before['metrics']['payoff_ratio']:.2f}")
        print(f"    Max Loss: ${before['metrics']['max_loss']:.2f}")
        print(f"    Loss dist: {before['metrics']['loss_distribution']}")
        
        print(f"\n  AFTER ({after['trade_count']} trades):")
        print(f"    Net P/L: ${after['metrics']['net_pnl']:+.2f}")
        print(f"    Avg P/L: ${after['metrics']['avg_pnl']:+.2f}")
        print(f"    Win Rate: {after['metrics']['win_rate']:.1f}%")
        print(f"    Avg Win: ${after['metrics']['avg_win']:.2f} | Avg Loss: ${after['metrics']['avg_loss']:.2f}")
        print(f"    Payoff Ratio: {after['metrics']['payoff_ratio']:.2f}")
        print(f"    Max Loss: ${after['metrics']['max_loss']:.2f}")
        print(f"    Loss dist: {after['metrics']['loss_distribution']}")
        
        print(f"\n  DELTA (After - Before):")
        print(f"    Net P/L: ${delta['net_pnl']:+.2f}")
        print(f"    Avg P/L: ${delta['avg_pnl']:+.2f}")
        print(f"    Win Rate: {delta['win_rate']:+.1f}%")
        print(f"    Avg Win: ${delta['avg_win']:+.2f}")
        print(f"    Avg Loss: ${delta['avg_loss']:+.2f}")
        print(f"    Payoff Ratio: {delta['payoff_ratio']:+.2f}")
        print(f"    Max Loss: ${delta['max_loss']:+.2f}")

if __name__ == '__main__':
    main()
