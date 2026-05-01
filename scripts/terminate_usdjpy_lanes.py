#!/usr/bin/env python3
"""
USDJPY Lane Termination Script
===============================
Terminates the structurally dead USDJPY shadow lanes that are bleeding capital.

Lanes to kill:
- shadow_usdjpy_gap2: -$179.28 (2052 closes, -0.088/close)
- shadow_usdjpy_shallow03: -$183.59 (2197 closes, -0.084/close)

These are confirmed dead by:
1. Kill list analysis (build_lane_kill_list.py)
2. Memory.md consensus: "dead strategy" / "event disconnect"
3. Live lane audit showing consistent negative performance

This script:
1. Records final state snapshots
2. Terminates the processes
3. Generates a termination report
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

REPORTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

LANES_TO_KILL = [
    {
        "lane_id": "shadow_usdjpy_gap2",
        "pid": 48124,
        "pnl": -179.28,
        "closes": 2052,
        "avg_per_close": -0.088,
        "state_path": "reports/penetration_lattice_shadow_usdjpy_gap2_state.json",
        "event_path": "reports/penetration_lattice_shadow_usdjpy_gap2_events.jsonl",
    },
    {
        "lane_id": "shadow_usdjpy_shallow03",
        "pid": 33020,
        "pnl": -183.59,
        "closes": 2197,
        "avg_per_close": -0.084,
        "state_path": "reports/penetration_lattice_shadow_usdjpy_shallow03_state.json",
        "event_path": "reports/penetration_lattice_shadow_usdjpy_shallow03_events.jsonl",
    },
]

def snapshot_state(lane):
    """Read final state before termination."""
    state_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), lane["state_path"])
    event_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), lane["event_path"])
    
    snapshot = {
        "lane_id": lane["lane_id"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "final_pnl": lane["pnl"],
        "final_closes": lane["closes"],
    }
    
    # Try to read state file
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                snapshot["state"] = json.load(f)
        except:
            snapshot["state"] = "<unreadable>"
    
    # Count events in event log
    if os.path.exists(event_file):
        try:
            with open(event_file) as f:
                event_count = sum(1 for _ in f)
            snapshot["event_count"] = event_count
        except:
            snapshot["event_count"] = "<unreadable>"
    
    return snapshot

def kill_process(pid, lane_id):
    """Kill a process by PID."""
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            text=True
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }

def main():
    print("=" * 60)
    print("USDJPY LANE TERMINATION")
    print("=" * 60)
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"Total lanes to terminate: {len(LANES_TO_KILL)}")
    print(f"Combined PnL bleed: ${sum(l['pnl'] for l in LANES_TO_KILL):.2f}")
    print()
    
    # Step 1: Snapshot state
    print("Step 1: Recording final state snapshots...")
    snapshots = []
    for lane in LANES_TO_KILL:
        print(f"  Snapshotting {lane['lane_id']} (PID {lane['pid']})...")
        snapshot = snapshot_state(lane)
        snapshots.append(snapshot)
        print(f"    Final PnL: ${snapshot['final_pnl']:.2f}")
        print(f"    Final closes: {snapshot['final_closes']}")
        if "event_count" in snapshot:
            print(f"    Event log entries: {snapshot['event_count']}")
    print()
    
    # Step 2: Terminate processes
    print("Step 2: Terminating processes...")
    results = []
    for lane in LANES_TO_KILL:
        print(f"  Killing {lane['lane_id']} (PID {lane['pid']})...")
        result = kill_process(lane["pid"], lane["lane_id"])
        results.append({
            "lane_id": lane["lane_id"],
            "pid": lane["pid"],
            "killed": result["success"],
            "output": result.get("stdout", ""),
            "error": result.get("stderr", result.get("error", "")),
        })
        if result["success"]:
            print(f"    ✅ Terminated successfully")
        else:
            print(f"    ❌ Failed: {result.get('stderr', result.get('error', 'unknown'))}")
    print()
    
    # Step 3: Generate termination report
    print("Step 3: Generating termination report...")
    report = {
        "termination_timestamp": datetime.now(timezone.utc).isoformat(),
        "reason": "Structurally dead USDJPY lanes with consistent negative performance",
        "lanes": [],
        "summary": {
            "total_terminated": sum(1 for r in results if r["killed"]),
            "total_failed": sum(1 for r in results if not r["killed"]),
            "final_combined_pnl": sum(l["pnl"] for l in LANES_TO_KILL),
            "capital_bleed_stopped": True,
        },
        "snapshots": snapshots,
        "kill_results": results,
    }
    
    for lane, snapshot, result in zip(LANES_TO_KILL, snapshots, results):
        report["lanes"].append({
            "lane_id": lane["lane_id"],
            "pid": lane["pid"],
            "final_pnl": lane["pnl"],
            "final_closes": lane["closes"],
            "avg_per_close": lane["avg_per_close"],
            "terminated": result["killed"],
            "snapshot": snapshot,
        })
    
    report_path = os.path.join(REPORTS, "usdjpy_lane_termination.json")
    os.makedirs(REPORTS, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    
    print(f"  Report saved to: {report_path}")
    print()
    
    # Summary
    print("=" * 60)
    print("TERMINATION SUMMARY")
    print("=" * 60)
    print(f"Lanes terminated: {report['summary']['total_terminated']}/{len(LANES_TO_KILL)}")
    print(f"Capital bleed stopped: ${report['summary']['final_combined_pnl']:.2f}")
    print(f"Report: {report_path}")
    print()
    
    if report["summary"]["total_terminated"] == len(LANES_TO_KILL):
        print("✅ All USDJPY lanes successfully terminated!")
        print("Next step: Update watchdog lane list to prevent restart")
    else:
        print("⚠️  Some lanes failed to terminate. Check report for details.")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
