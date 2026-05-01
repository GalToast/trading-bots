#!/usr/bin/env python3
from __future__ import annotations

import json
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

DETACHED_REVIEW_JSON = REPORTS / "detached_inventory_review.json"
DETACHED_ORIGIN_JSON = REPORTS / "detached_inventory_origin_review.json"

OUTPUT_JSON = REPORTS / "detached_inventory_action_board.json"
OUTPUT_MD = REPORTS / "detached_inventory_action_board.md"

REFRESH_COMMANDS = [
    "python scripts/build_live_magic_scope_audit.py",
    "python scripts/build_mt5_user_visibility_board.py",
    "python scripts/build_detached_inventory_review.py",
    "python scripts/build_detached_inventory_origin_review.py",
    "python scripts/build_detached_inventory_action_board.py",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def bucket_item_name(row: dict[str, Any]) -> str:
    bucket = str(row.get("bucket") or "")
    if bucket == "active_legacy_outside_scope":
        owner_lane = str(row.get("owner_lane") or "")
        return f"{owner_lane}_legacy_outside_scope" if owner_lane else "legacy_outside_scope"
    if bucket == "unassigned_live_symbol":
        return "unassigned_live_symbol_inventory"
    return bucket or "unknown"


def summarize_symbols(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        counts[symbol] = counts.get(symbol, 0) + 1
    return dict(sorted(counts.items()))


def common_nonempty_comment(rows: list[dict[str, Any]]) -> str:
    comments = {
        str(row.get("comment") or "").strip()
        for row in rows
        if str(row.get("comment") or "").strip()
    }
    if len(comments) == 1:
        return next(iter(comments))
    return ""


def make_close_argv(rows: list[dict[str, Any]], *, apply: bool) -> list[str]:
    parts = ["python", "scripts/operators/mt5_close_filtered.py"]
    bucket = str(rows[0].get("bucket") or "") if rows else ""
    if bucket == "active_legacy_outside_scope":
        magic = parse_int(rows[0].get("magic"))
        if magic > 0:
            parts.extend(["--magic", str(magic)])
        for symbol in summarize_symbols(rows):
            parts.extend(["--symbol", symbol])
        comment = common_nonempty_comment(rows)
        if comment:
            parts.extend(["--comment-contains", comment])
    else:
        for ticket in sorted({parse_int(row.get("ticket")) for row in rows if parse_int(row.get("ticket")) > 0}):
            parts.extend(["--ticket", str(ticket)])
    parts.extend(["--expect-count", str(len(rows))])
    if apply:
        parts.append("--apply")
    return parts


def make_close_command(rows: list[dict[str, Any]], *, apply: bool) -> str:
    return shell_join(make_close_argv(rows, apply=apply))


def summarize_origin_classes(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        origin_class = str(row.get("origin_class") or "")
        if not origin_class:
            continue
        counts[origin_class] = counts.get(origin_class, 0) + 1
    return dict(sorted(counts.items()))


def build_action_items(
    detached_review_payload: dict[str, Any],
    detached_origin_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    origin_rows = list(detached_origin_payload.get("rows") or [])
    rows_by_item: dict[str, list[dict[str, Any]]] = {}
    for row in origin_rows:
        rows_by_item.setdefault(bucket_item_name(row), []).append(row)

    items: list[dict[str, Any]] = []
    for queue_row in list(detached_review_payload.get("decision_queue") or []):
        if str(queue_row.get("bucket") or "") != "active_detached_inventory":
            continue
        item_name = str(queue_row.get("item") or "")
        matched_rows = rows_by_item.get(item_name, [])
        if not matched_rows:
            continue
        symbols = summarize_symbols(matched_rows)
        origin_classes = summarize_origin_classes(matched_rows)
        unique_origin_reads = sorted({str(row.get("origin_read") or "").strip() for row in matched_rows if str(row.get("origin_read") or "").strip()})
        oldest_opened_at = min(
            (str(row.get("opened_at") or "") for row in matched_rows if str(row.get("opened_at") or "")),
            default=str(queue_row.get("oldest_opened_at") or ""),
        )
        dry_run_argv = make_close_argv(matched_rows, apply=False)
        apply_argv = make_close_argv(matched_rows, apply=True)
        item_payload = {
            "item": item_name,
            "bucket": str(queue_row.get("bucket") or ""),
            "decision": str(queue_row.get("decision") or ""),
            "status": str(queue_row.get("status") or ""),
            "recommended_action": str(queue_row.get("recommended_action") or ""),
            "positions": parse_int(queue_row.get("positions")),
            "floating_pnl_usd": round(sum(parse_float(row.get("profit_usd")) for row in matched_rows), 2),
            "oldest_opened_at": oldest_opened_at,
            "symbols": symbols,
            "owner_lane": str(matched_rows[0].get("owner_lane") or ""),
            "magic": parse_int(matched_rows[0].get("magic")),
            "origin_classes": origin_classes,
            "origin_reads": unique_origin_reads,
            "ticket_count": len(matched_rows),
            "tickets_sample": [parse_int(row.get("ticket")) for row in matched_rows[:5]],
            "dry_run_argv": dry_run_argv,
            "apply_argv": apply_argv,
            "dry_run_command": shell_join(dry_run_argv),
            "apply_command": shell_join(apply_argv),
            "expected_match_count": len(matched_rows),
            "operator_read": str(queue_row.get("read") or ""),
        }
        items.append(item_payload)

    items.sort(key=lambda row: (-parse_float(row.get("floating_pnl_usd")), row.get("item") or ""))
    return items


def build_scenarios(
    items: list[dict[str, Any]],
    account_snapshot: dict[str, Any],
    detached_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    current_positions = parse_int(detached_summary.get("active_detached_positions"))
    current_pnl = round(parse_float(detached_summary.get("active_detached_profit_usd")), 2)
    live_pnl = parse_float(account_snapshot.get("profit_usd"))
    item_by_name = {str(item.get("item") or ""): item for item in items}

    scenario_defs = [
        ("keep_all", "Keep current detached inventory", []),
        (
            "close_manual_btc_only",
            "Close manual/client BTC magic-zero inventory only",
            ["unassigned_live_symbol_inventory"],
        ),
        (
            "close_legacy_usdjpy_only",
            "Close live_rearm legacy USDJPY carry only",
            ["live_rearm_941777_legacy_outside_scope"],
        ),
        (
            "close_all_active_detached",
            "Close both current detached inventory blocks",
            ["unassigned_live_symbol_inventory", "live_rearm_941777_legacy_outside_scope"],
        ),
    ]

    scenarios: list[dict[str, Any]] = []
    for scenario_id, label, selected_item_names in scenario_defs:
        selected_items = [item_by_name[name] for name in selected_item_names if name in item_by_name]
        removed_positions = sum(parse_int(item.get("positions")) for item in selected_items)
        removed_pnl = round(sum(parse_float(item.get("floating_pnl_usd")) for item in selected_items), 2)
        remaining_positions = max(0, current_positions - removed_positions)
        remaining_pnl = round(current_pnl - removed_pnl, 2)
        remaining_share = (
            round((remaining_pnl / live_pnl) * 100.0, 1)
            if live_pnl not in (0.0, -0.0)
            else None
        )
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "label": label,
                "selected_items": selected_item_names,
                "removed_positions": removed_positions,
                "removed_pnl_usd": removed_pnl,
                "remaining_detached_positions": remaining_positions,
                "remaining_detached_pnl_usd": remaining_pnl,
                "remaining_detached_live_pnl_share_pct": remaining_share,
            }
        )
    return scenarios


def build_payload(
    detached_review_payload: dict[str, Any],
    detached_origin_payload: dict[str, Any],
) -> dict[str, Any]:
    account_snapshot = detached_review_payload.get("account_snapshot") if isinstance(detached_review_payload.get("account_snapshot"), dict) else {}
    detached_summary = detached_review_payload.get("summary") if isinstance(detached_review_payload.get("summary"), dict) else {}
    origin_summary = detached_origin_payload.get("summary") if isinstance(detached_origin_payload.get("summary"), dict) else {}
    mt5_connection = detached_origin_payload.get("mt5_connection") if isinstance(detached_origin_payload.get("mt5_connection"), dict) else {}

    items = build_action_items(detached_review_payload, detached_origin_payload)
    scenarios = build_scenarios(items, account_snapshot, detached_summary)

    summary = {
        "active_detached_positions": parse_int(detached_summary.get("active_detached_positions")),
        "active_detached_profit_usd": round(parse_float(detached_summary.get("active_detached_profit_usd")), 2),
        "active_detached_live_pnl_share_pct": detached_summary.get("active_detached_live_pnl_share_pct"),
        "action_item_count": len(items),
        "origin_counts": dict(origin_summary.get("origin_counts") or {}),
        "refresh_command_count": len(REFRESH_COMMANDS),
    }

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(DETACHED_REVIEW_JSON.relative_to(ROOT)),
            str(DETACHED_ORIGIN_JSON.relative_to(ROOT)),
        ],
        "mt5_connection": mt5_connection,
        "account_snapshot": account_snapshot,
        "summary": summary,
        "action_items": items,
        "scenarios": scenarios,
        "refresh_commands": list(REFRESH_COMMANDS),
        "historical_reference_note": (
            "Historical ghost carry remains reference-only here; this action board models only the currently active detached inventory that still moves MT5 equity now."
        ),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    account_snapshot = payload.get("account_snapshot") if isinstance(payload.get("account_snapshot"), dict) else {}
    mt5_connection = payload.get("mt5_connection") if isinstance(payload.get("mt5_connection"), dict) else {}
    action_items = list(payload.get("action_items") or [])
    scenarios = list(payload.get("scenarios") or [])
    refresh_commands = list(payload.get("refresh_commands") or [])

    lines = [
        "# Detached Inventory Action Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        (
            "- Summary: "
            f"`active_detached_positions={parse_int(summary.get('active_detached_positions'))}` "
            f"`active_detached_pnl={parse_float(summary.get('active_detached_profit_usd')):+.2f}` "
            f"`active_detached_live_pnl_share_pct={parse_float(summary.get('active_detached_live_pnl_share_pct')):+.1f}` "
            f"`action_item_count={parse_int(summary.get('action_item_count'))}`"
        ),
        f"- MT5 guard: `{'ok' if mt5_connection.get('identity_ok') else mt5_connection.get('reason', 'unknown')}`",
        "",
        "## Account Snapshot",
        "",
    ]
    if account_snapshot:
        lines.extend(
            [
                f"- Equity: `${parse_float(account_snapshot.get('equity_usd')):,.2f}`",
                f"- Balance: `${parse_float(account_snapshot.get('balance_usd')):,.2f}`",
                f"- Live PnL: `{parse_float(account_snapshot.get('profit_usd')):+.2f}`",
                f"- Broker open positions: `{parse_int(account_snapshot.get('position_count'))}`",
                "",
            ]
        )
    else:
        lines.extend(["- unavailable", ""])

    lines.extend(["## Action Queue", ""])
    if action_items:
        lines.append("| Item | Decision | Positions | Floating PnL USD | Origin | Dry Run |")
        lines.append("| --- | --- | ---: | ---: | --- | --- |")
        for item in action_items:
            origin_text = ", ".join(f"{name}:{count}" for name, count in dict(item.get("origin_classes") or {}).items()) or "-"
            lines.append(
                f"| {item.get('item') or '-'} | {item.get('decision') or '-'} | {parse_int(item.get('positions'))} | "
                f"{parse_float(item.get('floating_pnl_usd')):+.2f} | {origin_text} | `{item.get('dry_run_command') or '-'}` |"
            )
        lines.append("")
        for item in action_items:
            lines.append(f"### {item.get('item') or '-'}")
            lines.append("")
            lines.append(f"- Status: `{item.get('status') or '-'}`")
            if item.get("owner_lane"):
                lines.append(f"- Owner lane: `{item.get('owner_lane')}`")
            lines.append(
                f"- Current scope: `symbols={', '.join(f'{symbol}:{count}' for symbol, count in dict(item.get('symbols') or {}).items()) or '-'}` "
                f"`magic={parse_int(item.get('magic'))}`"
            )
            lines.append(f"- Expected dry-run match count: `{parse_int(item.get('expected_match_count'))}`")
            lines.append(f"- Current floating PnL USD: `{parse_float(item.get('floating_pnl_usd')):+.2f}`")
            lines.append(f"- Read: `{item.get('operator_read') or '-'}`")
            if list(item.get("origin_reads") or []):
                lines.append(f"- Origin read: `{'; '.join(item.get('origin_reads') or [])}`")
            lines.append("- Dry run:")
            lines.append("")
            lines.append("```bash")
            lines.append(str(item.get("dry_run_command") or ""))
            lines.append("```")
            lines.append("")
            lines.append("- Apply only after dry-run matches exactly:")
            lines.append("")
            lines.append("```bash")
            lines.append(str(item.get("apply_command") or ""))
            lines.append("```")
            lines.append("")
    else:
        lines.extend(["- none", ""])

    lines.extend(["## Scenario Impact", ""])
    if scenarios:
        lines.append("| Scenario | Removes | Removed PnL USD | Remaining Detached Positions | Remaining Detached PnL USD | Remaining Share Of Current Live PnL |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
        for scenario in scenarios:
            share = scenario.get("remaining_detached_live_pnl_share_pct")
            share_text = f"{parse_float(share):+.1f}" if share is not None else "-"
            lines.append(
                f"| {scenario.get('label') or '-'} | {parse_int(scenario.get('removed_positions'))} | "
                f"{parse_float(scenario.get('removed_pnl_usd')):+.2f} | {parse_int(scenario.get('remaining_detached_positions'))} | "
                f"{parse_float(scenario.get('remaining_detached_pnl_usd')):+.2f} | {share_text}% |"
            )
        lines.append("")
        lines.append(f"- {payload.get('historical_reference_note') or ''}")
        lines.append("- Scenario math models detached-exposure reduction only; it does not predict realized balance/equity after the close fills execute.")
        lines.append("")
    else:
        lines.extend(["- none", ""])

    lines.extend(["## Post-Action Refresh", ""])
    lines.append("- Re-run these after any applied close so the MT5 visibility surfaces reflect broker truth again:")
    lines.append("")
    lines.append("```bash")
    for command in refresh_commands:
        lines.append(command)
    lines.append("```")
    lines.append("")
    lines.append("- If an apply run returns nonzero or prints `post_apply_remaining_matches>0`, stop there, refresh the boards, and reassess from fresh broker truth before sending another batch close.")
    lines.append("")
    lines.extend(
        [
            "## Read",
            "",
            "- Use this board after the detached-inventory review/origin boards when the room is ready to take or defer action, not when it still needs attribution work.",
            "- The BTC magic-zero block is modeled as exact-ticket close path because it is manual/client inventory, not an enabled-lane position.",
            "- The USDJPY legacy block is modeled as grouped filter close path because it is prior live-lane inventory under the current `941777` magic and shares one symbol/comment pattern.",
            "- `mt5_close_filtered.py` now stops after the first failed close in an apply batch, then immediately re-reads broker positions for the same filter so partial-close state is visible before the next operator step.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    payload = build_payload(
        load_json(DETACHED_REVIEW_JSON),
        load_json(DETACHED_ORIGIN_JSON),
    )
    write_outputs(payload)
    print(f"wrote {OUTPUT_JSON}")
    print(f"wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
