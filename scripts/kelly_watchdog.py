#!/usr/bin/env python3
"""Kelly Shadow Watchdog — auto-restarts the runner if it dies.

Monitors the Kelly shadow state file for staleness. If no update in N seconds,
restarts the runner process. Posts switchboard alerts on restart.

Usage:
    python scripts/kelly_watchdog.py
    python scripts/kelly_watchdog.py --stale-seconds 120
    python scripts/kelly_watchdog.py --restart  # Actually restart (not just alert)
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "reports" / "kelly_shadow_state.json"
RUNNER_SCRIPT = ROOT / "scripts" / "multi_coin_isolated_runner.py"
KELLY_CONFIG = ROOT / "configs" / "kelly_optimal_runner_config.json"


def read_state():
    if not STATE_FILE.exists():
        return None
    with open(STATE_FILE) as f:
        return json.load(f)


def find_runner_pid():
    """Find PID of Kelly shadow runner process."""
    try:
        # Check common PIDs from previous launches
        for pid_file in [ROOT / "reports" / "kelly_runner.pid"]:
            if pid_file.exists():
                pid = int(pid_file.read_text().strip())
                try:
                    os.kill(pid, 0)  # Check if process exists
                    return pid
                except OSError:
                    pid_file.unlink()
    except Exception:
        pass
    return None


def save_runner_pid(pid):
    pid_file = ROOT / "reports" / "kelly_runner.pid"
    pid_file.write_text(str(pid))


def restart_runner():
    """Restart the Kelly shadow runner with --no-btc-regime-gate."""
    cmd = [
        sys.executable, str(RUNNER_SCRIPT),
        "--config-path", str(KELLY_CONFIG),
        "--total-cash", "48",
        "--state-path", str(STATE_FILE),
        "--event-path", str(ROOT / "reports" / "kelly_shadow_events.jsonl"),
        "--no-btc-regime-gate",
    ]
    print(f"[{datetime.now(timezone.utc).isoformat()}] RESTARTING Kelly runner: {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(cmd)
    save_runner_pid(proc.pid)
    print(f"  Started PID {proc.pid}", flush=True)
    return proc.pid


def monitor_loop(stale_seconds=120, auto_restart=True):
    last_cycle = 0
    last_update_time = 0

    print(f"Kelly Watchdog started at {datetime.now(timezone.utc).isoformat()}", flush=True)
    print(f"  State file: {STATE_FILE}", flush=True)
    print(f"  Stale threshold: {stale_seconds}s", flush=True)
    print(f"  Auto-restart: {'ON' if auto_restart else 'OFF'}", flush=True)

    while True:
        try:
            state = read_state()
            now = time.time()

            if state is None:
                if auto_restart:
                    print(f"[{datetime.now(timezone.utc).isoformat()}] State file missing — restarting runner", flush=True)
                    restart_runner()
                time.sleep(30)
                continue

            cycle = state.get("cycle", 0)
            equity = state.get("total_equity", 0)
            pnl = state.get("total_pnl", 0)

            # Check if state is stale
            updated_str = state.get("updated_at", "")
            if updated_str:
                try:
                    updated_dt = datetime.fromisoformat(updated_str)
                    updated_ts = updated_dt.timestamp()
                    age = now - updated_ts
                except Exception:
                    age = stale_seconds + 1
            else:
                age = stale_seconds + 1

            # New cycle detected
            if cycle > last_cycle:
                last_cycle = cycle
                last_update_time = now

                # Count active positions
                ledgers = state.get("ledgers", {})
                active = [k for k, v in ledgers.items() if v.get("position") == "active"]
                signals = sum(v.get("signals", 0) for v in ledgers.values())
                closes = sum(v.get("closes", 0) for v in ledgers.values())

                print(f"[{datetime.now(timezone.utc).isoformat()}] Cycle {cycle}: "
                      f"equity=${equity:.2f} pnl=${pnl:+.2f} "
                      f"positions={len(active)}/5 signals={signals} closes={closes} "
                      f"active={active}", flush=True)

            # Check staleness
            if age > stale_seconds:
                print(f"[{datetime.now(timezone.utc).isoformat()}] 🚨 STALE: "
                      f"state is {age:.0f}s old (threshold: {stale_seconds}s). "
                      f"Last cycle: {last_cycle}", flush=True)
                if auto_restart:
                    print(f"  Restarting runner...", flush=True)
                    restart_runner()
                    # Give it time to start
                    time.sleep(10)
                else:
                    print(f"  Auto-restart is OFF — manual intervention needed", flush=True)

        except Exception as e:
            print(f"[{datetime.now(timezone.utc).isoformat()}] Watchdog error: {e}", flush=True)

        time.sleep(15)  # Check every 15 seconds


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Kelly Shadow Watchdog")
    parser.add_argument("--stale-seconds", type=int, default=120,
                       help="Seconds before state is considered stale (default: 120)")
    parser.add_argument("--no-restart", action="store_true",
                       help="Don't auto-restart, just alert")
    args = parser.parse_args()

    monitor_loop(stale_seconds=args.stale_seconds, auto_restart=not args.no_restart)


if __name__ == "__main__":
    main()
