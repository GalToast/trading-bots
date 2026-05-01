#!/usr/bin/env python3
"""Register and launch the GBPUSD adaptive trend-harvest shadow packet.

Idempotency: checks if the lane is already running before launching.
"""
import json
import subprocess
import sys
from pathlib import Path

REGISTRY = Path("configs/penetration_lattice_runner_registry.json")
WATCHDOG = Path("configs/watchdog_groups.json")

LANE_NAME = "shadow_gbpusd_m15_trend_harvest_v1"
STATE_FILE = Path("reports/penetration_lattice_shadow_gbpusd_m15_trend_harvest_v1_state.json")
EVENT_FILE = Path("reports/penetration_lattice_shadow_gbpusd_m15_trend_harvest_v1_events.jsonl")

# Build the exact command from the packet contract
CMD = [
    sys.executable,
    "scripts/live_penetration_lattice_tick_crypto_shadow.py",
    "--symbol", "GBPUSD",
    "--fresh-start",
    "--timeframe", "M15",
    "--step", "0.00030",
    "--step-buy", "0.00040",
    "--step-sell", "0.00020",
    "--max-open-per-side", "12",
    "--raw-close-alpha", "0.5",
    "--raw-rearm-variant", "rearm_lvl2_exc1",
    "--raw-rearm-cooldown-bars", "0",
    "--raw-sell-gap", "1",
    "--raw-buy-gap", "3",
    "--state-path", str(STATE_FILE),
    "--event-path", str(EVENT_FILE),
    "--poll-seconds", "30",
    "--shared-price-max-age-ms", "0",
    "--max-floating-loss-usd", "-15.0",
    "--max-lattice-window-bars", "240",
    "--adaptive-overlay-autopilot",
]

# Step 1: Register in runner registry
reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
existing = [l["name"] for l in reg["lanes"]]

new_lane = {
    "name": LANE_NAME,
    "kind": "shadow_fx",
    "state_path": str(STATE_FILE),
    "event_path": str(EVENT_FILE),
    "poll_seconds": 30,
    "stale_after_seconds": 120,
    "process_match_substrings": [
        "scripts/live_penetration_lattice_tick_crypto_shadow.py",
        str(STATE_FILE),
    ],
    "restart_args": CMD[1:],  # skip python executable for restart args
}

registry_changed = False
if LANE_NAME not in existing:
    reg["lanes"].append(new_lane)
    registry_changed = True
    print(f"✅ Added {LANE_NAME} to runner registry")
else:
    lane_index = existing.index(LANE_NAME)
    current_lane = dict(reg["lanes"][lane_index] or {})
    refreshed_lane = dict(current_lane)
    refreshed_lane.update(new_lane)
    if refreshed_lane != current_lane:
        reg["lanes"][lane_index] = refreshed_lane
        registry_changed = True
        print(f"✅ Refreshed {LANE_NAME} runner registry contract")
    else:
        print(f"ℹ️  {LANE_NAME} already in runner registry")

if registry_changed:
    REGISTRY.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")

# Step 2: Add to crypto_watchdog group (uses tick-native runner)
wd = json.loads(WATCHDOG.read_text(encoding="utf-8"))
crypto_group = wd["groups"].get("crypto_watchdog", {})
if "lanes" not in crypto_group:
    crypto_group["lanes"] = []
if LANE_NAME not in crypto_group["lanes"]:
    crypto_group["lanes"].append(LANE_NAME)
    WATCHDOG.write_text(json.dumps(wd, indent=2) + "\n", encoding="utf-8")
    print(f"✅ Added {LANE_NAME} to crypto_watchdog group")
else:
    print(f"ℹ️  {LANE_NAME} already in crypto_watchdog group")

# Step 3: Idempotency guard — check if already running
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
    # Check state file for existing PID
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            pid = state.get("runner", {}).get("pid")
            if pid and is_process_alive(pid):
                return True, pid
        except Exception:
            pass

    # Fallback: scan for matching command line via wmic
    try:
        result = subprocess.run(
            ["wmic", "process", "where", f"name='python.exe'", "get", "processid,commandline"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.split("\n"):
            if LANE_NAME in line or STATE_FILE.stem in line:
                parts = line.split()
                for p in parts:
                    if p.isdigit():
                        pid = int(p)
                        if is_process_alive(pid):
                            return True, pid
    except Exception:
        pass

    return False, None

already_running, existing_pid = check_lane_running()
if already_running:
    print(f"⚠️  {LANE_NAME} is already running (PID {existing_pid}). Skipping launch to prevent duplicates.")
    print(f"State file: {STATE_FILE}")
    print(f"Event log: {EVENT_FILE}")
    sys.exit(0)

# Step 4: Launch the process
print(f"\n🚀 Launching {LANE_NAME}...")
print(f"Command: {' '.join(CMD)}")
proc = subprocess.Popen(CMD, cwd=str(Path(__file__).resolve().parent.parent))
print(f"✅ Launched with PID {proc.pid}")
print(f"\nMonitor with: python scripts/check_experimental_lanes.py")
print(f"State file: {STATE_FILE}")
print(f"Event log: {EVENT_FILE}")
