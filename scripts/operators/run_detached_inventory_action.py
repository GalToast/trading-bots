#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "reports"
ACTION_BOARD_JSON = REPORTS / "detached_inventory_action_board.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def select_action_item(payload: dict[str, Any], item_name: str) -> dict[str, Any] | None:
    for item in list(payload.get("action_items") or []):
        if str(item.get("item") or "") == item_name:
            return item
    return None


def command_argv_for_item(item: dict[str, Any], *, apply: bool) -> list[str]:
    argv_field = "apply_argv" if apply else "dry_run_argv"
    argv = list(item.get(argv_field) or [])
    return [str(part) for part in argv if str(part)]


def refresh_commands(payload: dict[str, Any]) -> list[str]:
    commands = [str(command) for command in list(payload.get("refresh_commands") or []) if str(command)]
    return commands


def run_refresh(payload: dict[str, Any]) -> int:
    for command in refresh_commands(payload):
        print(f"refresh command={command}")
        result = subprocess.run(command, cwd=ROOT, shell=True)
        if result.returncode != 0:
            print(f"refresh_failed command={command} returncode={result.returncode}")
            return result.returncode
    return 0


def print_item_summary(item: dict[str, Any], *, apply: bool) -> None:
    mode = "apply" if apply else "dry_run"
    symbol_text = ", ".join(
        f"{symbol}:{count}"
        for symbol, count in dict(item.get("symbols") or {}).items()
    ) or "-"
    print(
        f"item={item.get('item') or '-'} mode={mode} positions={parse_int(item.get('positions'))} "
        f"expected_match_count={parse_int(item.get('expected_match_count'))} floating_pnl_usd={parse_float(item.get('floating_pnl_usd')):+.2f}"
    )
    print(f"  symbols={symbol_text} magic={parse_int(item.get('magic'))} owner_lane={item.get('owner_lane') or '-'}")
    print(f"  read={item.get('operator_read') or '-'}")


def print_post_refresh_status(payload: dict[str, Any], item_name: str) -> None:
    item = select_action_item(payload, item_name)
    if item is None:
        print("post_refresh_item_resolved=true")
        return
    print(
        f"post_refresh_item_resolved=false positions={parse_int(item.get('positions'))} "
        f"floating_pnl_usd={parse_float(item.get('floating_pnl_usd')):+.2f}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh detached-inventory boards and run the current dry-run/apply command for one named action item."
    )
    parser.add_argument("--item", required=True, help="Action item name from reports/detached_inventory_action_board.json")
    parser.add_argument("--apply", action="store_true", help="Run the current apply command instead of dry-run")
    parser.add_argument(
        "--skip-refresh",
        action="store_true",
        help="Skip the pre-run board refresh and use the current action board as-is",
    )
    parser.add_argument(
        "--skip-post-refresh",
        action="store_true",
        help="Skip the post-apply board refresh",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not ACTION_BOARD_JSON.exists():
        print(f"missing_action_board path={ACTION_BOARD_JSON}")
        return 1

    current_payload = load_json(ACTION_BOARD_JSON)
    if not args.skip_refresh:
        refresh_rc = run_refresh(current_payload)
        if refresh_rc != 0:
            return refresh_rc

    payload = load_json(ACTION_BOARD_JSON)
    item = select_action_item(payload, args.item)
    if item is None:
        print(f"unknown_action_item item={args.item}")
        return 1

    argv = command_argv_for_item(item, apply=bool(args.apply))
    if not argv:
        print(f"missing_command item={args.item}")
        return 1

    print_item_summary(item, apply=bool(args.apply))
    print(f"command={' '.join(argv)}")
    result = subprocess.run(argv, cwd=ROOT)

    if not args.apply or args.skip_post_refresh:
        return result.returncode

    post_payload = load_json(ACTION_BOARD_JSON)
    refresh_rc = run_refresh(post_payload)
    if refresh_rc != 0:
        return refresh_rc
    refreshed_payload = load_json(ACTION_BOARD_JSON)
    print_post_refresh_status(refreshed_payload, args.item)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
