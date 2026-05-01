#!/usr/bin/env python3
"""
USDJPY Event Log Diagnostic Analyzer
=====================================
Analyzes USDJPY shadow lane event logs to understand WHERE the disconnect happens.

Key questions:
1. BUY vs SELL profitability split (hypothesis: SELL wins, BUY loses)
2. PnL over time (when did regime shift?)
3. Close type breakdown (rearm closes vs timeout closes)
4. Gap geometry effectiveness (gap2 vs shallow03 comparison)

Usage:
  python scripts/usdjpy_diagnostic_analyzer.py
"""

import json
import os
from datetime import datetime, timezone
from collections import defaultdict

REPORTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

def load_events(filepath):
    """Load JSONL event file."""
    events = []
    if not os.path.exists(filepath):
        print(f"  File not found: {filepath}")
        return events
    
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except:
                    pass
    return events

def analyze_lane(events, lane_id):
    """Analyze a single lane's events."""
    print(f"\n{'='*60}")
    print(f"LANE: {lane_id}")
    print(f"{'='*60}")
    print(f"Total events: {len(events)}")
    
    # Filter to close tickets
    closes = [e for e in events if e.get("action") == "close_ticket"]
    opens = [e for e in events if e.get("action") == "open_ticket"]
    
    print(f"Open events: {len(opens)}")
    print(f"Close events: {len(closes)}")
    
    if not closes:
        print("No closes to analyze!")
        return {}
    
    # PnL by direction
    pnl_by_direction = defaultdict(list)
    pnl_by_hour = defaultdict(list)
    pnl_by_date = defaultdict(list)
    all_pnl = []
    
    for close in closes:
        pnl = close.get("realized_pnl", 0.0)
        direction = close.get("direction", "UNKNOWN")
        ts = close.get("ts_utc", "")
        
        pnl_by_direction[direction].append(pnl)
        all_pnl.append(pnl)
        
        # Parse timestamp for time-based analysis
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("+00:00", "+00:00"))
                hour_key = dt.strftime("%Y-%m-%d %H:00")
                date_key = dt.strftime("%Y-%m-%d")
                pnl_by_hour[hour_key].append(pnl)
                pnl_by_date[date_key].append(pnl)
            except:
                pass
    
    # Direction analysis
    print(f"\n--- PnL by Direction ---")
    for direction, pnls in sorted(pnl_by_direction.items()):
        total = sum(pnls)
        avg = total / len(pnls) if pnls else 0
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / len(pnls) * 100 if pnls else 0
        print(f"  {direction}: {len(pnls)} closes, total=${total:.2f}, avg=${avg:.3f}, WR={win_rate:.1f}%")
    
    # Overall stats
    total_pnl = sum(all_pnl)
    avg_pnl = total_pnl / len(all_pnl) if all_pnl else 0
    wins = sum(1 for p in all_pnl if p > 0)
    win_rate = wins / len(all_pnl) * 100 if all_pnl else 0
    
    print(f"\n--- Overall ---")
    print(f"  Total PnL: ${total_pnl:.2f}")
    print(f"  Avg per close: ${avg_pnl:.3f}")
    print(f"  Win rate: {win_rate:.1f}% ({wins}/{len(all_pnl)})")
    
    # Time-based analysis (hourly)
    print(f"\n--- PnL Over Time (Hourly) ---")
    sorted_hours = sorted(pnl_by_hour.keys())
    
    # Show first 5, last 5, and worst/best hours
    if sorted_hours:
        print(f"  First 5 hours:")
        for hour in sorted_hours[:5]:
            pnls = pnl_by_hour[hour]
            total = sum(pnls)
            print(f"    {hour}: ${total:.2f} ({len(pnls)} closes)")
        
        if len(sorted_hours) > 10:
            print(f"  ...")
            print(f"  Last 5 hours:")
            for hour in sorted_hours[-5:]:
                pnls = pnl_by_hour[hour]
                total = sum(pnls)
                print(f"    {hour}: ${total:.2f} ({len(pnls)} closes)")
    
    # Find regime shifts (rolling 20-close average)
    print(f"\n--- Regime Shift Detection (20-close rolling avg) ---")
    window = 20
    if len(all_pnl) >= window:
        rolling_avgs = []
        for i in range(len(all_pnl) - window + 1):
            window_pnl = all_pnl[i:i+window]
            rolling_avgs.append(sum(window_pnl) / len(window_pnl))
        
        # Find where rolling avg crosses zero
        regime_changes = []
        for i in range(1, len(rolling_avgs)):
            prev_positive = rolling_avgs[i-1] > 0
            curr_positive = rolling_avgs[i] > 0
            if prev_positive != curr_positive:
                regime_changes.append({
                    "close_index": i + window,
                    "rolling_avg": rolling_avgs[i],
                    "direction": "profitable→losing" if prev_positive else "losing→profitable"
                })
        
        if regime_changes:
            for change in regime_changes[:5]:  # Show first 5
                print(f"  Close #{change['close_index']}: {change['direction']} (rolling avg: ${change['rolling_avg']:.3f})")
        else:
            if rolling_avgs:
                overall = "profitable" if rolling_avgs[-1] > 0 else "losing"
                print(f"  No regime shifts detected - consistently {overall}")
                print(f"  Final rolling avg: ${rolling_avgs[-1]:.3f}")
    
    # Best/worst hours
    hour_totals = {hour: sum(pnls) for hour, pnls in pnl_by_hour.items()}
    if hour_totals:
        best_hour = max(hour_totals, key=hour_totals.get)
        worst_hour = min(hour_totals, key=hour_totals.get)
        print(f"\n--- Extreme Hours ---")
        print(f"  Best: {best_hour} (${hour_totals[best_hour]:.2f})")
        print(f"  Worst: {worst_hour} (${hour_totals[worst_hour]:.2f})")
    
    return {
        "lane_id": lane_id,
        "total_pnl": total_pnl,
        "avg_pnl": avg_pnl,
        "win_rate": win_rate,
        "closes": len(closes),
        "pnl_by_direction": {k: {"total": sum(v), "avg": sum(v)/len(v) if v else 0, "count": len(v)} for k, v in pnl_by_direction.items()},
    }

def main():
    print("=" * 60)
    print("USDJPY DIAGNOSTIC ANALYSIS")
    print("=" * 60)
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    
    lanes = [
        {
            "lane_id": "shadow_usdjpy_gap2",
            "event_path": "reports/penetration_lattice_shadow_usdjpy_gap2_events.jsonl",
        },
        {
            "lane_id": "shadow_usdjpy_shallow03",
            "event_path": "reports/penetration_lattice_shadow_usdjpy_shallow03_events.jsonl",
        },
    ]
    
    results = []
    for lane in lanes:
        filepath = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), lane["event_path"])
        print(f"\nLoading {lane['lane_id']} events...")
        events = load_events(filepath)
        
        if events:
            result = analyze_lane(events, lane["lane_id"])
            results.append(result)
    
    # Cross-lane comparison
    if len(results) == 2:
        print(f"\n{'='*60}")
        print(f"CROSS-LANE COMPARISON")
        print(f"{'='*60}")
        
        gap2 = results[0]
        shallow = results[1]
        
        print(f"\n{'Metric':<20} {'gap2':>15} {'shallow03':>15}")
        print(f"{'-'*50}")
        print(f"{'Total PnL':<20} ${gap2['total_pnl']:>13.2f} ${shallow['total_pnl']:>13.2f}")
        print(f"{'Avg per close':<20} ${gap2['avg_pnl']:>13.3f} ${shallow['avg_pnl']:>13.3f}")
        print(f"{'Win rate':<20} {gap2['win_rate']:>12.1f}% {shallow['win_rate']:>12.1f}%")
        print(f"{'Total closes':<20} {gap2['closes']:>14} {shallow['closes']:>14}")
        
        # Direction comparison
        print(f"\n--- Direction Breakdown ---")
        for direction in ["BUY", "SELL"]:
            gap2_dir = gap2["pnl_by_direction"].get(direction, {})
            shallow_dir = shallow["pnl_by_direction"].get(direction, {})
            print(f"\n  {direction}:")
            print(f"    gap2: total=${gap2_dir.get('total', 0):.2f}, avg=${gap2_dir.get('avg', 0):.3f}, count={gap2_dir.get('count', 0)}")
            print(f"    shallow03: total=${shallow_dir.get('total', 0):.2f}, avg=${shallow_dir.get('avg', 0):.3f}, count={shallow_dir.get('count', 0)}")
    
    # Save results
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lanes": results,
    }
    
    report_path = os.path.join(REPORTS, "usdjpy_diagnostic_analysis.json")
    os.makedirs(REPORTS, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    
    print(f"\n{'='*60}")
    print(f"REPORT SAVED: {report_path}")
    print(f"{'='*60}")
    
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
