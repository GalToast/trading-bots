#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

MD_PATH = REPORTS / "coinbase_spot_router_conflict_board.md"
JSON_PATH = REPORTS / "coinbase_spot_router_conflict_board.json"

EVIDENCE_MATRIX_PATH = REPORTS / "coinbase_spot_evidence_matrix.json"
RUNTIME_BOARD_PATH = REPORTS / "coinbase_spot_runtime_board.json"
DEPLOYABILITY_BOARD_PATH = REPORTS / "coinbase_spot_deployability_board.json"
MOMENTUM_PROMOTION_PATH = REPORTS / "coinbase_momentum_promotion_queue.json"

ACTION_PRIORITY = {
    "anchor_momentum_keep_rsi_secondary": 0,
    "replace_negative_rsi_with_momentum_shadow": 1,
    "keep_rsi_primary_momentum_shadow_candidate": 2,
    "keep_rsi_only_for_now": 3,
}


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


def load_momentum_map() -> dict[str, dict[str, Any]]:
    payload = load_json(EVIDENCE_MATRIX_PATH)
    rows = payload.get("rows") or []
    return {
        str(row.get("coin") or ""): row
        for row in rows
        if str(row.get("family") or "") == "momentum"
        and str(row.get("verdict") or "") in {"deployable_priority", "bench_positive_wait_runtime"}
    }


def load_positive_rsi_map() -> dict[str, dict[str, Any]]:
    payload = load_json(RUNTIME_BOARD_PATH)
    rows = list(payload.get("rsi_shadow_queue") or [])
    for row in payload.get("key_lanes") or []:
        if str(row.get("product_id") or "") == "RAVE-USD" and str(row.get("family") or "") == "rsi_mean_reversion":
            rows.append(
                {
                    "product_id": "RAVE-USD",
                    "lane": str(row.get("lane") or ""),
                    "status": str(row.get("status") or ""),
                    "action": str(row.get("action") or ""),
                    "realized_net_usd": row.get("realized_net_usd"),
                    "closes": row.get("closes"),
                    "note": str(row.get("note") or ""),
                }
            )
    return {
        str(row.get("product_id") or ""): row
        for row in rows
        if str(row.get("status") or "") == "active"
    }


def load_rejected_rsi_map() -> dict[str, dict[str, Any]]:
    payload = load_json(DEPLOYABILITY_BOARD_PATH)
    return {
        str(row.get("product_id") or ""): row
        for row in payload.get("rejects") or []
        if str(row.get("family") or "") == "rsi_mean_reversion"
        and str(row.get("runner_status") or "") == "active"
    }


def load_momentum_actions() -> dict[str, dict[str, Any]]:
    payload = load_json(MOMENTUM_PROMOTION_PATH)
    rows = list(payload.get("queue") or []) + list(payload.get("blocked_or_deferred") or [])
    return {str(row.get("coin") or ""): row for row in rows}


def choose_action(*, coin: str, momentum_row: dict[str, Any], positive_rsi: dict[str, Any] | None, rejected_rsi: dict[str, Any] | None) -> tuple[str, str]:
    momentum_verdict = str(momentum_row.get("verdict") or "")
    momentum_net = to_float(
        momentum_row.get("reconciliation_30d_net_usd")
        if momentum_row.get("reconciliation_30d_net_usd") is not None
        else momentum_row.get("reconciliation_net_30d_usd")
    )

    if positive_rsi:
        rsi_net = to_float(positive_rsi.get("realized_net_usd"))
        if coin == "RAVE-USD" or momentum_verdict == "deployable_priority":
            return (
                "anchor_momentum_keep_rsi_secondary",
                "both lanes are positive, but momentum has the stronger benchmark evidence so it should anchor capital while RSI stays live as a secondary edge",
            )
        if momentum_net >= 10.0:
            return (
                "keep_rsi_primary_momentum_shadow_candidate",
                "RSI is already active and positive, so keep it primary while momentum earns a separate shadow slot instead of stealing the router immediately",
            )
        if momentum_net < rsi_net:
            return (
                "keep_rsi_only_for_now",
                "momentum is positive but weaker than the active RSI lane, so the router should stay on RSI for now",
            )
        return (
            "keep_rsi_only_for_now",
            "momentum is too thin to justify overriding an already-positive RSI lane",
        )

    if rejected_rsi:
        return (
            "replace_negative_rsi_with_momentum_shadow",
            "the active RSI lane is already losing, so the confirmed momentum lane should replace it as the next product-specific probe",
        )

    raise ValueError(f"coin {coin} is not a router conflict candidate")


def build_payload(*, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    momentum_map = load_momentum_map()
    positive_rsi_map = load_positive_rsi_map()
    rejected_rsi_map = load_rejected_rsi_map()
    momentum_actions = load_momentum_actions()

    rows: list[dict[str, Any]] = []
    candidate_coins = sorted(set(momentum_map) & (set(positive_rsi_map) | set(rejected_rsi_map)))
    for coin in candidate_coins:
        momentum_row = momentum_map[coin]
        positive_rsi = positive_rsi_map.get(coin)
        rejected_rsi = rejected_rsi_map.get(coin)
        action, rationale = choose_action(
            coin=coin,
            momentum_row=momentum_row,
            positive_rsi=positive_rsi,
            rejected_rsi=rejected_rsi,
        )
        momentum_action_row = momentum_actions.get(coin) or {}
        rsi_row = positive_rsi or rejected_rsi or {}

        rows.append(
            {
                "coin": coin,
                "momentum_strategy": str(momentum_row.get("strategy") or ""),
                "momentum_verdict": str(momentum_row.get("verdict") or ""),
                "momentum_reconciliation_30d_net_usd": round(
                    to_float(
                        momentum_row.get("reconciliation_30d_net_usd")
                        if momentum_row.get("reconciliation_30d_net_usd") is not None
                        else momentum_row.get("reconciliation_net_30d_usd")
                    ),
                    4,
                ),
                "momentum_reconciliation_30d_closes": to_int(
                    momentum_row.get("reconciliation_30d_closes")
                    if momentum_row.get("reconciliation_30d_closes") is not None
                    else momentum_row.get("reconciliation_closes_30d")
                ),
                "momentum_router_status": str(momentum_action_row.get("action") or ""),
                "rsi_lane": str(rsi_row.get("lane") or ""),
                "rsi_status": str(rsi_row.get("status") or rsi_row.get("runner_status") or ""),
                "rsi_action": str(rsi_row.get("action") or ""),
                "rsi_realized_usd": round(
                    to_float(
                        rsi_row.get("realized_net_usd")
                        if rsi_row.get("realized_net_usd") is not None
                        else rsi_row.get("observed_net_usd")
                    ),
                    4,
                ),
                "rsi_closes": to_int(rsi_row.get("closes") if rsi_row.get("closes") is not None else rsi_row.get("realized_closes")),
                "conflict_action": action,
                "rationale": rationale,
            }
        )

    rows.sort(
        key=lambda row: (
            ACTION_PRIORITY[row["conflict_action"]],
            -to_float(row["momentum_reconciliation_30d_net_usd"]),
            row["coin"],
        )
    )

    leadership_read = [
        "RAVE is the only true dual-winner right now, and momentum should anchor capital while RSI keeps running as a secondary edge.",
        "PRL is a real router conflict, but the answer is not to flip blindly: keep RSI primary and treat momentum as a separate shadow candidate.",
        "FARTCOIN has an active positive RSI lane and only a razor-thin momentum confirmation, so the router should stay on RSI for now.",
        "A8 is the cleanest product-level replacement case: its active RSI lane is losing, while momentum is confirmed positive enough to take over the next probe slot.",
    ]

    return {
        "generated_at": now.isoformat(),
        "leadership_read": leadership_read,
        "rows": rows,
    }


def write_reports(payload: dict[str, Any], *, md_path: Path = MD_PATH, json_path: Path = JSON_PATH) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Coinbase Spot Router Conflict Board",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Conflicts",
            "",
            "| Coin | Momentum | Recon 30d $ | Recon Closes | Momentum Queue | RSI Lane | RSI $ | RSI Closes | Conflict Action | Rationale |",
            "| --- | --- | ---: | ---: | --- | --- | ---: | ---: | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {coin} | {momentum_strategy} ({momentum_verdict}) | {momentum_reconciliation_30d_net_usd:.4f} | {momentum_reconciliation_30d_closes} | {momentum_router_status} | {rsi_lane} | {rsi_realized_usd:.4f} | {rsi_closes} | {conflict_action} | {rationale} |".format(
                **row
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
