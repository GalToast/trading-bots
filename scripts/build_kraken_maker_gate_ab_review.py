#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_EVENTS_PATH = REPORTS / "kraken_spot_maker_machinegun_shadow_events.jsonl"
DEFAULT_BOARD_PATH = REPORTS / "kraken_maker_opportunity_board.json"
DEFAULT_JSON_PATH = REPORTS / "kraken_maker_gate_ab_review.json"
DEFAULT_MD_PATH = REPORTS / "kraken_maker_gate_ab_review.md"
DEFAULT_CSV_PATH = REPORTS / "kraken_maker_gate_ab_current_decisions.csv"
DEFAULT_TAPE_PATH = REPORTS / "kraken_maker_gate_ab_current_decisions.jsonl"


@dataclass(frozen=True)
class Gate:
    name: str
    min_spread_bps: float
    min_mer: float
    min_atr_12_bps: float = 0.0


DEFAULT_GATES = [
    Gate("tight_spread100_mer3p5", 100.0, 3.5),
    Gate("loose_spread50_mer2p5", 50.0, 2.5),
    Gate("loose_spread50_mer2p5_atr20", 50.0, 2.5, 20.0),
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def entry_spread_bps(open_event: dict[str, Any]) -> float:
    bid = to_float(open_event.get("entry_price"))
    ask = to_float(open_event.get("ask_at_entry"))
    mid = (bid + ask) / 2.0
    if bid <= 0 or ask <= 0 or mid <= 0:
        return 0.0
    return ((ask - bid) / mid) * 10000.0


def pair_trades(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    opens: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    trades: list[dict[str, Any]] = []
    for event in events:
        action = str(event.get("action") or "")
        product_id = str(event.get("product_id") or "")
        if not product_id:
            continue
        if action == "open_maker_shadow":
            opens[product_id].append(event)
        elif action == "close_maker_shadow":
            open_event = opens[product_id].popleft() if opens[product_id] else {}
            spread_at_entry = entry_spread_bps(open_event)
            if spread_at_entry <= 0:
                spread_at_entry = to_float(event.get("spread_bps"))
            trades.append(
                {
                    "product_id": product_id,
                    "opened_at": open_event.get("ts_utc") or event.get("opened_at") or "",
                    "closed_at": event.get("ts_utc") or "",
                    "mode": open_event.get("mode") or "",
                    "entry_mer": to_float(open_event.get("mer"), to_float(event.get("entry_mer"))),
                    "entry_spread_bps": spread_at_entry,
                    "entry_heat_score": to_float(open_event.get("heat_score")),
                    "entry_pulse_score": to_float(open_event.get("pulse_score")),
                    "net": to_float(event.get("net")),
                    "net_pct": to_float(event.get("net_pct")),
                    "reason": str(event.get("reason") or ""),
                    "age_seconds": to_float(event.get("age_seconds")),
                }
            )
    return trades


def gate_trade_passes(trade: dict[str, Any], gate: Gate) -> bool:
    if to_float(trade.get("entry_spread_bps")) < gate.min_spread_bps:
        return False
    if to_float(trade.get("entry_mer")) < gate.min_mer:
        return False
    # Historical open events did not record entry ATR; ATR-gated historical
    # stats are intentionally reported as unknown-equivalent for that field.
    return True


def gate_board_decision(row: dict[str, Any], gate: Gate) -> tuple[bool, str]:
    spread_bps = to_float(row.get("spread_bps"))
    mer = to_float(row.get("mer"))
    atr = to_float(row.get("atr_12_bps"))
    if spread_bps < gate.min_spread_bps:
        return False, "spread_bps_below_gate"
    if mer < gate.min_mer:
        return False, "mer_below_gate"
    if atr < gate.min_atr_12_bps:
        return False, "atr_12_bps_below_gate"
    return True, "pass"


def summarize_gate(trades: list[dict[str, Any]], gate: Gate) -> dict[str, Any]:
    admitted = [trade for trade in trades if gate_trade_passes(trade, gate)]
    excluded = [trade for trade in trades if not gate_trade_passes(trade, gate)]
    admitted_wins = [trade for trade in admitted if to_float(trade.get("net")) > 0]
    admitted_losses = [trade for trade in admitted if to_float(trade.get("net")) <= 0]
    missed_winners = [trade for trade in excluded if to_float(trade.get("net")) > 0]
    avoided_losers = [trade for trade in excluded if to_float(trade.get("net")) <= 0]
    net = sum(to_float(trade.get("net")) for trade in admitted)
    return {
        "gate": gate.name,
        "min_spread_bps": gate.min_spread_bps,
        "min_mer": gate.min_mer,
        "min_atr_12_bps": gate.min_atr_12_bps,
        "historical_trades": len(trades),
        "admitted_trades": len(admitted),
        "admitted_net_usd": round(net, 6),
        "admitted_avg_net_pct": round(sum(to_float(trade.get("net_pct")) for trade in admitted) / len(admitted), 6) if admitted else 0.0,
        "admitted_win_rate": round(len(admitted_wins) / len(admitted), 6) if admitted else 0.0,
        "admitted_losses": len(admitted_losses),
        "missed_winners": len(missed_winners),
        "missed_winner_net_usd": round(sum(to_float(trade.get("net")) for trade in missed_winners), 6),
        "avoided_losers": len(avoided_losers),
        "avoided_loser_net_usd": round(sum(to_float(trade.get("net")) for trade in avoided_losers), 6),
        "admitted_products": sorted({str(trade.get("product_id")) for trade in admitted}),
        "admitted_loss_products": sorted({str(trade.get("product_id")) for trade in admitted_losses}),
        "missed_winner_products": sorted({str(trade.get("product_id")) for trade in missed_winners}),
    }


def chronological_halves(trades: list[dict[str, Any]], gate: Gate) -> dict[str, Any]:
    split = len(trades) // 2
    return {
        "first_half": summarize_gate(trades[:split], gate),
        "second_half": summarize_gate(trades[split:], gate),
    }


def current_decisions(rows: list[dict[str, Any]], gates: list[Gate]) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for row in rows:
        product_id = str(row.get("product_id") or "")
        if str(row.get("playbook") or "") != "maker_harvest" or not product_id:
            continue
        passes = {}
        reasons = {}
        for gate in gates:
            passes_gate, reason = gate_board_decision(row, gate)
            passes[gate.name] = passes_gate
            reasons[gate.name] = reason
        decisions.append(
            {
                "generated_at": utc_now_iso(),
                "product_id": product_id,
                "mer": round(to_float(row.get("mer")), 6),
                "spread_bps": round(to_float(row.get("spread_bps")), 6),
                "atr_12_bps": round(to_float(row.get("atr_12_bps")), 6),
                "ret_15m_bps": round(to_float(row.get("ret_15m_bps")), 6),
                "vol_24h_usd": round(to_float(row.get("vol_24h_usd")), 6),
                "pulse_score": round(to_float(row.get("pulse_score")), 6),
                "machinegun_score": round(to_float(row.get("machinegun_score")), 6),
                "tail_prob": round(to_float(row.get("tail_prob")), 6),
                "fast_green_prob": round(to_float(row.get("fast_green_prob")), 6),
                "gate_passes": passes,
                "gate_reasons": reasons,
            }
        )
    return decisions


def build_payload(*, events_path: Path, board_path: Path, gates: list[Gate]) -> dict[str, Any]:
    events = load_events(events_path)
    trades = pair_trades(events)
    board_payload = load_json(board_path)
    board_rows = [row for row in board_payload.get("rows", []) if isinstance(row, dict)]
    decisions = current_decisions(board_rows, gates)
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_maker_gate_ab_review",
        "parameters": {
            "events_path": str(events_path),
            "board_path": str(board_path),
            "historical_atr_note": "Historical open events do not include entry atr_12_bps; ATR gate is evaluated on current board decisions only.",
        },
        "summary": {
            "historical_trades": len(trades),
            "current_board_rows": len(board_rows),
            "current_decision_rows": len(decisions),
        },
        "gate_summaries": [summarize_gate(trades, gate) for gate in gates],
        "walk_forward_halves": {gate.name: chronological_halves(trades, gate) for gate in gates},
        "current_gate_counts": {
            gate.name: sum(1 for decision in decisions if decision["gate_passes"].get(gate.name))
            for gate in gates
        },
        "current_gate_products": {
            gate.name: [
                decision["product_id"]
                for decision in decisions
                if decision["gate_passes"].get(gate.name)
            ]
            for gate in gates
        },
        "current_decisions": decisions,
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, md_path: Path, csv_path: Path, tape_path: Path) -> None:
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    decisions = payload["current_decisions"]
    gate_names = [summary["gate"] for summary in payload["gate_summaries"]]
    columns = [
        "product_id",
        "mer",
        "spread_bps",
        "atr_12_bps",
        "pulse_score",
        "machinegun_score",
        *[f"{gate}_pass" for gate in gate_names],
        *[f"{gate}_reason" for gate in gate_names],
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for decision in decisions:
            row = {column: decision.get(column, "") for column in columns}
            for gate in gate_names:
                row[f"{gate}_pass"] = decision["gate_passes"].get(gate, False)
                row[f"{gate}_reason"] = decision["gate_reasons"].get(gate, "")
            writer.writerow(row)
    with tape_path.open("w", encoding="utf-8") as handle:
        for decision in decisions:
            handle.write(json.dumps(decision, separators=(",", ":")) + "\n")

    lines = [
        "# Kraken Maker Gate A/B Review",
        "",
        "## Summary",
        "",
        f"- Historical paired closes: `{payload['summary']['historical_trades']}`",
        f"- Current board rows: `{payload['summary']['current_board_rows']}`",
        f"- Current decision rows: `{payload['summary']['current_decision_rows']}`",
        "- Historical ATR note: entry `atr_12_bps` was not logged on old opens, so ATR gates are current-board only until new admission tape exists.",
        "",
        "## Historical Gate Replay",
        "",
        "| Gate | Spread | MER | ATR | Trades | Net $ | Avg Net % | Win Rate | Losses | Missed Winners | Avoided Losers |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for summary in payload["gate_summaries"]:
        lines.append(
            "| {gate} | {min_spread_bps:.1f} | {min_mer:.1f} | {min_atr_12_bps:.1f} | {admitted_trades} | {admitted_net_usd:.6f} | {admitted_avg_net_pct:.4f} | {admitted_win_rate:.2%} | {admitted_losses} | {missed_winners} | {avoided_losers} |".format(
                **summary
            )
        )
    lines.extend(["", "## Current Board Pass Counts", ""])
    for gate, count in payload["current_gate_counts"].items():
        products = ", ".join(payload["current_gate_products"][gate][:20])
        suffix = "" if len(payload["current_gate_products"][gate]) <= 20 else " ..."
        lines.append(f"- `{gate}`: `{count}` products - {products}{suffix}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay Kraken maker closes against admission gates and write current gate decisions.")
    parser.add_argument("--events-path", default=str(DEFAULT_EVENTS_PATH))
    parser.add_argument("--board-path", default=str(DEFAULT_BOARD_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--tape-path", default=str(DEFAULT_TAPE_PATH))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_payload(
        events_path=Path(args.events_path),
        board_path=Path(args.board_path),
        gates=DEFAULT_GATES,
    )
    write_reports(
        payload,
        json_path=Path(args.json_path),
        md_path=Path(args.md_path),
        csv_path=Path(args.csv_path),
        tape_path=Path(args.tape_path),
    )
    print(json.dumps({"summary": payload["summary"], "gate_summaries": payload["gate_summaries"], "md_path": args.md_path}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
