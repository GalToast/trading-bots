#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from coinbase_advanced_client import CoinbaseAdvancedClient


REPORTS = ROOT / "reports"
DEFAULT_STATE_PATH = REPORTS / "maker_fee_rsi_shadow_maker_taker_state.json"
DEFAULT_EVENTS_PATH = REPORTS / "maker_fee_rsi_shadow_maker_taker_events.jsonl"
DEFAULT_JSON_PATH = REPORTS / "maker_fee_rsi_shadow_review.json"
DEFAULT_CSV_PATH = REPORTS / "maker_fee_rsi_shadow_review.csv"
DEFAULT_MD_PATH = REPORTS / "maker_fee_rsi_shadow_review.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def fetch_ticks(product_ids: list[str]) -> dict[str, dict[str, float]]:
    if not product_ids:
        return {}
    client = CoinbaseAdvancedClient()
    payload = client.best_bid_ask(product_ids)
    ticks: dict[str, dict[str, float]] = {}
    for book in payload.get("pricebooks") or []:
        product_id = str(book.get("product_id") or "")
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not product_id or not bids or not asks:
            continue
        bid = to_float(bids[0].get("price"))
        ask = to_float(asks[0].get("price"))
        if bid > 0 and ask > 0:
            ticks[product_id] = {"bid": bid, "ask": ask, "mid": (bid + ask) / 2.0}
    return ticks


def mark_position(position: dict[str, Any], tick: dict[str, float], *, taker_fee_bps: float) -> dict[str, Any]:
    product_id = str(position.get("product_id") or "")
    entry_price = to_float(position.get("entry_price"))
    quantity = to_float(position.get("quantity"))
    cost_usd = to_float(position.get("cost_usd"))
    entry_fee = to_float(position.get("entry_fee"))
    target_pct = to_float(position.get("target_pct"))
    stop_pct = to_float(position.get("stop_pct"))
    bid = to_float(tick.get("bid"))
    ask = to_float(tick.get("ask"))
    spread_bps = ((ask - bid) / ((ask + bid) / 2.0)) * 10000.0 if bid > 0 and ask > 0 else 0.0
    exit_fee = quantity * bid * (taker_fee_bps / 10000.0)
    net_pnl = quantity * bid - exit_fee - cost_usd
    net_pct = (net_pnl / cost_usd) * 100.0 if cost_usd else 0.0
    gross_move_pct = ((bid - entry_price) / entry_price) * 100.0 if entry_price else 0.0
    highest = max(to_float(position.get("highest_price")), bid)
    gross_mfe_pct = ((highest - entry_price) / entry_price) * 100.0 if entry_price else 0.0
    return {
        "product_id": product_id,
        "entry_price": round(entry_price, 12),
        "bid": round(bid, 12),
        "ask": round(ask, 12),
        "spread_bps": round(spread_bps, 4),
        "cost_usd": round(cost_usd, 6),
        "entry_fee": round(entry_fee, 6),
        "exit_fee_now": round(exit_fee, 6),
        "gross_move_pct": round(gross_move_pct, 4),
        "gross_mfe_pct": round(gross_mfe_pct, 4),
        "net_pnl_now": round(net_pnl, 6),
        "net_pct_now": round(net_pct, 4),
        "target_pct": round(target_pct, 4),
        "stop_pct": round(stop_pct, 4),
        "target_gap_pct": round(max(0.0, target_pct - gross_move_pct), 4),
        "stop_gap_pct": round(gross_move_pct + stop_pct, 4),
        "entry_rsi": round(to_float(position.get("entry_rsi")), 4),
        "max_hold_bars": int(to_float(position.get("max_hold_bars"))),
    }


def build_payload(
    *,
    state_path: Path,
    events_path: Path,
    ticks: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    state = load_json(state_path)
    events = load_events(events_path)
    positions = state.get("open_positions") if isinstance(state.get("open_positions"), dict) else {}
    product_ids = sorted(str(product_id) for product_id in positions)
    tick_map = ticks if ticks is not None else fetch_ticks(product_ids)
    taker_fee_bps = to_float(state.get("taker_fee_bps"))
    marks = []
    for product_id in product_ids:
        tick = tick_map.get(product_id)
        if not tick:
            continue
        position = positions.get(product_id)
        if isinstance(position, dict):
            marks.append(mark_position(position, tick, taker_fee_bps=taker_fee_bps))
    close_events = [event for event in events if event.get("event") == "exit"]
    entry_events = [event for event in events if event.get("event") == "entry"]
    open_net = sum(to_float(row.get("net_pnl_now")) for row in marks)
    if close_events:
        realized = to_float(state.get("realized_net_usd"))
        verdict = "green_realized" if realized > 0 else "red_or_flat_realized"
    elif marks:
        verdict = "open_proof_collecting"
    else:
        verdict = "flat_no_close_proof"
    return {
        "generated_at": utc_now_iso(),
        "mode": "maker_fee_rsi_shadow_review",
        "parameters": {
            "state_path": str(state_path),
            "events_path": str(events_path),
        },
        "summary": {
            "cash_usd": round(to_float(state.get("cash_usd")), 6),
            "realized_net_usd": round(to_float(state.get("realized_net_usd")), 6),
            "realized_closes": int(to_float(state.get("realized_closes"))),
            "open_positions": len(positions),
            "marked_positions": len(marks),
            "entry_events": len(entry_events),
            "close_events": len(close_events),
            "open_marked_net_usd": round(open_net, 6),
            "open_marked_equity_usd": round(to_float(state.get("cash_usd")) + sum(to_float(row.get("cost_usd")) for row in marks) + open_net, 6),
            "maker_fee_bps": round(to_float(state.get("maker_fee_bps")), 4),
            "taker_fee_bps": round(taker_fee_bps, 4),
            "exit_mode": str(state.get("exit_mode") or ""),
            "proof_verdict": verdict,
        },
        "leadership_read": [
            "This review marks open Coinbase maker RSI positions at bid after taker exit fee.",
            "Post-only entry fills remain assumptions until order-book fill telemetry exists.",
            "Do not count this lane as profitable until close events are fee-paid green.",
        ],
        "positions": marks,
        "recent_events": events[-20:],
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, csv_path: Path, md_path: Path) -> None:
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    columns = [
        "product_id",
        "entry_price",
        "bid",
        "ask",
        "spread_bps",
        "cost_usd",
        "exit_fee_now",
        "gross_move_pct",
        "gross_mfe_pct",
        "net_pnl_now",
        "net_pct_now",
        "target_gap_pct",
        "stop_gap_pct",
        "entry_rsi",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload["positions"]:
            writer.writerow({column: row.get(column, "") for column in columns})
    summary = payload["summary"]
    lines = [
        "# Maker Fee RSI Shadow Review",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Proof verdict: `{summary['proof_verdict']}`",
            f"- Cash: `${summary['cash_usd']:.6f}`",
            f"- Realized net: `${summary['realized_net_usd']:.6f}`",
            f"- Open marked net: `${summary['open_marked_net_usd']:.6f}`",
            f"- Open marked equity: `${summary['open_marked_equity_usd']:.6f}`",
            f"- Realized closes: `{summary['realized_closes']}`",
            f"- Open positions: `{summary['open_positions']}`",
            f"- Fees: maker `{summary['maker_fee_bps']}` bps, taker `{summary['taker_fee_bps']}` bps, exit mode `{summary['exit_mode']}`",
            "",
            "## Open Marks",
            "",
            "| Product | Entry | Bid | Spread bps | Gross % | MFE % | Net Now $ | Net Now % | Target Gap % | Stop Gap % |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload["positions"]:
        lines.append(
            "| {product_id} | {entry_price:.10f} | {bid:.10f} | {spread_bps:.2f} | {gross_move_pct:.4f} | {gross_mfe_pct:.4f} | {net_pnl_now:+.6f} | {net_pct_now:+.4f} | {target_gap_pct:.4f} | {stop_gap_pct:.4f} |".format(
                **row
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review maker-fee RSI shadow open/closed proof.")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--events-path", default=str(DEFAULT_EVENTS_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_payload(state_path=Path(args.state_path), events_path=Path(args.events_path))
    write_reports(payload, json_path=Path(args.json_path), csv_path=Path(args.csv_path), md_path=Path(args.md_path))
    print(json.dumps({"summary": payload["summary"], "json_path": args.json_path, "md_path": args.md_path}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
