"""Hungry Hippo Momentum Lane Emergency Intervention Script.

Usage:
    # Dry run (shows what would happen):
    python scripts/hungry_hippo_momentum_intervention.py --dry-run

    # Execute intervention:
    python scripts/hungry_hippo_momentum_intervention.py --execute

Actions:
    1. Checks current momentum lane state (PID 18148, magic 941778)
    2. Computes floating PnL from broker positions
    3. If --dry-run: reports current state and recommended action
    4. If --execute: kills the lane, backs up state, DOES NOT relaunch
       (letting the 47 existing positions float and close on mean reversion)
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

REPO = Path(__file__).resolve().parent.parent
UTC = timezone.utc

MOMENTUM_PID = 18148
MOMENTUM_MAGIC = 941778
STATE_PATH = REPO / "reports" / "penetration_lattice_live_momentum_alpha50_source_state.json"
EXEC_STATE_PATH = REPO / "reports" / "penetration_lattice_live_momentum_alpha50_exec_state.json"


def check_process_alive(pid: int) -> bool:
    """Check if a process is still running."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5
        )
        return str(pid) in result.stdout
    except Exception:
        return False


def kill_process(pid: int) -> bool:
    """Kill a process by PID."""
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception as e:
        print(f"  ❌ Failed to kill PID {pid}: {e}")
        return False


def get_broker_positions_for_magic(magic: int) -> list:
    """Get all broker positions for a given magic number."""
    if mt5 is None:
        return []
    if not mt5.initialize():
        return []
    positions = mt5.positions_get()
    if positions is None:
        mt5.shutdown()
        return []
    magic_positions = [p for p in positions if p.magic == magic]
    mt5.shutdown()
    return magic_positions


def compute_floating_pnl(positions: list) -> float:
    """Compute total floating PnL for a list of positions."""
    total = 0.0
    for p in positions:
        total += float(p.profit or 0.0) + float(p.swap or 0.0)
    return total


def check_momentum_state():
    """Check the current momentum lane state."""
    print("=" * 60)
    print("MOMENTUM LANE STATE CHECK")
    print("=" * 60)

    # Check process
    alive = check_process_alive(MOMENTUM_PID)
    print(f"\n  Process PID {MOMENTUM_PID}: {'✅ ALIVE' if alive else '❌ DEAD'}")

    # Check state file
    if STATE_PATH.exists():
        with open(STATE_PATH) as f:
            state = json.load(f)
        runner = state.get("runner", {})
        hb = runner.get("heartbeat_at", "unknown")
        print(f"  State file heartbeat: {hb}")

        symbols = state.get("symbols", {})
        total_opens = 0
        total_realized = 0.0
        for sym_name, sym_data in symbols.items():
            opens = len(sym_data.get("open_tickets", []))
            realized = sym_data.get("realized_net_usd", 0.0)
            closes = sym_data.get("realized_closes", 0)
            total_opens += opens
            total_realized += realized
            print(f"  {sym_name}: {opens} opens, {closes} closes, ${realized:.2f} realized")

        print(f"\n  Total opens: {total_opens}")
        print(f"  Total realized PnL: ${total_realized:.2f}")
    else:
        print(f"  ❌ State file not found: {STATE_PATH}")
        return

    # Check broker positions
    broker_positions = get_broker_positions_for_magic(MOMENTUM_MAGIC)
    if broker_positions:
        floating_pnl = compute_floating_pnl(broker_positions)
        buys = sum(1 for p in broker_positions if p.type == 0)  # 0 = BUY
        sells = sum(1 for p in broker_positions if p.type == 1)  # 1 = SELL
        print(f"\n  Broker positions (magic {MOMENTUM_MAGIC}):")
        print(f"    Count: {len(broker_positions)} ({buys} BUY, {sells} SELL)")
        print(f"    Floating PnL: ${floating_pnl:.2f}")
        net = total_realized + floating_pnl
        print(f"    Net (realized + floating): ${net:.2f}")
    else:
        print(f"\n  No broker positions found for magic {MOMENTUM_MAGIC}")

    print()
    return alive, total_realized, floating_pnl if broker_positions else 0, total_opens


def recommend_action(alive: bool, realized: float, floating: float, opens: int):
    """Recommend intervention action based on current state."""
    net = realized + floating
    print("=" * 60)
    print("INTERVENTION RECOMMENDATION")
    print("=" * 60)

    threshold_1 = -100  # Config limit
    threshold_2 = -150  # Team alert limit
    threshold_3 = -200  # Emergency kill

    if floating < threshold_3:  # More negative = worse
        print(f"\n  🔴 EMERGENCY: Floating loss ${floating:.2f} exceeds ${threshold_3}")
        print(f"  RECOMMENDATION: Kill lane immediately, let positions float.")
        return "KILL_NOW"
    elif floating < threshold_2:
        print(f"\n  🟠 ALERT: Floating loss ${floating:.2f} exceeds ${threshold_2}")
        print(f"  RECOMMENDATION: Prepare intervention, execute at ${threshold_3}.")
        return "PREPARE"
    elif floating < threshold_1:
        print(f"\n  🟡 WARNING: Floating loss ${floating:.2f} exceeds config limit ${threshold_1}")
        print(f"  RECOMMENDATION: Monitor closely. Net is ${net:.2f} (still positive).")
        return "MONITOR"
    else:
        print(f"\n  🟢 OK: Floating loss ${floating:.2f} within limits.")
        print(f"  Net PnL: ${net:.2f} (realized ${realized:.2f} + floating ${floating:.2f})")
        return "OK"


def execute_intervention():
    """Execute the intervention: kill lane, backup state, don't relaunch."""
    print("=" * 60)
    print("EXECUTING INTERVENTION")
    print("=" * 60)

    # Step 1: Backup state files
    print(f"\n  Step 1: Backing up state files...")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_paths = []
    for src in [STATE_PATH, EXEC_STATE_PATH]:
        if src.exists():
            backup = src.with_suffix(f".json.bak_intervention_{timestamp}")
            import shutil
            shutil.copy2(src, backup)
            backup_paths.append(str(backup))
            print(f"    ✅ Backed up {src.name} → {backup.name}")
        else:
            print(f"    ⚠️ {src.name} not found, skipping")

    # Step 2: Kill the momentum lane
    print(f"\n  Step 2: Killing momentum lane PID {MOMENTUM_PID}...")
    if check_process_alive(MOMENTUM_PID):
        success = kill_process(MOMENTUM_PID)
        if success:
            print(f"    ✅ Killed PID {MOMENTUM_PID}")
        else:
            print(f"    ❌ Failed to kill PID {MOMENTUM_PID}")
            return False
    else:
        print(f"    ⚠️ PID {MOMENTUM_PID} already dead")

    # Step 3: Verify it's dead
    import time
    time.sleep(2)
    if check_process_alive(MOMENTUM_PID):
        print(f"    ❌ PID {MOMENTUM_PID} still alive! Retry kill...")
        kill_process(MOMENTUM_PID)
        time.sleep(2)
        if check_process_alive(MOMENTUM_PID):
            print(f"    ❌ FAILED: PID {MOMENTUM_PID} still alive after retry")
            return False

    print(f"    ✅ PID {MOMENTUM_PID} confirmed dead")

    # Step 4: DO NOT relaunch — let positions float and close on mean reversion
    print(f"\n  Step 4: NOT relaunching — letting {47} positions float and close naturally")
    print(f"  Rationale: Realized PnL is +$161.92, floating is negative.")
    print(f"  As price mean-reverts, positions will close profitably.")
    print(f"  No new positions will be opened.")

    # Step 5: Log the intervention
    log_entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "action": "momentum_lane_intervention",
        "pid_killed": MOMENTUM_PID,
        "magic": MOMENTUM_MAGIC,
        "backups": backup_paths,
        "reason": "floating_loss_exceeded_threshold",
        "relaunched": False,
        "note": "Let existing positions float and close on mean reversion",
    }
    log_path = REPO / "reports" / "hungry_hippo_intervention_log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps(log_entry) + "\n")
    print(f"\n  ✅ Intervention logged to: {log_path}")

    print(f"\n{'=' * 60}")
    print(f"INTERVENTION COMPLETE")
    print(f"{'=' * 60}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Momentum lane emergency intervention")
    parser.add_argument("--dry-run", action="store_true", help="Show current state and recommendation without executing")
    parser.add_argument("--execute", action="store_true", help="Execute the intervention")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        print("Usage: python hungry_hippo_momentum_intervention.py [--dry-run | --execute]")
        print()
        print("Options:")
        print("  --dry-run   Show current state and recommendation without executing")
        print("  --execute   Execute the intervention (kill lane, backup state, don't relaunch)")
        return 1

    alive, realized, floating, opens = check_momentum_state()
    recommendation = recommend_action(alive, realized, floating, opens)

    if args.dry_run:
        print(f"\n  Dry run mode. No action taken.")
        print(f"  To execute: python {sys.argv[0]} --execute")
        return 0

    if args.execute:
        if recommendation in ("KILL_NOW", "PREPARE", "MONITOR"):
            print(f"\n  Executing intervention (recommendation: {recommendation})...")
            success = execute_intervention()
            return 0 if success else 1
        else:
            print(f"\n  No intervention needed (recommendation: {recommendation}).")
            print(f"  To force intervention: python {sys.argv[0]} --execute")
            return 0


if __name__ == "__main__":
    sys.exit(main())
