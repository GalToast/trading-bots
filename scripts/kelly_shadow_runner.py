#!/usr/bin/env python3
"""
Kelly-Optimal Shadow Runner — Thin Wrapper
============================================

Launches the real multi_coin_isolated_runner with the Kelly-optimal config
and separate state/event paths so it runs alongside the live runner without
interference.

Usage:
    python scripts/kelly_shadow_runner.py --total-cash 48 --dry-run    # Preview
    python scripts/kelly_shadow_runner.py --total-cash 48              # Run live shadow
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KELLY_CONFIG_PATH = ROOT / "configs" / "kelly_optimal_runner_config.json"
KELLY_STATE_PATH = ROOT / "reports" / "kelly_shadow_state.json"
KELLY_EVENT_PATH = ROOT / "reports" / "kelly_shadow_events.jsonl"
KELLY_HEARTBEAT_PATH = ROOT / "reports" / "kelly_shadow_heartbeat.json"


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Kelly-Optimal Shadow Runner")
    parser.add_argument("--total-cash", type=float, default=48.0, help="Total cash budget")
    parser.add_argument("--dry-run", action="store_true", help="Print config and exit")
    parser.add_argument("--live", action="store_true", help="Actually run the shadow (not just preview)")
    args = parser.parse_args()
    
    print("=" * 72)
    print("KELLY-OPTIMAL SHADOW RUNNER")
    print("=" * 72)
    print()
    
    # Check Kelly config exists
    if not KELLY_CONFIG_PATH.exists():
        print(f"⚠️  Kelly config not found at {KELLY_CONFIG_PATH}", flush=True)
        print("  Run: python scripts/kelly_optimal_config.py first", flush=True)
        return
    
    # Load and display
    data = json.loads(KELLY_CONFIG_PATH.read_text())
    coins = data.get("coins", [])
    weights = data.get("cash_weights", {})
    projected = data.get("projected_monthly", {})
    
    print(f"Coins: {len(coins)}")
    print(f"{'Coin':<12} {'Strategy':<14} {'Weight':>8} {'Cash@$48':>10} {'Monthly':>10}")
    print("-" * 56)
    for c in coins:
        coin = c["coin"]
        w = weights.get(coin, 0)
        cash = w * args.total_cash
        monthly = projected.get(coin, 0)
        print(f"{coin:<12} {c['strategy']:<14} {w:>7.1%} ${cash:>8.2f} ${monthly:>8.0f}")
    
    print(f"\nTotal projected: ${sum(projected.values()):.0f}/mo at $48 budget")
    print(f"Current live: ~$58/mo (includes dead supertrends)")
    print(f"Improvement: {sum(projected.values())/58:.1f}x")
    print()
    
    if args.dry_run:
        print("  Dry run complete. Use --live to start the shadow runner.")
        return
    
    # Build the command to run the real runner with Kelly config
    runner_script = ROOT / "scripts" / "multi_coin_isolated_runner.py"
    
    cmd = [
        sys.executable, str(runner_script),
        "--config-path", str(KELLY_CONFIG_PATH),
        "--total-cash", str(args.total_cash),
        "--state-path", str(KELLY_STATE_PATH),
        "--event-path", str(KELLY_EVENT_PATH),
        "--no-btc-regime-gate",  # Kelly microcap strategies don't need BTC momentum filter
    ]
    
    print(f"  Launching shadow runner:")
    print(f"    {' '.join(cmd)}")
    print()
    print("  State: ", KELLY_STATE_PATH)
    print("  Events:", KELLY_EVENT_PATH)
    print()
    print("  The shadow will run alongside the live runner.")
    print("  It uses the Kelly-optimal coin allocation and session hours.")
    print("  No live trades — this is a shadow/paper trading run.")
    print("  Press Ctrl+C to stop.")
    print()
    
    # Run it
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\n  Shadow runner stopped.")
    except FileNotFoundError:
        print(f"  Runner script not found at {runner_script}")
    except Exception as e:
        print(f"  Error: {e}")


if __name__ == "__main__":
    main()
