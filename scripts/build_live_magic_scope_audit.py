#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

import build_execution_monitor_report as execution_monitor
import mt5_terminal_guard


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
EXECUTION_MONITOR_JSON = ROOT / "reports" / "execution_monitor_report.json"
REPORT_JSON = ROOT / "reports" / "live_magic_scope_audit.json"
REPORT_MD = ROOT / "reports" / "live_magic_scope_audit.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def epoch_to_iso(value: Any) -> str:
    try:
        seconds = int(value or 0)
    except Exception:
        return ""
    if seconds <= 0:
        return ""
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return execution_monitor.load_json(path)


def read_live_registry() -> list[dict[str, Any]]:
    lanes = execution_monitor.read_registry(REGISTRY_PATH)
    return [lane for lane in lanes if str(lane.get("kind") or "").startswith("live")]


def lane_enabled_value(lane: dict[str, Any]) -> bool:
    raw = lane.get("enabled")
    if raw is None:
        return True
    return bool(raw)


def execution_rows_by_lane(path: Path) -> dict[str, dict[str, Any]]:
    payload = load_json(path)
    rows = payload.get("rows") if isinstance(payload, dict) else []
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        lane = str(row.get("lane") or "").strip()
        if lane:
            mapped[lane] = row
    return mapped


def collect_broker_positions_by_magic() -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for pos in mt5.positions_get() or []:
        magic = int(getattr(pos, "magic", 0) or 0)
        grouped.setdefault(magic, []).append(
            {
                "ticket": int(getattr(pos, "ticket", 0) or 0),
                "magic": magic,
                "symbol": str(getattr(pos, "symbol", "") or "").upper(),
                "side": "SELL" if int(getattr(pos, "type", 0) or 0) == 1 else "BUY",
                "volume": float(getattr(pos, "volume", 0.0) or 0.0),
                "price_open": float(getattr(pos, "price_open", 0.0) or 0.0),
                "profit_usd": float(getattr(pos, "profit", 0.0) or 0.0),
                "comment": str(getattr(pos, "comment", "") or ""),
                "opened_at": epoch_to_iso(getattr(pos, "time", 0) or 0),
            }
        )
    for rows in grouped.values():
        rows.sort(key=lambda row: (row["symbol"], row["ticket"]))
    return grouped


def collect_account_snapshot() -> dict[str, Any]:
    account_info = mt5.account_info()
    if account_info is None:
        return {}
    return {
        "collected_at": utc_now_iso(),
        "balance_usd": float_value(getattr(account_info, "balance", 0.0)),
        "equity_usd": float_value(getattr(account_info, "equity", 0.0)),
        "profit_usd": float_value(getattr(account_info, "profit", 0.0)),
        "margin_level_pct": float_value(getattr(account_info, "margin_level", 0.0)),
    }


def lane_state_payload(lane: dict[str, Any]) -> dict[str, Any]:
    state_path = ROOT / str(lane.get("state_path") or "")
    payload = load_json(state_path)
    return payload if isinstance(payload, dict) else {}


def enabled_live_symbol_universe(registry: list[dict[str, Any]]) -> set[str]:
    symbols: set[str] = set()
    for lane in registry:
        if not lane_enabled_value(lane):
            continue
        scoped = execution_monitor.lane_scoped_symbols(lane, lane_state_payload(lane))
        symbols.update(str(symbol or "").upper() for symbol in scoped if str(symbol or "").strip())
    return symbols


def known_live_magics(registry: list[dict[str, Any]]) -> set[int]:
    magics: set[int] = set()
    for lane in registry:
        if not lane_enabled_value(lane):
            continue
        for live_magic in execution_monitor.lane_live_magics(lane, lane_state_payload(lane)):
            if int(live_magic or 0) > 0:
                magics.add(int(live_magic))
    return magics


def collect_unassigned_live_symbol_positions(
    registry: list[dict[str, Any]],
    broker_positions_by_magic: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    live_symbols = enabled_live_symbol_universe(registry)
    assigned_magics = known_live_magics(registry)
    rows: list[dict[str, Any]] = []
    for magic, positions in broker_positions_by_magic.items():
        if magic in assigned_magics:
            continue
        for pos in positions:
            symbol = str(pos.get("symbol") or "").upper()
            if symbol not in live_symbols:
                continue
            row = dict(pos)
            row["magic"] = int(magic)
            rows.append(row)
    rows.sort(key=lambda row: (row.get("symbol") or "", int(row.get("ticket") or 0)))
    return rows


def classify_scope_status(
    *,
    live_magic: int,
    lane_enabled: bool,
    managed_open_count: int,
    broker_scoped_open_count: int,
    outside_scope_open_count: int,
) -> tuple[str, str]:
    if int(live_magic or 0) <= 0:
        return "no_live_magic", "fix_magic_wiring"
    if int(broker_scoped_open_count) != int(managed_open_count):
        if not lane_enabled and int(managed_open_count) > 0 and int(broker_scoped_open_count) == 0 and int(outside_scope_open_count) == 0:
            return "managed_state_only_flat_broker", "clear_stale_state_or_document_parked"
        if int(outside_scope_open_count) > 0:
            return "scoped_mismatch_with_legacy", "inspect_rehydration_then_manual_review"
        return "scoped_mismatch", "inspect_rehydration_or_scope"
    if int(outside_scope_open_count) > 0:
        return "outside_scope_legacy_inventory", "manual_review_do_not_autoclose"
    return "aligned", "none"


def build_rows(
    registry: list[dict[str, Any]],
    execution_rows: dict[str, dict[str, Any]],
    broker_positions_by_magic: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lane in registry:
        name = str(lane.get("name") or "").strip()
        if not name:
            continue
        lane_enabled = lane_enabled_value(lane)
        state_path = ROOT / str(lane.get("state_path") or "")
        state_payload = load_json(state_path)
        execution_row = execution_rows.get(name) or {}
        live_magic = execution_monitor.lane_live_magic(lane, state_payload if isinstance(state_payload, dict) else {})
        live_magics = execution_monitor.lane_live_magics(lane, state_payload if isinstance(state_payload, dict) else {})
        scoped_symbols = execution_monitor.lane_scoped_symbols(lane, state_payload if isinstance(state_payload, dict) else {})
        scope_summary = execution_monitor.broker_scope_summary(
            broker_positions_by_magic,
            live_magics=live_magics,
            scoped_symbols=scoped_symbols,
        )
        outside_positions = [
            row
            for magic in live_magics
            for row in broker_positions_by_magic.get(int(magic or 0), [])
            if scoped_symbols and str(row.get("symbol") or "").upper() not in scoped_symbols
        ]
        outside_symbols = scope_summary.get("outside_counts") or {}
        status, action = classify_scope_status(
            live_magic=live_magic,
            lane_enabled=lane_enabled,
            managed_open_count=int(execution_row.get("open_count") or 0),
            broker_scoped_open_count=int(scope_summary.get("scoped_open_count") or 0),
            outside_scope_open_count=int(scope_summary.get("outside_open_count") or 0),
        )
        outside_scope_profit_usd = round(
            sum(float(row.get("profit_usd") or 0.0) for row in outside_positions),
            2,
        )
        outside_scope_opened_at = [str(row.get("opened_at") or "") for row in outside_positions if str(row.get("opened_at") or "")]
        rows.append(
            {
                "lane": name,
                "kind": str(lane.get("kind") or ""),
                "enabled": lane_enabled,
                "live_magic": int(live_magic or 0),
                "attached_live_magics": [magic for magic in live_magics if int(magic or 0) != int(live_magic or 0)],
                "scoped_symbols": sorted(scoped_symbols),
                "managed_open_count": int(execution_row.get("open_count") or 0),
                "broker_scoped_open_count": int(scope_summary.get("scoped_open_count") or 0),
                "broker_total_open_count": int(scope_summary.get("total_open_count") or 0),
                "outside_scope_open_count": int(scope_summary.get("outside_open_count") or 0),
                "outside_scope_symbols": dict(sorted((str(symbol), int(count)) for symbol, count in outside_symbols.items())),
                "scope_status": status,
                "recommended_action": action,
                "notes": str(execution_row.get("notes") or ""),
                "outside_scope_profit_usd": outside_scope_profit_usd,
                "oldest_outside_scope_opened_at": min(outside_scope_opened_at) if outside_scope_opened_at else "",
                "newest_outside_scope_opened_at": max(outside_scope_opened_at) if outside_scope_opened_at else "",
                "outside_scope_positions": outside_positions,
            }
        )
    rows.sort(
        key=lambda row: (
            row["scope_status"] == "aligned",
            row["outside_scope_open_count"] == 0,
            abs(int(row["broker_scoped_open_count"]) - int(row["managed_open_count"])) == 0,
            row["lane"],
        )
    )
    return rows


def build_payload() -> dict[str, Any]:
    registry = read_live_registry()
    execution_rows = execution_rows_by_lane(EXECUTION_MONITOR_JSON)
    mt5_ready, mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5)
    broker_positions_by_magic: dict[int, list[dict[str, Any]]] = {}
    account_snapshot: dict[str, Any] = {}
    if mt5_ready:
        broker_positions_by_magic = collect_broker_positions_by_magic()
        account_snapshot = collect_account_snapshot()
    try:
        rows = build_rows(registry, execution_rows, broker_positions_by_magic)
        unassigned_positions = collect_unassigned_live_symbol_positions(registry, broker_positions_by_magic)
    finally:
        if mt5_ready:
            mt5.shutdown()
    if account_snapshot:
        account_snapshot["position_count"] = sum(len(positions) for positions in broker_positions_by_magic.values())

    summary = {
        "total_live_lanes": len(rows),
        "aligned_lanes": sum(1 for row in rows if row["scope_status"] == "aligned"),
        "outside_scope_legacy_lanes": sum(1 for row in rows if row["outside_scope_open_count"] > 0),
        "scoped_mismatch_lanes": sum(
            1 for row in rows if row["scope_status"] in {"scoped_mismatch", "scoped_mismatch_with_legacy", "managed_state_only_flat_broker"}
        ),
        "missing_magic_lanes": sum(1 for row in rows if row["scope_status"] == "no_live_magic"),
        "managed_state_only_flat_broker_lanes": sum(1 for row in rows if row["scope_status"] == "managed_state_only_flat_broker"),
        "outside_scope_positions": sum(int(row["outside_scope_open_count"]) for row in rows),
        "unassigned_live_symbol_positions": len(unassigned_positions),
    }
    return {
        "generated_at": utc_now_iso(),
        "broker_connected": bool(mt5_ready),
        "mt5_connection": mt5_connection,
        "account_snapshot": account_snapshot,
        "sources": [
            str(REGISTRY_PATH.relative_to(ROOT)),
            str(EXECUTION_MONITOR_JSON.relative_to(ROOT)),
        ],
        "summary": summary,
        "unassigned_live_symbol_positions": unassigned_positions,
        "rows": rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    mt5_connection = payload.get("mt5_connection") if isinstance(payload.get("mt5_connection"), dict) else {}
    account_snapshot = payload.get("account_snapshot") if isinstance(payload.get("account_snapshot"), dict) else {}
    contract = mt5_connection.get("contract") if isinstance(mt5_connection.get("contract"), dict) else {}
    identity_mismatches = list(mt5_connection.get("identity_mismatches") or [])
    lines = [
        "# Live Magic Scope Audit",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        f"- Broker connected: `{str(bool(payload.get('broker_connected'))).lower()}`",
        f"- MT5 identity guard: `{'ok' if mt5_connection.get('identity_ok') else mt5_connection.get('reason', 'unknown')}`",
        f"- MT5 binding mode: `{str(contract.get('binding_mode') or 'account_only')}`",
        (
            f"- Connected MT5 target: `login={int(mt5_connection.get('login') or 0) or '-'} "
            f"server={str(mt5_connection.get('server') or '-')}`"
        ),
        f"- Connected terminal path: `{str(mt5_connection.get('terminal_path') or '') or '-'}`",
    ]
    if account_snapshot:
        lines.append(
            f"- Account snapshot: equity `${float_value(account_snapshot.get('equity_usd')):,.2f}` | "
            f"balance `${float_value(account_snapshot.get('balance_usd')):,.2f}` | "
            f"live PnL `{float_value(account_snapshot.get('profit_usd')):+.2f}` | "
            f"broker open positions `{int(account_snapshot.get('position_count') or 0)}`"
        )
    lines.extend(
        [
            "- Scope: live lanes only; compares broker inventory by live magic against the lane's current scoped symbols and managed open count.",
            (
                "- Summary: "
                f"`live={summary.get('total_live_lanes', 0)}` "
                f"`aligned={summary.get('aligned_lanes', 0)}` "
                f"`legacy_outside_scope={summary.get('outside_scope_legacy_lanes', 0)}` "
                f"`scoped_mismatch={summary.get('scoped_mismatch_lanes', 0)}` "
                f"`managed_state_only={summary.get('managed_state_only_flat_broker_lanes', 0)}` "
                f"`missing_magic={summary.get('missing_magic_lanes', 0)}` "
                f"`outside_scope_positions={summary.get('outside_scope_positions', 0)}` "
                f"`unassigned_live_symbol_positions={summary.get('unassigned_live_symbol_positions', 0)}`"
            ),
            "",
        ]
    )
    if identity_mismatches:
        lines.extend(
            [
                "## MT5 Connection Guard",
                "",
                f"- Identity mismatches: `{', '.join(identity_mismatches)}`",
                f"- Expected login: `{int(contract.get('expected_login') or 0) or '-'}`",
                f"- Expected server: `{str(contract.get('expected_server') or '') or '-'}`",
                f"- Expected terminal path: `{str(contract.get('expected_terminal_path') or '') or '-'}`",
                "- Guardrail: broker inventory is intentionally withheld from this audit until the MT5 contract matches the expected live account/server/terminal.",
                "",
            ]
        )
    elif str(contract.get("binding_mode") or "account_only") != "path_pinned":
        lines.extend(
            [
                "## MT5 Connection Guard",
                "",
                "- Status: `account_only`",
                "- Guardrail: login/server are pinned, but `MT5_TERMINAL_PATH` is not configured yet, so this audit is account-authoritative rather than explicitly terminal-instance-authoritative.",
                "",
            ]
        )
    lines.extend(
        [
            "| Lane | Enabled | Magic | Scoped Symbols | Managed Open | Broker Scoped | Outside Scope | Status | Action |",
            "| --- | --- | ---: | --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in rows:
        scoped = ", ".join(row["scoped_symbols"]) or "-"
        attached_text = ",".join(str(int(magic)) for magic in row.get("attached_live_magics") or [])
        magic_text = f"{row['live_magic']} (+{attached_text})" if attached_text else str(row["live_magic"])
        lines.append(
            f"| {row['lane']} | {str(bool(row['enabled'])).lower()} | {magic_text} | {scoped} | {row['managed_open_count']} | "
            f"{row['broker_scoped_open_count']} | {row['outside_scope_open_count']} | {row['scope_status']} | {row['recommended_action']} |"
        )

    lines.extend(["", "## Outside-Scope Inventory", ""])
    outside_rows = [row for row in rows if int(row.get("outside_scope_open_count") or 0) > 0]
    if not outside_rows:
        lines.append("- none")
    for row in outside_rows:
        symbol_counts = row.get("outside_scope_symbols") or {}
        counts_text = ", ".join(f"{symbol}:{count}" for symbol, count in symbol_counts.items()) or "-"
        lines.extend(
            [
                f"### {row['lane']}",
                "",
                f"- Live magic: `{row['live_magic']}`",
                f"- Scoped symbols: `{', '.join(row['scoped_symbols']) or '-'}`",
                f"- Outside-scope counts: `{counts_text}`",
                f"- Outside-scope PnL USD: `{float(row.get('outside_scope_profit_usd') or 0.0):+.2f}`",
                f"- Oldest outside-scope open: `{row.get('oldest_outside_scope_opened_at') or '-'}`",
                f"- Recommended action: `{row['recommended_action']}`",
                "- Operator guardrail: `do not auto-close from this audit surface; legacy inventory requires explicit human review/approval`",
                f"- Notes: `{row['notes'] or '-'}`",
                "",
            ]
        )
        sample_positions = row.get("outside_scope_positions") or []
        if sample_positions:
            lines.append("| Ticket | Symbol | Side | Volume | Open Price | PnL USD | Comment | Opened At |")
            lines.append("| ---: | --- | --- | ---: | ---: | ---: | --- | --- |")
            for pos in sample_positions[:12]:
                lines.append(
                    f"| {int(pos.get('ticket') or 0)} | {pos.get('symbol') or '-'} | {pos.get('side') or '-'} | "
                    f"{float(pos.get('volume') or 0.0):.2f} | {float(pos.get('price_open') or 0.0):.5f} | "
                    f"{float(pos.get('profit_usd') or 0.0):+.2f} | {pos.get('comment') or '-'} | {pos.get('opened_at') or '-'} |"
                )
        if len(sample_positions) > 12:
            lines.append("")
            lines.append(f"- truncated ticket sample: showing first `12` of `{len(sample_positions)}` positions")
        lines.append("")

    lines.extend(["## Unassigned Live-Symbol Inventory", ""])
    unassigned_positions = list(payload.get("unassigned_live_symbol_positions") or [])
    if not unassigned_positions:
        lines.append("- none")
    else:
        total_profit = sum(float(row.get("profit_usd") or 0.0) for row in unassigned_positions)
        counts: dict[str, int] = {}
        for row in unassigned_positions:
            symbol = str(row.get("symbol") or "").upper()
            counts[symbol] = counts.get(symbol, 0) + 1
        counts_text = ", ".join(f"{symbol}:{count}" for symbol, count in sorted(counts.items()))
        lines.extend(
            [
                "- Meaning: broker positions exist on currently live-traded symbols, but their broker magic is not claimed by any enabled live lane.",
                "- Operator guardrail: `do not assume these positions are managed just because the symbol is live elsewhere; review before closing or attributing them to a lane.`",
                f"- Counts: `{counts_text or '-'}`",
                f"- Floating PnL USD: `{total_profit:+.2f}`",
                "",
                "| Ticket | Symbol | Magic | Side | Volume | Open Price | PnL USD | Comment | Opened At |",
                "| ---: | --- | ---: | --- | ---: | ---: | ---: | --- | --- |",
            ]
        )
        for pos in unassigned_positions[:12]:
            lines.append(
                f"| {int(pos.get('ticket') or 0)} | {pos.get('symbol') or '-'} | {int(pos.get('magic') or 0)} | {pos.get('side') or '-'} | "
                f"{float(pos.get('volume') or 0.0):.2f} | {float(pos.get('price_open') or 0.0):.5f} | "
                f"{float(pos.get('profit_usd') or 0.0):+.2f} | {pos.get('comment') or '-'} | {pos.get('opened_at') or '-'} |"
            )
        if len(unassigned_positions) > 12:
            lines.append("")
            lines.append(f"- truncated ticket sample: showing first `12` of `{len(unassigned_positions)}` positions")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    REPORT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    REPORT_MD.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit live magic broker scope against current lane scope")
    parser.add_argument("--json", action="store_true", help="Print JSON payload instead of Markdown")
    args = parser.parse_args()

    payload = build_payload()
    write_outputs(payload)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(render_markdown(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
