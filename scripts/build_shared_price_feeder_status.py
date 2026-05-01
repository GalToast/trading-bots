#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORT_JSON = ROOT / "reports" / "shared_price_feeder_status.json"
REPORT_MD = ROOT / "reports" / "shared_price_feeder_status.md"
HEARTBEAT_PATH = ROOT / "reports" / "shared_price_feeder_heartbeat.json"
PRICE_CACHE_PATH = ROOT / "reports" / "shared_price_cache.json"
TICK_CACHE_PATH = ROOT / "reports" / "shared_tick_cache.json"
LAUNCHER_STATE_PATH = ROOT / "reports" / "watchdog" / "shared_price_feeder_launcher_state.json"
PRICE_FEEDER_WATCHDOG_STATE_PATH = ROOT / "reports" / "price_feeder_watchdog_state.json"
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
WATCHDOG_GROUPS_PATH = ROOT / "configs" / "watchdog_groups.json"
FEEDER_GROUP_PREFIX = "feeder_"
UNGROUPED_SHARED_GROUP = "ungrouped_shared"


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def parse_iso(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def age_seconds_from_iso(value: str | None, *, now: datetime) -> float | None:
    dt = parse_iso(value)
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds())


def summarize_price_cache(cache: dict[str, Any] | None, *, now: datetime) -> dict[str, Any]:
    if not isinstance(cache, dict) or not cache:
        return {"symbols": 0, "fresh_symbols": 0, "newest_age_seconds": None, "oldest_age_seconds": None}
    ages: list[float] = []
    for payload in cache.values():
        if not isinstance(payload, dict):
            continue
        age_seconds = age_seconds_from_iso(payload.get("ts"), now=now)
        if age_seconds is not None:
            ages.append(age_seconds)
    if not ages:
        return {"symbols": len(cache), "fresh_symbols": 0, "newest_age_seconds": None, "oldest_age_seconds": None}
    return {
        "symbols": len(cache),
        "fresh_symbols": sum(1 for age in ages if age <= 1.0),
        "newest_age_seconds": min(ages),
        "oldest_age_seconds": max(ages),
    }


def summarize_tick_cache(cache: dict[str, Any] | None, *, now: datetime) -> dict[str, Any]:
    if not isinstance(cache, dict) or not cache:
        return {"symbols": 0, "symbols_with_recent_ticks": 0, "total_ticks": 0, "newest_age_seconds": None, "oldest_age_seconds": None}
    latest_ages: list[float] = []
    total_ticks = 0
    for ticks in cache.values():
        if not isinstance(ticks, list) or not ticks:
            continue
        total_ticks += len(ticks)
        latest_tick = ticks[-1]
        tick_msc = int((latest_tick or {}).get("time_msc", 0) or 0)
        if tick_msc <= 0:
            continue
        latest_dt = datetime.fromtimestamp(tick_msc / 1000.0, tz=timezone.utc)
        latest_ages.append(max(0.0, (now - latest_dt).total_seconds()))
    if not latest_ages:
        return {"symbols": len(cache), "symbols_with_recent_ticks": 0, "total_ticks": total_ticks, "newest_age_seconds": None, "oldest_age_seconds": None}
    return {
        "symbols": len(cache),
        "symbols_with_recent_ticks": sum(1 for age in latest_ages if age <= 1.0),
        "total_ticks": total_ticks,
        "newest_age_seconds": min(latest_ages),
        "oldest_age_seconds": max(latest_ages),
    }


def summarize_launcher_state(launcher_state: dict[str, Any] | None, *, now: datetime) -> dict[str, Any]:
    if not isinstance(launcher_state, dict):
        return {
            "present": False,
            "status": "",
            "observed_at": "",
            "observed_age_seconds": None,
            "wrapper_pid": 0,
            "child_pid": 0,
            "launch_mode": "",
            "auto_restart_requested": False,
            "auto_restart_reason": "",
        }

    observed_at = ""
    for field in ("launcher_finished_at", "checked_at", "launcher_started_at", "launched_at"):
        candidate = str(launcher_state.get(field) or "").strip()
        if candidate:
            observed_at = candidate
            break

    return {
        "present": True,
        "status": str(launcher_state.get("status") or ""),
        "observed_at": observed_at,
        "observed_age_seconds": age_seconds_from_iso(observed_at, now=now),
        "wrapper_pid": int(launcher_state.get("wrapper_pid") or 0),
        "child_pid": int(launcher_state.get("child_pid") or launcher_state.get("pid") or 0),
        "launch_mode": str(launcher_state.get("launch_mode") or ""),
        "auto_restart_requested": bool(launcher_state.get("auto_restart_requested", False)),
        "auto_restart_reason": str(launcher_state.get("auto_restart_reason") or ""),
    }


def summarize_watchdog_state(watchdog_state: dict[str, Any] | None, *, now: datetime) -> dict[str, Any]:
    if not isinstance(watchdog_state, dict):
        return {
            "present": False,
            "status": "",
            "observed_at": "",
            "observed_age_seconds": None,
            "feeder_pid": 0,
            "feeder_alive": False,
            "last_restart": "",
            "consecutive_failures": 0,
        }

    observed_at = str(watchdog_state.get("watchdog_updated_at") or "").strip()
    observed_age_seconds = age_seconds_from_iso(observed_at, now=now)
    raw_status = str(watchdog_state.get("watchdog_status") or watchdog_state.get("status") or "")
    status = raw_status
    if observed_age_seconds is not None and observed_age_seconds > 30.0 and raw_status:
        status = f"stale_{raw_status}"
    return {
        "present": True,
        "status": status,
        "observed_at": observed_at,
        "observed_age_seconds": observed_age_seconds,
        "feeder_pid": int(watchdog_state.get("feeder_pid") or 0),
        "feeder_alive": bool(watchdog_state.get("feeder_alive", False)),
        "last_restart": str(watchdog_state.get("last_restart") or ""),
        "consecutive_failures": int(watchdog_state.get("consecutive_failures") or 0),
    }


def summarize_supervisor(
    *,
    launcher: dict[str, Any],
    watchdog: dict[str, Any],
    heartbeat: dict[str, Any] | None,
    heartbeat_age_seconds: float | None,
) -> dict[str, Any]:
    heartbeat_pid = int(((heartbeat or {}).get("feeder_pid")) or 0)

    launcher_pid = int(launcher.get("child_pid") or 0)
    launcher_active = bool(
        launcher.get("present")
        and str(launcher.get("status") or "") == "running"
        and (heartbeat_pid <= 0 or launcher_pid <= 0 or launcher_pid == heartbeat_pid)
    )

    watchdog_age = watchdog.get("observed_age_seconds")
    watchdog_fresh = watchdog_age is not None and float(watchdog_age) <= 30.0
    watchdog_pid = int(watchdog.get("feeder_pid") or 0)
    watchdog_active = bool(
        watchdog.get("present")
        and str(watchdog.get("status") or "") == "ok"
        and bool(watchdog.get("feeder_alive"))
        and watchdog_fresh
        and (heartbeat_pid <= 0 or watchdog_pid <= 0 or watchdog_pid == heartbeat_pid)
    )

    heartbeat_fresh = heartbeat_age_seconds is not None and heartbeat_age_seconds <= 2.0
    if launcher_active and watchdog_active:
        status = "dual_supervision_visible"
        if heartbeat_pid > 0 and launcher_pid > 0 and watchdog_pid > 0 and launcher_pid != watchdog_pid:
            status = "dual_supervisor_conflict"
        return {
            "mode": "dual",
            "status": status,
            "heartbeat_pid": heartbeat_pid,
            "observed_at": watchdog.get("observed_at") or launcher.get("observed_at") or "",
            "observed_age_seconds": watchdog.get("observed_age_seconds"),
        }
    if watchdog_active:
        status = "watchdog_ok"
        if str(launcher.get("status") or "") not in ("", "running"):
            status = "watchdog_ok_wrapper_failed"
        return {
            "mode": "price_feeder_watchdog",
            "status": status,
            "heartbeat_pid": heartbeat_pid,
            "observed_at": watchdog.get("observed_at") or "",
            "observed_age_seconds": watchdog.get("observed_age_seconds"),
        }
    if launcher_active:
        return {
            "mode": "wrapper",
            "status": "wrapper_running",
            "heartbeat_pid": heartbeat_pid,
            "observed_at": launcher.get("observed_at") or "",
            "observed_age_seconds": launcher.get("observed_age_seconds"),
        }
    if heartbeat_fresh:
        return {
            "mode": "unknown",
            "status": "heartbeat_fresh_supervisor_unobserved",
            "heartbeat_pid": heartbeat_pid,
            "observed_at": "",
            "observed_age_seconds": None,
        }
    return {
        "mode": "none",
        "status": "no_active_supervisor_visible",
        "heartbeat_pid": heartbeat_pid,
        "observed_at": "",
        "observed_age_seconds": None,
    }


def _lane_shared_price_max_age_ms(restart_args: list[Any]) -> int:
    args = [str(item) for item in (restart_args or [])]
    for index, item in enumerate(args):
        if item == "--shared-price-max-age-ms" and index + 1 < len(args):
            try:
                return max(0, int(args[index + 1]))
            except Exception:
                return 0
    return 0


def classify_runtime_mode(row: dict[str, Any]) -> str:
    tick_history_source = str(row.get("tick_history_source_last") or "")
    latest_tick_source = str(row.get("latest_tick_source_last") or "")
    latest_tick_append_source = str(row.get("latest_tick_append_source_last") or "")
    if tick_history_source == "shared_tick_cache" or latest_tick_source == "shared_price_cache":
        return "shared_cache_active"
    if tick_history_source == "copy_ticks_range" and latest_tick_source == "symbol_info_tick":
        return "direct_mt5_fallback"
    if tick_history_source == "copy_ticks_range":
        return "direct_tick_history_only"
    if latest_tick_source == "symbol_info_tick" or latest_tick_append_source == "symbol_info_tick":
        return "direct_latest_tick_only"
    if not tick_history_source and not latest_tick_source and not latest_tick_append_source:
        return "no_runtime_source_reported"
    return "mixed_or_unknown"


def read_canary_runtime(state_path: str) -> dict[str, Any]:
    payload = load_json(ROOT / str(state_path))
    if not isinstance(payload, dict):
        return {}
    runner = payload.get("runner") or {}
    if not isinstance(runner, dict):
        return {}
    tick_counts = runner.get("tick_history_source_counts") or {}
    latest_counts = runner.get("latest_tick_source_counts") or {}
    append_counts = runner.get("latest_tick_append_source_counts") or {}
    return {
        "runner_pid": int(runner.get("pid", 0) or 0),
        "runner_started_at": str(runner.get("started_at") or ""),
        "heartbeat_at": str(runner.get("heartbeat_at") or ""),
        "tick_history_source_last": str(runner.get("tick_history_source_last") or ""),
        "tick_history_source_counts": tick_counts if isinstance(tick_counts, dict) else {},
        "latest_tick_source_last": str(runner.get("latest_tick_source_last") or ""),
        "latest_tick_source_counts": latest_counts if isinstance(latest_counts, dict) else {},
        "latest_tick_append_source_last": str(runner.get("latest_tick_append_source_last") or ""),
        "latest_tick_append_source_counts": append_counts if isinstance(append_counts, dict) else {},
    }


def build_group_rows(registry_rows: dict[str, dict[str, Any]], lane_names: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lane_name in lane_names:
        row = registry_rows.get(lane_name) or {}
        shared_ms = _lane_shared_price_max_age_ms(list(row.get("restart_args") or []))
        row_payload = {
            "lane": lane_name,
            "kind": str(row.get("kind") or ""),
            "enabled": bool(row.get("enabled", True)),
            "shared_price_max_age_ms": shared_ms,
            "shared_price_enabled": shared_ms > 0,
            "state_path": str(row.get("state_path") or ""),
            "event_path": str(row.get("event_path") or ""),
            **read_canary_runtime(str(row.get("state_path") or "")),
        }
        row_payload["runtime_mode"] = classify_runtime_mode(row_payload)
        rows.append(row_payload)
    return rows


def build_feeder_groups(registry: list[dict[str, Any]], watchdog_groups: dict[str, Any]) -> list[dict[str, Any]]:
    groups_payload = ((watchdog_groups or {}).get("groups") or {})
    registry_rows = {str(row.get("name") or ""): row for row in (registry or [])}
    groups: list[dict[str, Any]] = []
    for group_name in sorted(str(name) for name in groups_payload.keys() if str(name).startswith(FEEDER_GROUP_PREFIX)):
        group = groups_payload.get(group_name) or {}
        lane_names = [str(name) for name in (group.get("lanes") or []) if str(name or "").strip()]
        rows = build_group_rows(registry_rows, lane_names)
        enabled_rows = [row for row in rows if bool(row.get("enabled"))]
        active_shared_rows = [row for row in enabled_rows if bool(row.get("shared_price_enabled"))]
        enabled_nonshared_rows = [row for row in enabled_rows if not bool(row.get("shared_price_enabled"))]
        disabled_rows = [row for row in rows if not bool(row.get("enabled"))]
        active_shared_noncache_rows = [
            row for row in active_shared_rows if str(row.get("runtime_mode") or "") != "shared_cache_active"
        ]
        groups.append(
            {
                "group": group_name,
                "label": str(group.get("label") or group_name),
                "rows": rows,
                "active_shared_rows": len(active_shared_rows),
                "enabled_nonshared_rows": len(enabled_nonshared_rows),
                "disabled_rows": len(disabled_rows),
                "active_shared_noncache_rows": len(active_shared_noncache_rows),
                "all_active_shared_using_cache": bool(active_shared_rows) and not bool(active_shared_noncache_rows),
            }
        )
    return groups


def build_shared_runtime_groups(registry: list[dict[str, Any]], watchdog_groups: dict[str, Any]) -> list[dict[str, Any]]:
    groups_payload = ((watchdog_groups or {}).get("groups") or {})
    registry_rows = {str(row.get("name") or ""): row for row in (registry or [])}
    lane_to_group: dict[str, str] = {}
    for group_name, group_payload in groups_payload.items():
        group = group_payload if isinstance(group_payload, dict) else {}
        for lane_name in group.get("lanes") or []:
            lane_text = str(lane_name or "").strip()
            if lane_text and lane_text not in lane_to_group:
                lane_to_group[lane_text] = str(group_name)

    grouped_lane_names: dict[str, list[str]] = {}
    for lane_name, row in registry_rows.items():
        if not bool(row.get("enabled", True)):
            continue
        shared_ms = _lane_shared_price_max_age_ms(list(row.get("restart_args") or []))
        if shared_ms <= 0:
            continue
        group_name = lane_to_group.get(lane_name, UNGROUPED_SHARED_GROUP)
        grouped_lane_names.setdefault(group_name, []).append(lane_name)

    groups: list[dict[str, Any]] = []
    for group_name in sorted(grouped_lane_names.keys()):
        group = groups_payload.get(group_name) or {}
        lane_names = sorted(grouped_lane_names.get(group_name) or [])
        rows = build_group_rows(registry_rows, lane_names)
        groups.append(
            {
                "group": group_name,
                "label": str((group if isinstance(group, dict) else {}).get("label") or group_name),
                "rows": rows,
            }
        )
    return groups


def summarize_runtime_modes(groups: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for group in groups or []:
        for row in group.get("rows") or []:
            mode = str(row.get("runtime_mode") or "")
            counts[mode] = counts.get(mode, 0) + 1
    return counts


def build_status(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    heartbeat = load_json(HEARTBEAT_PATH)
    price_cache = load_json(PRICE_CACHE_PATH)
    tick_cache = load_json(TICK_CACHE_PATH)
    launcher_state = load_json(LAUNCHER_STATE_PATH)
    price_feeder_watchdog_state = load_json(PRICE_FEEDER_WATCHDOG_STATE_PATH)
    registry_payload = load_json(REGISTRY_PATH) or {}
    registry = list((registry_payload.get("lanes") or [])) if isinstance(registry_payload, dict) else list(registry_payload or [])
    watchdog_groups = load_json(WATCHDOG_GROUPS_PATH) or {}
    heartbeat_age_seconds = None
    if isinstance(heartbeat, dict):
        heartbeat_age_seconds = age_seconds_from_iso(heartbeat.get("heartbeat_at"), now=now)
    price_summary = summarize_price_cache(price_cache, now=now)
    tick_summary = summarize_tick_cache(tick_cache, now=now)
    launcher_summary = summarize_launcher_state(launcher_state, now=now)
    watchdog_summary = summarize_watchdog_state(price_feeder_watchdog_state, now=now)
    supervisor_summary = summarize_supervisor(
        launcher=launcher_summary,
        watchdog=watchdog_summary,
        heartbeat=heartbeat if isinstance(heartbeat, dict) else None,
        heartbeat_age_seconds=heartbeat_age_seconds,
    )
    feeder_groups = build_feeder_groups(registry, watchdog_groups)
    shared_runtime_groups = build_shared_runtime_groups(registry, watchdog_groups)
    shared_runtime_mode_counts = summarize_runtime_modes(shared_runtime_groups)
    first_group = feeder_groups[0] if feeder_groups else {}
    canary_rows = list(first_group.get("rows") or [])
    active_feeder_groups = [group for group in feeder_groups if int(group.get("active_shared_rows", 0) or 0) > 0]
    all_active_canaries_using_cache = bool(active_feeder_groups) and all(
        bool(group.get("all_active_shared_using_cache")) for group in active_feeder_groups
    )
    feeder_enabled_nonshared_rows = sum(int(group.get("enabled_nonshared_rows", 0) or 0) for group in feeder_groups)
    feeder_disabled_rows = sum(int(group.get("disabled_rows", 0) or 0) for group in feeder_groups)
    active_feeder_shared_lane_count = sum(int(group.get("active_shared_rows", 0) or 0) for group in feeder_groups)
    active_feeder_noncache_rows = sum(int(group.get("active_shared_noncache_rows", 0) or 0) for group in feeder_groups)
    heartbeat_ok = heartbeat_age_seconds is not None and heartbeat_age_seconds <= 2.0
    direct_fallback_count = int(shared_runtime_mode_counts.get("direct_mt5_fallback", 0) or 0)
    shared_cache_active_count = int(shared_runtime_mode_counts.get("shared_cache_active", 0) or 0)
    active_shared_runtime_lane_count = shared_cache_active_count + direct_fallback_count
    if heartbeat_ok and (
        (active_feeder_shared_lane_count > 0 and active_feeder_noncache_rows == 0)
        or (active_shared_runtime_lane_count > 0 and direct_fallback_count == 0 and shared_cache_active_count > 0)
    ):
        status = "ok"
    elif active_feeder_noncache_rows > 0 or direct_fallback_count > 0:
        status = "degraded_fallback"
    else:
        status = "needs_attention"
    return {
        "generated_at": now.isoformat(),
        "status": status,
        "heartbeat_path": str(HEARTBEAT_PATH),
        "heartbeat_present": isinstance(heartbeat, dict),
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "heartbeat": heartbeat if isinstance(heartbeat, dict) else {},
        "launcher": launcher_summary,
        "watchdog": watchdog_summary,
        "supervisor": supervisor_summary,
        "price_cache": price_summary,
        "tick_cache": tick_summary,
        "feeder_groups": feeder_groups,
        "feeder_group_count": len(feeder_groups),
        "canary_group": str(first_group.get("group") or ""),
        "canary_rows": canary_rows,
        "all_active_canaries_using_cache": all_active_canaries_using_cache,
        "active_feeder_shared_lane_count": active_feeder_shared_lane_count,
        "active_feeder_noncache_rows": active_feeder_noncache_rows,
        "feeder_enabled_nonshared_rows": feeder_enabled_nonshared_rows,
        "feeder_disabled_rows": feeder_disabled_rows,
        "shared_runtime_groups": shared_runtime_groups,
        "shared_runtime_group_count": len(shared_runtime_groups),
        "shared_enabled_lane_count": sum(len(group.get("rows") or []) for group in shared_runtime_groups),
        "active_shared_runtime_lane_count": active_shared_runtime_lane_count,
        "shared_runtime_mode_counts": shared_runtime_mode_counts,
        "shared_cache_active_lane_count": shared_cache_active_count,
        "direct_mt5_fallback_lane_count": direct_fallback_count,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    heartbeat_age = payload.get("heartbeat_age_seconds")
    heartbeat_text = "missing"
    if heartbeat_age is not None:
        heartbeat_text = f"{float(heartbeat_age):.1f}s"
    launcher = payload.get("launcher") or {}
    launcher_age = launcher.get("observed_age_seconds")
    launcher_age_text = "missing"
    if launcher_age is not None:
        launcher_age_text = f"{float(launcher_age):.1f}s"
    watchdog = payload.get("watchdog") or {}
    watchdog_age = watchdog.get("observed_age_seconds")
    watchdog_age_text = "missing"
    if watchdog_age is not None:
        watchdog_age_text = f"{float(watchdog_age):.1f}s"
    supervisor = payload.get("supervisor") or {}
    supervisor_age = supervisor.get("observed_age_seconds")
    supervisor_age_text = "missing"
    if supervisor_age is not None:
        supervisor_age_text = f"{float(supervisor_age):.1f}s"
    price_cache = payload.get("price_cache") or {}
    tick_cache = payload.get("tick_cache") or {}
    runtime_counts = payload.get("shared_runtime_mode_counts") or {}
    lines = [
        "# Shared Price Feeder Status",
        "",
        f"- generated_at: `{payload.get('generated_at', '')}`",
        f"- status: `{payload.get('status', '')}`",
        f"- heartbeat_age: `{heartbeat_text}`",
        f"- supervisor_mode: `{str(supervisor.get('mode') or 'missing')}`",
        f"- supervisor_status: `{str(supervisor.get('status') or 'missing')}`",
        f"- supervisor_observed_age: `{supervisor_age_text}`",
        f"- launcher_status: `{str(launcher.get('status') or 'missing')}`",
        f"- launcher_observed_age: `{launcher_age_text}`",
        f"- watchdog_status: `{str(watchdog.get('status') or 'missing')}`",
        f"- watchdog_observed_age: `{watchdog_age_text}`",
        f"- price_cache_symbols: `{int(price_cache.get('symbols', 0) or 0)}`",
        f"- price_cache_fresh_symbols: `{int(price_cache.get('fresh_symbols', 0) or 0)}`",
        f"- tick_cache_symbols: `{int(tick_cache.get('symbols', 0) or 0)}`",
        f"- tick_cache_recent_symbols: `{int(tick_cache.get('symbols_with_recent_ticks', 0) or 0)}`",
        f"- tick_cache_total_ticks: `{int(tick_cache.get('total_ticks', 0) or 0)}`",
        f"- feeder_groups: `{int(payload.get('feeder_group_count', 0) or 0)}`",
        f"- feeder_active_shared_lanes: `{int(payload.get('active_feeder_shared_lane_count', 0) or 0)}`",
        f"- feeder_active_shared_using_cache: `{bool(payload.get('all_active_canaries_using_cache'))}`",
        f"- feeder_nonshared_rows: `{int(payload.get('feeder_enabled_nonshared_rows', 0) or 0)}`",
        f"- feeder_disabled_rows: `{int(payload.get('feeder_disabled_rows', 0) or 0)}`",
        f"- shared_enabled_groups: `{int(payload.get('shared_runtime_group_count', 0) or 0)}`",
        f"- shared_enabled_lanes: `{int(payload.get('shared_enabled_lane_count', 0) or 0)}`",
        f"- active_shared_runtime_lanes: `{int(payload.get('active_shared_runtime_lane_count', 0) or 0)}`",
        f"- shared_cache_active_lanes: `{int(runtime_counts.get('shared_cache_active', 0) or 0)}`",
        f"- direct_mt5_fallback_lanes: `{int(runtime_counts.get('direct_mt5_fallback', 0) or 0)}`",
        "",
    ]
    shared_runtime_groups = payload.get("shared_runtime_groups") or []
    if shared_runtime_groups:
        lines.extend(
            [
                "| Group | Lane | Max Age ms | Runtime Mode | Tick History Source | Latest Tick Source | Append Source |",
                "| --- | --- | ---: | --- | --- | --- | --- |",
            ]
        )
        for group in shared_runtime_groups:
            group_name = str(group.get("group") or "")
            for row in group.get("rows") or []:
                lines.append(
                    f"| `{group_name}` | `{row['lane']}` | `{int(row['shared_price_max_age_ms'])}` | `{row.get('runtime_mode', '') or '-'}` | `{row.get('tick_history_source_last', '') or '-'}` | `{row.get('latest_tick_source_last', '') or '-'}` | `{row.get('latest_tick_append_source_last', '') or '-'}` |"
                )
        lines.append("")
        if int(runtime_counts.get("direct_mt5_fallback", 0) or 0) > 0:
            lines.append("Interpretation:")
            lines.append("`direct_mt5_fallback` means the lane is still getting live MT5 data via `copy_ticks_range(...)` + `symbol_info_tick()` even though the shared cache path was stale or uncovered. `tick_history_fallback` events alone are not dead-tick proof.")
            lines.append("")
        if int(payload.get("feeder_enabled_nonshared_rows", 0) or 0) > 0 or int(payload.get("feeder_disabled_rows", 0) or 0) > 0:
            lines.append("Feeder-group caveat:")
            lines.append("`feeder_nonshared_rows` and `feeder_disabled_rows` are shown separately and do not by themselves mean the active shared canary pack is unhealthy.")
        lines.append("")
        lines.append("Operator launch path:")
        lines.append("`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/operators/start_shared_price_feeder.ps1`")
        if str(supervisor.get("mode") or "") == "price_feeder_watchdog":
            lines.append("Active supervisor:")
            lines.append("`reports/price_feeder_watchdog_state.json` currently owns the live feeder heartbeat/pid continuity.")
        if launcher.get("present"):
            lines.append("Launcher state:")
            lines.append("`reports/watchdog/shared_price_feeder_launcher_state.json` records the current wrapper child/attach posture and bounded auto-restart outcome.")
        for group in payload.get("feeder_groups") or []:
            lines.append(
                f"`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/operators/start_watchdog_group_loop.ps1 -GroupName {group.get('group', '')}`"
            )
    else:
        lines.extend(
            [
                "| Group | Lane | Max Age ms | Runtime Mode | Tick History Source | Latest Tick Source | Append Source |",
                "| --- | --- | ---: | --- | --- | --- | --- |",
                "| _none_ | _none_ | | | | | |",
                "",
                "Operator launch path:",
                "`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/operators/start_shared_price_feeder.ps1`",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_status()
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    REPORT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(f"wrote {REPORT_JSON}")
    print(f"wrote {REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
