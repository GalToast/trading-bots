#!/usr/bin/env python
"""Triage running Python processes to identify zombies and cleanup candidates."""
import subprocess
import json
import time
from pathlib import Path

TRADING_BOTS = Path(__file__).parent.parent

def get_python_processes():
    """Get all Python processes with command lines."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Select-Object ProcessId, CommandLine | ConvertTo-Json"],
        capture_output=True, text=True
    )
    try:
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            return [data]
        return data
    except:
        return []

def classify_process(cmdline):
    """Classify a Python process by its role."""
    if not cmdline:
        return "unknown", ""
    
    cmd_lower = cmdline.lower()
    
    # Live lanes
    if "live_penetration_lattice" in cmd_lower:
        if "m5_warp" in cmd_lower:
            return "live_btc_m5_warp", "CRITICAL - do not kill"
        elif "m15_warp" in cmd_lower:
            return "live_btc_m15_warp", "CRITICAL - do not kill"
        elif "exc2_tight" in cmd_lower or "exc2" in cmd_lower:
            return "live_btc_exc2", "CRITICAL - do not kill"
        elif "momentum" in cmd_lower:
            return "live_fx_momentum", "CRITICAL - do not kill"
        elif "rearm" in cmd_lower:
            return "live_fx_rearm", "CRITICAL - do not kill"
        return "live_lane", "CRITICAL - do not kill"
    
    # Watchdog
    if "watch_penetration_lattice" in cmd_lower or "watchdog" in cmd_lower:
        return "watchdog", "SUPERVISION - check heartbeat before killing"
    
    # Shadow lanes
    if "shadow" in cmd_lower:
        if "btcusd_m15" in cmd_lower or "btcusd_m5" in cmd_lower:
            return "shadow_btc", "SHADOW - check state before killing"
        elif "ethusd" in cmd_lower:
            return "shadow_eth", "SHADOW - check state before killing"
        elif "solusd" in cmd_lower:
            return "shadow_sol", "SHADOW - check state before killing"
        elif "xrpusd" in cmd_lower:
            return "shadow_xrp", "SHADOW - check state before killing"
        return "shadow_lane", "SHADOW - check state before killing"
    
    # Shared builders / reports
    if "build_" in cmd_lower or "execution_monitor" in cmd_lower:
        return "report_builder", "SHARED - may be idle between runs"
    
    # Benchmark / analysis scripts
    if "benchmark" in cmd_lower or "analyze" in cmd_lower or "test_" in cmd_lower:
        return "analysis_script", "LIKELY IDLE if completed"
    
    # Cross-symbol tracker
    if "track_m5" in cmd_lower or "cross_symbol" in cmd_lower:
        return "tracker", "MONITORING - check if still producing output"
    
    # Comms server
    if "comms_server" in cmd_lower:
        return "comms_server", "INFRASTRUCTURE - do not kill"
    
    # Kelly shadow
    if "kelly" in cmd_lower:
        return "kelly_shadow", "MONITORING - check activity"
    
    return "unknown", "UNKNOWN - investigate before killing"

def main():
    processes = get_python_processes()
    if not processes:
        print("No Python processes found or failed to query.")
        return
    
    classifications = {}
    zombie_candidates = []
    critical = []
    
    print(f"=== Python Process Triage ({len(processes)} processes) ===\n")
    
    for proc in processes:
        pid = proc.get("ProcessId", "?")
        cmdline = proc.get("CommandLine", "") or ""
        role, advice = classify_process(cmdline)
        
        if role not in classifications:
            classifications[role] = []
        classifications[role].append(pid)
        
        # Truncate cmdline for display
        display_cmd = cmdline[:80] + "..." if len(cmdline) > 80 else cmdline
        print(f"  PID {pid:>6} [{role:20s}] {display_cmd}")
        
        if "CRITICAL" in advice:
            critical.append((pid, role))
        elif "IDLE" in advice or "investigate" in advice.lower():
            zombie_candidates.append((pid, role, cmdline[:100]))
    
    print(f"\n=== Summary by Role ===")
    for role, pids in sorted(classifications.items()):
        print(f"  {role:25s}: {len(pids)} processes (PIDs: {', '.join(str(p) for p in pids)})")
    
    print(f"\n=== Critical Processes (DO NOT KILL) ===")
    for pid, role in critical:
        print(f"  PID {pid} [{role}]")
    
    print(f"\n=== Cleanup Candidates ===")
    if zombie_candidates:
        for pid, role, cmd in zombie_candidates:
            print(f"  PID {pid} [{role}] - {cmd[:80]}")
    else:
        print("  None identified.")
    
    print(f"\n=== Recommendations ===")
    total = len(processes)
    crit_count = len(critical)
    zombie_count = len(zombie_candidates)
    print(f"  Total: {total} | Critical: {crit_count} | Cleanup candidates: {zombie_count}")
    print(f"  Potential savings: {zombie_count} processes could be reviewed for cleanup")
    if zombie_count > 10:
        print(f"  ⚠️  High process count suggests stale/zombie accumulation")
        print(f"  Consider: kill completed analysis scripts, restart dead watchdog loops")

if __name__ == "__main__":
    main()
