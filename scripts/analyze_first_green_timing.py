#!/usr/bin/env python3
"""
Analyze first-green timing to optimize trailing stop behavior.

Answers: How long does it take winning trades to go green?
This tells us whether our early trail tiers are too aggressive or too passive.

Usage:
    python scripts/analyze_first_green_timing.py
"""

import json
import os
from collections import defaultdict

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(script_dir)
    
    log_path = None
    for candidate in ['trade_behavior_log.jsonl', os.path.join(parent_dir, 'trade_behavior_log.jsonl')]:
        if os.path.exists(candidate):
            log_path = candidate
            break
    
    if not log_path:
        print("ERROR: Cannot find trade_behavior_log.jsonl")
        return
    
    # Load trades
    winners = []
    losers = []
    
    with open(log_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trade = json.loads(line)
                if trade.get('adopted', False):
                    continue
                pnl = float(trade.get('realized_pnl', 0.0) or 0.0)
                first_green_sec = trade.get('time_to_first_green_seconds')
                hold_sec = float(trade.get('hold_seconds', 0) or 0)
                
                record = {
                    'symbol': str(trade.get('symbol', '?')),
                    'mode': str(trade.get('entry_mode', '?')),
                    'signal': str(trade.get('entry_signal_type', '?')),
                    'pnl': pnl,
                    'hold_sec': hold_sec,
                    'first_green_sec': first_green_sec if first_green_sec is not None else None,
                    'peak_pnl': float(trade.get('peak_pnl_before_exit', 0) or 0),
                    'exit_reason': str(trade.get('exit_reason', '') or ''),
                }
                
                if pnl > 0:
                    winners.append(record)
                else:
                    losers.append(record)
            except (json.JSONDecodeError, ValueError):
                continue
    
    print(f"\n{'='*70}")
    print(f"FIRST GREEN TIMING ANALYSIS")
    print(f"{'='*70}")
    print(f"Winners: {len(winners)}, Losers: {len(losers)}")
    
    # Winners: how fast did they go green?
    winners_with_fg = [w for w in winners if w['first_green_sec'] is not None]
    winners_no_fg = [w for w in winners if w['first_green_sec'] is None]
    
    print(f"\n--- WINNERS: {len(winners_with_fg)} went green, {len(winners_no_fg)} never did ---")
    
    if winners_with_fg:
        fg_times = [w['first_green_sec'] for w in winners_with_fg]
        print(f"\n  First green timing distribution:")
        print(f"    Min: {min(fg_times):.0f}s | Max: {max(fg_times):.0f}s | Avg: {sum(fg_times)/len(fg_times):.0f}s | Median: {sorted(fg_times)[len(fg_times)//2]:.0f}s")
        
        # Buckets
        buckets = {'<10s': 0, '10-30s': 0, '30-60s': 0, '60-120s': 0, '120-300s': 0, '300s+': 0}
        for t in fg_times:
            if t < 10: buckets['<10s'] += 1
            elif t < 30: buckets['10-30s'] += 1
            elif t < 60: buckets['30-60s'] += 1
            elif t < 120: buckets['60-120s'] += 1
            elif t < 300: buckets['120-300s'] += 1
            else: buckets['300s+'] += 1
        
        print(f"  Buckets: {buckets}")
        
        # By mode
        by_mode = defaultdict(list)
        for w in winners_with_fg:
            by_mode[w['mode']].append(w['first_green_sec'])
        
        print(f"\n  By mode:")
        for mode, times in sorted(by_mode.items()):
            avg = sum(times) / len(times)
            print(f"    {mode}: {len(times)} winners, avg first green = {avg:.0f}s")
        
        # Losers: did they ever go green?
        losers_with_fg = [l for l in losers if l['first_green_sec'] is not None]
        losers_no_fg = [l for l in losers if l['first_green_sec'] is None]
        
        print(f"\n--- LOSERS: {len(losers_with_fg)} went green then lost, {len(losers_no_fg)} never green ---")
        
        if losers_with_fg:
            loser_fg_times = [l['first_green_sec'] for l in losers_with_fg]
            print(f"\n  Green-to-red timing:")
            print(f"    Min: {min(loser_fg_times):.0f}s | Max: {max(loser_fg_times):.0f}s | Avg: {sum(loser_fg_times)/len(loser_fg_times):.0f}s")
            
            # These are the trades the sub-ATR trails should catch
            caught_by_trail = [l for l in losers_with_fg if l['first_green_sec'] is not None and l['first_green_sec'] < 120]
            print(f"\n  Would be caught by sub-ATR trails (<120s first green): {len(caught_by_trail)}/{len(losers_with_fg)}")
            
            # Net loss from these trades
            caught_loss = sum(l['pnl'] for l in caught_by_trail)
            total_loser_loss = sum(l['pnl'] for l in losers_with_fg)
            print(f"  Net loss from green-to-red trades: ${total_loser_loss:+.2f}")
            print(f"  Portion catchable by sub-ATR trails: ${caught_loss:+.2f}")
    
    # Key insight: peak PNL vs realized PNL for winners
    print(f"\n--- PEAK vs REALIZED (winners) ---")
    if winners:
        peaks = [w['peak_pnl'] for w in winners]
        realized = [w['pnl'] for w in winners]
        giveback = [w['peak_pnl'] - w['pnl'] for w in winners]
        giveback_pct = [(w['peak_pnl'] - w['pnl']) / w['peak_pnl'] * 100 if w['peak_pnl'] > 0 else 0 for w in winners]
        
        print(f"  Avg peak: ${sum(peaks)/len(peaks):.2f}")
        print(f"  Avg realized: ${sum(realized)/len(realized):.2f}")
        print(f"  Avg giveback: ${sum(giveback)/len(giveback):.2f} ({sum(giveback_pct)/len(giveback_pct):.0f}%)")
        
        # By exit reason
        by_exit = defaultdict(list)
        for w in winners:
            reason = w['exit_reason'].split('(')[0].strip() if '(' in w['exit_reason'] else w['exit_reason']
            by_exit[reason].append(w)
        
        print(f"\n  By exit reason:")
        for reason, ws in sorted(by_exit.items(), key=lambda x: -sum(w['pnl'] for w in x[1])):
            avg_peak = sum(w['peak_pnl'] for w in ws) / len(ws)
            avg_realized = sum(w['pnl'] for w in ws) / len(ws)
            avg_gb = (avg_peak - avg_realized) / avg_peak * 100 if avg_peak > 0 else 0
            print(f"    {reason}: {len(ws)} trades, avg peak ${avg_peak:.2f} → realized ${avg_realized:.2f} (giveback {avg_gb:.0f}%)")

if __name__ == '__main__':
    main()
