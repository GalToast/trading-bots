#!/usr/bin/env python3
"""
Price Feeder Watchdog — monitors shared_price_feeder.py and restarts it if it dies.
Run: python scripts/price_feeder_watchdog.py --loop
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEEDER_SCRIPT = ROOT / "scripts" / "shared_price_feeder.py"
HEARTBEAT_PATH = ROOT / "reports" / "shared_price_feeder_heartbeat.json"
WATCHDOG_STATE_PATH = ROOT / "reports" / "price_feeder_watchdog_state.json"
POLL_INTERVAL = 10  # seconds
STALE_THRESHOLD = 60  # seconds


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def feeder_is_alive():
    """Check if the price feeder heartbeat is fresh."""
    if not HEARTBEAT_PATH.exists():
        return False
    try:
        mtime = os.path.getmtime(HEARTBEAT_PATH)
        age = time.time() - mtime
        return age < STALE_THRESHOLD
    except Exception:
        return False


def get_feeder_pid():
    """Get the price feeder PID from heartbeat file."""
    if not HEARTBEAT_PATH.exists():
        return None
    try:
        d = json.load(open(HEARTBEAT_PATH))
        return d.get("feeder_pid")
    except Exception:
        return None


def process_alive(pid):
    """Check if a process is alive."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def launch_feeder():
    """Launch the price feeder as a background process."""
    log_dir = ROOT / "reports" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "shared_price_feeder.out.log"
    stderr_path = log_dir / "shared_price_feeder.err.log"
    with open(stdout_path, "a", encoding="utf-8") as stdout_f, open(stderr_path, "a", encoding="utf-8") as stderr_f:
        creationflags = 0
        if os.name == "nt":
            try:
                import ctypes
                DETACHED_PROCESS = 0x00000008
                creationflags = DETACHED_PROCESS
            except ImportError:
                pass
        proc = subprocess.Popen(
            [sys.executable, str(FEEDER_SCRIPT)],
            cwd=str(ROOT),
            stdout=stdout_f,
            stderr=stderr_f,
            creationflags=creationflags,
        )
    return int(proc.pid)


def write_state(status, feeder_pid, feeder_alive, last_restart, consecutive_failures):
    """Write watchdog state file."""
    WATCHDOG_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "watchdog_status": status,
        "feeder_pid": feeder_pid,
        "feeder_alive": feeder_alive,
        "last_restart": last_restart,
        "consecutive_failures": consecutive_failures,
        "watchdog_updated_at": utc_now_iso(),
    }
    try:
        tmp = WATCHDOG_STATE_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, WATCHDOG_STATE_PATH)
    except Exception as e:
        print(f"[watchdog] Failed to write state: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()

    if not args.loop:
        # Single check mode
        alive = feeder_is_alive()
        pid = get_feeder_pid()
        print(f"Price feeder: pid={pid}, alive={alive}")
        sys.exit(0 if alive else 1)

    print(f"[watchdog] Price feeder watchdog starting at {utc_now_iso()}")
    print(f"[watchdog] Feeder script: {FEEDER_SCRIPT}")
    print(f"[watchdog] Poll interval: {POLL_INTERVAL}s, stale threshold: {STALE_THRESHOLD}s")

    last_restart = None
    consecutive_failures = 0

    while True:
        try:
            feeder_alive = feeder_is_alive()
            feeder_pid = get_feeder_pid()
            pid_still_alive = process_alive(feeder_pid) if feeder_pid else False

            if not feeder_alive or not pid_still_alive:
                if feeder_pid and pid_still_alive:
                    print(f"[watchdog] Feeder PID {feeder_pid} alive but heartbeat stale — killing")
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/F", "/PID", str(feeder_pid)], capture_output=True, timeout=5)
                    else:
                        os.kill(feeder_pid, 9)
                    time.sleep(1)

                print(f"[watchdog] Feeder not alive (pid={feeder_pid}, heartbeat={'yes' if feeder_alive else 'no'}) — restarting")
                new_pid = launch_feeder()
                last_restart = utc_now_iso()
                consecutive_failures = 0
                print(f"[watchdog] Launched feeder PID {new_pid}")

                # Wait a moment for the feeder to write its first heartbeat
                time.sleep(3)
                if process_alive(new_pid):
                    print(f"[watchdog] Feeder PID {new_pid} confirmed alive")
                    write_state("ok", new_pid, True, last_restart, consecutive_failures)
                else:
                    consecutive_failures += 1
                    print(f"[watchdog] Feeder PID {new_pid} died immediately (failure {consecutive_failures})")
                    write_state("feeder_crash", new_pid, False, last_restart, consecutive_failures)
            else:
                write_state("ok", feeder_pid, True, last_restart, consecutive_failures)

        except Exception as e:
            consecutive_failures += 1
            print(f"[watchdog] Error in poll cycle: {e}")
            write_state("error", feeder_pid, False, last_restart, consecutive_failures)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
