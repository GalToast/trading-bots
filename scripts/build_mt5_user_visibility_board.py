#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import build_live_magic_scope_audit


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"

LIVE_SCOPE_PATH = REPORTS / "live_magic_scope_audit.json"
EXECUTION_MONITOR_PATH = REPORTS / "execution_monitor_report.json"
SHADOW_DEPLOY_PATH = CONFIGS / "hungry_hippo_gbpusd_deploy.json"
GBPUSD_LIVE_NAMED_CONFIG_PATH = CONFIGS / "hungry_hippo_gbpusd_live.json"
RUNNER_REGISTRY_PATH = CONFIGS / "penetration_lattice_runner_registry.json"

OUTPUT_JSON = REPORTS / "mt5_user_visibility_board.json"
OUTPUT_MD = REPORTS / "mt5_user_visibility_board.md"
ACCOUNT_SNAPSHOT_STALE_SECONDS = 120


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def snapshot_freshness(account_snapshot: dict[str, Any], *, generated_at: str) -> dict[str, Any]:
    collected_at = str(account_snapshot.get("collected_at") or "").strip()
    generated_dt = parse_iso_datetime(generated_at)
    collected_dt = parse_iso_datetime(collected_at)
    if generated_dt is None or collected_dt is None:
        return {
            "collected_at": collected_at,
            "age_seconds": None,
            "status": "unknown",
            "warning": "account snapshot age is unknown; freshness could not be verified",
        }
    age_seconds = max(0, int((generated_dt - collected_dt).total_seconds()))
    status = "stale" if age_seconds > ACCOUNT_SNAPSHOT_STALE_SECONDS else "fresh"
    warning = (
        f"account snapshot is {age_seconds}s old, above the {ACCOUNT_SNAPSHOT_STALE_SECONDS}s freshness threshold"
        if status == "stale"
        else ""
    )
    return {
        "collected_at": collected_at,
        "age_seconds": age_seconds,
        "status": status,
        "warning": warning,
    }


def refresh_live_scope_payload_if_needed(live_scope_payload: dict[str, Any], *, generated_at: str) -> dict[str, Any]:
    payload = live_scope_payload if isinstance(live_scope_payload, dict) else {}
    account_snapshot = payload.get("account_snapshot") if isinstance(payload.get("account_snapshot"), dict) else {}
    freshness = snapshot_freshness(account_snapshot, generated_at=generated_at) if account_snapshot else {
        "status": "missing"
    }
    if payload and freshness.get("status") == "fresh":
        return payload
    refreshed_payload = build_live_magic_scope_audit.build_payload()
    build_live_magic_scope_audit.write_outputs(refreshed_payload)
    return load_json(LIVE_SCOPE_PATH)


def sanitize_notes(notes: str, *, outside_scope_open_count: int) -> str:
    text = str(notes or "").strip()
    if not text or outside_scope_open_count > 0:
        return text
    parts = [part.strip() for part in text.split(",")]
    kept = [part for part in parts if not part.startswith("broker_scope_outside_lane=")]
    return ", ".join(part for part in kept if part) or "-"


def registry_rows_by_lane(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for row in list(payload.get("lanes") or []):
        if not isinstance(row, dict):
            continue
        lane = str(row.get("name") or "")
        if lane:
            mapped[lane] = row
    return mapped


def live_rows_by_lane(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        lane = str(row.get("lane") or "").strip()
        if lane:
            mapped[lane] = row
    return mapped


def detect_recent_visibility_changes(previous_payload: dict[str, Any], current_live_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous_rows = live_rows_by_lane(list(previous_payload.get("live_rows") or [])) if isinstance(previous_payload, dict) else {}
    changes: list[dict[str, Any]] = []
    for row in current_live_rows:
        lane = str(row.get("lane") or "")
        previous = previous_rows.get(lane)
        if not previous:
            continue
        previous_status = str(previous.get("mt5_visibility_status") or "")
        current_status = str(row.get("mt5_visibility_status") or "")
        previous_enabled = bool(previous.get("enabled", True))
        current_enabled = bool(row.get("enabled", True))
        previous_show = bool(previous.get("should_show_trades_in_mt5_now", False))
        current_show = bool(row.get("should_show_trades_in_mt5_now", False))
        if (
            previous_status == current_status
            and previous_enabled == current_enabled
            and previous_show == current_show
        ):
            continue
        changes.append(
            {
                "lane": lane,
                "previous_status": previous_status or "-",
                "current_status": current_status or "-",
                "previous_enabled": previous_enabled,
                "current_enabled": current_enabled,
                "previous_show": previous_show,
                "current_show": current_show,
                "pause_note": str(row.get("pause_note") or ""),
            }
        )
    return changes


def classify_live_lane(row: dict[str, Any], execution_row: dict[str, Any] | None = None) -> dict[str, Any]:
    lane = str(row.get("lane") or "")
    enabled = bool(row.get("enabled", True))
    live_magic = int(row.get("live_magic") or 0)
    managed_open_count = int(row.get("managed_open_count") or 0)
    broker_scoped_open_count = int(row.get("broker_scoped_open_count") or 0)
    broker_total_open_count = int(row.get("broker_total_open_count") or 0)
    outside_scope_open_count = int(row.get("outside_scope_open_count") or 0)
    scope_status = str(row.get("scope_status") or "")
    pause_note = str(row.get("pause_note") or "")
    execution_row = execution_row or {}
    is_stale_inactive = (
        not execution_row.get("watchdog_status")
        and not execution_row.get("state_last_write_at")
        and not execution_row.get("event_last_write_at")
        and not execution_row.get("heartbeat_at")
    )

    if not enabled:
        if broker_total_open_count > 0:
            visibility_status = "disabled_but_broker_inventory_present"
            should_show = True
            reason = "lane is paused or disabled, but broker still has positions under this live magic"
        else:
            visibility_status = "disabled_not_expected_in_mt5"
            should_show = False
            reason = "lane is paused or disabled, so fresh lane-managed MT5 trades are not expected right now"
    elif scope_status in {"scoped_mismatch", "scoped_mismatch_with_legacy"}:
        visibility_status = "inactive_stale_managed_state" if is_stale_inactive else "managed_state_only_mismatch"
        should_show = broker_total_open_count > 0
        if is_stale_inactive:
            reason = "managed state carries opens, but the lane has no fresh state/event heartbeat and no broker positions under its magic"
        else:
            reason = "managed state carries opens that are not currently present under the lane's broker magic/scope"
    elif broker_scoped_open_count > 0:
        if outside_scope_open_count > 0:
            visibility_status = "visible_now_with_legacy_inventory_runtime_stale" if is_stale_inactive else "visible_now_with_legacy_inventory"
            if is_stale_inactive:
                reason = "broker still has in-scope live positions plus legacy inventory, but the lane has no fresh state/event heartbeat"
            else:
                reason = "broker has in-scope live positions, plus extra legacy positions outside the lane's current symbol scope"
        else:
            visibility_status = "visible_now_runtime_stale" if is_stale_inactive else "visible_now"
            if is_stale_inactive:
                reason = "broker still has in-scope live positions under the lane's magic, but the lane has no fresh state/event heartbeat"
            else:
                reason = "broker has in-scope live positions under the lane's magic"
        should_show = True
    else:
        visibility_status = "live_but_flat_now"
        reason = "lane is live but currently has no broker positions under its scoped symbols"
        should_show = False

    return {
        "lane": lane,
        "kind": str(row.get("kind") or ""),
        "enabled": enabled,
        "live_magic": live_magic,
        "scoped_symbols": list(row.get("scoped_symbols") or []),
        "managed_open_count": managed_open_count,
        "broker_scoped_open_count": broker_scoped_open_count,
        "broker_total_open_count": broker_total_open_count,
        "outside_scope_open_count": outside_scope_open_count,
        "outside_scope_symbols": dict(row.get("outside_scope_symbols") or {}),
        "outside_scope_profit_usd": float_value(row.get("outside_scope_profit_usd")),
        "scope_status": scope_status,
        "pause_note": pause_note,
        "recommended_action": str(row.get("recommended_action") or ""),
        "notes": sanitize_notes(
            str(row.get("notes") or ""),
            outside_scope_open_count=outside_scope_open_count,
        ),
        "execution_status": execution_row.get("watchdog_status"),
        "last_state_write": execution_row.get("state_last_write_at"),
        "last_event": execution_row.get("event_last_write_at"),
        "last_seen": execution_row.get("heartbeat_at"),
        "mt5_visibility_status": visibility_status,
        "should_show_trades_in_mt5_now": should_show,
        "visibility_reason": reason,
    }


def build_shadow_confusion_rows(
    shadow_deploy_payload: dict[str, Any],
    gbpusd_live_named_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    kind = str(shadow_deploy_payload.get("kind") or "")
    name = str(shadow_deploy_payload.get("name") or "")
    live_named_version = str(gbpusd_live_named_payload.get("version") or "")
    live_named_reason = str(gbpusd_live_named_payload.get("deploy_reason") or "")
    return [
        {
            "lane": name,
            "kind": kind,
            "mt5_visibility_status": "shadow_only_not_expected_in_mt5",
            "should_show_trades_in_mt5_now": False,
            "visibility_reason": f"runtime config kind is {kind}, so this launch writes shadow state/events instead of MT5 orders",
            "state_path": str(shadow_deploy_payload.get("state_path") or ""),
            "event_path": str(shadow_deploy_payload.get("event_path") or ""),
            "named_live_config_path": str(GBPUSD_LIVE_NAMED_CONFIG_PATH.relative_to(ROOT)),
            "named_live_config_version": live_named_version,
            "named_live_config_note": (
                "filename contains 'live', but it is currently a shapeshifter design/deploy surface rather than proof of a direct-live MT5 launch"
            ),
            "named_live_config_deploy_reason": live_named_reason,
        }
    ]


def build_payload(
    live_scope_payload: dict[str, Any],
    execution_monitor_payload: dict[str, Any],
    shadow_deploy_payload: dict[str, Any],
    gbpusd_live_named_payload: dict[str, Any],
    runner_registry_payload: dict[str, Any] | None = None,
    previous_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    generated_at = utc_now_iso()
    execution_rows = {
        str(row.get("lane") or ""): row
        for row in list(execution_monitor_payload.get("rows") or [])
        if isinstance(row, dict) and str(row.get("lane") or "")
    }
    registry_rows = registry_rows_by_lane(runner_registry_payload or {})
    live_scope_rows: list[dict[str, Any]] = []
    for row in list(live_scope_payload.get("rows") or []):
        if not isinstance(row, dict):
            continue
        lane = str(row.get("lane") or "")
        registry_row = registry_rows.get(lane, {})
        enriched_row = dict(row)
        if "enabled" not in enriched_row and registry_row:
            enriched_row["enabled"] = bool(registry_row.get("enabled", True))
        if not str(enriched_row.get("pause_note") or "").strip() and registry_row:
            enriched_row["pause_note"] = str(registry_row.get("pause_note") or "")
        live_scope_rows.append(enriched_row)
    live_rows = [
        classify_live_lane(row, execution_rows.get(str(row.get("lane") or "")))
        for row in live_scope_rows
    ]
    live_rows.sort(
        key=lambda row: (
            row["should_show_trades_in_mt5_now"] is False,
            row["mt5_visibility_status"] != "visible_now_with_legacy_inventory",
            row["mt5_visibility_status"] != "visible_now",
            row["lane"],
        )
    )
    shadow_rows = build_shadow_confusion_rows(shadow_deploy_payload, gbpusd_live_named_payload)

    visible_now_rows = [row for row in live_rows if row["should_show_trades_in_mt5_now"]]
    flat_rows = [
        row
        for row in live_rows
        if row["enabled"] and row["mt5_visibility_status"] == "live_but_flat_now"
    ]
    disabled_rows = [
        row
        for row in live_rows
        if row["mt5_visibility_status"] in {"disabled_not_expected_in_mt5", "disabled_but_broker_inventory_present"}
    ]
    mismatch_rows = [
        row
        for row in live_rows
        if row["mt5_visibility_status"] in {"managed_state_only_mismatch", "inactive_stale_managed_state"}
    ]
    legacy_rows = [row for row in live_rows if row["mt5_visibility_status"] == "visible_now_with_legacy_inventory"]
    stale_visible_rows = [
        row
        for row in live_rows
        if row["mt5_visibility_status"] in {"visible_now_runtime_stale", "visible_now_with_legacy_inventory_runtime_stale"}
    ]
    unassigned_positions = list(live_scope_payload.get("unassigned_live_symbol_positions") or [])
    account_snapshot = live_scope_payload.get("account_snapshot") if isinstance(live_scope_payload.get("account_snapshot"), dict) else {}
    detached_legacy_positions = sum(int(row.get("outside_scope_open_count") or 0) for row in legacy_rows)
    detached_legacy_profit_usd = round(sum(float_value(row.get("outside_scope_profit_usd")) for row in legacy_rows), 2)
    unassigned_profit_usd = round(sum(float_value(row.get("profit_usd")) for row in unassigned_positions), 2)
    detached_inventory_positions = detached_legacy_positions + len(unassigned_positions)
    detached_inventory_profit_usd = round(detached_legacy_profit_usd + unassigned_profit_usd, 2)
    account_live_pnl_usd = float_value(account_snapshot.get("profit_usd")) if account_snapshot else 0.0
    detached_inventory_live_pnl_share = (
        round((detached_inventory_profit_usd / account_live_pnl_usd) * 100.0, 1)
        if account_live_pnl_usd not in (0.0, -0.0)
        else None
    )

    visible_lane_names = ", ".join(row["lane"] for row in visible_now_rows) or "none"
    flat_lane_names = ", ".join(row["lane"] for row in flat_rows) or "none"
    disabled_lane_names = ", ".join(row["lane"] for row in disabled_rows) or "none"
    mt5_connection = live_scope_payload.get("mt5_connection") if isinstance(live_scope_payload.get("mt5_connection"), dict) else {}
    contract = mt5_connection.get("contract") if isinstance(mt5_connection.get("contract"), dict) else {}
    account_snapshot_freshness = snapshot_freshness(account_snapshot, generated_at=generated_at) if account_snapshot else {}
    leadership_read = [
        f"Current direct-live lanes with broker-visible MT5 trades: {visible_lane_names}.",
        f"Current live lanes that are flat at the broker right now: {flat_lane_names}.",
        "The launched GBPUSD Hungry Hippo is shadow-only, so it should not appear in MT5 even though a differently named config file contains 'live'.",
    ]
    if list(mt5_connection.get("identity_mismatches") or []):
        leadership_read.insert(
            0,
            f"MT5 connection guard is blocking broker-authoritative reads until the expected live terminal contract is restored (`{', '.join(mt5_connection.get('identity_mismatches') or [])}`).",
        )
    elif str(contract.get("binding_mode") or "account_only") != "path_pinned":
        leadership_read.insert(
            0,
            "MT5 visibility is currently account-pinned but not terminal-path-pinned; configure `MT5_TERMINAL_PATH` if you want the board to fail closed on the wrong terminal instance.",
        )
    if account_snapshot:
        leadership_read.insert(
            1,
            "Pinned MT5 account snapshot: "
            f"equity `${float_value(account_snapshot.get('equity_usd')):,.2f}`, "
            f"balance `${float_value(account_snapshot.get('balance_usd')):,.2f}`, "
            f"live PnL `{float_value(account_snapshot.get('profit_usd')):+.2f}`, "
            f"broker open positions `{int(account_snapshot.get('position_count') or 0)}`, "
            f"freshness `{account_snapshot_freshness.get('status') or 'unknown'}`.",
        )
    if account_snapshot_freshness.get("warning"):
        leadership_read.insert(
            1,
            f"MT5 account snapshot freshness warning: {account_snapshot_freshness.get('warning')}.",
        )
    if detached_inventory_positions > 0:
        detached_inventory_read = (
            f"Detached inventory still moving MT5 equity: `{detached_inventory_positions}` broker position(s), "
            f"floating PnL `{detached_inventory_profit_usd:+.2f}`"
        )
        if detached_inventory_live_pnl_share is not None:
            detached_inventory_read += f", or `{detached_inventory_live_pnl_share:+.1f}%` of current MT5 live PnL."
        else:
            detached_inventory_read += "."
        leadership_read.insert(2 if account_snapshot else 1, detached_inventory_read)
    if unassigned_positions:
        counts: dict[str, int] = {}
        for row in unassigned_positions:
            symbol = str(row.get("symbol") or "").upper()
            counts[symbol] = counts.get(symbol, 0) + 1
        counts_text = ", ".join(f"{symbol}:{count}" for symbol, count in sorted(counts.items()))
        leadership_read.insert(
            1,
            f"Broker also has `{len(unassigned_positions)}` open position(s) on currently live-traded symbols that do not map to any enabled live magic (`{counts_text}`). Those positions can move account equity while staying invisible to lane-specific live-magic totals.",
        )
    if mismatch_rows:
        leadership_read.insert(
            2,
            f"`{len(mismatch_rows)}` live row(s) still carry managed-state/broker mismatch and should not be expected to show user-side MT5 trades until that drift is resolved.",
        )
    if stale_visible_rows:
        leadership_read.insert(
            1 if not unassigned_positions else 2,
            f"`{len(stale_visible_rows)}` broker-visible live lane(s) still lack fresh runtime telemetry; those trades can remain visible in MT5 while the lane process is stale or detached.",
        )
    if disabled_rows:
        leadership_read.insert(
            len(leadership_read) - 1,
            f"`{len(disabled_rows)}` paused or disabled live id(s) should not be expected to open fresh MT5 trades right now ({disabled_lane_names}).",
        )

    summary = {
        "live_lane_count": len(live_rows),
        "enabled_live_lane_count": sum(1 for row in live_rows if row["enabled"]),
        "disabled_live_lane_count": len(disabled_rows),
        "live_lanes_visible_now": len(visible_now_rows),
        "live_lanes_flat_now": len(flat_rows),
        "disabled_not_expected_now": sum(1 for row in disabled_rows if not row["should_show_trades_in_mt5_now"]),
        "live_lanes_with_state_mismatch": len(mismatch_rows),
        "live_lanes_visible_but_runtime_stale": len(stale_visible_rows),
        "shadow_confusion_rows": len(shadow_rows),
        "scoped_live_positions_visible_now": sum(int(row["broker_scoped_open_count"]) for row in visible_now_rows),
        "legacy_outside_scope_positions_visible_now": sum(int(row["outside_scope_open_count"]) for row in legacy_rows),
        "broker_total_positions_under_live_magics_now": sum(int(row["broker_total_open_count"]) for row in visible_now_rows),
        "unassigned_live_symbol_positions": len(unassigned_positions),
        "detached_inventory_positions": detached_inventory_positions,
        "detached_inventory_profit_usd": detached_inventory_profit_usd,
        "detached_inventory_live_pnl_share_pct": detached_inventory_live_pnl_share,
        "account_snapshot_freshness": str(account_snapshot_freshness.get("status") or "missing"),
    }
    recent_visibility_changes = detect_recent_visibility_changes(previous_payload or {}, live_rows)
    summary["recent_visibility_changes"] = len(recent_visibility_changes)
    if recent_visibility_changes:
        changed_lane_names = ", ".join(str(row.get("lane") or "") for row in recent_visibility_changes) or "none"
        leadership_read.insert(
            1,
            f"Recent MT5 visibility changes detected on `{len(recent_visibility_changes)}` lane(s): {changed_lane_names}. Read the Recent Visibility Changes section before treating a count shift as a regression.",
        )

    return {
        "generated_at": generated_at,
        "sources": [
            str(LIVE_SCOPE_PATH.relative_to(ROOT)),
            str(EXECUTION_MONITOR_PATH.relative_to(ROOT)),
            str(SHADOW_DEPLOY_PATH.relative_to(ROOT)),
            str(GBPUSD_LIVE_NAMED_CONFIG_PATH.relative_to(ROOT)),
        ],
        "mt5_connection": mt5_connection,
        "account_snapshot": account_snapshot,
        "account_snapshot_freshness": account_snapshot_freshness,
        "summary": summary,
        "leadership_read": leadership_read,
        "recent_visibility_changes": recent_visibility_changes,
        "live_rows": live_rows,
        "unassigned_live_symbol_positions": unassigned_positions,
        "shadow_confusion_rows": shadow_rows,
    }


def render_live_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Lane | Magic | Symbols | Managed Open | Broker Scoped | Broker Total | MT5 Status |",
        "| --- | ---: | --- | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        scoped_symbols = ", ".join(row.get("scoped_symbols") or []) or "-"
        lines.append(
            f"| {row['lane']} | {int(row['live_magic'])} | {scoped_symbols} | {int(row['managed_open_count'])} | "
            f"{int(row['broker_scoped_open_count'])} | {int(row['broker_total_open_count'])} | {row['mt5_visibility_status']} |"
        )
    return lines


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    live_rows = list(payload.get("live_rows") or [])
    shadow_rows = list(payload.get("shadow_confusion_rows") or [])
    mt5_connection = payload.get("mt5_connection") if isinstance(payload.get("mt5_connection"), dict) else {}
    account_snapshot = payload.get("account_snapshot") if isinstance(payload.get("account_snapshot"), dict) else {}
    account_snapshot_freshness = (
        payload.get("account_snapshot_freshness")
        if isinstance(payload.get("account_snapshot_freshness"), dict)
        else {}
    )
    contract = mt5_connection.get("contract") if isinstance(mt5_connection.get("contract"), dict) else {}
    identity_mismatches = list(mt5_connection.get("identity_mismatches") or [])

    visible_now_rows = [row for row in live_rows if row["should_show_trades_in_mt5_now"]]
    flat_rows = [
        row
        for row in live_rows
        if row["enabled"] and row["mt5_visibility_status"] == "live_but_flat_now"
    ]
    disabled_rows = [
        row
        for row in live_rows
        if row["mt5_visibility_status"] in {"disabled_not_expected_in_mt5", "disabled_but_broker_inventory_present"}
    ]
    mismatch_rows = [
        row
        for row in live_rows
        if row["mt5_visibility_status"] in {"managed_state_only_mismatch", "inactive_stale_managed_state"}
    ]

    lines = [
        "# MT5 User Visibility Board",
        "",
        "> Current runtime generated board.",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: answer which lanes should currently show trades in the user's MT5 terminal, and which should not.",
        (
            "- Summary: "
            f"`live_ids={summary.get('live_lane_count', 0)}` "
            f"`enabled={summary.get('enabled_live_lane_count', 0)}` "
            f"`disabled={summary.get('disabled_live_lane_count', 0)}` "
            f"`visible_now={summary.get('live_lanes_visible_now', 0)}` "
            f"`flat_now={summary.get('live_lanes_flat_now', 0)}` "
            f"`disabled_not_expected={summary.get('disabled_not_expected_now', 0)}` "
            f"`state_mismatch={summary.get('live_lanes_with_state_mismatch', 0)}` "
            f"`runtime_stale_visible={summary.get('live_lanes_visible_but_runtime_stale', 0)}` "
            f"`shadow_confusion={summary.get('shadow_confusion_rows', 0)}` "
            f"`scoped_live_positions={summary.get('scoped_live_positions_visible_now', 0)}` "
            f"`legacy_outside_scope_positions={summary.get('legacy_outside_scope_positions_visible_now', 0)}` "
            f"`unassigned_live_symbol_positions={summary.get('unassigned_live_symbol_positions', 0)}` "
            f"`detached_inventory_positions={summary.get('detached_inventory_positions', 0)}` "
            f"`detached_inventory_pnl={float_value(summary.get('detached_inventory_profit_usd')):+.2f}` "
            f"`recent_visibility_changes={summary.get('recent_visibility_changes', 0)}`"
        ),
        "",
        "## MT5 Connection Guard",
        "",
        f"- Status: `{'ok' if mt5_connection.get('identity_ok') else mt5_connection.get('reason', 'unknown')}`",
        f"- Binding mode: `{str(contract.get('binding_mode') or 'account_only')}`",
        (
            f"- Connected target: `login={int(mt5_connection.get('login') or 0) or '-'} "
            f"server={str(mt5_connection.get('server') or '-')}`"
        ),
        f"- Connected terminal path: `{str(mt5_connection.get('terminal_path') or '') or '-'}`",
    ]
    lines.extend(["", "## Pinned Account Snapshot", ""])
    if account_snapshot:
        lines.extend(
            [
                f"- Collected at: `{str(account_snapshot_freshness.get('collected_at') or account_snapshot.get('collected_at') or '-')}`",
                f"- Freshness: `{str(account_snapshot_freshness.get('status') or 'unknown')}`",
                f"- Snapshot age seconds: `{account_snapshot_freshness.get('age_seconds') if account_snapshot_freshness.get('age_seconds') is not None else '-'}`",
                f"- Equity: `${float_value(account_snapshot.get('equity_usd')):,.2f}`",
                f"- Balance: `${float_value(account_snapshot.get('balance_usd')):,.2f}`",
                f"- Live PnL: `{float_value(account_snapshot.get('profit_usd')):+.2f}`",
                f"- Broker open positions: `{int(account_snapshot.get('position_count') or 0)}`",
                "",
            ]
        )
        if str(account_snapshot_freshness.get("warning") or "").strip():
            lines.extend(
                [
                    "### Freshness Warning",
                    "",
                    f"- {str(account_snapshot_freshness.get('warning') or '')}",
                    "",
                ]
            )
    else:
        lines.extend(["- unavailable", ""])
    lines.extend(["## Leadership Read", ""])
    if identity_mismatches:
        lines.extend(
            [
                f"- Identity mismatches: `{', '.join(identity_mismatches)}`",
                f"- Expected terminal path: `{str(contract.get('expected_terminal_path') or '') or '-'}`",
                "",
            ]
        )
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(["", "## Recent Visibility Changes", ""])
    recent_visibility_changes = list(payload.get("recent_visibility_changes") or [])
    if recent_visibility_changes:
        lines.append("| Lane | Prev Status | Now Status | Prev Enabled | Now Enabled | Prev Show | Now Show | Pause Note |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for row in recent_visibility_changes:
            lines.append(
                f"| {row.get('lane') or '-'} | {row.get('previous_status') or '-'} | {row.get('current_status') or '-'} | "
                f"{'yes' if row.get('previous_enabled') else 'no'} | {'yes' if row.get('current_enabled') else 'no'} | "
                f"{'yes' if row.get('previous_show') else 'no'} | {'yes' if row.get('current_show') else 'no'} | "
                f"{row.get('pause_note') or '-'} |"
            )
        lines.append("")
    else:
        lines.append("- none")
        lines.append("")

    lines.extend(["", "## Detached Inventory Still Moving MT5 Equity", ""])
    if int(summary.get("detached_inventory_positions", 0) or 0) > 0:
        lines.append(
            f"- Combined detached inventory: `{int(summary.get('detached_inventory_positions', 0) or 0)}` broker position(s), floating PnL `{float_value(summary.get('detached_inventory_profit_usd')):+.2f}`."
        )
        if summary.get("detached_inventory_live_pnl_share_pct") is not None:
            lines.append(
                f"- Share of current MT5 live PnL: `{float_value(summary.get('detached_inventory_live_pnl_share_pct')):+.1f}%`."
            )
        legacy_rows = [row for row in live_rows if int(row.get("outside_scope_open_count") or 0) > 0]
        if legacy_rows:
            legacy_positions = sum(int(row.get("outside_scope_open_count") or 0) for row in legacy_rows)
            legacy_profit = round(sum(float_value(row.get("outside_scope_profit_usd")) for row in legacy_rows), 2)
            legacy_lane_names = ", ".join(str(row.get("lane") or "") for row in legacy_rows) or "none"
            lines.append(
                f"- Legacy outside-scope inventory under enabled live magics: `{legacy_positions}` position(s), `{legacy_profit:+.2f}`, lane(s) `{legacy_lane_names}`."
            )
        unassigned_positions = list(payload.get("unassigned_live_symbol_positions") or [])
        if unassigned_positions:
            unassigned_profit = round(sum(float_value(row.get("profit_usd")) for row in unassigned_positions), 2)
            counts: dict[str, int] = {}
            for row in unassigned_positions:
                symbol = str(row.get("symbol") or "").upper()
                counts[symbol] = counts.get(symbol, 0) + 1
            counts_text = ", ".join(f"{symbol}:{count}" for symbol, count in sorted(counts.items())) or "-"
            lines.append(
                f"- Unassigned live-symbol inventory: `{len(unassigned_positions)}` position(s), `{unassigned_profit:+.2f}`, symbols `{counts_text}`."
            )
        lines.append("")
    else:
        lines.append("- none")
        lines.append("")

    lines.extend(["", "## Live Lanes Visible In MT5 Now", ""])
    if visible_now_rows:
        lines.extend(render_live_table(visible_now_rows))
        lines.append("")
        for row in visible_now_rows:
            lines.append(f"### {row['lane']}")
            lines.append("")
            lines.append(f"- Status: `{row['mt5_visibility_status']}`")
            lines.append(f"- Why visible: `{row['visibility_reason']}`")
            lines.append(f"- Recommended action: `{row['recommended_action']}`")
            lines.append(f"- Notes: `{row['notes'] or '-'}`")
            if int(row.get("outside_scope_open_count") or 0) > 0:
                symbol_counts = dict(row.get("outside_scope_symbols") or {})
                counts_text = ", ".join(f"{symbol}:{count}" for symbol, count in symbol_counts.items()) or "-"
                lines.append(f"- Legacy outside-scope positions also visible: `{counts_text}`")
            lines.append("")
    else:
        lines.append("- none")
        lines.append("")

    lines.extend(["## Live But Flat Right Now", ""])
    if flat_rows:
        lines.extend(render_live_table(flat_rows))
        lines.append("")
    else:
        lines.append("- none")
        lines.append("")

    lines.extend(["## Paused Or Disabled Live IDs", ""])
    if disabled_rows:
        lines.extend(render_live_table(disabled_rows))
        lines.append("")
        for row in disabled_rows:
            lines.append(f"### {row['lane']}")
            lines.append("")
            lines.append(f"- Status: `{row['mt5_visibility_status']}`")
            lines.append(f"- Should show user-side MT5 trades now: `{'true' if row['should_show_trades_in_mt5_now'] else 'false'}`")
            lines.append(f"- Reason: `{row['visibility_reason']}`")
            lines.append(f"- Pause note: `{row.get('pause_note') or '-'}`")
            lines.append(f"- Notes: `{row['notes'] or '-'}`")
            lines.append("")
    else:
        lines.append("- none")
        lines.append("")

    lines.extend(["## Managed-State Mismatches", ""])
    if mismatch_rows:
        lines.extend(render_live_table(mismatch_rows))
        lines.append("")
        for row in mismatch_rows:
            lines.append(f"### {row['lane']}")
            lines.append("")
            lines.append("- Should show user-side MT5 trades now: `false`")
            lines.append(f"- Reason: `{row['visibility_reason']}`")
            lines.append(f"- Execution heartbeat status: `{row.get('execution_status') or 'none'}`")
            lines.append(f"- Last state write: `{row.get('last_state_write') or '-'}`")
            lines.append(f"- Last event: `{row.get('last_event') or '-'}`")
            lines.append(f"- Last seen: `{row.get('last_seen') or '-'}`")
            lines.append(f"- Recommended action: `{row['recommended_action']}`")
            lines.append(f"- Notes: `{row['notes'] or '-'}`")
            lines.append("")
    else:
        lines.append("- none")
        lines.append("")

    lines.extend(["## Unassigned Broker Positions On Live Symbols", ""])
    unassigned_positions = list(payload.get("unassigned_live_symbol_positions") or [])
    if unassigned_positions:
        total_profit = sum(float(row.get("profit_usd") or 0.0) for row in unassigned_positions)
        lines.append(
            f"- These positions sit on symbols used by enabled live lanes, but their magic is not claimed by any enabled live lane. They still affect user-visible MT5 PnL and equity."
        )
        lines.append(f"- Floating PnL USD: `{total_profit:+.2f}`")
        lines.append("")
        lines.append("| Ticket | Symbol | Magic | Side | Volume | Open Price | PnL USD | Comment | Opened At |")
        lines.append("| ---: | --- | ---: | --- | ---: | ---: | ---: | --- | --- |")
        for row in unassigned_positions[:12]:
            lines.append(
                f"| {int(row.get('ticket') or 0)} | {row.get('symbol') or '-'} | {int(row.get('magic') or 0)} | {row.get('side') or '-'} | "
                f"{float(row.get('volume') or 0.0):.2f} | {float(row.get('price_open') or 0.0):.5f} | "
                f"{float(row.get('profit_usd') or 0.0):+.2f} | {row.get('comment') or '-'} | {row.get('opened_at') or '-'} |"
            )
        lines.append("")
    else:
        lines.append("- none")
        lines.append("")

    lines.extend(["## Shadow Confusion", ""])
    if shadow_rows:
        for row in shadow_rows:
            lines.append(f"### {row['lane']}")
            lines.append("")
            lines.append(f"- Runtime kind: `{row['kind']}`")
            lines.append("- Should show user-side MT5 trades now: `false`")
            lines.append(f"- Reason: `{row['visibility_reason']}`")
            lines.append(f"- Shadow state path: `{row['state_path']}`")
            lines.append(f"- Shadow event path: `{row['event_path']}`")
            lines.append(f"- Named live config surface: `{row['named_live_config_path']}`")
            lines.append(f"- Named live config version: `{row['named_live_config_version']}`")
            lines.append(f"- Note: `{row['named_live_config_note']}`")
            lines.append(f"- Named live config deploy reason: `{row['named_live_config_deploy_reason']}`")
            lines.append("")
    else:
        lines.append("- none")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    generated_at = utc_now_iso()
    previous_payload = load_json(OUTPUT_JSON) if OUTPUT_JSON.exists() else {}
    live_scope_payload = refresh_live_scope_payload_if_needed(load_json(LIVE_SCOPE_PATH), generated_at=generated_at)
    payload = build_payload(
        live_scope_payload,
        load_json(EXECUTION_MONITOR_PATH),
        load_json(SHADOW_DEPLOY_PATH),
        load_json(GBPUSD_LIVE_NAMED_CONFIG_PATH),
        load_json(RUNNER_REGISTRY_PATH),
        previous_payload,
    )
    write_outputs(payload)
    print(f"wrote {OUTPUT_JSON}")
    print(f"wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
