#!/usr/bin/env python3
"""Register and launch the USDJPY bounded forward proof lane.

Idempotency: checks if the lane is already running before launching.
Uses the bounded-family runner (live_penetration_lattice_tick_shadow.py).
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "configs" / "penetration_lattice_runner_registry.json"
WATCHDOG = ROOT / "configs" / "watchdog_groups.json"

LANE_NAME = "shadow_usdjpy_gap2"
STATE_PATH = ROOT / "reports" / "penetration_lattice_shadow_usdjpy_gap2_state.json"
EVENT_PATH = ROOT / "reports" / "penetration_lattice_shadow_usdjpy_gap2_events.jsonl"
STATE_PATH_TEXT = "reports/penetration_lattice_shadow_usdjpy_gap2_state.json"
EVENT_PATH_TEXT = "reports/penetration_lattice_shadow_usdjpy_gap2_events.jsonl"

# Build the exact command from the overnight packet contract
CMD = [
    sys.executable,
    "scripts/live_penetration_lattice_tick_shadow.py",
    "--symbols", "USDJPY",
    "--bounded-rearm-variant", "rearm_lvl2_exc2",
    "--bounded-close-gap", "2",
    "--adaptive-overlay-autopilot",
    "--max-entry-spread-ratio", "0.30",
    "--state-path", STATE_PATH_TEXT,
    "--event-path", EVENT_PATH_TEXT,
    "--poll-seconds", "5",
    "--max-floating-loss-usd", "-15.0",
]

# Step 1: Idempotency guard — check if already running
def is_process_alive(pid: int) -> bool:
    """Check if a process with given PID is still running."""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_INFORMATION = 0x0400
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | SYNCHRONIZE, False, pid)
        if handle == 0:
            return False
        kernel32.CloseHandle(handle)
        return True
    except Exception:
        return False

def check_lane_running() -> tuple:
    """Check if the lane is already running. Returns (is_running, pid)."""
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            pid = state.get("runner", {}).get("pid")
            if pid and is_process_alive(pid):
                return True, pid
        except Exception:
            pass

    # Fallback: scan for matching command line
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=15, encoding="utf-8"
        )
        if result.returncode == 0 and result.stdout.strip():
            import json as _json
            procs = _json.loads(result.stdout)
            if isinstance(procs, dict):
                procs = [procs]
            state_leaf = STATE_PATH.name
            for p in procs:
                cl = p.get("CommandLine", "")
                if LANE_NAME in cl or state_leaf in cl:
                    pid = p.get("ProcessId")
                    if pid and is_process_alive(pid):
                        return True, pid
    except Exception:
        pass

    return False, None

already_running, existing_pid = check_lane_running()
if already_running:
    print(f"WARNING: {LANE_NAME} is already running (PID {existing_pid}). Skipping launch.")
    print(f"State file: {STATE_PATH}")
    print(f"Event log: {EVENT_PATH}")
    sys.exit(0)

# Step 2: Register in runner registry
reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
existing = [l["name"] for l in reg["lanes"]]

new_lane = {
    "name": LANE_NAME,
    "kind": "shadow_fx",
    "state_path": STATE_PATH_TEXT,
    "event_path": EVENT_PATH_TEXT,
    "poll_seconds": 5,
    "stale_after_seconds": 60,
    "process_match_substrings": [
        "scripts/live_penetration_lattice_tick_shadow.py",
        STATE_PATH_TEXT,
    ],
    "restart_args": CMD[1:],  # skip python executable
}

if LANE_NAME not in existing:
    reg["lanes"].append(new_lane)
    REGISTRY.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")
    print(f"Added {LANE_NAME} to runner registry")
else:
    # Refresh the restart contract and re-enable if disabled.
    for lane in reg["lanes"]:
        if lane["name"] == LANE_NAME:
            lane["restart_args"] = CMD[1:]
            lane["process_match_substrings"] = [
                "scripts/live_penetration_lattice_tick_shadow.py",
                STATE_PATH_TEXT,
            ]
            lane["state_path"] = STATE_PATH_TEXT
            lane["event_path"] = EVENT_PATH_TEXT
            lane["poll_seconds"] = 5
            lane["stale_after_seconds"] = 60
            if lane.get("enabled") is False:
                lane["enabled"] = True
                print(f"Re-enabled {LANE_NAME} in runner registry")
            else:
                print(f"{LANE_NAME} already in runner registry (enabled)")
            REGISTRY.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")
            break

# Step 3: Add to shadow_watchdog group
wd = json.loads(WATCHDOG.read_text(encoding="utf-8"))
shadow_group = wd["groups"].get("shadow_watchdog", {})
if "lanes" not in shadow_group:
    shadow_group["lanes"] = []
if LANE_NAME not in shadow_group["lanes"]:
    shadow_group["lanes"].append(LANE_NAME)
    WATCHDOG.write_text(json.dumps(wd, indent=2) + "\n", encoding="utf-8")
    print(f"Added {LANE_NAME} to shadow_watchdog group")
else:
    print(f"{LANE_NAME} already in shadow_watchdog group")

# Step 4: Launch the process
# NOTE: Launch from scripts/ directory to avoid import path issues
print(f"\nLaunching {LANE_NAME}...")
print(f"Command: {' '.join(CMD)}")
proc = subprocess.Popen(CMD, cwd=str(ROOT))
print(f"Launched with PID {proc.pid}")
print(f"\nMonitor with: python scripts/check_experimental_lanes.py")
print(f"State file: {STATE_PATH}")
print(f"Event log: {EVENT_PATH}")
