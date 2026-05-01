#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

RSI_FORWARD_PATH = REPORTS / "coinbase_spot_rsi_forward_review.csv"
PIRANHA_CANDIDATE_PATH = REPORTS / "coinbase_spot_piranha_candidates_72h.csv"
PIRANHA_STATE_PATHS = [
    REPORTS / "coinbase_spot_shadow_xrpusd_piranha_state.json",
    REPORTS / "coinbase_spot_shadow_dogeusd_piranha_state.json",
    REPORTS / "coinbase_spot_shadow_suiusd_piranha_state.json",
    REPORTS / "coinbase_spot_shadow_adausd_piranha_state.json",
]
PULSE_BOARD_PATH = REPORTS / "coinbase_spot_pulse_board.json"

JSON_PATH = REPORTS / "coinbase_spot_hot_capital_router.json"
MD_PATH = REPORTS / "coinbase_spot_hot_capital_router.md"


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


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
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_seconds(raw: str, *, now: datetime) -> float | None:
    parsed = parse_iso(raw)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds())


def freshness(age: float | None) -> str:
    if age is None:
        return "missing"
    if age <= 300.0:
        return "active"
    if age <= 3600.0:
        return "stale"
    return "offline"


def piranha_candidate_support() -> dict[str, dict[str, Any]]:
    support: dict[str, dict[str, Any]] = {}
    for row in load_csv(PIRANHA_CANDIDATE_PATH):
        product_id = str(row.get("product_id") or "").strip()
        if product_id:
            support[product_id] = row
    return support


def build_rsi_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in load_csv(RSI_FORWARD_PATH):
        product_id = str(row.get("product_id") or "")
        if product_id == "TOTAL" or not product_id:
            continue
        realized = to_float(row.get("realized_net_usd"))
        closes = to_int(row.get("realized_closes"))
        age = to_float(row.get("heartbeat_age_seconds"))
        readiness = str(row.get("readiness_verdict") or "")
        forward_status = str(row.get("forward_status") or "")
        baseline = to_float(row.get("baseline_72h_net_usd"))
        ratio = to_float(row.get("realized_to_baseline_ratio"))
        in_position = to_int(row.get("in_position"))
        active = age <= 300.0
        clean_probation = readiness == "probationary" and forward_status.startswith("holding_up")
        allocate_state = "eligible_shadow_hot" if active and clean_probation and realized > 0.0 and closes >= 50 else "watch_only"
        if readiness != "probationary":
            allocate_state = "observe_unrated"
        if realized <= 0.0:
            allocate_state = "reject_negative"
        score = (
            max(realized, 0.0)
            + min(closes / 10.0, 15.0)
            + min(max(ratio, 0.0), 8.0) * 2.0
            + min(max(baseline, 0.0), 10.0)
            - (2.0 if in_position else 0.0)
        )
        rows.append(
            {
                "product_id": product_id,
                "family": "rsi_mean_reversion",
                "lane": str(row.get("lane_name") or ""),
                "status": "active" if active else freshness(age),
                "allocation_state": allocate_state,
                "score": round(score, 4),
                "realized_net_usd": round(realized, 4),
                "supporting_net_usd": round(baseline, 4),
                "closes": closes,
                "in_position": in_position,
                "cash_usd": round(to_float(row.get("cash_usd")), 2),
                "read": str(row.get("forward_note") or ""),
            }
        )
    return rows


def build_piranha_rows(*, now: datetime) -> list[dict[str, Any]]:
    support = piranha_candidate_support()
    rows: list[dict[str, Any]] = []
    for path in PIRANHA_STATE_PATHS:
        payload = load_json(path)
        metadata = payload.get("metadata") or {}
        product_id = str(metadata.get("product_id") or "")
        if not product_id:
            continue
        runner = payload.get("runner") or {}
        symbol = ((payload.get("symbols") or {}).get(product_id) or {})
        heartbeat_age = age_seconds(str(runner.get("heartbeat_at") or ""), now=now)
        status = freshness(heartbeat_age)
        realized = to_float(symbol.get("realized_net_usd"))
        closes = to_int(symbol.get("realized_closes"))
        open_lots = len(symbol.get("open_lots") or [])
        candidate = support.get(product_id) or {}
        sim_net = to_float(candidate.get("sim_realized_usd"))
        sim_closes = to_int(candidate.get("sim_closes"))
        product_type = str(metadata.get("product_type") or "").upper()
        spot_ok = product_type == "SPOT"
        if not spot_ok:
            allocation_state = "reject_not_spot"
        elif status == "active" and realized > 0.0 and closes >= 3:
            allocation_state = "eligible_shadow_probe"
        elif status == "active" and sim_net > 0.0:
            allocation_state = "collect_first_closes"
        else:
            allocation_state = "watch_only"
        score = max(realized, 0.0) * 4.0 + min(closes, 10) + max(sim_net, 0.0) * 6.0 - max(open_lots - 2, 0) * 0.25
        rows.append(
            {
                "product_id": product_id,
                "family": "spot_piranha",
                "lane": "coinbase_spot_piranha",
                "status": status,
                "allocation_state": allocation_state,
                "score": round(score, 4),
                "realized_net_usd": round(realized, 4),
                "supporting_net_usd": round(sim_net, 4),
                "closes": closes,
                "open_lots": open_lots,
                "cash_usd": round(to_float(symbol.get("cash_usd")), 2),
                "read": f"sim_closes={sim_closes}, product_type={product_type}",
            }
        )
    return rows


def build_pulse_scout_rows(*, existing_products: set[str]) -> list[dict[str, Any]]:
    payload = load_json(PULSE_BOARD_PATH)
    rows: list[dict[str, Any]] = []
    for row in payload.get("rows") or []:
        product_id = str(row.get("product_id") or "")
        pulse_state = str(row.get("pulse_state") or row.get("status") or "")
        live_tradable = bool(row.get("live_tradable"))
        if not product_id or product_id in existing_products:
            continue
        if not live_tradable:
            continue
        if pulse_state not in {"hot_momentum", "warming"}:
            continue
        rows.append(
            {
                "product_id": product_id,
                "quote_currency": str(row.get("quote_currency") or ""),
                "live_route_state": str(row.get("live_route_state") or ""),
                "pulse_state": pulse_state,
                "pulse_score": round(to_float(row.get("pulse_score")), 4),
                "ret_15m_pct": round(to_float(row.get("ret_15m_pct")), 4),
                "ret_60m_pct": round(to_float(row.get("ret_60m_pct")), 4),
                "ret_4h_pct": round(to_float(row.get("ret_4h_pct")), 4),
                "spread_bps": round(to_float(row.get("spread_bps")), 2),
                "candles": to_int(row.get("candles")),
                "next_action": "shadow_candidate" if pulse_state == "hot_momentum" else "watch_for_shadow",
            }
        )
    rows.sort(key=lambda row: (-to_float(row["pulse_score"]), row["product_id"]))
    return rows[:20]


def build_budget(rows: list[dict[str, Any]], bankroll: float) -> list[dict[str, Any]]:
    eligible = [
        row
        for row in rows
        if row["allocation_state"] in {"eligible_shadow_hot", "eligible_shadow_probe"}
    ]
    eligible.sort(key=lambda row: (-to_float(row["score"]), -to_float(row["realized_net_usd"]), row["product_id"]))
    slots = 2 if bankroll <= 50.0 else 3
    weights = [0.70, 0.30] if slots == 2 else [0.55, 0.30, 0.15]
    reserve = round(bankroll * 0.20, 2)
    deployable = bankroll - reserve
    plan: list[dict[str, Any]] = []
    for row, weight in zip(eligible[:slots], weights):
        plan.append(
            {
                "product_id": row["product_id"],
                "family": row["family"],
                "lane": row["lane"],
                "shadow_budget_usd": round(deployable * weight, 2),
                "reason": row["allocation_state"],
            }
        )
    return plan


def build_payload(*, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    rows = [*build_rsi_rows(), *build_piranha_rows(now=now)]
    rows.sort(key=lambda row: (-to_float(row["score"]), -to_float(row["realized_net_usd"]), row["product_id"]))
    pulse_scout_rows = build_pulse_scout_rows(existing_products={str(row.get("product_id") or "") for row in rows})
    active_spot_rows = [
        row for row in rows if row["status"] == "active" and row["allocation_state"] != "reject_not_spot"
    ]
    return {
        "generated_at": utc_now_iso(),
        "mode": "shadow_only_spot_router",
        "leadership_read": [
            "Build the machine as a rotating allocator over Coinbase spot products, not as independent bots fighting for the same small bankroll.",
            "Current deployable evidence has been reset under account-level spot fees; no lane receives budget until it prints positive forward net after fees.",
            "Tiny-bankroll mode should reserve cash first, concentrate into one or two hot symbols, and demote anything stale, unrated, or negative.",
            "No futures, perps, MT5 crypto, ratio sleeves, or burst/god-mode rows are admitted to this router.",
            "Pulse-board names are scout candidates only; they must be Coinbase live-tradable spot products and still need a shadow lane or route-faithful backtest before receiving budget.",
        ],
        "spot_only_runtime_count": len(active_spot_rows),
        "rows": rows,
        "pulse_scout_rows": pulse_scout_rows,
        "shadow_budget_plans": {
            "50_usd": build_budget(rows, 50.0),
            "100_usd": build_budget(rows, 100.0),
        },
    }


def write_reports(payload: dict[str, Any]) -> None:
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = [
        "# Coinbase Spot Hot Capital Router",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Shadow Budget Plans",
            "",
            "| Bankroll | Product | Family | Lane | Shadow Budget $ | Reason |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for bankroll, plan in payload["shadow_budget_plans"].items():
        for row in plan:
            lines.append(
                "| {bankroll} | {product_id} | {family} | {lane} | {shadow_budget_usd:.2f} | {reason} |".format(
                    bankroll=bankroll, **row
                )
            )
    lines.extend(
        [
            "",
            "## Hot Rows",
            "",
            "| Product | Family | Lane | Status | Allocation State | Score | Realized $ | Support $ | Closes | Cash $ | Read |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {product_id} | {family} | {lane} | {status} | {allocation_state} | {score:.4f} | {realized_net_usd:.4f} | {supporting_net_usd:.4f} | {closes} | {cash_usd:.2f} | {read} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Pulse Scout Rows",
            "",
            "| Product | Quote | Live Route | State | Score | 15m % | 60m % | 4h % | Spread bps | Candles | Next Action |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in payload.get("pulse_scout_rows", []):
        lines.append(
            "| {product_id} | {quote_currency} | {live_route_state} | {pulse_state} | {pulse_score:.4f} | {ret_15m_pct:.4f} | {ret_60m_pct:.4f} | {ret_4h_pct:.4f} | {spread_bps:.2f} | {candles} | {next_action} |".format(
                **row
            )
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
