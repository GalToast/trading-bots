#!/usr/bin/env python3
"""Launch ALL spot crypto shadow lanes with one command."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

def launch_lane(name: str, state_path: Path, events_path: Path, extra_args: list[str] = None):
    cmd = [
        "python",
        str(SCRIPTS / "live_kraken_spot_frontier_maker_machinegun_shadow.py"),
        "--state-path", str(state_path),
        "--events-path", str(events_path),
        "--no-mfe-stop-min-age-seconds", "30",
        "--min-in-position-spread-bps", "50",
    ]
    if extra_args:
        cmd.extend(extra_args)
    
    print(f"Launching {name}...")
    print(f"  Command: {' '.join(cmd[:6])}...")
    
    # For now, just print the command
    # In production, you'd use subprocess.Popen
    return cmd

def main():
    print("=" * 60)
    print("SPOT CRYPTO LANE LAUNCHER")
    print("=" * 60)
    
    lanes = [
        {
            "name": "Cooldown (20-30s, max=1)",
            "state": ROOT / "reports" / "kraken_spot_maker_machinegun_shadow_state.json",
            "events": ROOT / "reports" / "kraken_spot_maker_machinegun_shadow_events.jsonl",
            "args": ["--max-quote-usd", "8.0", "--max-loss-pct", "3.0"],
        },
        {
            "name": "Combined (20-30s, max=3)",
            "state": ROOT / "reports" / "kraken_spot_maker_machinegun_combined_state.json",
            "events": ROOT / "reports" / "kraken_spot_maker_machinegun_combined_events.jsonl",
            "args": ["--max-quote-usd", "8.0", "--max-loss-pct", "3.0", "--systemic-max-positions", "3"],
        },
        {
            "name": "Size12 ($12/pos, 20-30s)",
            "state": ROOT / "reports" / "kraken_spot_maker_machinegun_cooldown_size12_ab_state.json",
            "events": ROOT / "reports" / "kraken_spot_maker_machinegun_cooldown_size12_ab_events.jsonl",
            "args": ["--max-quote-usd", "12.0", "--max-loss-pct", "3.0"],
        },
        {
            "name": "Size40 ($40/pos, 20-30s) [NEW]",
            "state": ROOT / "reports" / "kraken_spot_maker_machinegun_size40_shadow_state.json",
            "events": ROOT / "reports" / "kraken_spot_maker_machinegun_size40_events.jsonl",
            "args": ["--max-quote-usd", "40.0", "--max-loss-pct", "1.5", "--systemic-max-positions", "3"],
        },
    ]
    
    for lane in lanes:
        state_path = lane["state"]
        if not state_path.exists():
            print(f"\n[WARN]  {lane['name']}: State file not found: {state_path}")
            continue
        
        cmd = launch_lane(lane["name"], state_path, lane["events"], lane["args"])
        print(f"  Full command: {' '.join(cmd)}\n")
    
    print("=" * 60)
    print("To launch: Run each command in a separate terminal/session")
    print("=" * 60)

if __name__ == "__main__":
    main()
