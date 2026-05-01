#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_kraken_spot_velocity_shadow import candidate_rows, load_json, parse_set, to_float  # noqa: E402


DEFAULT_BOARD_PATH = REPORTS / "kraken_spot_money_velocity_board.json"
DEFAULT_SHADOW_STATE_PATH = REPORTS / "kraken_spot_velocity_shadow_state.json"
DEFAULT_TAPE_PATH = REPORTS / "kraken_spot_guarded_candidate_forward_tape.jsonl"
DEFAULT_JSON_PATH = REPORTS / "kraken_spot_guarded_candidate_forward_review.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_spot_guarded_candidate_forward_review.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_spot_guarded_candidate_forward_review.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def iso_to_epoch(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    rows.sort(key=lambda row: str(row.get("entry_at") or ""))
    return rows


def parse_horizons(value: str) -> list[int]:
    horizons = sorted({int(float(item.strip())) for item in str(value or "").split(",") if item.strip()})
    return [horizon for horizon in horizons if horizon > 0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Passive forward tape for Kraken guarded would-enter candidates.")
    parser.add_argument("--board-path", default=str(DEFAULT_BOARD_PATH))
    parser.add_argument("--shadow-state-path", default=str(DEFAULT_SHADOW_STATE_PATH))
    parser.add_argument("--tape-path", default=str(DEFAULT_TAPE_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--starting-cash", type=float, default=100.0)
    parser.add_argument("--deploy-pct", type=float, default=0.8)
    parser.add_argument("--taker-fee-bps", type=float, default=40.0)
    parser.add_argument("--horizons-seconds", default="60,180,300,600")
    parser.add_argument("--dedupe-seconds", type=float, default=600.0)
    parser.add_argument("--max-new-candidates-per-run", type=int, default=3)
    parser.add_argument("--refresh-board", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--min-kraken-edge-bps", type=float, default=50.0)
    parser.add_argument("--max-spread-bps", type=float, default=100.0)
    parser.add_argument("--allowed-signal-states", default="live_hot")
    parser.add_argument("--allowed-best-windows", default="last,30s,60s,5m")
    parser.add_argument("--required-verdicts", default="clears_both_fee_models,kraken_fee_flip_candidate")
    parser.add_argument("--max-entry-chase-bps", type=float, default=450.0)
    return parser.parse_args()


def refresh_board() -> None:
    subprocess.run(
        [sys.executable, "scripts/build_kraken_spot_money_velocity_board.py", "--starting-cash", "100", "--deploy-pct", "0.8"],
        cwd=ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def entry_id(product_id: str, entry_epoch: float) -> str:
    return f"{product_id}:{int(entry_epoch)}"


def is_recent_duplicate(rows: list[dict[str, Any]], product_id: str, now_epoch: float, dedupe_seconds: float, horizons: list[int]) -> bool:
    max_horizon = max(horizons) if horizons else dedupe_seconds
    for row in reversed(rows):
        if row.get("product_id") != product_id:
            continue
        age = now_epoch - to_float(row.get("entry_epoch"))
        if age < max(dedupe_seconds, float(max_horizon)):
            return True
        return False
    return False


def make_entry(row: dict[str, Any], args: argparse.Namespace, horizons: list[int], now_epoch: float, now_iso: str) -> dict[str, Any]:
    deploy_usd = float(args.starting_cash) * float(args.deploy_pct)
    ask = to_float(row.get("ask"))
    bid = to_float(row.get("bid"))
    entry_fee = deploy_usd * float(args.taker_fee_bps) / 10000.0
    quantity = (deploy_usd - entry_fee) / ask if ask > 0 else 0.0
    product_id = str(row.get("product_id") or "")
    return {
        "event": "guarded_candidate_enter",
        "entry_id": entry_id(product_id, now_epoch),
        "entry_at": now_iso,
        "entry_epoch": now_epoch,
        "product_id": product_id,
        "status": "pending",
        "horizons_seconds": horizons,
        "cost_usd": round(deploy_usd, 6),
        "entry_fee": round(entry_fee, 6),
        "quantity": quantity,
        "entry_ask": ask,
        "entry_bid": bid,
        "entry_spread_bps": row.get("spread_bps"),
        "entry_best_move_bps": row.get("best_move_bps"),
        "entry_best_move_window": row.get("best_move_window"),
        "entry_kraken_edge_bps": row.get("kraken_edge_bps"),
        "entry_coinbase_edge_bps": row.get("coinbase_edge_bps"),
        "entry_verdict": row.get("verdict"),
        "entry_signal_state": row.get("signal_state"),
        "entry_row": row,
        "marks": {},
    }


def mark_entry(entry: dict[str, Any], board_row: dict[str, Any], args: argparse.Namespace, now_epoch: float, now_iso: str) -> tuple[dict[str, Any], bool]:
    changed = False
    marks = entry.get("marks") if isinstance(entry.get("marks"), dict) else {}
    bid = to_float(board_row.get("bid"))
    quantity = to_float(entry.get("quantity"))
    cost_usd = to_float(entry.get("cost_usd"))
    if bid <= 0.0 or quantity <= 0.0 or cost_usd <= 0.0:
        return entry, False
    for horizon in entry.get("horizons_seconds") or []:
        key = str(int(horizon))
        if key in marks:
            continue
        if now_epoch - to_float(entry.get("entry_epoch")) < float(horizon):
            continue
        gross_exit_value = bid * quantity
        exit_fee = gross_exit_value * float(args.taker_fee_bps) / 10000.0
        net_exit_value = gross_exit_value - exit_fee
        net_pnl = net_exit_value - cost_usd
        marks[key] = {
            "marked_at": now_iso,
            "horizon_seconds": int(horizon),
            "exit_bid": bid,
            "exit_fee": round(exit_fee, 6),
            "net_exit_value": round(net_exit_value, 6),
            "net_pnl": round(net_pnl, 6),
            "net_pct_on_cost": round((net_pnl / cost_usd) * 100.0, 6),
            "exit_signal_state": board_row.get("signal_state"),
            "exit_spread_bps": board_row.get("spread_bps"),
        }
        changed = True
    entry["marks"] = marks
    expected = {str(int(horizon)) for horizon in entry.get("horizons_seconds") or []}
    entry["status"] = "complete" if expected and expected.issubset(set(marks.keys())) else "pending"
    return entry, changed


def rebuild_tape(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    tmp.replace(path)


def state_from_shadow(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    state = payload.get("state") if isinstance(payload, dict) and isinstance(payload.get("state"), dict) else {}
    return state if isinstance(state, dict) else {}


def summarize(rows: list[dict[str, Any]], horizons: list[int]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "entries": len(rows),
        "pending": sum(1 for row in rows if row.get("status") != "complete"),
        "complete": sum(1 for row in rows if row.get("status") == "complete"),
        "horizons": {},
    }
    for horizon in horizons:
        values = []
        for row in rows:
            mark = (row.get("marks") or {}).get(str(int(horizon)))
            if isinstance(mark, dict):
                values.append(mark)
        net_values = [to_float(mark.get("net_pnl")) for mark in values]
        pct_values = [to_float(mark.get("net_pct_on_cost")) for mark in values]
        summary["horizons"][str(int(horizon))] = {
            "marked": len(values),
            "win_rate_pct": round((sum(1 for value in net_values if value > 0) / len(net_values)) * 100.0, 6) if net_values else 0.0,
            "avg_net_pnl": round(sum(net_values) / len(net_values), 6) if net_values else 0.0,
            "sum_net_pnl": round(sum(net_values), 6),
            "avg_net_pct": round(sum(pct_values) / len(pct_values), 6) if pct_values else 0.0,
        }
    return summary


def write_reports(payload: dict[str, Any], csv_path: Path, md_path: Path) -> None:
    rows = payload.get("rows") or []
    horizons = [int(h) for h in payload.get("parameters", {}).get("horizons_seconds") or []]
    columns = [
        "entry_at",
        "product_id",
        "status",
        "entry_signal_state",
        "entry_best_move_window",
        "entry_best_move_bps",
        "entry_spread_bps",
        "entry_kraken_edge_bps",
    ]
    for horizon in horizons:
        columns.extend([f"h{horizon}_net_pnl", f"h{horizon}_net_pct"])
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            out = {column: row.get(column, "") for column in columns}
            marks = row.get("marks") if isinstance(row.get("marks"), dict) else {}
            for horizon in horizons:
                mark = marks.get(str(horizon)) if isinstance(marks.get(str(horizon)), dict) else {}
                out[f"h{horizon}_net_pnl"] = mark.get("net_pnl", "")
                out[f"h{horizon}_net_pct"] = mark.get("net_pct_on_cost", "")
            writer.writerow(out)
    summary = payload.get("summary") or {}
    lines = [
        "# Kraken Guarded Candidate Forward Tape",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Shadow only: `{payload.get('shadow_only')}`",
        f"- Passive only: `{payload.get('passive_only')}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in payload.get("read") or []])
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Entries: `{summary.get('entries', 0)}`",
            f"- Pending: `{summary.get('pending', 0)}`",
            f"- Complete: `{summary.get('complete', 0)}`",
            "",
            "## Horizon Results",
            "",
            "| Horizon | Marked | Win % | Avg Net $ | Sum Net $ | Avg Net % |",
            "| ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for horizon, stats in (summary.get("horizons") or {}).items():
        lines.append(
            "| {horizon}s | {marked} | {win_rate_pct:.2f} | {avg_net_pnl:.4f} | {sum_net_pnl:.4f} | {avg_net_pct:.4f} |".format(
                horizon=horizon,
                **stats,
            )
        )
    lines.extend(
        [
            "",
            "## Recent Entries",
            "",
            "| Entry | Product | Status | State | Window | Move bps | Edge bps | Last Mark | Last Net $ |",
            "| --- | --- | --- | --- | --- | ---: | ---: | --- | ---: |",
        ]
    )
    for row in rows[-30:][::-1]:
        marks = row.get("marks") if isinstance(row.get("marks"), dict) else {}
        last_key = max((int(key) for key in marks.keys()), default=0)
        last_mark = marks.get(str(last_key), {}) if last_key else {}
        lines.append(
            "| {entry_at} | {product_id} | {status} | {entry_signal_state} | {entry_best_move_window} | {entry_best_move_bps} | {entry_kraken_edge_bps} | {last_key}s | {last_net:.4f} |".format(
                entry_at=row.get("entry_at"),
                product_id=row.get("product_id"),
                status=row.get("status"),
                entry_signal_state=row.get("entry_signal_state"),
                entry_best_move_window=row.get("entry_best_move_window"),
                entry_best_move_bps=to_float(row.get("entry_best_move_bps")),
                entry_kraken_edge_bps=to_float(row.get("entry_kraken_edge_bps")),
                last_key=last_key,
                last_net=to_float(last_mark.get("net_pnl")) if isinstance(last_mark, dict) else 0.0,
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_once(args: argparse.Namespace) -> dict[str, Any]:
    if args.refresh_board:
        refresh_board()
    board = load_json(Path(str(args.board_path)))
    state = state_from_shadow(Path(str(args.shadow_state_path)))
    rows = load_jsonl(Path(str(args.tape_path)))
    now_epoch = time.time()
    now_iso = utc_now_iso()
    horizons = parse_horizons(str(args.horizons_seconds))
    board_rows = {str(row.get("product_id") or ""): row for row in board.get("rows") or []}
    changed = False
    for idx, row in enumerate(list(rows)):
        if row.get("status") == "complete":
            continue
        product_id = str(row.get("product_id") or "")
        board_row = board_rows.get(product_id)
        if not board_row:
            continue
        marked, did_change = mark_entry(row, board_row, args, now_epoch, now_iso)
        rows[idx] = marked
        changed = changed or did_change
    eligible = candidate_rows(board, args, state)
    new_entries: list[dict[str, Any]] = []
    for row in eligible[: max(0, int(args.max_new_candidates_per_run))]:
        product_id = str(row.get("product_id") or "")
        if not product_id:
            continue
        if is_recent_duplicate(rows + new_entries, product_id, now_epoch, float(args.dedupe_seconds), horizons):
            continue
        new_entries.append(make_entry(row, args, horizons, now_epoch, now_iso))
    if new_entries:
        rows.extend(new_entries)
        changed = True
    if changed:
        rebuild_tape(Path(str(args.tape_path)), rows)
    payload = {
        "generated_at": now_iso,
        "mode": "kraken_spot_guarded_candidate_forward_tape",
        "shadow_only": True,
        "passive_only": True,
        "parameters": {
            "board_path": str(args.board_path),
            "shadow_state_path": str(args.shadow_state_path),
            "tape_path": str(args.tape_path),
            "horizons_seconds": horizons,
            "dedupe_seconds": float(args.dedupe_seconds),
            "starting_cash": float(args.starting_cash),
            "deploy_pct": float(args.deploy_pct),
            "taker_fee_bps": float(args.taker_fee_bps),
            "min_kraken_edge_bps": float(args.min_kraken_edge_bps),
            "max_spread_bps": float(args.max_spread_bps),
            "allowed_signal_states": sorted(parse_set(args.allowed_signal_states)),
            "allowed_best_windows": sorted(parse_set(args.allowed_best_windows)),
            "required_verdicts": sorted(parse_set(args.required_verdicts)),
            "max_entry_chase_bps": float(args.max_entry_chase_bps),
        },
        "read": [
            "This is passive evidence capture; it does not open paper or live positions.",
            "Entries are candidates that satisfy the same guarded rules as the Kraken shadow runner, even while the runner is drawdown-blocked.",
            "Marks assume ask entry, bid exit, and taker fees on both sides using the same starter-fee model.",
        ],
        "board_generated_at": board.get("generated_at"),
        "shadow_last_action": state.get("last_action"),
        "new_entries": len(new_entries),
        "eligible_now": len(eligible),
        "summary": summarize(rows, horizons),
        "rows": rows[-200:],
    }
    write_json(Path(str(args.json_path)), payload)
    write_reports(payload, Path(str(args.csv_path)), Path(str(args.md_path)))
    return payload


def main() -> None:
    args = parse_args()
    while True:
        payload = build_once(args)
        if not args.loop:
            print(
                json.dumps(
                    {
                        "json_path": str(Path(args.json_path).resolve()),
                        "md_path": str(Path(args.md_path).resolve()),
                        "eligible_now": payload["eligible_now"],
                        "new_entries": payload["new_entries"],
                        "entries": payload["summary"]["entries"],
                    },
                    indent=2,
                )
            )
            return
        time.sleep(max(1.0, float(args.poll_seconds)))


if __name__ == "__main__":
    main()
