#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_coinbase_spot_shadow_trade_forensics import read_jsonl, to_float
from live_penetration_lattice_shadow import utc_now_iso


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
RSI_CONFIG_PATH = ROOT / "configs" / "coinbase_rsi_bundle_shadow.json"
PIRANHA_CONFIG_PATH = ROOT / "configs" / "coinbase_spot_piranha_bundle_shadow.json"
JSON_PATH = REPORTS / "coinbase_spot_fee_replay.json"
CSV_PATH = REPORTS / "coinbase_spot_fee_replay.csv"
MD_PATH = REPORTS / "coinbase_spot_fee_replay.md"
DEFAULT_TAKER_FEE_BPS = 120.0


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


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
            }
        )
    return rows


def fee_replay_net(*, entry_price: float, exit_price: float, quantity: float, fee_bps_per_side: float) -> dict[str, float]:
    gross = (exit_price - entry_price) * quantity
    fee_rate = fee_bps_per_side / 10000.0
    fee = ((entry_price * quantity) + (exit_price * quantity)) * fee_rate
    return {"gross_pnl": gross, "replayed_fee": fee, "replayed_net_pnl": gross - fee}


def derive_quantity(event: dict[str, Any]) -> float:
    entry = to_float(event.get("entry_price"))
    exit_price = to_float(event.get("exit_price"))
    gross = to_float(event.get("gross_pnl"))
    if entry != exit_price:
        return gross / (exit_price - entry)
    return 0.0


def parse_iso(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def runner_started_at(lane: dict[str, Any]) -> str:
    payload = load_json(Path(lane["state_path"]))
    return str((payload.get("runner") or {}).get("started_at") or "")


def replay_rsi_lane(lane: dict[str, Any], *, fee_bps_per_side: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    opens: deque[dict[str, Any]] = deque()
    for event in read_jsonl(Path(lane["event_path"])):
        action = str(event.get("action") or "")
        symbol = str(event.get("symbol") or event.get("product_id") or lane["product_id"])
        if symbol != lane["product_id"]:
            continue
        if action == "open_trade":
            opens.append(event)
            continue
        if action != "close_trade":
            continue
        opened = opens.popleft() if opens else {}
        entry = to_float(event.get("entry_price") or opened.get("entry_price"))
        exit_price = to_float(event.get("exit_price"))
        quantity = to_float(opened.get("quantity")) or derive_quantity(event)
        if entry <= 0.0 or exit_price <= 0.0 or quantity <= 0.0:
            continue
        replay = fee_replay_net(entry_price=entry, exit_price=exit_price, quantity=quantity, fee_bps_per_side=fee_bps_per_side)
        logged_net = to_float(event.get("net_pnl"))
        rows.append(
            {
                "ts_utc": str(event.get("ts_utc") or ""),
                "product_id": lane["product_id"],
                "lane_name": lane["lane_name"],
                "family": lane["family"],
                "exit_reason": str(event.get("exit_reason") or ""),
                "entry_price": entry,
                "exit_price": exit_price,
                "quantity": quantity,
                "logged_net_pnl": logged_net,
                "logged_fee": to_float(event.get("fee")),
                "replayed_fee_bps_per_side": fee_bps_per_side,
                **replay,
                "net_delta_vs_logged": replay["replayed_net_pnl"] - logged_net,
            }
        )
    return rows


def replay_piranha_lane(lane: dict[str, Any], *, fee_bps_per_side: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in read_jsonl(Path(lane["event_path"])):
        if str(event.get("action") or "") != "close_lot":
            continue
        symbol = str(event.get("symbol") or event.get("product_id") or lane["product_id"])
        if symbol != lane["product_id"]:
            continue
        entry = to_float(event.get("entry_price"))
        exit_price = to_float(event.get("exit_price"))
        quantity = to_float(event.get("quantity"))
        if entry <= 0.0 or exit_price <= 0.0 or quantity <= 0.0:
            continue
        replay = fee_replay_net(entry_price=entry, exit_price=exit_price, quantity=quantity, fee_bps_per_side=fee_bps_per_side)
        logged_net = to_float(event.get("realized_pnl"))
        rows.append(
            {
                "ts_utc": str(event.get("ts_utc") or ""),
                "product_id": lane["product_id"],
                "lane_name": lane["lane_name"],
                "family": lane["family"],
                "exit_reason": "profit_target",
                "entry_price": entry,
                "exit_price": exit_price,
                "quantity": quantity,
                "logged_net_pnl": logged_net,
                "logged_fee": to_float(event.get("fee")),
                "replayed_fee_bps_per_side": fee_bps_per_side,
                **replay,
                "net_delta_vs_logged": replay["replayed_net_pnl"] - logged_net,
            }
        )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "closes": 0,
            "logged_net_pnl": 0.0,
            "replayed_net_pnl": 0.0,
            "replayed_fees": 0.0,
            "net_delta_vs_logged": 0.0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": 0.0,
            "avg_replayed_net": 0.0,
            "median_replayed_net": 0.0,
            "worst_replayed_net": 0.0,
            "best_replayed_net": 0.0,
            "exit_reasons": {},
        }
    nets = [to_float(row.get("replayed_net_pnl")) for row in rows]
    wins = [net for net in nets if net > 0.0]
    return {
        "closes": len(rows),
        "logged_net_pnl": round(sum(to_float(row.get("logged_net_pnl")) for row in rows), 6),
        "replayed_net_pnl": round(sum(nets), 6),
        "replayed_fees": round(sum(to_float(row.get("replayed_fee")) for row in rows), 6),
        "net_delta_vs_logged": round(sum(to_float(row.get("net_delta_vs_logged")) for row in rows), 6),
        "wins": len(wins),
        "losses": len(rows) - len(wins),
        "win_rate_pct": round((len(wins) / len(rows)) * 100.0, 2),
        "avg_replayed_net": round(statistics.fmean(nets), 6),
        "median_replayed_net": round(statistics.median(nets), 6),
        "worst_replayed_net": round(min(nets), 6),
        "best_replayed_net": round(max(nets), 6),
        "exit_reasons": dict(Counter(str(row.get("exit_reason") or "") for row in rows).most_common()),
    }


def build_payload(*, fee_bps_per_side: float = DEFAULT_TAKER_FEE_BPS) -> dict[str, Any]:
    close_rows: list[dict[str, Any]] = []
    lane_summaries: list[dict[str, Any]] = []
    for lane in lane_rows():
        started_raw = runner_started_at(lane)
        started_at = parse_iso(started_raw)
        lane_closes = (
            replay_rsi_lane(lane, fee_bps_per_side=fee_bps_per_side)
            if lane["family"] == "rsi_mean_reversion"
            else replay_piranha_lane(lane, fee_bps_per_side=fee_bps_per_side)
        )
        session_closes = [
            row
            for row in lane_closes
            if started_at is not None and (parse_iso(row.get("ts_utc")) or datetime.min.replace(tzinfo=timezone.utc)) >= started_at
        ]
        close_rows.extend(lane_closes)
        lane_summaries.append(
            {
                **lane,
                "runner_started_at": started_raw,
                "all_log": summarize(lane_closes),
                "current_runner_session": summarize(session_closes),
            }
        )
    family_summaries = []
    for family in sorted({row["family"] for row in close_rows}):
        family_rows = [row for row in close_rows if row["family"] == family]
        family_summaries.append({"family": family, **summarize(family_rows)})
    current_session_rows = []
    for row in lane_summaries:
        started_at = parse_iso(row.get("runner_started_at"))
        if started_at is None:
            continue
        current_session_rows.extend(
            close
            for close in close_rows
            if close["lane_name"] == row["lane_name"]
            and (parse_iso(close.get("ts_utc")) or datetime.min.replace(tzinfo=timezone.utc)) >= started_at
        )
    total = summarize(close_rows)
    return {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_fee_replay",
        "fee_bps_per_side": fee_bps_per_side,
        "fee_model": "counterfactual_account_taker_fee_bps_per_side",
        "notes": [
            "Replays recorded shadow fills with the same entry/exit prices and quantities but replaces fees with the supplied taker bps on both entry and exit.",
            "This answers whether the historical shadow fills would have stayed positive under the current account taker tier; it does not add missing slippage beyond recorded bid/ask or candle proxy fills.",
            "RSI quantity is matched from open_trade events when available and derived from gross PnL otherwise.",
        ],
        "total": total,
        "current_runner_session_total": summarize(current_session_rows),
        "families": family_summaries,
        "lanes": [
            {key: (str(value) if isinstance(value, Path) else value) for key, value in row.items()}
            for row in lane_summaries
        ],
        "close_rows": close_rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    columns = [
        "product_id",
        "lane_name",
        "family",
        "all_closes",
        "all_logged_net_pnl",
        "all_replayed_net_pnl",
        "all_replayed_fees",
        "all_delta_vs_logged",
        "all_win_rate_pct",
        "session_closes",
        "session_logged_net_pnl",
        "session_replayed_net_pnl",
        "session_replayed_fees",
        "session_delta_vs_logged",
        "session_win_rate_pct",
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
                    "all_closes": all_log["closes"],
                    "all_logged_net_pnl": all_log["logged_net_pnl"],
                    "all_replayed_net_pnl": all_log["replayed_net_pnl"],
                    "all_replayed_fees": all_log["replayed_fees"],
                    "all_delta_vs_logged": all_log["net_delta_vs_logged"],
                    "all_win_rate_pct": all_log["win_rate_pct"],
                    "session_closes": session["closes"],
                    "session_logged_net_pnl": session["logged_net_pnl"],
                    "session_replayed_net_pnl": session["replayed_net_pnl"],
                    "session_replayed_fees": session["replayed_fees"],
                    "session_delta_vs_logged": session["net_delta_vs_logged"],
                    "session_win_rate_pct": session["win_rate_pct"],
                }
            )

    lines = [
        "# Coinbase Spot Fee Replay",
        "",
        f"- Fee replay: `{payload['fee_bps_per_side']}` bps per side",
        f"- Total logged net: `${payload['total']['logged_net_pnl']:.4f}`",
        f"- Total replayed net: `${payload['total']['replayed_net_pnl']:.4f}`",
        f"- Delta vs logged: `${payload['total']['net_delta_vs_logged']:.4f}`",
        f"- Replayed closes: `{payload['total']['closes']}`",
        f"- Current-session replayed net: `${payload['current_runner_session_total']['replayed_net_pnl']:.4f}` over `{payload['current_runner_session_total']['closes']}` closes",
        "",
        "## Family Summary",
        "",
        "| Family | Closes | Logged Net $ | Replayed Net $ | Replayed Fees $ | Delta $ | WR % | Avg Replay $ | Worst $ |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["families"]:
        lines.append(
            "| {family} | {closes} | {logged_net_pnl:.4f} | {replayed_net_pnl:.4f} | {replayed_fees:.4f} | {net_delta_vs_logged:.4f} | {win_rate_pct:.2f} | {avg_replayed_net:.4f} | {worst_replayed_net:.4f} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Lane Summary",
            "",
            "| Product | Family | Closes | Logged Net $ | Replayed Net $ | Fees $ | Delta $ | WR % | Avg Replay $ | Worst $ |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in sorted(payload["lanes"], key=lambda item: to_float((item.get("all_log") or {}).get("replayed_net_pnl")), reverse=True):
        all_log = row["all_log"]
        lines.append(
            "| {product_id} | {family} | {closes} | {logged_net_pnl:.4f} | {replayed_net_pnl:.4f} | {replayed_fees:.4f} | {net_delta_vs_logged:.4f} | {win_rate_pct:.2f} | {avg_replayed_net:.4f} | {worst_replayed_net:.4f} |".format(
                product_id=row["product_id"],
                family=row["family"],
                **all_log,
            )
        )
    lines.extend(
        [
            "",
            "## Current Runner Session",
            "",
            "| Product | Family | Session Closes | Session Logged Net $ | Session Replayed Net $ | Session Fees $ | Delta $ | WR % |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in sorted(payload["lanes"], key=lambda item: to_float((item.get("current_runner_session") or {}).get("replayed_net_pnl")), reverse=True):
        session = row["current_runner_session"]
        if session["closes"] == 0:
            continue
        lines.append(
            "| {product_id} | {family} | {closes} | {logged_net_pnl:.4f} | {replayed_net_pnl:.4f} | {replayed_fees:.4f} | {net_delta_vs_logged:.4f} | {win_rate_pct:.2f} |".format(
                product_id=row["product_id"],
                family=row["family"],
                **session,
            )
        )
    lines.extend(["", "## Notes", ""])
    for note in payload["notes"]:
        lines.append(f"- {note}")
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_reports(payload)
    print(
        json.dumps(
            {
                "json_path": str(JSON_PATH),
                "csv_path": str(CSV_PATH),
                "md_path": str(MD_PATH),
                "fee_bps_per_side": payload["fee_bps_per_side"],
                "total": payload["total"],
                "current_runner_session_total": payload["current_runner_session_total"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
