#!/usr/bin/env python3
"""
Find the top-performing symbol|signal|mode combos from trade history.
Generates a SYMBOL_SIGNAL_WHITELIST that can be added to mt5_bot_v10.py.

Instead of just banning losers, we actively PRIORITIZE winners.
This directly improves payoff ratio by increasing the proportion of
high-conviction entries.

Usage:
    python scripts/find_top_combos.py [--min-trades 3] [--min-win-rate 0.4]
"""

import json
import os
import argparse
from collections import defaultdict

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--min-trades', type=int, default=3, help='Minimum trades to consider a combo')
    parser.add_argument('--min-win-rate', type=float, default=0.40, help='Minimum win rate to whitelist')
    parser.add_argument('--min-net-pnl', type=float, default=0.0, help='Minimum net P/L to whitelist')
    parser.add_argument('--log-path', type=str, default=None)
    args = parser.parse_args()

    # Find log
    log_path = args.log_path
    if not log_path:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(script_dir)
        for candidate in ['trade_behavior_log.jsonl', os.path.join(parent_dir, 'trade_behavior_log.jsonl')]:
            if os.path.exists(candidate):
                log_path = candidate
                break

    if not log_path or not os.path.exists(log_path):
        print(f"ERROR: Cannot find trade_behavior_log.jsonl")
        return

    # Load trades
    trades = []
    with open(log_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trade = json.loads(line)
                pnl = float(trade.get('realized_pnl', 0.0) or 0.0)
                symbol = str(trade.get('symbol', 'UNKNOWN') or 'UNKNOWN').upper()
                mode = str(trade.get('entry_mode', 'UNKNOWN') or 'UNKNOWN').upper()
                signal = str(trade.get('entry_signal_type', 'unknown') or 'unknown').lower()

                # Skip adopted/reloaded — focus on FRESH entries only
                if trade.get('adopted', False):
                    continue
                # Skip unlabeled — can't build a signal whitelist without knowing the signal
                if signal in ('unlabeled', 'unknown', ''):
                    continue
                # Skip exotic/legacy signals that aren't in current rotation
                if signal in ('ride_momentum', 'velocity_acceleration', 'indicator_stack',
                              'mean_reversion', 'breakout_reject', 'trend_ride'):
                    continue

                trades.append({
                    'symbol': symbol,
                    'mode': mode,
                    'signal': signal,
                    'pnl': pnl,
                    'first_green': bool(trade.get('first_green_before_fail', False)),
                    'hold_sec': float(trade.get('hold_seconds', 0) or 0),
                    'exit_reason': str(trade.get('exit_reason', '') or ''),
                })
            except (json.JSONDecodeError, ValueError, TypeError):
                continue

    # Aggregate by symbol|signal
    combo_stats = defaultdict(lambda: {
        'count': 0, 'wins': 0, 'net_pnl': 0.0, 'first_green': 0,
        'avg_win': 0.0, 'avg_loss': 0.0, 'avg_hold': 0.0,
        'sniper': {'count': 0, 'wins': 0, 'net_pnl': 0.0},
        'shotgun': {'count': 0, 'wins': 0, 'net_pnl': 0.0},
    })

    for t in trades:
        key = (t['symbol'], t['signal'])
        s = combo_stats[key]
        s['count'] += 1
        if t['pnl'] > 0:
            s['wins'] += 1
            s['avg_win'] += t['pnl']
        else:
            s['avg_loss'] += t['pnl']
        s['net_pnl'] += t['pnl']
        if t['first_green']:
            s['first_green'] += 1
        s['avg_hold'] += t['hold_sec']

        if t['mode'] == 'SNIPER':
            s['sniper']['count'] += 1
            if t['pnl'] > 0: s['sniper']['wins'] += 1
            s['sniper']['net_pnl'] += t['pnl']
        elif t['mode'] == 'SHOTGUN':
            s['shotgun']['count'] += 1
            if t['pnl'] > 0: s['shotgun']['wins'] += 1
            s['shotgun']['net_pnl'] += t['pnl']

    # Compute derived metrics
    results = []
    for (symbol, signal), s in combo_stats.items():
        win_rate = s['wins'] / s['count'] if s['count'] > 0 else 0
        avg_win = s['avg_win'] / s['wins'] if s['wins'] > 0 else 0
        avg_loss = s['avg_loss'] / (s['count'] - s['wins']) if (s['count'] - s['wins']) > 0 else 0
        payoff = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        first_green_rate = s['first_green'] / s['count'] if s['count'] > 0 else 0
        avg_hold = s['avg_hold'] / s['count'] if s['count'] > 0 else 0

        results.append({
            'symbol': symbol,
            'signal': signal,
            'count': s['count'],
            'wins': s['wins'],
            'losses': s['count'] - s['wins'],
            'win_rate': round(win_rate * 100, 1),
            'net_pnl': round(s['net_pnl'], 2),
            'avg_pnl': round(s['net_pnl'] / s['count'], 2) if s['count'] > 0 else 0,
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'payoff_ratio': round(payoff, 2),
            'first_green_rate': round(first_green_rate * 100, 1),
            'avg_hold_sec': round(avg_hold, 0),
            'sniper_count': s['sniper']['count'],
            'sniper_wins': s['sniper']['wins'],
            'sniper_net': round(s['sniper']['net_pnl'], 2),
            'shotgun_count': s['shotgun']['count'],
            'shotgun_wins': s['shotgun']['wins'],
            'shotgun_net': round(s['shotgun']['net_pnl'], 2),
        })

    # Sort by net P/L descending
    results.sort(key=lambda r: r['net_pnl'], reverse=True)

    # Filter to whitelist candidates
    whitelist = [r for r in results if r['count'] >= args.min_trades and r['win_rate'] >= args.min_win_rate * 100 and r['net_pnl'] >= args.min_net_pnl]
    blacklist_candidates = [r for r in results if r['count'] >= args.min_trades and r['net_pnl'] < 0 and r['win_rate'] < 50]

    # Print
    print(f"\n{'='*80}")
    print(f"TOP COMBOS ANALYSIS ({len(trades)} fresh-entry trades, {len(combo_stats)} unique combos)")
    print(f"{'='*80}")
    print(f"Filters: min_trades={args.min_trades}, min_win_rate={args.min_win_rate*100:.0f}%, min_net_pnl=${args.min_net_pnl:.0f}")
    print(f"\n{'WHITELIST CANDIDATES' :^80}")
    print(f"{'='*80}")

    if whitelist:
        print(f"\n{'Symbol':<10} {'Signal':<35} {'N':>4} {'WR':>6} {'Net P/L':>10} {'Avg P/L':>9} {'Payoff':>7} {'FG%':>5}")
        print(f"{'-'*10} {'-'*35} {'-'*4} {'-'*6} {'-'*10} {'-'*9} {'-'*7} {'-'*5}")
        for r in whitelist:
            print(f"{r['symbol']:<10} {r['signal']:<35} {r['count']:>4} {r['win_rate']:>5.1f}% ${r['net_pnl']:>8.2f} ${r['avg_pnl']:>7.2f} {r['payoff_ratio']:>6.2f} {r['first_green_rate']:>4.0f}%")

        print(f"\n\n{'BLACKLIST CANDIDATES (active modes only)' :^80}")
        print(f"{'='*80}")
        print(f"\n{'Symbol':<10} {'Signal':<35} {'N':>4} {'WR':>6} {'Net P/L':>10} {'Avg P/L':>9} {'Payoff':>7}")
        print(f"{'-'*10} {'-'*35} {'-'*4} {'-'*6} {'-'*10} {'-'*9} {'-'*7}")
        for r in sorted(blacklist_candidates, key=lambda x: x['net_pnl'])[:20]:
            print(f"{r['symbol']:<10} {r['signal']:<35} {r['count']:>4} {r['win_rate']:>5.1f}% ${r['net_pnl']:>8.2f} ${r['avg_pnl']:>7.2f} {r['payoff_ratio']:>6.2f}")
    else:
        print("\nNo whitelist candidates found with these filters. Relaxing...")
        # Show top combos regardless of filters
        print(f"\n{'Symbol':<10} {'Signal':<35} {'N':>4} {'WR':>6} {'Net P/L':>10} {'Avg P/L':>9} {'Payoff':>7}")
        print(f"{'-'*10} {'-'*35} {'-'*4} {'-'*6} {'-'*10} {'-'*9} {'-'*7}")
        for r in results[:20]:
            print(f"{r['symbol']:<10} {r['signal']:<35} {r['count']:>4} {r['win_rate']:>5.1f}% ${r['net_pnl']:>8.2f} ${r['avg_pnl']:>7.2f} {r['payoff_ratio']:>6.2f}")

    # Generate Python code for the whitelist
    print(f"\n\n{'='*80}")
    print(f"GENERATED SYMBOL_SIGNAL_WHITELIST (copy-paste into mt5_bot_v10.py)")
    print(f"{'='*80}\n")

    if whitelist:
        print("SYMBOL_SIGNAL_WHITELIST = {")
        for r in whitelist:
            print(f"    ('{r['symbol']}', '{r['signal']}'),  # {r['count']} trades, {r['win_rate']}% WR, ${r['net_pnl']:+.2f}")
        print("}")
    else:
        print("# No clean whitelist found. Consider relaxing filters or gathering more data.")

    # Summary stats
    total_pnl = sum(r['net_pnl'] for r in whitelist) if whitelist else 0
    total_trades = sum(r['count'] for r in whitelist) if whitelist else 0
    total_wins = sum(r['wins'] for r in whitelist) if whitelist else 0
    overall_wr = total_wins / total_trades * 100 if total_trades > 0 else 0

    print(f"\n\n{'='*80}")
    print(f"WHITELIST SUMMARY: {len(whitelist)} combos, {total_trades} trades, {overall_wr:.1f}% WR, ${total_pnl:+.2f}")
    print(f"{'='*80}")

if __name__ == '__main__':
    main()
