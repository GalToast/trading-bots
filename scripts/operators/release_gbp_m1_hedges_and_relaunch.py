#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

EXPECTED_COUNT = 24

CLOSE_DRY_RUN = [
    sys.executable,
    "scripts/operators/mt5_close_filtered.py",
    "--magic",
    "941795",
    "--magic",
    "941797",
    "--symbol",
    "GBPUSD",
    "--expect-count",
    str(EXPECTED_COUNT),
]

CLOSE_APPLY = CLOSE_DRY_RUN + ["--apply"]

MICROHARVEST_RELAUNCH = [
    sys.executable,
    "scripts/launch_fx_m1_microharvest_live.py",
    "--symbol",
    "GBPUSD",
    "--apply",
    "--launch",
    "--fresh-start",
]

HYBRID_RELAUNCH = [
    sys.executable,
    "scripts/launch_fx_m1_hybrid_hedge_live.py",
    "--symbol",
    "GBPUSD",
    "--apply",
    "--launch",
    "--fresh-start",
]

REFRESH_COMMANDS = [
    [sys.executable, "scripts/build_execution_monitor_report.py"],
    [sys.executable, "scripts/live_lane_dashboard.py"],
    [sys.executable, "scripts/build_memory_live_lanes.py"],
]


def run_command(argv: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    print(f"command={' '.join(argv)}")
    result = subprocess.run(
        argv,
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, argv, result.stdout, result.stderr)
    return result


def output_mentions_market_closed(result: subprocess.CompletedProcess[str]) -> bool:
    text = f"{result.stdout}\n{result.stderr}".upper()
    return "TRADE_RETCODE_MARKET_CLOSED" in text or "MARKET CLOSED" in text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Release the stale GBP M1 hedge block and relaunch the GBP M1 seats once broker-flat."
    )
    parser.add_argument("--apply", action="store_true", help="Attempt the broker-side close and relaunch path.")
    parser.add_argument("--skip-refresh", action="store_true", help="Skip post-success report refresh commands.")
    parser.add_argument(
        "--retry-market-closed-seconds",
        type=float,
        default=0.0,
        help="When market-closed is returned, retry after this many seconds instead of exiting immediately.",
    )
    parser.add_argument(
        "--max-wait-seconds",
        type=float,
        default=None,
        help="Maximum total wait time when retrying market-closed responses; omit to wait indefinitely.",
    )
    args = parser.parse_args()
    if args.retry_market_closed_seconds < 0:
        parser.error("--retry-market-closed-seconds must be >= 0.")
    if args.max_wait_seconds is not None and args.max_wait_seconds < 0:
        parser.error("--max-wait-seconds must be >= 0.")
    if args.max_wait_seconds is not None and args.retry_market_closed_seconds <= 0:
        parser.error("--max-wait-seconds requires --retry-market-closed-seconds > 0.")
    return args


def should_retry_market_closed(
    started_at: float,
    *,
    retry_seconds: float,
    max_wait_seconds: float | None,
) -> tuple[bool, float]:
    if retry_seconds <= 0:
        return False, 0.0
    elapsed = time.monotonic() - started_at
    if max_wait_seconds is not None:
        remaining = max_wait_seconds - elapsed
        if remaining <= 0:
            return False, 0.0
        return True, min(retry_seconds, remaining)
    return True, retry_seconds


def main() -> int:
    args = parse_args()

    dry_result = run_command(CLOSE_DRY_RUN)
    if dry_result.returncode != 0:
        print("dry_run_failed=true")
        return dry_result.returncode

    if not args.apply:
        print("apply_required=false")
        print("next_commands=")
        print(f"  {' '.join(CLOSE_APPLY)}")
        print(f"  {' '.join(MICROHARVEST_RELAUNCH)}")
        print(f"  {' '.join(HYBRID_RELAUNCH)}")
        return 0

    started_at = time.monotonic()
    apply_result = None
    while True:
        apply_result = run_command(CLOSE_APPLY)
        if apply_result.returncode == 0:
            break
        if output_mentions_market_closed(apply_result):
            should_retry, sleep_seconds = should_retry_market_closed(
                started_at,
                retry_seconds=args.retry_market_closed_seconds,
                max_wait_seconds=args.max_wait_seconds,
            )
            if not should_retry:
                print("market_closed_blocked=true")
                if args.max_wait_seconds is not None:
                    print(f"wait_timeout_seconds={args.max_wait_seconds:g}")
                return 3
            print(f"market_closed_retrying=true sleep_seconds={sleep_seconds:g}")
            time.sleep(sleep_seconds)
            dry_result = run_command(CLOSE_DRY_RUN)
            if dry_result.returncode != 0:
                print("dry_run_failed=true")
                return dry_result.returncode
            continue
        print("close_apply_failed=true")
        return apply_result.returncode

    verify_result = run_command(CLOSE_DRY_RUN)
    if verify_result.returncode == 0 and f"matched_positions=0" not in verify_result.stdout:
        print("post_close_verification_failed=true")
        return 4

    for argv in (MICROHARVEST_RELAUNCH, HYBRID_RELAUNCH):
        launch_result = run_command(argv)
        if launch_result.returncode != 0:
            print("relaunch_failed=true")
            return launch_result.returncode

    if not args.skip_refresh:
        for argv in REFRESH_COMMANDS:
            refresh_result = run_command(argv)
            if refresh_result.returncode != 0:
                print("refresh_failed=true")
                return refresh_result.returncode

    print("release_and_relaunch_complete=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
