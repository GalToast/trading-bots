#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

import mt5_terminal_guard


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

LIVE_MAGIC_SCOPE_JSON = REPORTS / "live_magic_scope_audit.json"
DETACHED_REVIEW_JSON = REPORTS / "detached_inventory_review.json"

OUTPUT_JSON = REPORTS / "detached_inventory_origin_review.json"
OUTPUT_MD = REPORTS / "detached_inventory_origin_review.md"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


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


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def epoch_to_iso(value: Any) -> str:
    try:
        seconds = int(value or 0)
    except Exception:
        return ""
    if seconds <= 0:
        return ""
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def enum_name_map(module: Any, prefix: str) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for name in dir(module):
        if not name.startswith(prefix):
            continue
        value = getattr(module, name, None)
        if isinstance(value, int):
            mapping[value] = name
    return mapping


def collect_detached_positions(
    live_magic_scope_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lane_row in list(live_magic_scope_payload.get("rows") or []):
        lane_name = str(lane_row.get("lane") or "")
        for pos in list(lane_row.get("outside_scope_positions") or []):
            rows.append(
                {
                    "bucket": "active_legacy_outside_scope",
                    "owner_lane": lane_name,
                    "ticket": parse_int(pos.get("ticket")),
                    "symbol": str(pos.get("symbol") or ""),
                    "magic": parse_int(lane_row.get("live_magic")),
                    "side": str(pos.get("side") or ""),
                    "volume": parse_float(pos.get("volume")),
                    "price_open": parse_float(pos.get("price_open")),
                    "profit_usd": parse_float(pos.get("profit_usd")),
                    "comment": str(pos.get("comment") or ""),
                    "opened_at": str(pos.get("opened_at") or ""),
                }
            )
    for pos in list(live_magic_scope_payload.get("unassigned_live_symbol_positions") or []):
        rows.append(
            {
                "bucket": "unassigned_live_symbol",
                "owner_lane": "",
                "ticket": parse_int(pos.get("ticket")),
                "symbol": str(pos.get("symbol") or ""),
                "magic": parse_int(pos.get("magic")),
                "side": str(pos.get("side") or ""),
                "volume": parse_float(pos.get("volume")),
                "price_open": parse_float(pos.get("price_open")),
                "profit_usd": parse_float(pos.get("profit_usd")),
                "comment": str(pos.get("comment") or ""),
                "opened_at": str(pos.get("opened_at") or ""),
            }
        )
    rows.sort(key=lambda row: (row["bucket"], row["symbol"], row["ticket"]))
    return rows


def history_deals_snapshot(
    *,
    mt5_module: Any,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    deal_reason_names = enum_name_map(mt5_module, "DEAL_REASON_")
    deal_entry_names = enum_name_map(mt5_module, "DEAL_ENTRY_")
    rows: list[dict[str, Any]] = []
    for deal in mt5_module.history_deals_get(start, end) or []:
        rows.append(
            {
                "ticket": parse_int(getattr(deal, "ticket", 0)),
                "order": parse_int(getattr(deal, "order", 0)),
                "position_id": parse_int(getattr(deal, "position_id", 0)),
                "symbol": str(getattr(deal, "symbol", "") or ""),
                "magic": parse_int(getattr(deal, "magic", 0)),
                "comment": str(getattr(deal, "comment", "") or ""),
                "reason": parse_int(getattr(deal, "reason", -1), -1),
                "reason_name": deal_reason_names.get(parse_int(getattr(deal, "reason", -1), -1), ""),
                "entry": parse_int(getattr(deal, "entry", -1), -1),
                "entry_name": deal_entry_names.get(parse_int(getattr(deal, "entry", -1), -1), ""),
                "volume": parse_float(getattr(deal, "volume", 0.0)),
                "price": parse_float(getattr(deal, "price", 0.0)),
                "profit": parse_float(getattr(deal, "profit", 0.0)),
                "time": epoch_to_iso(getattr(deal, "time", 0)),
            }
        )
    return rows


def history_orders_snapshot(
    *,
    mt5_module: Any,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    order_reason_names = enum_name_map(mt5_module, "ORDER_REASON_")
    order_type_names = enum_name_map(mt5_module, "ORDER_TYPE_")
    rows: list[dict[str, Any]] = []
    for order in mt5_module.history_orders_get(start, end) or []:
        ticket = parse_int(getattr(order, "ticket", 0))
        rows.append(
            {
                "ticket": ticket,
                "position_id": parse_int(getattr(order, "position_id", 0)),
                "symbol": str(getattr(order, "symbol", "") or ""),
                "magic": parse_int(getattr(order, "magic", 0)),
                "comment": str(getattr(order, "comment", "") or ""),
                "reason": parse_int(getattr(order, "reason", -1), -1),
                "reason_name": order_reason_names.get(parse_int(getattr(order, "reason", -1), -1), ""),
                "type": parse_int(getattr(order, "type", -1), -1),
                "type_name": order_type_names.get(parse_int(getattr(order, "type", -1), -1), ""),
                "volume_initial": parse_float(getattr(order, "volume_initial", 0.0)),
                "price_open": parse_float(getattr(order, "price_open", 0.0)),
                "time_setup": epoch_to_iso(getattr(order, "time_setup", 0)),
                "time_done": epoch_to_iso(getattr(order, "time_done", 0)),
            }
        )
    return rows


def price_matches(position_price: float, candidate_price: float) -> bool:
    if position_price <= 0 or candidate_price <= 0:
        return False
    tolerance = max(0.0002, position_price * 0.00002)
    return abs(position_price - candidate_price) <= tolerance


def select_open_deal(
    position: dict[str, Any],
    deals: list[dict[str, Any]],
) -> dict[str, Any]:
    ticket = parse_int(position.get("ticket"))
    symbol = str(position.get("symbol") or "")
    opened_at = parse_iso(position.get("opened_at"))
    price_open = parse_float(position.get("price_open"))
    direct = [
        row
        for row in deals
        if parse_int(row.get("position_id")) == ticket
    ]
    if not direct:
        direct = [
            row
            for row in deals
            if str(row.get("symbol") or "") == symbol
            and price_matches(price_open, parse_float(row.get("price")))
        ]
    if opened_at is not None:
        lower = opened_at - timedelta(minutes=10)
        upper = opened_at + timedelta(minutes=10)
        direct = [
            row
            for row in direct
            if (parse_iso(row.get("time")) or opened_at) >= lower
            and (parse_iso(row.get("time")) or opened_at) <= upper
        ] or direct
    if not direct:
        return {}
    direct.sort(key=lambda row: (parse_iso(row.get("time")) or utc_now(), parse_int(row.get("ticket"))))
    return direct[0]


def select_open_order(
    position: dict[str, Any],
    orders: list[dict[str, Any]],
    open_deal: dict[str, Any],
) -> dict[str, Any]:
    deal_order = parse_int(open_deal.get("order"))
    ticket = parse_int(position.get("ticket"))
    symbol = str(position.get("symbol") or "")
    candidates = []
    if deal_order > 0:
        candidates = [row for row in orders if parse_int(row.get("ticket")) == deal_order]
    if not candidates:
        candidates = [row for row in orders if parse_int(row.get("position_id")) == ticket]
    if not candidates:
        candidates = [row for row in orders if str(row.get("symbol") or "") == symbol]
    if not candidates:
        return {}
    candidates.sort(key=lambda row: (parse_iso(row.get("time_setup")) or utc_now(), parse_int(row.get("ticket"))))
    return candidates[0]


def classify_origin(
    position: dict[str, Any],
    open_deal: dict[str, Any],
    open_order: dict[str, Any],
) -> tuple[str, str]:
    bucket = str(position.get("bucket") or "")
    comment = str(open_deal.get("comment") or open_order.get("comment") or position.get("comment") or "")
    deal_magic = parse_int(open_deal.get("magic"))
    order_magic = parse_int(open_order.get("magic"))
    current_magic = parse_int(position.get("magic"))
    reason_name = str(open_deal.get("reason_name") or open_order.get("reason_name") or "")

    if bucket == "active_legacy_outside_scope" and comment.startswith("PLIVE-LATTICE"):
        return "prior_live_lane_inventory", "comment prefix indicates prior lattice live inventory under the current live magic"
    if deal_magic > 0 and deal_magic == current_magic:
        return "ea_open_under_current_magic", "open deal magic matches the current position magic"
    if current_magic == 0 and deal_magic == 0 and "CLIENT" in reason_name:
        return "manual_or_terminal_open_magic_zero", "open deal reason is client-side and magic is zero"
    if current_magic == 0 and order_magic == 0 and reason_name:
        return "unassigned_magic_zero_origin_unclear", "open history keeps magic zero and does not map to an enabled live lane"
    if current_magic == 0:
        return "unassigned_magic_zero_origin_unclear", "current position magic is zero and no enabled live lane claims it"
    return "origin_needs_manual_review", "history clues are insufficient for automatic attribution"


def build_payload(
    live_magic_scope_payload: dict[str, Any],
    detached_review_payload: dict[str, Any],
    *,
    mt5_module: Any = mt5,
) -> dict[str, Any]:
    positions = collect_detached_positions(live_magic_scope_payload)
    account_snapshot = live_magic_scope_payload.get("account_snapshot") if isinstance(live_magic_scope_payload.get("account_snapshot"), dict) else {}
    start_candidates = [parse_iso(row.get("opened_at")) for row in positions if parse_iso(row.get("opened_at")) is not None]
    history_start = (min(start_candidates) - timedelta(days=1)) if start_candidates else (utc_now() - timedelta(days=7))
    history_end = utc_now()

    mt5_ready, mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5_module)
    deals: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    try:
        if mt5_ready:
            deals = history_deals_snapshot(mt5_module=mt5_module, start=history_start, end=history_end)
            orders = history_orders_snapshot(mt5_module=mt5_module, start=history_start, end=history_end)
    finally:
        if mt5_ready:
            mt5_module.shutdown()

    rows: list[dict[str, Any]] = []
    origin_counts: dict[str, int] = {}
    for position in positions:
        open_deal = select_open_deal(position, deals)
        open_order = select_open_order(position, orders, open_deal)
        origin_class, origin_read = classify_origin(position, open_deal, open_order)
        origin_counts[origin_class] = origin_counts.get(origin_class, 0) + 1
        rows.append(
            {
                **position,
                "origin_class": origin_class,
                "origin_read": origin_read,
                "open_deal": open_deal,
                "open_order": open_order,
            }
        )

    summary = {
        "detached_position_count": len(rows),
        "history_window_start": history_start.isoformat(),
        "history_window_end": history_end.isoformat(),
        "origin_counts": dict(sorted(origin_counts.items())),
        "active_detached_positions": parse_int((detached_review_payload.get("summary") or {}).get("active_detached_positions")),
        "active_detached_profit_usd": parse_float((detached_review_payload.get("summary") or {}).get("active_detached_profit_usd")),
        "active_detached_live_pnl_share_pct": (detached_review_payload.get("summary") or {}).get("active_detached_live_pnl_share_pct"),
    }

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(LIVE_MAGIC_SCOPE_JSON.relative_to(ROOT)),
            str(DETACHED_REVIEW_JSON.relative_to(ROOT)),
        ],
        "mt5_connection": mt5_connection,
        "account_snapshot": account_snapshot,
        "summary": summary,
        "rows": rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    mt5_connection = payload.get("mt5_connection") if isinstance(payload.get("mt5_connection"), dict) else {}
    account_snapshot = payload.get("account_snapshot") if isinstance(payload.get("account_snapshot"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    rows = list(payload.get("rows") or [])

    lines = [
        "# Detached Inventory Origin Review",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        f"- MT5 guard: `{'ok' if mt5_connection.get('identity_ok') else mt5_connection.get('reason', 'unknown')}`",
        (
            "- Summary: "
            f"`detached_positions={parse_int(summary.get('detached_position_count'))}` "
            f"`active_detached_pnl={parse_float(summary.get('active_detached_profit_usd')):+.2f}` "
            f"`active_detached_live_pnl_share_pct={parse_float(summary.get('active_detached_live_pnl_share_pct')):+.1f}`"
        ),
        f"- Origin classes: `{json.dumps(summary.get('origin_counts') or {}, sort_keys=True)}`",
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
                "",
            ]
        )
    else:
        lines.extend(["- unavailable", ""])

    lines.extend(["## Position Attribution", ""])
    if rows:
        lines.append("| Ticket | Bucket | Symbol | Magic | PnL USD | Origin Class | Open Deal Reason | Open Deal Magic | Open Comment |")
        lines.append("| ---: | --- | --- | ---: | ---: | --- | --- | ---: | --- |")
        for row in rows:
            open_deal = row.get("open_deal") if isinstance(row.get("open_deal"), dict) else {}
            lines.append(
                f"| {parse_int(row.get('ticket'))} | {row.get('bucket') or '-'} | {row.get('symbol') or '-'} | "
                f"{parse_int(row.get('magic'))} | {parse_float(row.get('profit_usd')):+.2f} | {row.get('origin_class') or '-'} | "
                f"{open_deal.get('reason_name') or '-'} | {parse_int(open_deal.get('magic'))} | {open_deal.get('comment') or row.get('comment') or '-'} |"
            )
        lines.append("")
        for row in rows:
            open_deal = row.get("open_deal") if isinstance(row.get("open_deal"), dict) else {}
            open_order = row.get("open_order") if isinstance(row.get("open_order"), dict) else {}
            lines.append(f"### Ticket {parse_int(row.get('ticket'))} {row.get('symbol') or '-'}")
            lines.append("")
            lines.append(f"- Bucket: `{row.get('bucket') or '-'}`")
            if row.get("owner_lane"):
                lines.append(f"- Owner lane: `{row.get('owner_lane')}`")
            lines.append(f"- Current magic: `{parse_int(row.get('magic'))}`")
            lines.append(f"- Current opened_at: `{row.get('opened_at') or '-'}`")
            lines.append(f"- Current floating PnL USD: `{parse_float(row.get('profit_usd')):+.2f}`")
            lines.append(f"- Origin class: `{row.get('origin_class') or '-'}`")
            lines.append(f"- Origin read: `{row.get('origin_read') or '-'}`")
            lines.append(
                f"- Open deal: `ticket={parse_int(open_deal.get('ticket'))} position_id={parse_int(open_deal.get('position_id'))} "
                f"reason={open_deal.get('reason_name') or '-'} magic={parse_int(open_deal.get('magic'))} "
                f"comment={open_deal.get('comment') or '-'} time={open_deal.get('time') or '-'} price={parse_float(open_deal.get('price')):.5f}`"
            )
            if open_order:
                lines.append(
                    f"- Open order: `ticket={parse_int(open_order.get('ticket'))} reason={open_order.get('reason_name') or '-'} "
                    f"magic={parse_int(open_order.get('magic'))} comment={open_order.get('comment') or '-'} "
                    f"time_setup={open_order.get('time_setup') or '-'} price_open={parse_float(open_order.get('price_open')):.5f}`"
                )
            lines.append("")
    else:
        lines.extend(["- none", ""])

    lines.extend(
        [
            "## Read",
            "",
            "- Use this board after `detached_inventory_review.md` when you need history-backed attribution clues for the current detached positions rather than just counts and PnL.",
            "- `prior_live_lane_inventory` means the position still looks like old bot-managed carry under a known live magic, not an unexplained manual stray.",
            "- `manual_or_terminal_open_magic_zero` and `unassigned_magic_zero_origin_unclear` are the key signals for whether the magic-zero BTC position can be safely treated as non-lane inventory.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    payload = build_payload(
        load_json(LIVE_MAGIC_SCOPE_JSON),
        load_json(DETACHED_REVIEW_JSON),
    )
    write_outputs(payload)
    print(f"wrote {OUTPUT_JSON}")
    print(f"wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
