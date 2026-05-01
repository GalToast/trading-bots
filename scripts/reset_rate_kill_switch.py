#!/usr/bin/env python3
"""
Reset-Rate Kill Switch Monitor

Monitors all running HH lanes by reading their event logs.
When a lane exceeds 6 resets/hour, it triggers an automatic kill.

This is the SAFETY NET that prevents NAS100-style disasters:
- NAS100 Breakout: 1676 closes in 20 min, 18 resets = -$1,720
- ETH M15 Micro: 3907 resets in 15 hours = -$1,214
- ETH M15 HH: 1177 resets in 15 hours = -$399

With the kill switch at 6 resets/hour:
- NAS100 Breakout would have been killed at ~20 min (instead of continuing to -$1,720)
- ETH M15 Micro would have been killed within 1 hour (instead of 15 hours)
- ETH M15 HH would have been killed within 1 hour (instead of 15 hours)

Architecture:
1. Scan for all HH event log files in reports/
2. Count resets in the last 60 minutes per file
3. If resets > 6/hour → log alert, trigger kill
4. Kill: stop the process (via PID tracking or watchdog notification)

Output: reports/reset_rate_alerts.json (alerts), and kills processes
Usage: Run every 5 minutes via cron or watchdog
"""
import json
import os
import time
import signal
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── Configuration ──────────────────────────────────────────────────────

RESET_RATE_LIMIT = 6  # resets per hour
CHECK_WINDOW_MINUTES = 60  # look back this many minutes
SCAN_INTERVAL_SECONDS = 300  # run every 5 minutes

REPORTS_DIR = Path(__file__).parent.parent / "reports"
ALERTS_FILE = REPORTS_DIR / "reset_rate_alerts.json"
PID_TRACKING_FILE = REPORTS_DIR / "lane_pids.json"


# ── Event Log Parser ──────────────────────────────────────────────────

def scan_event_logs() -> list:
    """Find all HH event log files in reports/."""
    logs = []
    for f in REPORTS_DIR.glob("*_events.jsonl"):
        logs.append(f)
    return logs


def count_resets_in_window(event_path: Path, window_minutes: int = 60, max_lines: int = 5000) -> dict:
    """
    Count resets and closes in the last N minutes of an event log.

    Only reads the last `max_lines` lines for efficiency — this should cover
    the last hour for normal lanes. If a lane has >5000 events in the window,
    it's already in a storm and should be killed.

    Returns:
    {
        "resets": int,
        "closes": int,
        "opens": int,
        "floating_loss": float,
        "window_start": str,
        "window_end": str,
    }
    """
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=window_minutes)

    resets = 0
    closes = 0
    opens = 0
    floating_loss = 0.0
    last_reset_time = None
    lines_read = 0

    if not event_path.exists():
        return {"resets": 0, "closes": 0, "opens": 0, "floating_loss": 0.0, "error": "file not found"}

    try:
        # Read last N lines efficiently by seeking from end of file
        file_size = event_path.stat().st_size
        # Estimate bytes to read: max_lines * avg_line_length (~200 chars)
        bytes_to_read = min(file_size, max_lines * 300)

        with open(event_path, "r", encoding="utf-8", errors="replace") as f:
            if file_size > bytes_to_read:
                f.seek(file_size - bytes_to_read)
                # Skip partial first line
                f.readline()

            lines = f.readlines()
    except Exception as e:
        return {"resets": 0, "closes": 0, "opens": 0, "floating_loss": 0.0, "error": str(e)}

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        lines_read += 1

        # Check if event is within the window
        ts = event.get("ts_utc")
        if ts:
            try:
                event_time = datetime.fromisoformat(ts)
                if event_time < window_start:
                    continue  # Skip old events
            except ValueError:
                pass

        event_type = event.get("event_type", event.get("action", ""))

        if "reset" in event_type.lower():
            resets += 1
            last_reset_time = ts
        elif "close" in event_type.lower():
            closes += 1
            pnl = event.get("pnl", event.get("realized_pnl", 0))
            if pnl:
                try:
                    floating_loss += float(pnl)
                except (ValueError, TypeError):
                    pass
        elif "open" in event_type.lower() or "fill" in event_type.lower():
            opens += 1

    # If we read max_lines and they're all within the window, there might be more
    # events we didn't read — flag this as potentially undercounting
    potentially_incomplete = lines_read >= max_lines

    return {
        "resets": resets,
        "closes": closes,
        "opens": opens,
        "floating_loss": round(floating_loss, 2),
        "last_reset_time": last_reset_time,
        "lines_read": lines_read,
        "potentially_incomplete": potentially_incomplete,
        "window_start": window_start.isoformat(),
        "window_end": now.isoformat(),
    }


# ── Process Killing ───────────────────────────────────────────────────

def load_pid_tracking() -> dict:
    """Load lane PID mappings."""
    if PID_TRACKING_FILE.exists():
        try:
            return json.loads(PID_TRACKING_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_pid_tracking(tracking: dict) -> None:
    """Save lane PID mappings."""
    PID_TRACKING_FILE.write_text(json.dumps(tracking, indent=2), encoding="utf-8")


def kill_process(pid: int, lane_name: str) -> dict:
    """Kill a process by PID."""
    try:
        import subprocess
        # On Windows, use taskkill
        result = subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return {
            "killed": result.returncode == 0,
            "pid": pid,
            "lane": lane_name,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "timestamp": utc_now_iso(),
        }
    except Exception as e:
        return {
            "killed": False,
            "pid": pid,
            "lane": lane_name,
            "error": str(e),
            "timestamp": utc_now_iso(),
        }


# ── Main Monitor ──────────────────────────────────────────────────────

def run_monitor() -> dict:
    """
    Run the reset-rate monitor.

    Scans all event logs, counts resets in the window,
    alerts and kills lanes that exceed the limit.

    Returns: summary of findings
    """
    logs = scan_event_logs()
    pid_tracking = load_pid_tracking()
    alerts = []
    kills = []

    for log_path in sorted(logs):
        lane_name = log_path.stem.replace("_events", "")
        stats = count_resets_in_window(log_path, window_minutes=CHECK_WINDOW_MINUTES)

        if "error" in stats:
            continue

        resets = stats["resets"]
        reset_rate = resets / (CHECK_WINDOW_MINUTES / 60.0)  # resets per hour

        if resets >= RESET_RATE_LIMIT:
            alert = {
                "lane": lane_name,
                "resets": resets,
                "reset_rate_per_hour": round(reset_rate, 1),
                "closes": stats["closes"],
                "opens": stats["opens"],
                "floating_loss": stats["floating_loss"],
                "last_reset_time": stats.get("last_reset_time"),
                "window_start": stats["window_start"],
                "window_end": stats["window_end"],
                "action": "KILL_TRIGGERED",
                "timestamp": utc_now_iso(),
            }
            alerts.append(alert)

            # Try to kill the process
            pid = pid_tracking.get(lane_name, {}).get("pid")
            if pid:
                kill_result = kill_process(pid, lane_name)
                kills.append(kill_result)
                alert["kill_result"] = kill_result

            # Remove from PID tracking since it's killed
            if lane_name in pid_tracking:
                del pid_tracking[lane_name]
                save_pid_tracking(pid_tracking)

    # Also check lanes that are safe
    safe_lanes = []
    for log_path in sorted(logs):
        lane_name = log_path.stem.replace("_events", "")
        stats = count_resets_in_window(log_path, window_minutes=CHECK_WINDOW_MINUTES)
        if "error" not in stats and stats["resets"] < RESET_RATE_LIMIT:
            safe_lanes.append({
                "lane": lane_name,
                "resets": stats["resets"],
                "reset_rate_per_hour": round(stats["resets"] / (CHECK_WINDOW_MINUTES / 60.0), 1),
                "closes": stats["closes"],
                "status": "SAFE",
            })

    # Save alerts
    ALERTS_FILE.parent.mkdir(exist_ok=True)
    report = {
        "timestamp": utc_now_iso(),
        "check_window_minutes": CHECK_WINDOW_MINUTES,
        "reset_rate_limit": RESET_RATE_LIMIT,
        "total_lanes_checked": len(logs),
        "lanes_killed": len(alerts),
        "lanes_safe": len(safe_lanes),
        "alerts": alerts,
        "safe_lanes": safe_lanes,
        "kills": kills,
    }

    with open(ALERTS_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    return report


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    report = run_monitor()

    print(f"Reset-Rate Kill Switch Monitor ({report['check_window_minutes']}-minute window, limit: {report['reset_rate_limit']}/hour)")
    print(f"Timestamp: {report['timestamp']}")
    print(f"Lanes checked: {report['total_lanes_checked']}")
    print(f"Lanes safe: {report['lanes_safe']}")
    print(f"Lanes killed: {report['lanes_killed']}")
    print()

    if report["alerts"]:
        print("🚨 ALERTS:")
        for alert in report["alerts"]:
            print(f"  {alert['lane']}: {alert['resets']} resets in {report['check_window_minutes']}min ({alert['reset_rate_per_hour']}/hour)")
            if alert.get("kill_result"):
                kr = alert["kill_result"]
                if kr.get("killed"):
                    print(f"    ✅ Killed PID {kr['pid']}")
                else:
                    print(f"    ❌ Kill failed: {kr.get('error', kr.get('stderr', 'unknown'))}")
            print()

    if report["safe_lanes"]:
        print("✅ SAFE:")
        for lane in report["safe_lanes"]:
            print(f"  {lane['lane']}: {lane['resets']} resets ({lane['reset_rate_per_hour']}/hour), {lane['closes']} closes")

    print(f"\nSaved to {ALERTS_FILE}")


if __name__ == "__main__":
    main()
