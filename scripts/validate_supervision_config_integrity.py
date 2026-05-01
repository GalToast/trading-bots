#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
WATCHDOG_GROUPS_PATH = ROOT / "configs" / "watchdog_groups.json"
FX_TICK_SHADOW_UNSUPPORTED_FLAGS = {
    "--symbol",
    "--timeframe",
    "--step",
    "--max-open-per-side",
}


def _restart_arg_value(restart_args: list[Any], flag: str) -> str:
    try:
        idx = restart_args.index(flag)
    except ValueError:
        return ""
    if idx + 1 >= len(restart_args):
        return ""
    return str(restart_args[idx + 1] or "").strip()


def _resolve_launcher_script(repo_root: Path, launcher_script: str) -> Path | None:
    script = str(launcher_script or "").strip()
    if not script or not script.endswith(".py"):
        return None
    path = Path(script)
    if not path.is_absolute():
        path = repo_root / path
    return path


def _script_uses_raw_mt5_initialize(script_path: Path) -> bool:
    try:
        text = script_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    if "mt5.initialize(" not in text:
        return False
    return "mt5_terminal_guard.initialize_mt5" not in text


def strict_load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(f"{path.name}: missing_file") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}: invalid_json line={exc.lineno} col={exc.colno}") from exc


def validate_registry(payload: Any, *, repo_root: Path = ROOT) -> tuple[list[str], dict[str, dict[str, Any]]]:
    errors: list[str] = []
    names: list[str] = []
    lane_rows: dict[str, dict[str, Any]] = {}
    if not isinstance(payload, dict):
        return ["penetration_lattice_runner_registry.json: root_not_object"], {}
    lanes = payload.get("lanes")
    if not isinstance(lanes, list):
        return ["penetration_lattice_runner_registry.json: lanes_not_list"], {}
    for index, lane in enumerate(lanes):
        prefix = f"penetration_lattice_runner_registry.json: lanes[{index}]"
        if not isinstance(lane, dict):
            errors.append(f"{prefix}: lane_not_object")
            continue
        name = str(lane.get("name") or "").strip()
        if not name:
            errors.append(f"{prefix}: missing_name")
        else:
            names.append(name)
            lane_rows[name] = lane
        state_path = str(lane.get("state_path") or "").strip()
        event_path = str(lane.get("event_path") or "").strip()
        if not state_path:
            errors.append(f"{prefix}: missing_state_path")
        process_match = lane.get("process_match_substrings")
        if not isinstance(process_match, list) or not any(str(item or "").strip() for item in process_match):
            errors.append(f"{prefix}: missing_process_match_substrings")
        restart_args = lane.get("restart_args")
        if not isinstance(restart_args, list) or not any(str(item or "").strip() for item in restart_args):
            errors.append(f"{prefix}: missing_restart_args")
            continue
        launcher_script = str(restart_args[0] or "").strip()
        launcher_script_path = _resolve_launcher_script(repo_root, launcher_script)
        restart_state_path = _restart_arg_value(restart_args, "--state-path")
        restart_event_path = _restart_arg_value(restart_args, "--event-path")
        if state_path and restart_state_path and restart_state_path != state_path:
            errors.append(
                f"{prefix}: restart_state_path_mismatch expected={state_path} actual={restart_state_path}"
            )
        if event_path and restart_event_path and restart_event_path != event_path:
            errors.append(
                f"{prefix}: restart_event_path_mismatch expected={event_path} actual={restart_event_path}"
            )
        if launcher_script == "scripts/live_penetration_lattice_tick_shadow.py":
            bad_flags = sorted({str(item).strip() for item in restart_args if str(item).strip() in FX_TICK_SHADOW_UNSUPPORTED_FLAGS})
            if bad_flags:
                errors.append(
                    f"{prefix}: restart_script_incompatible script={launcher_script} flags={','.join(bad_flags)}"
                )
        if launcher_script_path and launcher_script_path.exists() and _script_uses_raw_mt5_initialize(launcher_script_path):
            errors.append(f"{prefix}: restart_script_missing_mt5_guard script={launcher_script}")
    seen: set[str] = set()
    duplicates: set[str] = set()
    for name in names:
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    for name in sorted(duplicates):
        errors.append(f"penetration_lattice_runner_registry.json: duplicate_lane_name={name}")
    return errors, lane_rows


def validate_watchdog_groups(payload: Any, registry_rows: dict[str, dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["watchdog_groups.json: root_not_object"]
    groups = payload.get("groups")
    if not isinstance(groups, dict):
        return ["watchdog_groups.json: groups_not_object"]
    lane_owners: dict[str, list[str]] = {}
    for group_name, group_payload in groups.items():
        prefix = f"watchdog_groups.json: groups.{group_name}"
        if not isinstance(group_payload, dict):
            errors.append(f"{prefix}: group_not_object")
            continue
        lanes = group_payload.get("lanes")
        if not isinstance(lanes, list):
            errors.append(f"{prefix}: lanes_not_list")
            continue
        seen: set[str] = set()
        for lane_name in lanes:
            lane_text = str(lane_name or "").strip()
            if not lane_text:
                errors.append(f"{prefix}: blank_lane_name")
                continue
            if lane_text in seen:
                errors.append(f"{prefix}: duplicate_lane_name={lane_text}")
            seen.add(lane_text)
            lane_owners.setdefault(lane_text, []).append(str(group_name))
            registry_row = registry_rows.get(lane_text)
            if registry_row is None:
                errors.append(f"{prefix}: unknown_lane={lane_text}")
                continue
            lane_kind = str(registry_row.get("kind") or "").strip()
            if lane_kind == "infrastructure":
                errors.append(f"{prefix}: infrastructure_lane_not_allowed={lane_text}")
            if registry_row.get("enabled") is False:
                errors.append(f"{prefix}: disabled_lane_not_allowed={lane_text}")
    for lane_text, owners in sorted(lane_owners.items()):
        if len(owners) > 1:
            errors.append(
                "watchdog_groups.json: lane_in_multiple_groups="
                f"{lane_text} groups={','.join(sorted(owners))}"
            )
    return errors


def validate_configs(
    *,
    repo_root: Path = ROOT,
    registry_path: Path = REGISTRY_PATH,
    watchdog_groups_path: Path = WATCHDOG_GROUPS_PATH,
) -> dict[str, Any]:
    errors: list[str] = []
    try:
        registry_payload = strict_load_json(registry_path)
    except ValueError as exc:
        registry_payload = None
        errors.append(str(exc))
    try:
        watchdog_payload = strict_load_json(watchdog_groups_path)
    except ValueError as exc:
        watchdog_payload = None
        errors.append(str(exc))

    registry_rows: dict[str, dict[str, Any]] = {}
    if registry_payload is not None:
        registry_errors, registry_rows = validate_registry(registry_payload, repo_root=repo_root)
        errors.extend(registry_errors)
    if watchdog_payload is not None:
        errors.extend(validate_watchdog_groups(watchdog_payload, registry_rows))

    return {
        "ok": not errors,
        "registry_path": str(registry_path),
        "watchdog_groups_path": str(watchdog_groups_path),
        "errors": errors,
        "lane_count": len(registry_rows),
    }


def main() -> int:
    result = validate_configs()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
