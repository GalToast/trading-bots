#!/usr/bin/env python
"""Launch Guardian — Pre-Launch Validator + Post-Launch Verifier

Prevents the ETH M5 $5 runtime drift disaster (H1/60s instead of M5/1s) by:

1. PRE-LAUNCH: Validates launch command args against registry config
   - Checks --timeframe, --poll-seconds, --step, --max-open-per-side match registry
   - Reports any missing or mismatched params before launch

2. POST-LAUNCH: Verifies running process matches expected config
   - Reads process command line from OS
   - Compares actual runtime params vs registry expectations
   - Alerts if drift detected (e.g., missing --timeframe → defaulting to H1)

3. WATCHDOG HEALTH: Checks watchdog config matches registry
   - Compares watchdog_groups.json lanes vs registry entries
   - Alerts if lanes exist in registry but not in watchdog groups

Usage:
  python scripts/launch_guardian.py --pre-launch --lane-name live_ethusd_m5_warp_941784
  python scripts/launch_guardian.py --post-launch --lane-name live_ethusd_m5_warp_941784
  python scripts/launch_guardian.py --watchdog-health
  python scripts/launch_guardian.py --all
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
REGISTRY_FILE = ROOT / "configs" / "penetration_lattice_runner_registry.json"
WATCHDOG_FILE = ROOT / "configs" / "watchdog_groups.json"

# Expected params for each lane type
EXPECTED_PARAMS = {
    "live_crypto": {
        "required": ["--timeframe", "--poll-seconds", "--step", "--max-open-per-side"],
        "defaults": {
            "--timeframe": "M15",
            "--poll-seconds": "1",
        }
    },
    "shadow_crypto": {
        "required": ["--timeframe", "--poll-seconds", "--step", "--max-open-per-side"],
        "defaults": {
            "--timeframe": "M15",
            "--poll-seconds": "30",
        }
    },
    "live_fx": {
        "required": ["--timeframe", "--poll-seconds"],
        "defaults": {
            "--poll-seconds": "1",
        }
    },
    "shadow_fx": {
        "required": ["--timeframe", "--poll-seconds"],
        "defaults": {
            "--poll-seconds": "5",
        }
    },
}

# Lane name to process pattern mapping
PROCESS_PATTERNS = {
    "live_": "live_penetration_lattice_tick_crypto_shadow.py",
    "shadow_": "live_penetration_lattice_tick_crypto_shadow.py",
}


def parse_iso_datetime(value):
    """Parse an ISO datetime string into a UTC-aware datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def load_json(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except:
        return None


def load_jsonl_rows(path):
    """Load JSON objects from a .jsonl file."""
    if not path or not path.exists():
        return []
    try:
        rows = []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        return rows
    except Exception:
        return []


def _read_group_lanes(group_data):
    lanes = group_data.get("lanes") if isinstance(group_data, dict) else None
    if not lanes:
        return []
    return [str(lane) for lane in lanes if lane]


def select_latest_completed_watchdog_cycle(rows, loop_name):
    """Return rows for the most recent cycle window that has at least one summary exit."""
    if not rows:
        return []
    filtered = []
    for row in rows:
        if str(row.get("loop_name") or "") != str(loop_name):
            continue
        ts = parse_iso_datetime(row.get("ts_utc"))
        if ts is None:
            continue
        filtered.append((ts, row))
    if not filtered:
        return []
    filtered.sort(key=lambda item: item[0])
    cycle_start_indices = [
        idx
        for idx, (_ts, row) in enumerate(filtered)
        if str(row.get("action") or "") == "watchdog_startup"
        and str(row.get("event") or "") == "cycle_begin"
    ]
    if not cycle_start_indices:
        return []
    rows_sorted = [row for _, row in filtered]

    for i in range(len(cycle_start_indices) - 1, -1, -1):
        start_index = cycle_start_indices[i]
        end_index = cycle_start_indices[i + 1] if i + 1 < len(cycle_start_indices) else None
        cycle_rows = rows_sorted[start_index + 1 : end_index]
        if any(str(row.get("event") or "") == "run_watchdog_summary_exit" for row in cycle_rows):
            return cycle_rows

    return rows_sorted[cycle_start_indices[-1] + 1 :]


def summarize_cycle_checkpoint_rows(cycle_rows, expected_lanes):
    summary = {
        "run_watchdog_state_loaded": 0,
        "run_watchdog_summary_begin": 0,
        "run_watchdog_summary_enter": 0,
        "run_watchdog_summary_exit": 0,
    }
    for row in cycle_rows:
        event = str(row.get("event") or "")
        if event in summary:
            summary[event] += 1
    if expected_lanes is None:
        expected_lanes = 0
    return {
        "events": summary,
        "expected_lanes": int(expected_lanes),
        "complete": summary["run_watchdog_summary_exit"] == int(expected_lanes),
        "healthy": summary["run_watchdog_state_loaded"] == 1
        and summary["run_watchdog_summary_begin"] == 1
        and summary["run_watchdog_summary_enter"] == int(expected_lanes)
        and summary["run_watchdog_summary_exit"] == int(expected_lanes),
    }


def check_watchdog_events_health(group_name, expected_lanes):
    events_path = ROOT / "reports" / "watchdog" / f"{group_name}_events.jsonl"
    rows = load_jsonl_rows(events_path)
    if not rows:
        print(f"  WARN {group_name}: no events found at {events_path}")
        return False

    cycle_rows = select_latest_completed_watchdog_cycle(rows, group_name)
    if not cycle_rows:
        print(f"  WARN {group_name}: no completed watchdog cycle found")
        return False

    summary = summarize_cycle_checkpoint_rows(cycle_rows, expected_lanes)
    events = summary["events"]
    if summary["healthy"]:
        print(
            f"  ✅ {group_name} checkpoint telemetry healthy "
            f"(loaded={events['run_watchdog_state_loaded']} begin={events['run_watchdog_summary_begin']} "
            f"enter={events['run_watchdog_summary_enter']} exit={events['run_watchdog_summary_exit']}/{expected_lanes})"
        )
        return True

    print(
        f"  ALERT {group_name} checkpoint telemetry mismatch "
        f"(loaded={events['run_watchdog_state_loaded']} begin={events['run_watchdog_summary_begin']} "
        f"enter={events['run_watchdog_summary_enter']} exit={events['run_watchdog_summary_exit']}/{expected_lanes})"
    )
    return False


def find_lane_in_registry(registry, lane_name):
    """Find a lane by name in the registry."""
    if not registry:
        return None
    lanes = registry.get("lanes", [])
    for lane in lanes:
        if lane.get("name") == lane_name:
            return lane
    return None


def parse_restart_args(restart_args):
    """Parse restart_args list into a dict of param -> value."""
    params = {}
    i = 0
    while i < len(restart_args):
        arg = restart_args[i]
        if arg.startswith("--"):
            if i + 1 < len(restart_args) and not restart_args[i + 1].startswith("--"):
                params[arg] = restart_args[i + 1]
                i += 2
            else:
                params[arg] = True
                i += 1
        else:
            i += 1
    return params


def find_process_by_pattern(pattern_substring):
    """Find running processes matching a pattern."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Select-Object ProcessId, CommandLine | ConvertTo-Json"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            data = [data]
        matches = []
        for proc in data:
            cmdline = proc.get("CommandLine", "") or ""
            if pattern_substring in cmdline:
                matches.append({
                    "pid": proc.get("ProcessId"),
                    "cmdline": cmdline,
                })
        return matches
    except:
        return []


def check_pre_launch(lane_name):
    """Validate launch params against registry before launching."""
    registry = load_json(REGISTRY_FILE)
    lane = find_lane_in_registry(registry, lane_name)

    if not lane:
        print(f"❌ Lane '{lane_name}' NOT FOUND in registry")
        print(f"   Fix: Add lane to {REGISTRY_FILE.name} first")
        return False

    kind = lane.get("kind", "unknown")
    restart_args = lane.get("restart_args", [])
    params = parse_restart_args(restart_args)

    expected = EXPECTED_PARAMS.get(kind, {})
    required = expected.get("required", [])
    defaults = expected.get("defaults", {})

    print(f"=" * 70)
    print(f"PRE-LAUNCH VALIDATION: {lane_name}")
    print(f"=" * 70)
    print(f"  Kind: {kind}")
    print(f"  Registry entry: ✅ Found")
    print()

    all_ok = True
    print("  Required params check:")
    for param in required:
        if param in params:
            print(f"    ✅ {param} = {params[param]}")
        else:
            default_val = defaults.get(param, "NO DEFAULT")
            print(f"    ❌ {param} = MISSING (default: {default_val})")
            all_ok = False

    print()

    # Check for critical params that cause runtime drift if missing
    critical_params = ["--timeframe", "--poll-seconds", "--step"]
    print("  Critical params (runtime drift risk):")
    for param in critical_params:
        if param in params:
            print(f"    ✅ {param} = {params[param]}")
        else:
            print(f"    🔴 {param} = MISSING → will use WRONG default!")
            all_ok = False

    print()

    # Check state files exist
    state_path = lane.get("state_path", "")
    if state_path:
        state_file = ROOT / state_path
        if state_file.exists():
            print(f"  State file: ✅ {state_file.name}")
        else:
            print(f"  State file: ⚠️ {state_file.name} (will be created on launch)")

    # Check watchdog membership
    watchdog = load_json(WATCHDOG_FILE)
    in_watchdog = False
    watchdog_group = None
    if watchdog:
        groups = watchdog.get("groups", {})
        for group_name, group_data in groups.items():
            lanes = group_data.get("lanes", [])
            if lane_name in lanes:
                in_watchdog = True
                watchdog_group = group_name
                break

    if in_watchdog:
        print(f"  Watchdog: ✅ In '{watchdog_group}' group")
    else:
        print(f"  Watchdog: ❌ NOT in any watchdog group → no supervision!")
        all_ok = False

    print()
    if all_ok:
        print(f"  VERDICT: ✅ SAFE TO LAUNCH")
    else:
        print(f"  VERDICT: 🔴 FIX ISSUES BEFORE LAUNCH")

    print(f"=" * 70)
    return all_ok


def check_post_launch(lane_name):
    """Verify running process matches registry config."""
    registry = load_json(REGISTRY_FILE)
    lane = find_lane_in_registry(registry, lane_name)

    if not lane:
        print(f"❌ Lane '{lane_name}' NOT FOUND in registry")
        return False

    kind = lane.get("kind", "unknown")
    restart_args = lane.get("restart_args", [])
    expected_params = parse_restart_args(restart_args)

    # Find running processes
    pattern = PROCESS_PATTERNS.get("live_", "live_penetration_lattice_tick_crypto_shadow.py")
    processes = find_process_by_pattern(pattern)

    print(f"=" * 70)
    print(f"POST-LAUNCH VERIFICATION: {lane_name}")
    print(f"=" * 70)
    print(f"  Expected params from registry:")
    for param, value in expected_params.items():
        if param.startswith("--") and param not in ["--direct-live", "--fresh-start", "--symbol", "--symbols"]:
            print(f"    {param} = {value}")
    print()

    # Find the specific process for this lane (by magic number or state path)
    magic = None
    for arg in restart_args:
        if "--live-magic" in arg:
            idx = restart_args.index(arg)
            if idx + 1 < len(restart_args):
                magic = restart_args[idx + 1]
            break

    matching_process = None
    for proc in processes:
        cmdline = proc.get("cmdline", "")
        if magic and magic in cmdline:
            matching_process = proc
            break
        elif lane_name in cmdline:
            matching_process = proc
            break

    if not matching_process:
        print(f"  Running process: ❌ NOT FOUND")
        print(f"  Expected magic: {magic}")
        print(f"  VERDICT: 🔴 Lane is NOT running!")
        print(f"=" * 70)
        return False

    actual_params = parse_restart_args(matching_process["cmdline"].split())

    print(f"  Running process: ✅ PID {matching_process['pid']}")
    print()

    # Compare expected vs actual
    all_ok = True
    print("  Param comparison:")
    critical_params = ["--timeframe", "--poll-seconds", "--step", "--max-open-per-side"]
    for param in critical_params:
        expected = expected_params.get(param, "N/A")
        actual = actual_params.get(param, "MISSING")
        if expected == actual:
            print(f"    ✅ {param}: {actual}")
        else:
            print(f"    🔴 {param}: expected={expected}, actual={actual} ← DRIFT!")
            all_ok = False

    print()
    if all_ok:
        print(f"  VERDICT: ✅ Runtime matches registry")
    else:
        print(f"  VERDICT: 🔴 RUNTIME DRIFT DETECTED — Kill and relaunch from registry!")

    print(f"=" * 70)
    return all_ok


def check_watchdog_health():
    """Check watchdog config matches registry."""
    registry = load_json(REGISTRY_FILE)
    watchdog = load_json(WATCHDOG_FILE)

    if not registry or not watchdog:
        print("❌ Cannot load registry or watchdog config")
        return False

    registry_lanes = set()
    for lane in registry.get("lanes", []):
        registry_lanes.add(lane.get("name"))

    watchdog_lanes = set()
    groups = watchdog.get("groups", {})
    for group_name, group_data in groups.items():
        for lane_name in group_data.get("lanes", []):
            watchdog_lanes.add(lane_name)

    print(f"=" * 70)
    print(f"WATCHDOG HEALTH CHECK")
    print(f"=" * 70)
    print(f"  Registry lanes: {len(registry_lanes)}")
    print(f"  Watchdog lanes: {len(watchdog_lanes)}")
    print()

    # Lanes in registry but not in watchdog
    missing_from_watchdog = registry_lanes - watchdog_lanes
    if missing_from_watchdog:
        print(f"  🔴 Lanes in registry but NOT supervised by watchdog:")
        for lane in sorted(missing_from_watchdog):
            print(f"    - {lane}")
    else:
        print(f"  ✅ All registry lanes are in watchdog groups")

    # Lanes in watchdog but not in registry
    extra_in_watchdog = watchdog_lanes - registry_lanes
    if extra_in_watchdog:
        print(f"  ⚠️ Lanes in watchdog but NOT in registry:")
        for lane in sorted(extra_in_watchdog):
            print(f"    - {lane}")

    print()

    # Group health
    checkpoint_ok = True
    print(f"  Watchdog groups:")
    for group_name, group_data in sorted(groups.items()):
        lanes = group_data.get("lanes", [])
        label = group_data.get("label", group_name)
        expected_lanes = len(_read_group_lanes(group_data))
        missing = [l for l in lanes if l not in registry_lanes]
        print(f"    {label}: {len(lanes)} lanes", end="")
        if missing:
            print(f" (⚠️ {len(missing)} not in registry)", end="")
        print()
        if not check_watchdog_events_health(group_name, expected_lanes):
            checkpoint_ok = False

    print()
    if missing_from_watchdog:
        print(f"  VERDICT: 🔴 {len(missing_from_watchdog)} lanes unsupervised — restart watchdog!")
    elif checkpoint_ok:
        print(f"  VERDICT: ✅ All lanes supervised")
    else:
        print(f"  VERDICT: 🔴 One or more watchdog loops missing checkpoint telemetry")

    print(f"=" * 70)
    return len(missing_from_watchdog) == 0 and checkpoint_ok


def main():
    parser = argparse.ArgumentParser(description="Launch Guardian")
    parser.add_argument("--pre-launch", action="store_true", help="Validate launch params")
    parser.add_argument("--post-launch", action="store_true", help="Verify running process")
    parser.add_argument("--watchdog-health", action="store_true", help="Check watchdog config")
    parser.add_argument("--all", action="store_true", help="Run all checks")
    parser.add_argument("--lane-name", type=str, help="Lane name to check")
    args = parser.parse_args()

    if args.all:
        # Run all checks
        print()
        check_watchdog_health()
        print()

        # Check all live lanes
        registry = load_json(REGISTRY_FILE)
        if registry:
            for lane in registry.get("lanes", []):
                name = lane.get("name", "")
                if name.startswith("live_"):
                    check_post_launch(name)
                    print()
        return

    if args.pre_launch:
        if not args.lane_name:
            print("❌ --lane-name required for --pre-launch")
            return
        check_pre_launch(args.lane_name)
        return

    if args.post_launch:
        if not args.lane_name:
            print("❌ --lane-name required for --post-launch")
            return
        check_post_launch(args.lane_name)
        return

    if args.watchdog_health:
        check_watchdog_health()
        return

    # Default: run watchdog health check
    check_watchdog_health()


if __name__ == "__main__":
    main()
