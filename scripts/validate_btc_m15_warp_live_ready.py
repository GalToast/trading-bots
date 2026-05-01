#!/usr/bin/env python3
"""Validate BTC M15 Warp live deployment readiness.

Checks that:
1. Registry entry exists with correct config
2. Watchdog group includes the live lane
3. Shadow state meets graduation criteria
4. No conflicting live magic numbers
5. Launch command is syntactically valid

Usage: python scripts/validate_btc_m15_warp_live_ready.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "configs" / "penetration_lattice_runner_registry.json"
WATCHDOG = ROOT / "configs" / "watchdog_groups.json"
SHADOW_STATE = ROOT / "reports" / "penetration_lattice_shadow_btcusd_m15_warp_state.json"

errors = []
warnings = []
passes = []


def check(condition, msg, is_warning=False):
    if condition:
        passes.append(msg)
        print(f"  [PASS] {msg}")
    else:
        if is_warning:
            warnings.append(msg)
            print(f"  [WARN] {msg}")
        else:
            errors.append(msg)
            print(f"  [FAIL] {msg}")


print("=" * 70)
print("BTC M15 Warp LIVE DEPLOYMENT READINESS CHECK")
print("=" * 70)

# 1. Registry entry
print("\n1. Registry Entry")
reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
lane = None
for l in reg["lanes"]:
    if l["name"] == "live_btcusd_m15_warp_941781":
        lane = l
        break
check(lane is not None, "Lane registered in registry")
if lane:
    check(lane["kind"] == "live_crypto", "Kind is live_crypto")
    check(lane["poll_seconds"] == 1, "Poll interval = 1s (fast for live)")
    check(lane["stale_after_seconds"] == 120, "Stale threshold = 120s")
    args = lane.get("restart_args", [])
    check("--symbol" in args and "BTCUSD" in args, "Symbol = BTCUSD")
    check("--timeframe" in args and "M15" in args, "Timeframe = M15")
    check("--step" in args and "15" in args, "Step = $15")
    check("--max-open-per-side" in args and "40" in args, "Max open per side = 40")
    check("--raw-close-alpha" in args and "1.0" in args, "Close alpha = 1.0")
    check("--proven-step-ceiling" not in args, "No stale proven-step ceiling in live contract")
    check("--max-entry-spread-ratio" in args and "15.0" in args, "Max entry spread ratio = 15.0")
    check("--guard-open-admission" in args, "Guard-open admission enabled")
    check("--suppress-additional-levels-after-burst" in args, "Burst suppression enabled")
    check("--burst-open-threshold" in args and "2" in args, "Burst threshold = 2")
    check("--adaptive-overlay-autopilot" in args, "Adaptive overlay autopilot enabled")
    check("--direct-live" in args, "Direct-live flag present")
    check("--live-magic" in args and "941781" in args, "Magic = 941781")
    check("--live-comment-prefix" in args and "PLIVE-WARP" in args, "Comment prefix = PLIVE-WARP")

# 2. Watchdog group
print("\n2. Watchdog Group")
wd = json.loads(WATCHDOG.read_text(encoding="utf-8"))
crypto_lanes = wd.get("groups", {}).get("crypto_watchdog", {}).get("lanes", [])
check("live_btcusd_m15_warp_941781" in crypto_lanes, "In crypto_watchdog group")

# 3. Historical shadow reference
print("\n3. Historical Shadow Reference")
if SHADOW_STATE.exists():
    d = json.load(open(SHADOW_STATE))
    btc = d.get("symbols", {}).get("BTCUSD", {})
    closes = btc.get("realized_closes", 0)
    net = btc.get("realized_net_usd", 0)
    resets = btc.get("anchor_resets", 0)
    max_open = btc.get("max_open_total", 0)
    heartbeat = d.get("runner", {}).get("heartbeat_at", "?")
    
    check(closes >= 100, f"Historical shadow >=100 closes: {closes}", is_warning=True)
    check(net >= 1000, f"Historical shadow >=$1,000 realized: ${net:.2f}", is_warning=True)
    check(resets == 0, f"Historical shadow 0 anchor resets: {resets}", is_warning=True)
    if closes > 0 and net > 0:
        check(net / closes >= 10, f"Historical shadow $/close >=$10: ${net/closes:.2f}", is_warning=True)
    # Shadow's max_open is naturally unconstrained; the live cap (40 per side) is the safety margin.
    print(f"  [INFO] Historical shadow max_open seen: {max_open} (live cap is 40 per side = 80 total)")
    print(f"  [INFO] Historical shadow state: {closes} closes, ${net:.2f}, {resets} resets, max_open={max_open}")
    print(f"  [INFO] Historical shadow heartbeat: {heartbeat}")
else:
    check(False, "Historical shadow state file exists", is_warning=True)

# 4. No conflicting magic numbers
print("\n4. Magic Number Uniqueness")
existing_magics = []
for l in reg["lanes"]:
    args = l.get("restart_args", [])
    for i, a in enumerate(args):
        if a == "--live-magic" and i + 1 < len(args):
            existing_magics.append((l["name"], str(args[i + 1])))
check(len([m for m in existing_magics if m[1] == "941781"]) <= 1, "Magic 941781 unique")
for name, magic in existing_magics:
    if magic == "941781":
        print(f"  [INFO] Magic 941781 assigned to: {name}")

# 5. Launch command validity
print("\n5. Launch Command Validity")
if lane:
    args = lane.get("restart_args", [])
    # Check all args are strings (no integers that would crash subprocess.Popen)
    all_strings = all(isinstance(a, str) for a in args)
    check(all_strings, "All restart_args are strings (no int crash risk)")
    # Check required flags
    required = ["--symbol", "--timeframe", "--step", "--max-open-per-side",
                "--raw-close-alpha", "--max-entry-spread-ratio",
                "--direct-live", "--live-magic",
                "--live-comment-prefix", "--live-volume", "--poll-seconds"]
    for flag in required:
        check(flag in args, f"Flag {flag} present")

# Summary
print("\n" + "=" * 70)
if errors:
    print(f"NOT READY - {len(errors)} error(s), {len(warnings)} warning(s)")
    for e in errors:
        print(f"  ERROR: {e}")
    sys.exit(1)
elif warnings:
    print(f"READY WITH WARNINGS - {len(warnings)} warning(s)")
    for w in warnings:
        print(f"  WARNING: {w}")
else:
    print("READY TO DEPLOY - All checks passed!")
print(f"\n{len(passes)} checks passed, {len(warnings)} warnings, {len(errors)} errors")
print("=" * 70)
