#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

LIVE_MAGIC_SCOPE_JSON = REPORTS / "live_magic_scope_audit.json"
MT5_VISIBILITY_JSON = REPORTS / "mt5_user_visibility_board.json"
LIVE_M5_PORTFOLIO_JSON = REPORTS / "live_m5_portfolio_board.json"

OUTPUT_JSON = REPORTS / "detached_inventory_review.json"
OUTPUT_MD = REPORTS / "detached_inventory_review.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def build_payload(
    live_magic_scope_payload: dict[str, Any],
    mt5_visibility_payload: dict[str, Any],
    live_m5_portfolio_payload: dict[str, Any],
) -> dict[str, Any]:
    live_scope_rows = list(live_magic_scope_payload.get("rows") or [])
    active_legacy_rows = [
        row
        for row in live_scope_rows
        if int(row.get("outside_scope_open_count") or 0) > 0
    ]
    unassigned_positions = list(live_magic_scope_payload.get("unassigned_live_symbol_positions") or [])
    ghost_rows = list(live_m5_portfolio_payload.get("ghost_rows") or [])
    account_snapshot = live_magic_scope_payload.get("account_snapshot") if isinstance(live_magic_scope_payload.get("account_snapshot"), dict) else {}
    visibility_summary = mt5_visibility_payload.get("summary") if isinstance(mt5_visibility_payload.get("summary"), dict) else {}

    active_legacy_positions = sum(parse_int(row.get("outside_scope_open_count")) for row in active_legacy_rows)
    active_legacy_profit_usd = round(sum(parse_float(row.get("outside_scope_profit_usd")) for row in active_legacy_rows), 2)
    unassigned_positions_count = len(unassigned_positions)
    unassigned_profit_usd = round(sum(parse_float(row.get("profit_usd")) for row in unassigned_positions), 2)
    active_detached_positions = active_legacy_positions + unassigned_positions_count
    active_detached_profit_usd = round(active_legacy_profit_usd + unassigned_profit_usd, 2)

    historical_ghost_positions = sum(parse_int(row.get("position_count")) for row in ghost_rows)
    historical_ghost_profit_usd = round(sum(parse_float(row.get("floating_usd")) for row in ghost_rows), 2)
    active_ghost_positions = sum(
        parse_int(row.get("position_count"))
        for row in ghost_rows
        if str(row.get("audit_state") or "") == "active"
    )
    stale_ghost_positions = sum(
        parse_int(row.get("position_count"))
        for row in ghost_rows
        if str(row.get("audit_state") or "") != "active"
    )

    account_live_pnl_usd = parse_float(account_snapshot.get("profit_usd"))
    active_detached_live_pnl_share_pct = (
        round((active_detached_profit_usd / account_live_pnl_usd) * 100.0, 1)
        if account_live_pnl_usd not in (0.0, -0.0)
        else None
    )

    decision_queue: list[dict[str, Any]] = []
    for row in active_legacy_rows:
        lane = str(row.get("lane") or "")
        counts = dict(row.get("outside_scope_symbols") or {})
        symbols_text = ", ".join(f"{symbol}:{count}" for symbol, count in sorted(counts.items())) or "-"
        decision_queue.append(
            {
                "item": f"{lane}_legacy_outside_scope",
                "bucket": "active_detached_inventory",
                "decision": "carry_vs_close",
                "status": "needs_human_review",
                "positions": parse_int(row.get("outside_scope_open_count")),
                "floating_pnl_usd": round(parse_float(row.get("outside_scope_profit_usd")), 2),
                "read": (
                    f"{lane} still has {parse_int(row.get('outside_scope_open_count'))} outside-scope position(s) "
                    f"({symbols_text}) with floating PnL {parse_float(row.get('outside_scope_profit_usd')):+.2f}."
                ),
                "recommended_action": str(row.get("recommended_action") or ""),
                "oldest_opened_at": str(row.get("oldest_outside_scope_opened_at") or ""),
            }
        )
    if unassigned_positions:
        symbols: dict[str, int] = {}
        for pos in unassigned_positions:
            symbol = str(pos.get("symbol") or "").upper()
            symbols[symbol] = symbols.get(symbol, 0) + 1
        symbols_text = ", ".join(f"{symbol}:{count}" for symbol, count in sorted(symbols.items())) or "-"
        oldest_opened_at = min(str(pos.get("opened_at") or "") for pos in unassigned_positions if str(pos.get("opened_at") or ""))
        decision_queue.append(
            {
                "item": "unassigned_live_symbol_inventory",
                "bucket": "active_detached_inventory",
                "decision": "attribute_vs_close",
                "status": "needs_human_review",
                "positions": unassigned_positions_count,
                "floating_pnl_usd": unassigned_profit_usd,
                "read": (
                    f"{unassigned_positions_count} live-symbol position(s) are not mapped to any enabled live magic "
                    f"({symbols_text}) with floating PnL {unassigned_profit_usd:+.2f}."
                ),
                "recommended_action": "review_magic_origin_before_close",
                "oldest_opened_at": oldest_opened_at,
            }
        )
    if ghost_rows:
        decision_queue.append(
            {
                "item": "historical_ghost_carry_audit",
                "bucket": "historical_reference_only",
                "decision": "refresh_if_needed",
                "status": "non_blocking",
                "positions": historical_ghost_positions,
                "floating_pnl_usd": historical_ghost_profit_usd,
                "read": (
                    f"Ghost carry audit still records {historical_ghost_positions} historical paused/stale position(s) "
                    f"with combined floating {historical_ghost_profit_usd:+.2f}; current active count is {active_ghost_positions}."
                ),
                "recommended_action": "refresh_before_any_manual_liquidation_based_on_historical_tickets",
                "oldest_opened_at": "",
            }
        )

    summary = {
        "active_detached_positions": active_detached_positions,
        "active_legacy_positions": active_legacy_positions,
        "unassigned_live_symbol_positions": unassigned_positions_count,
        "active_detached_profit_usd": active_detached_profit_usd,
        "active_detached_live_pnl_share_pct": active_detached_live_pnl_share_pct,
        "historical_ghost_positions": historical_ghost_positions,
        "active_ghost_positions": active_ghost_positions,
        "stale_ghost_positions": stale_ghost_positions,
        "historical_ghost_profit_usd": historical_ghost_profit_usd,
        "needs_human_decision_count": sum(
            1 for row in decision_queue if str(row.get("status") or "") == "needs_human_review"
        ),
        "visible_now_lanes": parse_int(visibility_summary.get("live_lanes_visible_now")),
        "enabled_live_lanes": parse_int(visibility_summary.get("enabled_live_lane_count")),
        "disabled_live_ids": parse_int(visibility_summary.get("disabled_live_lane_count")),
    }

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(LIVE_MAGIC_SCOPE_JSON.relative_to(ROOT)),
            str(MT5_VISIBILITY_JSON.relative_to(ROOT)),
            str(LIVE_M5_PORTFOLIO_JSON.relative_to(ROOT)),
        ],
        "account_snapshot": account_snapshot,
        "summary": summary,
        "active_legacy_rows": active_legacy_rows,
        "unassigned_live_symbol_positions": unassigned_positions,
        "ghost_rows": ghost_rows,
        "decision_queue": decision_queue,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    account_snapshot = payload.get("account_snapshot") if isinstance(payload.get("account_snapshot"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    active_legacy_rows = list(payload.get("active_legacy_rows") or [])
    unassigned_positions = list(payload.get("unassigned_live_symbol_positions") or [])
    ghost_rows = list(payload.get("ghost_rows") or [])
    decision_queue = list(payload.get("decision_queue") or [])

    lines = [
        "# Detached Inventory Review",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        (
            "- Summary: "
            f"`active_detached_positions={parse_int(summary.get('active_detached_positions'))}` "
            f"`active_detached_pnl={parse_float(summary.get('active_detached_profit_usd')):+.2f}` "
            f"`active_legacy_positions={parse_int(summary.get('active_legacy_positions'))}` "
            f"`unassigned_live_symbol_positions={parse_int(summary.get('unassigned_live_symbol_positions'))}` "
            f"`historical_ghost_positions={parse_int(summary.get('historical_ghost_positions'))}` "
            f"`needs_human_decision_count={parse_int(summary.get('needs_human_decision_count'))}`"
        ),
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

    lines.extend(["## Active Detached Inventory Affecting MT5 Now", ""])
    lines.append(
        f"- Current detached inventory still moving MT5 equity: `{parse_int(summary.get('active_detached_positions'))}` position(s), `{parse_float(summary.get('active_detached_profit_usd')):+.2f}`."
    )
    if summary.get("active_detached_live_pnl_share_pct") is not None:
        lines.append(
            f"- Share of current MT5 live PnL: `{parse_float(summary.get('active_detached_live_pnl_share_pct')):+.1f}%`."
        )
    lines.append("")

    lines.extend(["### Legacy Outside-Scope Inventory Under Enabled Live Magics", ""])
    if active_legacy_rows:
        lines.append("| Lane | Symbols | Positions | Floating PnL USD | Oldest Open | Action |")
        lines.append("| --- | --- | ---: | ---: | --- | --- |")
        for row in active_legacy_rows:
            symbols = ", ".join(f"{symbol}:{count}" for symbol, count in sorted(dict(row.get("outside_scope_symbols") or {}).items())) or "-"
            lines.append(
                f"| {row.get('lane') or '-'} | {symbols} | {parse_int(row.get('outside_scope_open_count'))} | "
                f"{parse_float(row.get('outside_scope_profit_usd')):+.2f} | {row.get('oldest_outside_scope_opened_at') or '-'} | "
                f"{row.get('recommended_action') or '-'} |"
            )
        lines.append("")
    else:
        lines.extend(["- none", ""])

    lines.extend(["### Unassigned Live-Symbol Inventory", ""])
    if unassigned_positions:
        lines.append("| Ticket | Symbol | Magic | Side | Volume | Open Price | PnL USD | Opened At |")
        lines.append("| ---: | --- | ---: | --- | ---: | ---: | ---: | --- |")
        for row in unassigned_positions[:12]:
            lines.append(
                f"| {parse_int(row.get('ticket'))} | {row.get('symbol') or '-'} | {parse_int(row.get('magic'))} | "
                f"{row.get('side') or '-'} | {parse_float(row.get('volume')):.2f} | {parse_float(row.get('price_open')):.5f} | "
                f"{parse_float(row.get('profit_usd')):+.2f} | {row.get('opened_at') or '-'} |"
            )
        lines.append("")
    else:
        lines.extend(["- none", ""])

    lines.extend(["## Historical Ghost Carry Reference", ""])
    lines.append(
        f"- Historical ghost audit positions: `{parse_int(summary.get('historical_ghost_positions'))}` with floating `{parse_float(summary.get('historical_ghost_profit_usd')):+.2f}`."
    )
    lines.append(
        f"- Current ghost reconciliation: active `{parse_int(summary.get('active_ghost_positions'))}` / stale-or-cleared `{parse_int(summary.get('stale_ghost_positions'))}`."
    )
    lines.append("")
    if ghost_rows:
        lines.append("| Lane | Symbol | Magic | Audit State | Tickets | Floating USD |")
        lines.append("| --- | --- | ---: | --- | ---: | ---: |")
        for row in ghost_rows:
            lines.append(
                f"| {row.get('lane') or '-'} | {row.get('symbol') or '-'} | {parse_int(row.get('live_magic'))} | "
                f"{row.get('audit_state') or '-'} | {parse_int(row.get('position_count'))} | {parse_float(row.get('floating_usd')):+.2f} |"
            )
        lines.append("")
    else:
        lines.extend(["- none", ""])

    lines.extend(["## Decision Queue", ""])
    if decision_queue:
        lines.append("| Item | Bucket | Decision | Status | Positions | Floating USD | Read |")
        lines.append("| --- | --- | --- | --- | ---: | ---: | --- |")
        for row in decision_queue:
            lines.append(
                f"| {row.get('item') or '-'} | {row.get('bucket') or '-'} | {row.get('decision') or '-'} | "
                f"{row.get('status') or '-'} | {parse_int(row.get('positions'))} | "
                f"{parse_float(row.get('floating_pnl_usd')):+.2f} | {row.get('read') or '-'} |"
            )
        lines.append("")
    else:
        lines.extend(["- none", ""])

    lines.extend(
        [
            "## Read",
            "",
            "- Use this board when the question is not just which lane is live, but which positions are still moving the human MT5 account outside the clean enabled-lane mapping.",
            "- `active_detached_inventory` means broker-real positions that still affect MT5 equity right now and need a carry-vs-close or attribute-vs-close decision.",
            "- `historical_ghost_carry_reference` is not current broker truth by itself; use it as ticket-level context before liquidating anything based on stale historical carry.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    payload = build_payload(
        load_json(LIVE_MAGIC_SCOPE_JSON),
        load_json(MT5_VISIBILITY_JSON),
        load_json(LIVE_M5_PORTFOLIO_JSON),
    )
    write_outputs(payload)
    print(f"wrote {OUTPUT_JSON}")
    print(f"wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
