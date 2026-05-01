#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
RSI_CONFIG_PATH = ROOT / "configs" / "coinbase_rsi_bundle_shadow.json"
PIRANHA_CONFIG_PATH = ROOT / "configs" / "coinbase_spot_piranha_bundle_shadow.json"
ROUTER_PATH = REPORTS / "coinbase_spot_hot_capital_router.json"
JSON_PATH = REPORTS / "coinbase_spot_shadow_trade_forensics.json"
CSV_PATH = REPORTS / "coinbase_spot_shadow_trade_forensics.csv"
MD_PATH = REPORTS / "coinbase_spot_shadow_trade_forensics.md"


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def parse_iso(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def router_states() -> dict[str, str]:
    payload = load_json(ROUTER_PATH)
    states: dict[str, str] = {}
    for row in payload.get("rows") or []:
        product_id = str(row.get("product_id") or "")
        if product_id:
            states[product_id] = str(row.get("allocation_state") or "")
    return states


def lane_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lane in (load_json(RSI_CONFIG_PATH).get("lanes") or []):
        rows.append(
            {
                "family": "rsi_mean_reversion",
                "lane_name": str(lane.get("lane_name") or ""),
                "product_id": str(lane.get("product_id") or ""),
                "state_path": ROOT / str(lane.get("state_path") or ""),
                "event_path": ROOT / str(lane.get("event_path") or ""),
                "fee_bps_per_side": to_float(lane.get("maker_fee_bps")),
            }
        )
    for lane in (load_json(PIRANHA_CONFIG_PATH).get("lanes") or []):
        rows.append(
            {
                "family": "spot_piranha",
                "lane_name": str(lane.get("lane_name") or ""),
                "product_id": str(lane.get("product_id") or ""),
                "state_path": ROOT / str(lane.get("state_path") or ""),
                "event_path": ROOT / str(lane.get("event_path") or ""),
                "fee_bps_per_side": to_float(lane.get("taker_fee_bps")),
            }
        )
    return rows


def rsi_close_metrics(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "net_pnl": to_float(event.get("net_pnl")),
        "gross_pnl": to_float(event.get("gross_pnl")),
        "fee": to_float(event.get("fee")),
        "hold_bars": to_float(event.get("hold_bars")),
        "exit_reason": str(event.get("exit_reason") or ""),
        "entry_rsi": to_float(event.get("entry_rsi")),
        "exit_rsi": to_float(event.get("exit_rsi")),
    }


def piranha_close_metrics(event: dict[str, Any], *, fee_bps_per_side: float) -> dict[str, Any]:
    entry = to_float(event.get("entry_price"))
    exit_price = to_float(event.get("exit_price"))
    qty = to_float(event.get("quantity"))
    fee_rate = fee_bps_per_side / 10000.0
    gross = (exit_price - entry) * qty
    fee = (entry * qty * fee_rate) + (exit_price * qty * fee_rate)
    return {
        "net_pnl": to_float(event.get("realized_pnl")),
        "gross_pnl": gross,
        "fee": fee,
        "hold_bars": 0.0,
        "exit_reason": "profit_target",
        "entry_rsi": 0.0,
        "exit_rsi": 0.0,
    }


def summarize_closes(closes: list[dict[str, Any]]) -> dict[str, Any]:
    if not closes:
        return {
            "closes": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": 0.0,
            "net_pnl": 0.0,
            "gross_pnl": 0.0,
            "fees": 0.0,
            "avg_net": 0.0,
            "median_net": 0.0,
            "best": 0.0,
            "worst": 0.0,
            "avg_hold_bars": 0.0,
            "exit_reasons": {},
        }
    pnls = [to_float(row["net_pnl"]) for row in closes]
    wins = [pnl for pnl in pnls if pnl > 0.0]
    losses = [pnl for pnl in pnls if pnl <= 0.0]
    exit_reasons = Counter(str(row.get("exit_reason") or "") for row in closes)
    hold_bars = [to_float(row.get("hold_bars")) for row in closes if to_float(row.get("hold_bars")) > 0.0]
    return {
        "closes": len(closes),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round((len(wins) / len(closes)) * 100.0, 2),
        "net_pnl": round(sum(pnls), 6),
        "gross_pnl": round(sum(to_float(row.get("gross_pnl")) for row in closes), 6),
        "fees": round(sum(to_float(row.get("fee")) for row in closes), 6),
        "avg_net": round(statistics.fmean(pnls), 6),
        "median_net": round(statistics.median(pnls), 6),
        "best": round(max(pnls), 6),
        "worst": round(min(pnls), 6),
        "avg_hold_bars": round(statistics.fmean(hold_bars), 2) if hold_bars else 0.0,
        "exit_reasons": dict(exit_reasons.most_common()),
    }


def state_snapshot(lane: dict[str, Any]) -> dict[str, Any]:
    payload = load_json(Path(lane["state_path"]))
    runner = payload.get("runner") or {}
    if lane["family"] == "rsi_mean_reversion":
        state = payload.get("state") or {}
        return {
            "runner_started_at": str(runner.get("started_at") or ""),
            "heartbeat_at": str(runner.get("heartbeat_at") or ""),
            "state_realized_net": to_float(state.get("realized_net_usd")),
            "state_realized_closes": to_int(state.get("realized_closes")),
            "state_total_fees": to_float(state.get("total_fees")),
            "cash_usd": to_float(state.get("cash_usd")),
            "open_exposure": 1 if state.get("in_position") else 0,
            "open_inventory_units": to_float((state.get("current_trade") or {}).get("quantity")),
        }
    symbol_state = (payload.get("symbols") or {}).get(lane["product_id"]) or {}
    return {
        "runner_started_at": str(runner.get("started_at") or ""),
        "heartbeat_at": str(runner.get("heartbeat_at") or ""),
        "state_realized_net": to_float(symbol_state.get("realized_net_usd")),
        "state_realized_closes": to_int(symbol_state.get("realized_closes")),
        "state_total_fees": 0.0,
        "cash_usd": to_float(symbol_state.get("cash_usd")),
        "open_exposure": len(symbol_state.get("open_lots") or []),
        "open_inventory_units": to_float(symbol_state.get("inventory_units")),
    }


def lane_close_rows(lane: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in read_jsonl(Path(lane["event_path"])):
        action = str(event.get("action") or "")
        if lane["family"] == "rsi_mean_reversion" and action != "close_trade":
            continue
        if lane["family"] == "spot_piranha" and action != "close_lot":
            continue
        product_id = str(event.get("symbol") or event.get("product_id") or lane["product_id"])
        if product_id != lane["product_id"]:
            continue
        metrics = (
            rsi_close_metrics(event)
            if lane["family"] == "rsi_mean_reversion"
            else piranha_close_metrics(event, fee_bps_per_side=to_float(lane["fee_bps_per_side"]))
        )
        rows.append(
            {
                "ts_utc": str(event.get("ts_utc") or ""),
                "product_id": product_id,
                "lane_name": lane["lane_name"],
                "family": lane["family"],
                **metrics,
            }
        )
    return rows


def build_payload() -> dict[str, Any]:
    allocation = router_states()
    lanes = lane_rows()
    summaries: list[dict[str, Any]] = []
    close_rows: list[dict[str, Any]] = []
    for lane in lanes:
        snapshot = state_snapshot(lane)
        started_at = parse_iso(snapshot.get("runner_started_at"))
        all_closes = lane_close_rows(lane)
        session_closes = [
            row
            for row in all_closes
            if started_at is not None and (parse_iso(row.get("ts_utc")) or datetime.min.replace(tzinfo=timezone.utc)) >= started_at
        ]
        all_summary = summarize_closes(all_closes)
        session_summary = summarize_closes(session_closes)
        close_rows.extend(all_closes)
        summaries.append(
            {
                "product_id": lane["product_id"],
                "lane_name": lane["lane_name"],
                "family": lane["family"],
                "allocation_state": allocation.get(lane["product_id"], ""),
                "fee_bps_per_side": lane["fee_bps_per_side"],
                **snapshot,
                "all_log": all_summary,
                "current_runner_session": session_summary,
                "state_vs_log_close_delta": snapshot["state_realized_closes"] - all_summary["closes"],
                "state_vs_log_net_delta": round(snapshot["state_realized_net"] - all_summary["net_pnl"], 6),
            }
        )
    summaries.sort(
        key=lambda row: (
            str(row.get("allocation_state") or "").startswith("eligible"),
            to_float((row.get("all_log") or {}).get("net_pnl")),
        ),
        reverse=True,
    )
    return {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_shadow_trade_forensics",
        "leadership_read": [
            "This board analyzes shadow fills already recorded by the Coinbase spot deployable lanes.",
            "All-log metrics can include older experiments; current-runner-session metrics are bounded by each state file's runner.started_at.",
            "RSI events include explicit fee/net fields. Piranha fee is reconstructed from entry/exit quantity and configured taker bps.",
            "Use this board to find which lanes and exit reasons are really printing after fees before adding capital or new symbols.",
        ],
        "lanes": summaries,
        "close_rows": close_rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    columns = [
        "product_id",
        "lane_name",
        "family",
        "allocation_state",
        "fee_bps_per_side",
        "state_realized_net",
        "state_realized_closes",
        "state_total_fees",
        "cash_usd",
        "open_exposure",
        "all_closes",
        "all_net_pnl",
        "all_win_rate_pct",
        "all_fees",
        "all_avg_net",
        "all_worst",
        "session_closes",
        "session_net_pnl",
        "state_vs_log_close_delta",
        "state_vs_log_net_delta",
    ]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload["lanes"]:
            all_log = row["all_log"]
            session = row["current_runner_session"]
            writer.writerow(
                {
                    "product_id": row["product_id"],
                    "lane_name": row["lane_name"],
                    "family": row["family"],
                    "allocation_state": row["allocation_state"],
                    "fee_bps_per_side": row["fee_bps_per_side"],
                    "state_realized_net": row["state_realized_net"],
                    "state_realized_closes": row["state_realized_closes"],
                    "state_total_fees": row["state_total_fees"],
                    "cash_usd": row["cash_usd"],
                    "open_exposure": row["open_exposure"],
                    "all_closes": all_log["closes"],
                    "all_net_pnl": all_log["net_pnl"],
                    "all_win_rate_pct": all_log["win_rate_pct"],
                    "all_fees": all_log["fees"],
                    "all_avg_net": all_log["avg_net"],
                    "all_worst": all_log["worst"],
                    "session_closes": session["closes"],
                    "session_net_pnl": session["net_pnl"],
                    "state_vs_log_close_delta": row["state_vs_log_close_delta"],
                    "state_vs_log_net_delta": row["state_vs_log_net_delta"],
                }
            )

    lines = ["# Coinbase Spot Shadow Trade Forensics", "", "## Leadership Read", ""]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Lane Summary",
            "",
            "| Product | Family | Allocation | State Net $ | State Closes | All Net $ | All Closes | WR % | Fees $ | Avg Net $ | Worst $ | Open | Session Net $ | Session Closes | Delta |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in payload["lanes"]:
        all_log = row["all_log"]
        session = row["current_runner_session"]
        delta = f"{row['state_vs_log_close_delta']} closes / {row['state_vs_log_net_delta']:+.4f}"
        lines.append(
            "| {product_id} | {family} | {allocation_state} | {state_realized_net:.4f} | {state_realized_closes} | {all_net:.4f} | {all_closes} | {wr:.2f} | {fees:.4f} | {avg:.4f} | {worst:.4f} | {open_exposure} | {session_net:.4f} | {session_closes} | {delta} |".format(
                product_id=row["product_id"],
                family=row["family"],
                allocation_state=row["allocation_state"],
                state_realized_net=row["state_realized_net"],
                state_realized_closes=row["state_realized_closes"],
                all_net=all_log["net_pnl"],
                all_closes=all_log["closes"],
                wr=all_log["win_rate_pct"],
                fees=all_log["fees"],
                avg=all_log["avg_net"],
                worst=all_log["worst"],
                open_exposure=row["open_exposure"],
                session_net=session["net_pnl"],
                session_closes=session["closes"],
                delta=delta,
            )
        )
    lines.extend(["", "## Exit Reasons", ""])
    for row in payload["lanes"]:
        reasons = row["all_log"].get("exit_reasons") or {}
        if not reasons:
            continue
        lines.append(f"- `{row['product_id']}`: `{reasons}`")
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_reports(payload)
    print(json.dumps({"json_path": str(JSON_PATH), "csv_path": str(CSV_PATH), "md_path": str(MD_PATH), "lanes": len(payload["lanes"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
