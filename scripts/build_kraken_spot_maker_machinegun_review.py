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
DEFAULT_STATE_PATH = REPORTS / "kraken_spot_maker_machinegun_shadow_state.json"
DEFAULT_EVENTS_PATH = REPORTS / "kraken_spot_maker_machinegun_shadow_events.jsonl"
DEFAULT_JSON_PATH = REPORTS / "kraken_spot_maker_machinegun_review.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_spot_maker_machinegun_review.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_spot_maker_machinegun_review.md"


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


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


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


def position_risks(
    positions: dict[str, Any],
    *,
    microcap_trail_floor_pct: float,
    max_quote_usd: float,
) -> list[dict[str, Any]]:
    rows = []
    for product_id, position in positions.items():
        if not isinstance(position, dict):
            continue
        entry_price = to_float(position.get("entry_price"))
        trail = to_float(position.get("trail_giveback_pct"))
        mer = to_float(position.get("entry_mer"))
        risk_flags: list[str] = []
        if entry_price > 0 and entry_price < 0.01 and trail < microcap_trail_floor_pct:
            risk_flags.append("legacy_microcap_trail_too_tight")
        if mer <= 0:
            risk_flags.append("missing_mer_join")
        if to_float(position.get("entry_tail_prob")) <= 0 and to_float(position.get("entry_fast_green_prob")) <= 0:
            risk_flags.append("missing_entry_model_scores")
        if str(position.get("playbook") or "") != "maker_harvest":
            risk_flags.append("non_maker_playbook_open")
        if max_quote_usd > 0 and to_float(position.get("cost_usd")) > max_quote_usd + 0.01:
            risk_flags.append("position_cost_over_max_quote_cap")
        rows.append(
            {
                "product_id": product_id,
                "entry_price": entry_price,
                "cost_usd": to_float(position.get("cost_usd")),
                "trail_giveback_pct": trail,
                "entry_mer": mer,
                "entry_tail_prob": to_float(position.get("entry_tail_prob")),
                "entry_fast_green_prob": to_float(position.get("entry_fast_green_prob")),
                "risk_flags": ",".join(risk_flags) if risk_flags else "ok",
            }
        )
    return rows


def build_payload(
    *,
    state_path: Path,
    events_path: Path,
    microcap_trail_floor_pct: float,
) -> dict[str, Any]:
    state_payload = load_json(state_path)
    state = state_payload.get("state") if isinstance(state_payload.get("state"), dict) else state_payload
    positions = state.get("active_positions") if isinstance(state.get("active_positions"), dict) else {}
    events = load_events(events_path)
    close_events = [event for event in events if str(event.get("action") or event.get("event") or "").startswith("close")]
    open_events = [event for event in events if "open" in str(event.get("action") or event.get("event") or "")]
    realized_net = to_float(state.get("realized_net_usd"))
    realized_closes = int(to_float(state.get("realized_closes")))
    maker_fee_bps = to_float(state.get("maker_fee_bps"))
    max_quote_usd = to_float(state.get("max_quote_usd"))
    position_rows = position_risks(
        positions,
        microcap_trail_floor_pct=microcap_trail_floor_pct,
        max_quote_usd=max_quote_usd,
    )
    risk_flags = sorted({flag for row in position_rows for flag in row["risk_flags"].split(",") if flag and flag != "ok"})
    if len(position_rows) > 6:
        risk_flags.append("open_position_count_over_idiosyncratic_cap")
    if maker_fee_bps <= 0:
        risk_flags.append("zero_maker_fee_model")
    if realized_closes <= 0:
        proof_verdict = "no_close_proof"
    elif realized_net > 0 and not risk_flags:
        proof_verdict = "green_but_still_shadow"
    elif realized_net > 0:
        proof_verdict = "green_with_execution_risks"
    else:
        proof_verdict = "red_or_flat_after_closes"

    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_spot_maker_machinegun_review",
        "parameters": {
            "state_path": str(state_path),
            "events_path": str(events_path),
            "microcap_trail_floor_pct": microcap_trail_floor_pct,
        },
        "summary": {
            "cash_usd": round(to_float(state.get("cash_usd")), 6),
            "realized_net_usd": round(realized_net, 6),
            "realized_closes": realized_closes,
            "maker_fee_bps": round(maker_fee_bps, 4),
            "max_quote_usd": round(max_quote_usd, 6),
            "open_positions": len(position_rows),
            "open_events": len(open_events),
            "close_events": len(close_events),
            "risk_flags": risk_flags,
            "proof_verdict": proof_verdict,
        },
        "leadership_read": [
            "Maker spread harvesting is not proven by an open position; it needs close events after realistic fill assumptions.",
            "High spread can be edge or toxicity. Treat MER as a candidate feature, not a fill guarantee.",
            "Legacy open positions with tight microcap trails should be recycled or explicitly migrated before judging a patched runner.",
        ],
        "positions": position_rows,
        "recent_events": events[-20:],
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, csv_path: Path, md_path: Path) -> None:
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    columns = [
        "product_id",
        "entry_price",
        "cost_usd",
        "trail_giveback_pct",
        "entry_mer",
        "entry_tail_prob",
        "entry_fast_green_prob",
        "risk_flags",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload["positions"]:
            writer.writerow({column: row.get(column, "") for column in columns})
    summary = payload["summary"]
    lines = [
        "# Kraken Spot Maker Machinegun Review",
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
            f"- Realized closes: `{summary['realized_closes']}`",
            f"- Maker fee bps: `{summary['maker_fee_bps']}`",
            f"- Open positions: `{summary['open_positions']}`",
            f"- Open events / close events: `{summary['open_events']}` / `{summary['close_events']}`",
            f"- Risk flags: `{summary['risk_flags']}`",
            "",
            "## Open Positions",
            "",
            "| Product | Entry | Cost | Trail % | MER | Tail | FastGreen | Risk Flags |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in payload["positions"]:
        lines.append(
            "| {product_id} | {entry_price:.10f} | {cost_usd:.4f} | {trail_giveback_pct:.4f} | {entry_mer:.4f} | {entry_tail_prob:.4f} | {entry_fast_green_prob:.4f} | {risk_flags} |".format(
                **row
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review Kraken maker machinegun shadow proof quality.")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--events-path", default=str(DEFAULT_EVENTS_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--microcap-trail-floor-pct", type=float, default=2.5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_payload(
        state_path=Path(args.state_path),
        events_path=Path(args.events_path),
        microcap_trail_floor_pct=float(args.microcap_trail_floor_pct),
    )
    write_reports(
        payload,
        json_path=Path(args.json_path),
        csv_path=Path(args.csv_path),
        md_path=Path(args.md_path),
    )
    print(json.dumps({"summary": payload["summary"], "json_path": args.json_path, "md_path": args.md_path}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
