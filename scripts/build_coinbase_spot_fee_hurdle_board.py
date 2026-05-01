#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from live_penetration_lattice_shadow import utc_now_iso


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
PULSE_PATH = REPORTS / "coinbase_spot_pulse_board.json"
JSON_PATH = REPORTS / "coinbase_spot_fee_hurdle_board.json"
CSV_PATH = REPORTS / "coinbase_spot_fee_hurdle_board.csv"
MD_PATH = REPORTS / "coinbase_spot_fee_hurdle_board.md"


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def classify_row(row: dict[str, Any], *, hurdle_pct: float, max_spread_bps: float) -> tuple[str, str]:
    spread_bps = to_float(row.get("spread_bps"))
    ret_15 = to_float(row.get("ret_15m_pct"))
    ret_60 = to_float(row.get("ret_60m_pct"))
    ret_4h = to_float(row.get("ret_4h_pct"))
    if str(row.get("live_route_state") or "") != "ready_direct_usd_or_stable":
        return "route_blocked", "needs quote inventory or conversion costing"
    if spread_bps > max_spread_bps:
        return "spread_blocked", "spread too wide for a high-fee account"
    if ret_15 >= hurdle_pct and ret_60 >= hurdle_pct:
        return "clears_fast_hurdle", "fast move already covers fee hurdle; trail-only shadow candidate"
    if ret_60 >= hurdle_pct and ret_4h >= hurdle_pct:
        return "clears_hour_hurdle", "hour move covers fee hurdle; momentum-continuation shadow candidate"
    if ret_4h >= hurdle_pct and ret_15 <= 0.0:
        return "pullback_reentry_watch", "larger move cleared hurdle but current pullback needs reload confirmation"
    if max(ret_15, ret_60, ret_4h) >= hurdle_pct * 0.75:
        return "near_hurdle_watch", "near fee hurdle; watch but do not allocate"
    return "fee_hurdle_blocked", "move is too small after taker fees and spread"


def build_row(row: dict[str, Any], *, taker_fee_bps: float, profit_buffer_pct: float, max_spread_bps: float) -> dict[str, Any]:
    fee_round_trip_pct = (2.0 * taker_fee_bps) / 100.0
    spread_pct = to_float(row.get("spread_bps")) / 100.0
    hurdle_pct = fee_round_trip_pct + spread_pct + profit_buffer_pct
    state, read = classify_row(row, hurdle_pct=hurdle_pct, max_spread_bps=max_spread_bps)
    ret_15 = to_float(row.get("ret_15m_pct"))
    ret_60 = to_float(row.get("ret_60m_pct"))
    ret_4h = to_float(row.get("ret_4h_pct"))
    best_move = max(ret_15, ret_60, ret_4h)
    p90_range = to_float(row.get("p90_range_60m_pct"))
    median_range = to_float(row.get("median_range_60m_pct"))
    trail_giveback_pct = max(0.25, min(max(median_range * 1.5, p90_range * 0.75), max(best_move - hurdle_pct, 0.25)))
    return {
        "product_id": str(row.get("product_id") or ""),
        "quote_currency": str(row.get("quote_currency") or ""),
        "live_route_state": str(row.get("live_route_state") or ""),
        "pulse_state": str(row.get("pulse_state") or ""),
        "hurdle_state": state,
        "hurdle_read": read,
        "pulse_score": round(to_float(row.get("pulse_score")), 4),
        "ret_15m_pct": round(ret_15, 4),
        "ret_60m_pct": round(ret_60, 4),
        "ret_4h_pct": round(ret_4h, 4),
        "best_move_pct": round(best_move, 4),
        "fee_round_trip_pct": round(fee_round_trip_pct, 4),
        "spread_bps": round(to_float(row.get("spread_bps")), 4),
        "spread_pct": round(spread_pct, 4),
        "profit_buffer_pct": round(profit_buffer_pct, 4),
        "all_in_hurdle_pct": round(hurdle_pct, 4),
        "edge_over_hurdle_pct": round(best_move - hurdle_pct, 4),
        "median_range_60m_pct": round(median_range, 4),
        "p90_range_60m_pct": round(p90_range, 4),
        "suggested_trail_giveback_pct": round(trail_giveback_pct, 4),
        "quote_volume_native": round(to_float(row.get("quote_volume_native")), 4),
        "candles": int(to_float(row.get("candles"))),
    }


def build_payload(*, taker_fee_bps: float, profit_buffer_pct: float, max_spread_bps: float, top: int) -> dict[str, Any]:
    pulse = load_json(PULSE_PATH)
    rows = [
        build_row(row, taker_fee_bps=taker_fee_bps, profit_buffer_pct=profit_buffer_pct, max_spread_bps=max_spread_bps)
        for row in (pulse.get("rows") or [])
        if row.get("live_tradable") and str(row.get("status") or "") == "ok"
    ]
    rows.sort(key=lambda row: (row["hurdle_state"].startswith("clears"), row["edge_over_hurdle_pct"], row["pulse_score"]), reverse=True)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["hurdle_state"]] = counts.get(row["hurdle_state"], 0) + 1
    return {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_fee_hurdle_board",
        "parameters": {
            "taker_fee_bps": taker_fee_bps,
            "profit_buffer_pct": profit_buffer_pct,
            "max_spread_bps": max_spread_bps,
            "pulse_path": str(PULSE_PATH),
        },
        "summary": {
            "rows": len(rows),
            "state_counts": counts,
            "eligible_hurdle_rows": sum(1 for row in rows if row["hurdle_state"].startswith("clears")),
        },
        "leadership_read": [
            "At this account fee tier, a spot trade must clear round-trip taker fees before it is even interesting.",
            "This board ranks live-tradable Coinbase spot products by whether recent momentum exceeds fee + spread + profit buffer.",
            "Clearing the hurdle is not live permission; it is the queue for trail-only shadow experiments that protect profit and allow re-entry on reload.",
        ],
        "rows": rows[:top],
    }


def write_reports(payload: dict[str, Any]) -> None:
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    columns = [
        "product_id",
        "quote_currency",
        "hurdle_state",
        "pulse_state",
        "pulse_score",
        "ret_15m_pct",
        "ret_60m_pct",
        "ret_4h_pct",
        "best_move_pct",
        "all_in_hurdle_pct",
        "edge_over_hurdle_pct",
        "spread_bps",
        "suggested_trail_giveback_pct",
        "hurdle_read",
    ]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload["rows"]:
            writer.writerow({key: row.get(key, "") for key in columns})
    lines = [
        "# Coinbase Spot Fee Hurdle Board",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    params = payload["parameters"]
    summary = payload["summary"]
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Taker fee per side: `{params['taker_fee_bps']}` bps",
            f"- Profit buffer: `{params['profit_buffer_pct']}`%",
            f"- Max spread: `{params['max_spread_bps']}` bps",
            f"- Rows scored: `{summary['rows']}`",
            f"- Hurdle-clearing rows: `{summary['eligible_hurdle_rows']}`",
            f"- State counts: `{summary['state_counts']}`",
            "",
            "## Top Rows",
            "",
            "| Product | State | Pulse | 15m % | 60m % | 4h % | Hurdle % | Edge % | Spread bps | Trail Giveback % | Read |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {product_id} | {hurdle_state} | {pulse_score:.4f} | {ret_15m_pct:.4f} | {ret_60m_pct:.4f} | {ret_4h_pct:.4f} | {all_in_hurdle_pct:.4f} | {edge_over_hurdle_pct:.4f} | {spread_bps:.2f} | {suggested_trail_giveback_pct:.4f} | {hurdle_read} |".format(
                **row
            )
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rank Coinbase spot pulse rows against account fee hurdle.")
    parser.add_argument("--taker-fee-bps", type=float, default=120.0)
    parser.add_argument("--profit-buffer-pct", type=float, default=0.75)
    parser.add_argument("--max-spread-bps", type=float, default=75.0)
    parser.add_argument("--top", type=int, default=80)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_payload(
        taker_fee_bps=float(args.taker_fee_bps),
        profit_buffer_pct=float(args.profit_buffer_pct),
        max_spread_bps=float(args.max_spread_bps),
        top=max(1, int(args.top)),
    )
    write_reports(payload)
    print(json.dumps({"json_path": str(JSON_PATH), "csv_path": str(CSV_PATH), "md_path": str(MD_PATH), "summary": payload["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
