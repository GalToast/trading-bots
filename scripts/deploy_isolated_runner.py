#!/usr/bin/env python3
"""
Deploy Isolated Runner — One-command deployment with clean state.

Usage:
    # Deploy with default settings ($48 total, 9 coins, background)
    python scripts/deploy_isolated_runner.py

    # Deploy with custom bankroll
    python scripts/deploy_isolated_runner.py --total-cash 100

    # Deploy specific coins only
    python scripts/deploy_isolated_runner.py --coins NOM-USD GHST-USD RAVE-USD

    # Run in foreground (see output directly)
    python scripts/deploy_isolated_runner.py --foreground

    # Dry run (backfill only, no live entries)
    python scripts/deploy_isolated_runner.py --dry-run
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "multi_coin_isolated_state.json"
EVENT_PATH = ROOT / "reports" / "multi_coin_isolated_events.jsonl"
RUNNER_SCRIPT = ROOT / "scripts" / "multi_coin_isolated_runner.py"
HEALTH_SCRIPT = ROOT / "scripts" / "runner_health_check.py"
LOG_PATH = ROOT / "reports" / "isolated_runner_deploy.log"


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def windows_no_window_creationflags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def log(msg):
    print(f"[{utc_now()}] {msg}")


def archive_state():
    """Archive current state if it exists."""
    if STATE_PATH.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup = STATE_PATH.with_name(f"multi_coin_isolated_state_{timestamp}.json")
        STATE_PATH.rename(backup)
        log(f"  Archived state → {backup.name}")
        
        # Report what was archived
        try:
            state = json.loads(backup.read_text())
            equity = state.get("total_equity", 0)
            cycle = state.get("cycle", 0)
            ledgers = state.get("ledgers", {})
            active = [c for c, l in ledgers.items() if l.get("position") == "active"]
            if active:
                log(f"  WARNING: Had {len(active)} active position(s): {', '.join(active)}")
            log(f"  Archived: cycle {cycle}, equity ${equity:.2f}")
        except:
            pass
    else:
        log("  No existing state to archive")


def check_runner_alive():
    """Check if a runner process is already running."""
    # Check for Python processes running the isolated runner
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=windows_no_window_creationflags(),
        )
        # We can't easily check command line from tasklist, so just warn
        if "python.exe" in result.stdout:
            log("  WARNING: Python processes detected — check for existing runners")
            return True
    except:
        pass
    return False


def validate_setup(total_cash, coins):
    """Validate deployment parameters."""
    per_coin = total_cash / len(coins)
    issues = []
    
    if per_coin < 2.0:
        issues.append(f"Per-coin cash ${per_coin:.2f} < $2.00 minimum")
    if total_cash < len(coins) * 2:
        issues.append(f"Total cash ${total_cash:.2f} insufficient for {len(coins)} coins")
    if not RUNNER_SCRIPT.exists():
        issues.append(f"Runner script not found: {RUNNER_SCRIPT}")
    
    return issues


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Deploy isolated runner")
    parser.add_argument("--total-cash", type=float, default=48.0)
    parser.add_argument("--coins", nargs="+", default=None)
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-cycles", type=int, default=0)
    args = parser.parse_args()
    
    # Default coin set
    default_coins = [
        "NOM-USD", "GHST-USD", "RAVE-USD", "TRU-USD", "SUP-USD",
        "A8-USD", "BAL-USD", "CFG-USD", "IOTX-USD"
    ]
    coins = args.coins if args.coins else default_coins
    
    print("=" * 70)
    print("  ISOLATED RUNNER DEPLOYMENT")
    print("=" * 70)
    
    # Step 1: Validate
    log("Step 1: Validating setup...")
    issues = validate_setup(args.total_cash, coins)
    if issues:
        for issue in issues:
            log(f"  ERROR: {issue}")
        log("Deployment aborted. Fix issues and try again.")
        return 1
    log(f"  VALIDATED: ${args.total_cash:.2f} across {len(coins)} coins (${args.total_cash/len(coins):.2f}/coin)")
    
    # Step 2: Check for existing runners
    log("Step 2: Checking for existing runners...")
    check_runner_alive()
    
    # Step 3: Archive state
    log("Step 3: Archiving current state...")
    archive_state()
    
    # Step 4: Build command
    log("Step 4: Building deployment command...")
    cmd = [
        sys.executable, str(RUNNER_SCRIPT),
        "--total-cash", str(args.total_cash),
        "--coins", *coins,
    ]
    if args.dry_run:
        cmd.append("--dry-run")
        log("  Mode: DRY RUN (backfill only)")
    elif args.max_cycles > 0:
        cmd.extend(["--max-cycles", str(args.max_cycles)])
        log(f"  Mode: BOUNDED ({args.max_cycles} cycles)")
    else:
        log("  Mode: LIVE (unlimited)")
    
    cmd_str = " ".join(cmd)
    log(f"  Command: {cmd_str}")
    
    # Step 5: Deploy
    log("Step 5: Launching runner...")
    
    if args.foreground:
        log("  Running in foreground (Ctrl+C to stop)...")
        log("=" * 70)
        try:
            subprocess.run(cmd, timeout=None, creationflags=windows_no_window_creationflags())
        except KeyboardInterrupt:
            log("\n  Runner stopped by user")
        return 0
    else:
        # Background deployment
        log_path = LOG_PATH
        log(f"  Running in background...")
        log(f"  Log file: {log_path}")
        log(f"  Health check: python scripts/runner_health_check.py")
        
        with open(log_path, "w") as f:
            f.write(f"Deployment started at {utc_now()}\n")
            f.write(f"Command: {' '.join(cmd)}\n\n")
            f.flush()
            
            process = subprocess.Popen(
                cmd,
                stdout=f,
                stderr=subprocess.STDOUT,
                cwd=str(ROOT),
                creationflags=windows_no_window_creationflags(),
            )
        
        log(f"  Runner PID: {process.pid}")
        
        # Wait a moment and check if it started
        time.sleep(5)
        
        # Check if process is still running
        if process.poll() is None:
            log(f"  Runner started successfully (PID {process.pid})")
            log(f"  Monitor with: python scripts/runner_health_check.py --watch")
        else:
            # Process exited - check if it was a clean exit (dry-run or max-cycles)
            exit_code = process.poll()
            if exit_code == 0:
                log(f"  Runner completed successfully (exit code 0)")
                log(f"  Check log for details: {log_path}")
            else:
                log(f"  ERROR: Runner exited with code {exit_code} — check log: {log_path}")
            try:
                with open(log_path) as f:
                    content = f.read()
                    log(f"  Last 500 chars of log:")
                    log(content[-500:])
            except:
                pass
            return 1
        
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
