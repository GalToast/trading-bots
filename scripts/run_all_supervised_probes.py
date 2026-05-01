#!/usr/bin/env python3
"""
Automated Supervised Probe Runner — Runs all 7 remaining coin probes in sequence.

Runs 3-cycle supervised probes on: NOM, RAVE, GHST, A8, BAL, CFG, IOTX
Each coin gets its own state/event files for clean isolation.

Usage:
    python scripts/run_all_supervised_probes.py [--cycles N] [--coins COIN1 COIN2 ...]
"""

import json
import os
import sys
import time
import subprocess
import argparse
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
REPORTS_DIR = SCRIPTS_DIR.parent / "reports"

COINS = {
    "NOM-USD": "fibonacci",
    "RAVE-USD": "supertrend",
    "GHST-USD": "fibonacci",
    "A8-USD": "momentum",
    "BAL-USD": "momentum",
    "CFG-USD": "momentum",
    "IOTX-USD": "momentum",
}


def run_probe(coin, strategy, cycles=3):
    """Run a supervised probe for a single coin."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    state_file = REPORTS_DIR / f"probe_{coin.replace('-USD', '')}_state_{timestamp}.json"
    events_file = REPORTS_DIR / f"probe_{coin.replace('-USD', '')}_events_{timestamp}.jsonl"

    print(f"\n  Running probe: {coin} ({strategy}) — {cycles} cycles")
    print(f"  State: {state_file}")
    print(f"  Events: {events_file}")

    # Build command
    cmd = [
        sys.executable, str(SCRIPTS_DIR / "multi_coin_isolated_runner.py"),
        "--total-cash", "48",
        "--coins", coin,
        "--max-cycles", str(cycles),
        "--state-path", str(state_file),
        "--event-path", str(events_file),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min timeout
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        if result.returncode == 0:
            print(f"  ✅ Probe PASSED for {coin}")
            return {
                "coin": coin,
                "strategy": strategy,
                "status": "pass",
                "cycles": cycles,
                "state_file": str(state_file),
                "events_file": str(events_file),
                "stdout": result.stdout[-500:] if result.stdout else "",
                "stderr": result.stderr[-200:] if result.stderr else "",
            }
        else:
            print(f"  ❌ Probe FAILED for {coin}")
            return {
                "coin": coin,
                "strategy": strategy,
                "status": "fail",
                "cycles": cycles,
                "returncode": result.returncode,
                "stderr": result.stderr[-500:] if result.stderr else "",
            }

    except subprocess.TimeoutExpired:
        print(f"  ⏰ Probe TIMED OUT for {coin}")
        return {
            "coin": coin,
            "strategy": strategy,
            "status": "timeout",
            "cycles": cycles,
        }
    except Exception as e:
        print(f"  💥 Probe ERROR for {coin}: {e}")
        return {
            "coin": coin,
            "strategy": strategy,
            "status": "error",
            "cycles": cycles,
            "error": str(e),
        }


def main():
    parser = argparse.ArgumentParser(description="Automated Supervised Probe Runner")
    parser.add_argument("--cycles", type=int, default=3, help="Cycles per coin (default: 3)")
    parser.add_argument("--coins", nargs="+", default=None, help="Specific coins to probe")
    args = parser.parse_args()

    coins_to_probe = COINS.copy()
    if args.coins:
        coins_to_probe = {c: coins_to_probe[c] for c in args.coins if c in coins_to_probe}

    start_time = time.time()
    print(f"\n{'='*70}")
    print(f"  AUTOMATED SUPERVISED PROBE RUNNER")
    print(f"  Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Coins to probe: {len(coins_to_probe)}")
    print(f"  Cycles per coin: {args.cycles}")
    print(f"{'='*70}")

    results = []
    for coin, strategy in coins_to_probe.items():
        result = run_probe(coin, strategy, args.cycles)
        results.append(result)
        time.sleep(2)  # Brief pause between probes

    # Summary
    elapsed = time.time() - start_time
    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] != "pass")

    print(f"\n{'='*70}")
    print(f"  PROBE RUN COMPLETE")
    print(f"  Elapsed: {elapsed:.0f}s")
    print(f"{'='*70}\n")

    print(f"  {'Coin':<15} {'Strategy':<15} {'Status':<10} {'Cycles':<8}")
    print(f"  {'-'*50}")
    for r in results:
        status_emoji = "✅" if r["status"] == "pass" else "❌"
        print(f"  {status_emoji} {r['coin']:<15} {r['strategy']:<15} {r['status']:<10} {r.get('cycles', '?'):<8}")

    print(f"\n  Summary: {passed}/{len(results)} passed, {failed}/{len(results)} failed")

    # Save results
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed, 1),
        "cycles_per_coin": args.cycles,
        "results": results,
        "summary": {
            "total_coins": len(results),
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / len(results) * 100, 1) if results else 0,
        }
    }

    out_path = REPORTS_DIR / "automated_supervised_probes.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n  Report saved: {out_path}\n")


if __name__ == "__main__":
    main()
