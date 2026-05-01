#!/usr/bin/env python3
"""
Runner Heartbeat Watchdog — detects stale runner processes.

Reads the heartbeat file (touched every cycle by the runner) and alerts
if it's older than the stale threshold. Can be run as a cron job or
supervised probe.

Usage:
    python scripts/runner_heartbeat_watchdog.py --state-path reports/multi_coin_isolated_state.json --stale-minutes 10
    python scripts/runner_heartbeat_watchdog.py  # defaults
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = ROOT / "reports" / "multi_coin_isolated_state.json"
DEFAULT_STALE_MINUTES = 10


def check_runner_stale(state_path, stale_minutes):
    """Check if the runner state file is stale. Returns (is_stale, info_dict)."""
    state_path = Path(state_path)
    
    if not state_path.exists():
        return True, {
            "status": "NO_STATE_FILE",
            "message": f"State file not found: {state_path}",
            "severity": "critical",
        }
    
    try:
        mtime = state_path.stat().st_mtime
        age_seconds = time.time() - mtime
        age_minutes = age_seconds / 60
        
        with open(state_path) as f:
            state = json.load(f)
        
        cycle = state.get("cycle", "?")
        equity = state.get("total_equity", "?")
        updated_at = state.get("updated_at", "unknown")
        
        if age_minutes > stale_minutes:
            return True, {
                "status": "STALE",
                "cycle": cycle,
                "equity": equity,
                "updated_at": updated_at,
                "age_minutes": round(age_minutes, 1),
                "stale_threshold_minutes": stale_minutes,
                "message": f"Runner stale: cycle {cycle}, equity ${equity}, last update {age_minutes:.0f}m ago (threshold: {stale_minutes}m)",
                "severity": "critical",
                "action": "restart_runner",
            }
        else:
            return False, {
                "status": "ALIVE",
                "cycle": cycle,
                "equity": equity,
                "updated_at": updated_at,
                "age_minutes": round(age_minutes, 1),
                "message": f"Runner alive: cycle {cycle}, equity ${equity}, last update {age_minutes:.0f}m ago",
                "severity": "ok",
            }
    except json.JSONDecodeError as e:
        return True, {
            "status": "CORRUPT_STATE",
            "message": f"State file corrupt: {e}",
            "severity": "critical",
        }
    except Exception as e:
        return True, {
            "status": "CHECK_ERROR",
            "message": f"Check failed: {e}",
            "severity": "warning",
        }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Runner heartbeat watchdog")
    parser.add_argument("--state-path", type=str, default=None, help="State file path")
    parser.add_argument("--stale-minutes", type=int, default=DEFAULT_STALE_MINUTES, help="Stale threshold in minutes")
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    args = parser.parse_args()
    
    state_path = Path(args.state_path) if args.state_path else DEFAULT_STATE_PATH
    is_stale, info = check_runner_stale(state_path, args.stale_minutes)
    
    if args.json:
        print(json.dumps(info, indent=2, sort_keys=True))
    else:
        severity_icon = {"ok": "✅", "warning": "⚠️", "critical": "🚨"}.get(info.get("severity", "?"), "?")
        print(f"{severity_icon} {info['message']}")
    
    sys.exit(1 if is_stale else 0)


if __name__ == "__main__":
    main()
