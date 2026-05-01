#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

MD_PATH = REPORTS / "coinbase_momentum_promotion_queue.md"
JSON_PATH = REPORTS / "coinbase_momentum_promotion_queue.json"

EVIDENCE_MATRIX_PATH = REPORTS / "coinbase_spot_evidence_matrix.json"
RUNTIME_BOARD_PATH = REPORTS / "coinbase_spot_runtime_board.json"
DEPLOYABILITY_BOARD_PATH = REPORTS / "coinbase_spot_deployability_board.json"

ACTION_PRIORITY = {
    "keep_live_priority": 0,
    "launch_shadow_next": 1,
    "launch_shadow_after_top_batch": 2,
    "resolve_router_conflict": 3,
    "watch_probe_only": 4,
    "debug_before_promotion": 5,
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


def load_active_rsi_map() -> dict[str, dict[str, Any]]:
    payload = load_json(RUNTIME_BOARD_PATH)
    rows = payload.get("rsi_shadow_queue") or []
    return {
        str(row.get("product_id") or ""): row
        for row in rows
        if str(row.get("status") or "") == "active"
        and str(row.get("action") or "") == "promote_small_live"
    }


def load_router_map() -> dict[str, dict[str, Any]]:
    payload = load_json(DEPLOYABILITY_BOARD_PATH)
    return {
        str(row.get("product_id") or ""): row
        for row in payload.get("router") or []
    }


def promotion_action(
    *,
    coin: str,
    recon_net: float,
    recon_closes: int,
    runtime_net: float | None,
    runtime_closes: int | None,
    has_active_rsi: bool,
) -> str:
    if runtime_net is not None and runtime_net > 0.0 and (runtime_closes or 0) > 0:
        return "keep_live_priority"
    if runtime_net is not None and runtime_net < 0.0:
        return "debug_before_promotion"
    if recon_net < 2.0:
        return "watch_probe_only" if not has_active_rsi else "resolve_router_conflict"
    if has_active_rsi:
        return "resolve_router_conflict"
    if recon_net >= 15.0 and recon_closes >= 40:
        return "launch_shadow_next"
    return "launch_shadow_after_top_batch"


def action_note(action: str) -> str:
    notes = {
        "keep_live_priority": "already live-positive, so keep this as a capital priority instead of relaunching it",
        "launch_shadow_next": "strong enough to become the next standalone momentum shadow lane",
        "launch_shadow_after_top_batch": "positive and real, but not the first shadow promotion slot",
        "resolve_router_conflict": "positive momentum exists, but another active lane on this product should be arbitrated first",
        "watch_probe_only": "confirmed positive but too thin to promote honestly yet",
        "debug_before_promotion": "bench-positive, but runtime contradiction should be resolved before more capital",
    }
    return notes.get(action, "")


def build_payload(*, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    evidence = load_json(EVIDENCE_MATRIX_PATH)
    active_rsi_map = load_active_rsi_map()
    router_map = load_router_map()

    queue: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for row in evidence.get("rows") or []:
        if str(row.get("family") or "") != "momentum":
            continue
        verdict = str(row.get("verdict") or "")
        if verdict not in {"deployable_priority", "bench_positive_wait_runtime"}:
            continue

        coin = str(row.get("coin") or "")
        strategy = str(row.get("strategy") or "")
        recon_net = to_float(row.get("reconciliation_net_30d_usd"))
        recon_closes = to_int(row.get("reconciliation_closes_30d"))
        sweep_net = to_float(row.get("library_sweep_partial_14d_net_usd"))
        runtime_raw = row.get("runtime_realized_usd")
        runtime_net = None if runtime_raw is None else to_float(runtime_raw)
        runtime_closes = row.get("runtime_closes")
        runtime_closes_int = None if runtime_closes is None else to_int(runtime_closes)
        has_active_rsi = coin in active_rsi_map
        router_row = router_map.get(coin) or {}

        action = promotion_action(
            coin=coin,
            recon_net=recon_net,
            recon_closes=recon_closes,
            runtime_net=runtime_net,
            runtime_closes=runtime_closes_int,
            has_active_rsi=has_active_rsi,
        )

        score = round(
            recon_net
            + min(recon_closes, 80) * 0.12
            + min(sweep_net, 25.0) * 0.25
            - (12.0 if has_active_rsi else 0.0)
            - (8.0 if runtime_net is not None and runtime_net < 0.0 else 0.0)
            - (10.0 if recon_net < 2.0 else 0.0),
            4,
        )

        record = {
            "coin": coin,
            "strategy": strategy,
            "verdict": verdict,
            "action": action,
            "score": score,
            "reconciliation_30d_net_usd": round(recon_net, 4),
            "reconciliation_30d_closes": recon_closes,
            "library_sweep_partial_14d_net_usd": round(sweep_net, 4),
            "runtime_realized_usd": None if runtime_net is None else round(runtime_net, 4),
            "runtime_closes": runtime_closes_int,
            "has_active_rsi_lane": has_active_rsi,
            "router_lane": str(router_row.get("recommended_lane") or ""),
            "router_action": str(router_row.get("action") or ""),
            "note": action_note(action),
        }

        if action in {"resolve_router_conflict", "watch_probe_only", "debug_before_promotion"}:
            blocked.append(record)
        else:
            queue.append(record)

    queue.sort(
        key=lambda row: (
            ACTION_PRIORITY[row["action"]],
            -to_float(row["score"]),
            -to_float(row["reconciliation_30d_net_usd"]),
            row["coin"],
        )
    )
    blocked.sort(
        key=lambda row: (
            ACTION_PRIORITY[row["action"]],
            -to_float(row["score"]),
            -to_float(row["reconciliation_30d_net_usd"]),
            row["coin"],
        )
    )

    leadership_read = [
        "CFG and A8 are the cleanest new standalone momentum shadow promotions because they have meaningful 30d confirmation without an active competing lane.",
        "PRL momentum is real, but it should not outrank the already-positive PRL RSI lane without a router decision.",
        "Thin confirmations like DASH and FARTCOIN should stay explicitly restrained so the board does not confuse barely-positive with scalable.",
        "RAVE momentum remains the live benchmark leader, while BLUR momentum is blocked by its own negative runtime contradiction.",
    ]

    return {
        "generated_at": now.isoformat(),
        "leadership_read": leadership_read,
        "queue": queue,
        "blocked_or_deferred": blocked,
    }


def write_reports(payload: dict[str, Any], *, md_path: Path = MD_PATH, json_path: Path = JSON_PATH) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Coinbase Momentum Promotion Queue",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")

    lines.extend(
        [
            "",
            "## Promotion Queue",
            "",
            "| Coin | Strategy | Verdict | Action | Score | Recon 30d $ | Recon Closes | Sweep 14d $ | Runtime $ | Active RSI | Router Lane | Note |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in payload["queue"]:
        runtime = "" if row["runtime_realized_usd"] is None else f"{float(row['runtime_realized_usd']):.4f}"
        lines.append(
            "| {coin} | {strategy} | {verdict} | {action} | {score:.4f} | {reconciliation_30d_net_usd:.4f} | {reconciliation_30d_closes} | {library_sweep_partial_14d_net_usd:.4f} | {runtime} | {active_rsi} | {router_lane} | {note} |".format(
                coin=row["coin"],
                strategy=row["strategy"],
                verdict=row["verdict"],
                action=row["action"],
                score=float(row["score"]),
                reconciliation_30d_net_usd=float(row["reconciliation_30d_net_usd"]),
                reconciliation_30d_closes=row["reconciliation_30d_closes"],
                library_sweep_partial_14d_net_usd=float(row["library_sweep_partial_14d_net_usd"]),
                runtime=runtime,
                active_rsi="yes" if row["has_active_rsi_lane"] else "no",
                router_lane=row["router_lane"] or "",
                note=row["note"],
            )
        )

    lines.extend(
        [
            "",
            "## Blocked Or Deferred",
            "",
            "| Coin | Strategy | Action | Recon 30d $ | Recon Closes | Runtime $ | Active RSI | Router Lane | Note |",
            "| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in payload["blocked_or_deferred"]:
        runtime = "" if row["runtime_realized_usd"] is None else f"{float(row['runtime_realized_usd']):.4f}"
        lines.append(
            "| {coin} | {strategy} | {action} | {reconciliation_30d_net_usd:.4f} | {reconciliation_30d_closes} | {runtime} | {active_rsi} | {router_lane} | {note} |".format(
                coin=row["coin"],
                strategy=row["strategy"],
                action=row["action"],
                reconciliation_30d_net_usd=float(row["reconciliation_30d_net_usd"]),
                reconciliation_30d_closes=row["reconciliation_30d_closes"],
                runtime=runtime,
                active_rsi="yes" if row["has_active_rsi_lane"] else "no",
                router_lane=row["router_lane"] or "",
                note=row["note"],
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
