#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_STATE_PATH = REPORTS / "kraken_spot_velocity_shadow_state.json"
DEFAULT_BOARD_PATH = REPORTS / "kraken_spot_money_velocity_board.json"
DEFAULT_JSON_PATH = REPORTS / "kraken_spot_velocity_forward_review.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_spot_velocity_forward_review.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_spot_velocity_forward_review.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Kraken spot velocity shadow forward review.")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--board-path", default=str(DEFAULT_BOARD_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--rotation-buffer-bps", type=float, default=80.0)
    parser.add_argument("--max-spread-bps", type=float, default=100.0)
    parser.add_argument("--min-kraken-edge-bps", type=float, default=50.0)
    parser.add_argument("--allowed-signal-states", default="live_hot")
    parser.add_argument("--allowed-best-windows", default="last,30s,60s,5m")
    parser.add_argument("--required-verdicts", default="clears_both_fee_models,kraken_fee_flip_candidate")
    parser.add_argument("--max-entry-chase-bps", type=float, default=450.0)
    return parser.parse_args()


def unwrap_state(payload: dict[str, Any]) -> dict[str, Any]:
    state = payload.get("state") if isinstance(payload, dict) else {}
    return state if isinstance(state, dict) else {}


def parse_set(value: Any) -> set[str]:
    return {str(item).strip() for item in str(value or "").split(",") if str(item).strip()}


def candidate_rows(
    board: dict[str, Any],
    *,
    max_spread_bps: float,
    min_kraken_edge_bps: float = 0.0,
    allowed_signal_states: set[str] | None = None,
    allowed_best_windows: set[str] | None = None,
    required_verdicts: set[str] | None = None,
    max_entry_chase_bps: float = 999999.0,
) -> list[dict[str, Any]]:
    rows = []
    for row in board.get("rows") or []:
        if not row.get("can_trade_starting_cash"):
            continue
        if to_float(row.get("spread_bps")) > max_spread_bps:
            continue
        if to_float(row.get("kraken_edge_bps")) < min_kraken_edge_bps:
            continue
        if allowed_signal_states and str(row.get("signal_state") or "") not in allowed_signal_states:
            continue
        if allowed_best_windows and str(row.get("best_move_window") or "") not in allowed_best_windows:
            continue
        if required_verdicts and str(row.get("verdict") or "") not in required_verdicts:
            continue
        if to_float(row.get("best_move_bps")) > max_entry_chase_bps:
            continue
        rows.append(row)
    rows.sort(key=lambda row: (to_float(row.get("kraken_edge_bps")), to_float(row.get("best_move_bps"))), reverse=True)
    return rows


def held_review(state: dict[str, Any]) -> dict[str, Any]:
    position = state.get("position") if isinstance(state.get("position"), dict) else None
    if not position:
        return {
            "held_product": "",
            "held_net_pnl": 0.0,
            "held_net_pct": 0.0,
            "held_max_net_pnl": 0.0,
            "held_age_seconds": 0.0,
            "held_status": "flat",
        }
    opened_raw = str(position.get("opened_at") or "")
    age_seconds = 0.0
    try:
        age_seconds = (datetime.now(timezone.utc) - datetime.fromisoformat(opened_raw)).total_seconds()
    except ValueError:
        age_seconds = 0.0
    net_pnl = to_float(position.get("net_pnl"))
    max_net_pnl = to_float(position.get("max_net_pnl"))
    if net_pnl > 0:
        status = "green_fee_paid"
    elif max_net_pnl > 0:
        status = "gave_back_green"
    else:
        status = "red_waiting_or_timeout"
    return {
        "held_product": position.get("product_id") or "",
        "held_net_pnl": round(net_pnl, 6),
        "held_net_pct": round(to_float(position.get("net_pct_on_cost")), 6),
        "held_max_net_pnl": round(max_net_pnl, 6),
        "held_age_seconds": round(age_seconds, 1),
        "held_status": status,
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    state_payload = load_json(Path(str(args.state_path)))
    board = load_json(Path(str(args.board_path)))
    state = unwrap_state(state_payload)
    held = held_review(state)
    candidates = candidate_rows(
        board,
        max_spread_bps=float(args.max_spread_bps),
        min_kraken_edge_bps=float(args.min_kraken_edge_bps),
        allowed_signal_states=parse_set(args.allowed_signal_states),
        allowed_best_windows=parse_set(args.allowed_best_windows),
        required_verdicts=parse_set(args.required_verdicts),
        max_entry_chase_bps=float(args.max_entry_chase_bps),
    )
    best = candidates[0] if candidates else {}
    held_edge = 0.0
    held_product = held.get("held_product")
    for row in board.get("rows") or []:
        if row.get("product_id") == held_product:
            held_edge = to_float(row.get("kraken_edge_bps"))
            break
    best_edge = to_float(best.get("kraken_edge_bps"))
    rotation_delta_bps = best_edge - held_edge
    last_action = str(state.get("last_action") or "")
    if last_action == "circuit_breaker_realized_drawdown":
        action = "circuit_breaker_realized_drawdown"
    elif not held_product:
        action = "flat_wait_for_candidate" if not best else "flat_candidate_available"
    elif to_float(held.get("held_net_pnl")) < 0:
        action = "hold_red_no_rotation"
    elif rotation_delta_bps >= float(args.rotation_buffer_bps):
        action = "rotate_candidate_stronger"
    else:
        action = "hold_current"
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "kraken_spot_velocity_forward_review",
        "shadow_only": True,
        "state_updated_at": state_payload.get("updated_at"),
        "board_generated_at": board.get("generated_at"),
        "runner": state_payload.get("runner") or {},
        "summary": {
            "cash_usd": round(to_float(state.get("cash_usd")), 6),
            "equity_mark_usd": round(to_float(state.get("cash_usd")) + to_float(held.get("held_net_pnl")) + to_float((state.get("position") or {}).get("cost_usd")), 6)
            if held_product
            else round(to_float(state.get("cash_usd")), 6),
            "realized_net_usd": round(to_float(state.get("realized_net_usd")), 6),
            "realized_closes": int(to_float(state.get("realized_closes"))),
            "total_fees": round(to_float(state.get("total_fees")), 6),
            "last_runner_action": last_action,
            **held,
            "best_candidate": best.get("product_id") or "",
            "best_candidate_kraken_edge_bps": round(best_edge, 6),
            "best_candidate_coinbase_edge_bps": round(to_float(best.get("coinbase_edge_bps")), 6),
            "rotation_delta_bps": round(rotation_delta_bps, 6),
            "action": action,
        },
        "leadership_read": [
            "This is a shadow-only Kraken execution read, not live permission.",
            "Red held positions are not rotated by default; the runner waits for profit-lock, timeout, or max-loss to avoid paying churn fees into losses.",
            "Fee-flip candidates are important: those are moves that Kraken can theoretically monetize while Coinbase would still lose after fees.",
        ],
        "candidate_rows": candidates[:30],
    }
    write_reports(payload, Path(str(args.json_path)), Path(str(args.csv_path)), Path(str(args.md_path)))
    return payload


def write_reports(payload: dict[str, Any], json_path: Path, csv_path: Path, md_path: Path) -> None:
    write_json(json_path, payload)
    columns = [
        "product_id",
        "verdict",
        "signal_state",
        "best_move_window",
        "best_move_bps",
        "spread_bps",
        "kraken_edge_bps",
        "coinbase_edge_bps",
        "kraken_net_usd_on_deploy",
        "coinbase_net_usd_on_deploy",
        "samples",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload.get("candidate_rows") or []:
            writer.writerow({column: row.get(column, "") for column in columns})
    summary = payload.get("summary") or {}
    lines = [
        "# Kraken Spot Velocity Forward Review",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Shadow only: `{payload.get('shadow_only')}`",
        f"- State updated: `{payload.get('state_updated_at')}`",
        f"- Board generated: `{payload.get('board_generated_at')}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in payload.get("leadership_read") or []])
    lines.extend(
        [
            "",
            "## Current State",
            "",
            f"- Action: `{summary.get('action')}`",
            f"- Last runner action: `{summary.get('last_runner_action') or 'unknown'}`",
            f"- Cash: `${to_float(summary.get('cash_usd')):.4f}`",
            f"- Marked equity: `${to_float(summary.get('equity_mark_usd')):.4f}`",
            f"- Realized net: `${to_float(summary.get('realized_net_usd')):.4f}` over `{summary.get('realized_closes')}` closes",
            f"- Held: `{summary.get('held_product') or 'flat'}` status `{summary.get('held_status')}` net `${to_float(summary.get('held_net_pnl')):.4f}` / `{to_float(summary.get('held_net_pct')):.4f}%`",
            f"- Best candidate: `{summary.get('best_candidate') or 'none'}` edge `{to_float(summary.get('best_candidate_kraken_edge_bps')):.4f}` bps",
            "",
            "## Candidates",
            "",
            "| Rank | Product | Verdict | Signal | Window | Move bps | Spread | Kraken Edge | Coinbase Edge | Kraken Net $ | Coinbase Net $ |",
            "| ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for idx, row in enumerate((payload.get("candidate_rows") or [])[:20], start=1):
        lines.append(
            "| {idx} | {product_id} | {verdict} | {signal_state} | {best_move_window} | {best_move_bps:.4f} | {spread_bps:.2f} | {kraken_edge_bps:.4f} | {coinbase_edge_bps:.4f} | {kraken_net_usd_on_deploy:.4f} | {coinbase_net_usd_on_deploy:.4f} |".format(
                idx=idx,
                **row,
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    payload = build(args)
    print(json.dumps({"json_path": str(Path(args.json_path).resolve()), "md_path": str(Path(args.md_path).resolve()), "action": payload["summary"]["action"]}, indent=2))


if __name__ == "__main__":
    main()
