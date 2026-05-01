#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

SEAT_BOARD_PATH = REPORTS / "per_symbol_live_seat_board.json"
NEXT_ACTION_PATH = REPORTS / "max_profit_next_action_board.json"
OUTPUT_JSON_PATH = REPORTS / "max_profit_contract_gap_board.json"
OUTPUT_MD_PATH = REPORTS / "max_profit_contract_gap_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def proposed_lane(row: dict[str, Any]) -> str:
    family = str(row.get("best_challenger_family") or "")
    if family == "hungry_hippo_shadow":
        return "shadow HH"
    if family == "adaptive_shadow":
        return "shadow FX"
    return "shadow research"


def proposed_task_id(symbol: str, seat_action: str) -> str:
    symbol_key = str(symbol or "").lower()
    mapping = {
        "launch_challenger_proof": f"{symbol_key}_first_live_seat_proof_contract",
        "prepare_first_live_seat_case": f"{symbol_key}_first_live_seat_contract",
        "enrich_challenger_telemetry_first": f"{symbol_key}_telemetry_contract",
    }
    return mapping.get(seat_action, f"{symbol_key}_queue_contract")


def proposed_title(symbol: str, row: dict[str, Any]) -> str:
    action = str(row.get("seat_unblocker_action") or "")
    if action == "launch_challenger_proof":
        return f"Launch the {symbol} first live-seat proof contract"
    if action == "prepare_first_live_seat_case":
        return f"Formalize the {symbol} first live-seat decision contract"
    if action == "enrich_challenger_telemetry_first":
        return f"Enrich telemetry for the {symbol} challenger before seat judgment"
    return f"Define the {symbol} max-profit queue contract"


def proposed_next_action_class(row: dict[str, Any]) -> str:
    action = str(row.get("seat_unblocker_action") or "")
    mapping = {
        "launch_challenger_proof": "formalize_first_seat_proof_contract",
        "prepare_first_live_seat_case": "formalize_first_live_seat_contract",
        "enrich_challenger_telemetry_first": "formalize_telemetry_enrichment_contract",
    }
    return mapping.get(action, "formalize_queue_contract")


def contract_priority(row: dict[str, Any]) -> tuple[int, str]:
    runtime_status = str(row.get("best_challenger_runtime_status") or "")
    seat_verdict = str(row.get("seat_verdict") or "")
    seat_action = str(row.get("seat_unblocker_action") or "")
    symbol = str(row.get("symbol") or "")

    if runtime_status == "forward_proof_started":
        return (0, symbol)
    if seat_action == "enrich_challenger_telemetry_first" and seat_verdict != "no_live_seat":
        return (1, symbol)
    if seat_action == "launch_challenger_proof":
        return (2, symbol)
    if seat_action == "prepare_first_live_seat_case":
        return (3, symbol)
    return (4, symbol)


def contract_read(row: dict[str, Any]) -> str:
    symbol = str(row.get("symbol") or "")
    runtime_status = str(row.get("best_challenger_runtime_status") or "")
    candidate_class = str(row.get("best_challenger_candidate_class") or "")
    seat_action = str(row.get("seat_unblocker_action") or "")
    best_lane = str(row.get("best_challenger_lane") or "")

    if runtime_status == "forward_proof_started":
        return (
            f"`{symbol}` already has forward proof in motion on `{best_lane}`, so the room should formalize a queue contract now "
            "instead of leaving an active first-seat seam outside the max-profit planning stack."
        )
    if seat_action == "enrich_challenger_telemetry_first":
        return (
            f"`{symbol}` already has a running challenger, but honest seat judgment still depends on telemetry enrichment; "
            "the missing contract is for proof-quality upgrade, not for initial launch."
        )
    return (
        f"`{symbol}` still has no queue-backed contract even though current seat truth says `{seat_action}` and the best challenger "
        f"`{best_lane}` is `{candidate_class}` / `{runtime_status}`."
    )


def build_row(seat_row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(seat_row.get("symbol") or "")
    seat_action = str(seat_row.get("seat_unblocker_action") or "")
    return {
        "symbol": symbol,
        "seat_verdict": str(seat_row.get("seat_verdict") or ""),
        "best_challenger_lane": str(seat_row.get("best_challenger_lane") or ""),
        "best_challenger_label": str(seat_row.get("best_challenger_label") or ""),
        "best_challenger_family": str(seat_row.get("best_challenger_family") or ""),
        "best_challenger_candidate_class": str(seat_row.get("best_challenger_candidate_class") or ""),
        "best_challenger_runtime_status": str(seat_row.get("best_challenger_runtime_status") or ""),
        "seat_unblocker_action": seat_action,
        "seat_unblocker_read": str(seat_row.get("seat_unblocker_read") or ""),
        "proposed_queue_task_id": proposed_task_id(symbol, seat_action),
        "proposed_queue_title": proposed_title(symbol, seat_row),
        "proposed_queue_lane": proposed_lane(seat_row),
        "proposed_next_action_class": proposed_next_action_class(seat_row),
        "contract_gap_read": contract_read(seat_row),
        "priority_bucket": contract_priority(seat_row)[0],
        "sort_key": contract_priority(seat_row),
    }


def build_payload(*, seat_board: dict[str, Any], next_action_board: dict[str, Any]) -> dict[str, Any]:
    missing_symbols = set(dict(next_action_board.get("summary") or {}).get("queue_contract_missing_symbols") or [])
    seat_rows = [dict(row) for row in list(seat_board.get("rows") or []) if isinstance(row, dict)]
    rows = [build_row(row) for row in seat_rows if str(row.get("symbol") or "") in missing_symbols]
    rows.sort(key=lambda row: row["sort_key"])
    for row in rows:
        row.pop("sort_key", None)

    ordered_symbols = [row["symbol"] for row in rows]
    highest_contract_gap_symbol = ordered_symbols[0] if ordered_symbols else ""
    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(SEAT_BOARD_PATH.relative_to(ROOT)),
            str(NEXT_ACTION_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "contract_gap_symbols": ordered_symbols,
            "highest_contract_gap_symbol": highest_contract_gap_symbol,
            "contract_gap_count": len(rows),
            "contract_read": (
                f"Highest-leverage missing queue contract is `{highest_contract_gap_symbol}`."
                if highest_contract_gap_symbol
                else "No actionable-but-unqueued symbols remain."
            ),
        },
        "leadership_read": [
            (
                f"Highest-leverage missing queue contract is `{highest_contract_gap_symbol}`."
                if highest_contract_gap_symbol
                else "There are no actionable queue-contract gaps."
            ),
            f"Current queue-contract backlog is `{ordered_symbols}`.",
            "Use this board to formalize missing queue contracts before inventing new launch priorities elsewhere.",
        ],
        "rows": rows,
        "notes": [
            "This board is passive. It does not create queue rows; it only proposes them from existing seat and max-profit truth.",
            "Priority favors already-running proof without a contract first, then live-seat audit seams with running challengers, then parked first-seat launch contracts.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Max Profit Contract Gap Board",
        "",
        "This board turns actionable-but-unqueued max-profit seams into an explicit contract-formation backlog.",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        f"- contract_gap_symbols: `{summary.get('contract_gap_symbols', [])}`",
        f"- highest_contract_gap_symbol: `{summary.get('highest_contract_gap_symbol', '')}`",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Backlog",
            "",
            "| Symbol | Proposed Task | Lane | Next Action Class | Challenger Status |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in list(payload.get("rows") or []):
        lines.append(
            f"| `{row['symbol']}` | `{row['proposed_queue_task_id']}` | `{row['proposed_queue_lane']}` | "
            f"`{row['proposed_next_action_class']}` | `{row['best_challenger_candidate_class']}` / `{row['best_challenger_runtime_status']}` |"
        )

    lines.extend(["", "## Detail", ""])
    for row in list(payload.get("rows") or []):
        lines.extend(
            [
                f"### {row['symbol']}",
                "",
                f"- seat_verdict: `{row['seat_verdict']}`",
                f"- seat_unblocker_action: `{row['seat_unblocker_action']}`",
                f"- seat_unblocker_read: {row['seat_unblocker_read']}",
                f"- best_challenger_lane: `{row['best_challenger_lane']}`",
                f"- best_challenger_label: `{row['best_challenger_label']}`",
                f"- best_challenger_family: `{row['best_challenger_family']}`",
                f"- best_challenger_candidate_class: `{row['best_challenger_candidate_class']}`",
                f"- best_challenger_runtime_status: `{row['best_challenger_runtime_status']}`",
                f"- proposed_queue_task_id: `{row['proposed_queue_task_id']}`",
                f"- proposed_queue_title: `{row['proposed_queue_title']}`",
                f"- proposed_queue_lane: `{row['proposed_queue_lane']}`",
                f"- proposed_next_action_class: `{row['proposed_next_action_class']}`",
                f"- contract_gap_read: {row['contract_gap_read']}",
                "",
            ]
        )

    lines.extend(["## Notes", ""])
    for item in list(payload.get("notes") or []):
        lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    payload = build_payload(
        seat_board=load_json(SEAT_BOARD_PATH),
        next_action_board=load_json(NEXT_ACTION_PATH),
    )
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
