#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

MD_PATH = REPORTS / "coinbase_spot_next_launch_wave.md"
JSON_PATH = REPORTS / "coinbase_spot_next_launch_wave.json"

MOMENTUM_PROMOTION_PATH = REPORTS / "coinbase_momentum_promotion_queue.json"
ROUTER_CONFLICT_PATH = REPORTS / "coinbase_spot_router_conflict_board.json"
RUNTIME_BOARD_PATH = REPORTS / "coinbase_spot_runtime_board.json"
DEPLOYABILITY_BOARD_PATH = REPORTS / "coinbase_spot_deployability_board.json"
VALIDATION_RESULTS_PATH = REPORTS / "coinbase_momentum_validation_results.json"
BREAKOUT_PROMOTION_PATH = REPORTS / "coinbase_breakout_promotion_queue.json"

WAVE_PRIORITY = {
    "maintain_live": 0,
    "launch_now": 1,
    "launch_after_wave_1": 2,
    "router_hold": 3,
    "debug_hold": 4,
    "watch_only": 5,
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


def build_payload(*, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    momentum = load_json(MOMENTUM_PROMOTION_PATH)
    conflicts = load_json(ROUTER_CONFLICT_PATH)
    runtime = load_json(RUNTIME_BOARD_PATH)
    deploy = load_json(DEPLOYABILITY_BOARD_PATH)
    validation = load_json(VALIDATION_RESULTS_PATH)
    breakout = load_json(BREAKOUT_PROMOTION_PATH)

    conflict_map = {str(row.get("coin") or ""): row for row in conflicts.get("rows") or []}
    runtime_key_lanes = {str(row.get("product_id") or ""): row for row in runtime.get("key_lanes") or []}
    deploy_router = {str(row.get("product_id") or ""): row for row in deploy.get("router") or []}
    existing_lanes: set[tuple[str, str]] = set()

    rows: list[dict[str, Any]] = []

    for row in momentum.get("queue") or []:
        coin = str(row.get("coin") or "")
        action = str(row.get("action") or "")
        runtime_row = runtime_key_lanes.get(coin) or {}
        conflict_row = conflict_map.get(coin) or {}
        router_row = deploy_router.get(coin) or {}

        if action == "keep_live_priority":
            launch_wave = "maintain_live"
            launch_reason = "already live-positive and should stay in the active capital stack"
        elif action == "launch_shadow_next":
            launch_wave = "launch_now"
            launch_reason = "clean standalone momentum promotion with no stronger competing lane"
        elif action == "launch_shadow_after_top_batch":
            launch_wave = "launch_after_wave_1"
            launch_reason = "positive and promotable, but not ahead of the top confirmed names"
        else:
            continue

        rows.append(
            {
                "coin": coin,
                "strategy": str(row.get("strategy") or ""),
                "launch_wave": launch_wave,
                "priority_score": round(to_float(row.get("score")), 4),
                "reconciliation_30d_net_usd": round(to_float(row.get("reconciliation_30d_net_usd")), 4),
                "reconciliation_30d_closes": to_int(row.get("reconciliation_30d_closes")),
                "runtime_realized_usd": (
                    None if row.get("runtime_realized_usd") is None else round(to_float(row.get("runtime_realized_usd")), 4)
                ),
                "router_decision": str(conflict_row.get("conflict_action") or router_row.get("action") or ""),
                "precondition": (
                    "keep both momentum and RSI visible"
                    if coin == "RAVE-USD"
                    else "replace losing RSI probe first"
                    if str(conflict_row.get("conflict_action") or "") == "replace_negative_rsi_with_momentum_shadow"
                    else "new standalone momentum shadow"
                ),
                "reason": launch_reason,
            }
        )
        existing_lanes.add((coin, str(row.get("strategy") or "")))

    for row in momentum.get("blocked_or_deferred") or []:
        coin = str(row.get("coin") or "")
        conflict_row = conflict_map.get(coin) or {}
        action = str(row.get("action") or "")
        if action == "resolve_router_conflict":
            launch_wave = "router_hold"
            reason = str(conflict_row.get("rationale") or row.get("note") or "")
        elif action == "debug_before_promotion":
            launch_wave = "debug_hold"
            reason = str(row.get("note") or "")
        elif action == "watch_probe_only":
            launch_wave = "watch_only"
            reason = str(row.get("note") or "")
        else:
            continue

        rows.append(
            {
                "coin": coin,
                "strategy": str(row.get("strategy") or ""),
                "launch_wave": launch_wave,
                "priority_score": round(to_float(row.get("score")), 4),
                "reconciliation_30d_net_usd": round(to_float(row.get("reconciliation_30d_net_usd")), 4),
                "reconciliation_30d_closes": to_int(row.get("reconciliation_30d_closes")),
                "runtime_realized_usd": (
                    None if row.get("runtime_realized_usd") is None else round(to_float(row.get("runtime_realized_usd")), 4)
                ),
                "router_decision": str(conflict_row.get("conflict_action") or ""),
                "precondition": (
                    "explicit router ruling required"
                    if launch_wave == "router_hold"
                    else "runtime contradiction must be cleared"
                    if launch_wave == "debug_hold"
                    else "needs more edge before capital"
                ),
                "reason": reason,
            }
        )
        existing_lanes.add((coin, str(row.get("strategy") or "")))

    for row in breakout.get("queue") or []:
        coin = str(row.get("coin") or "")
        strategy = str(row.get("strategy") or "")
        if not coin or not strategy:
            continue
        rows.append(
            {
                "coin": coin,
                "strategy": strategy,
                "launch_wave": "launch_after_wave_1" if str(row.get("action") or "") == "launch_shadow_after_top_batch" else "launch_now",
                "priority_score": round(to_float(row.get("score")), 4),
                "reconciliation_30d_net_usd": round(to_float(row.get("reconciliation_30d_net_usd")), 4),
                "reconciliation_30d_closes": to_int(row.get("reconciliation_30d_closes")),
                "runtime_realized_usd": None,
                "router_decision": "breakout_shadow_candidate",
                "precondition": "new standalone breakout shadow",
                "reason": str(row.get("note") or ""),
            }
        )
        existing_lanes.add((coin, strategy))

    for row in breakout.get("blocked_or_deferred") or []:
        coin = str(row.get("coin") or "")
        strategy = str(row.get("strategy") or "")
        if not coin or not strategy:
            continue
        action = str(row.get("action") or "")
        launch_wave = "router_hold" if action == "resolve_router_conflict" else "watch_only"
        rows.append(
            {
                "coin": coin,
                "strategy": strategy,
                "launch_wave": launch_wave,
                "priority_score": round(to_float(row.get("score")), 4),
                "reconciliation_30d_net_usd": round(to_float(row.get("reconciliation_30d_net_usd")), 4),
                "reconciliation_30d_closes": to_int(row.get("reconciliation_30d_closes")),
                "runtime_realized_usd": None,
                "router_decision": str(row.get("router_conflict_action") or action),
                "precondition": "explicit router ruling required" if launch_wave == "router_hold" else "needs stronger breakout edge before capital",
                "reason": str(row.get("note") or ""),
            }
        )
        existing_lanes.add((coin, strategy))

    for row in validation.get("results") or []:
        coin = str(row.get("coin") or "")
        if (coin, "momentum_registry_validation") in existing_lanes:
            continue
        verdict = str(row.get("verdict") or "")
        recon_net = round(to_float(row.get("reconciliation_30d_net_usd")), 4)
        recon_closes = to_int(row.get("reconciliation_30d_closes"))
        max_dd = to_float(row.get("reconciliation_30d_max_dd"))
        if verdict == "confirmed_positive" and recon_net >= 20.0 and max_dd <= 30.0:
            launch_wave = "launch_after_wave_1"
            precondition = "carry forward into the second promotion wave"
            reason = "validated positive from the registry inbox, but still behind the already-queued A8/CFG batch"
        elif verdict == "confirmed_positive":
            launch_wave = "watch_only"
            precondition = "needs stronger edge before promotion"
            reason = "validated positive, but not strong enough yet to displace the current queued names"
        else:
            launch_wave = "debug_hold"
            precondition = "do not promote from a failed 30d validation"
            reason = "registry claim failed the 30d validation pass"

        rows.append(
            {
                "coin": coin,
                "strategy": "momentum_registry_validation",
                "launch_wave": launch_wave,
                "priority_score": recon_net,
                "reconciliation_30d_net_usd": recon_net,
                "reconciliation_30d_closes": recon_closes,
                "runtime_realized_usd": None,
                "router_decision": verdict,
                "precondition": precondition,
                "reason": reason,
            }
        )
        existing_lanes.add((coin, "momentum_registry_validation"))

    # Non-momentum operational holds that still matter for the next wave.
    for product_id in ("DOGE-USD", "XRP-USD"):
        runtime_row = runtime_key_lanes.get(product_id) or {}
        if runtime_row:
            rows.append(
                {
                    "coin": product_id,
                    "strategy": "spot_piranha",
                    "launch_wave": "router_hold",
                    "priority_score": 0.0,
                    "reconciliation_30d_net_usd": 0.0,
                    "reconciliation_30d_closes": 0,
                    "runtime_realized_usd": round(to_float(runtime_row.get("realized_net_usd")), 4),
                    "router_decision": str(runtime_row.get("action") or ""),
                    "precondition": "runner heartbeat and first closes needed",
                    "reason": "keep current probe alive, but do not let it outrank the confirmed momentum wave",
                }
            )

    rows.sort(
        key=lambda row: (
            WAVE_PRIORITY[row["launch_wave"]],
            -to_float(row["reconciliation_30d_net_usd"]),
            -to_float(row["priority_score"]),
            row["coin"],
        )
    )

    leadership_read = [
        "Wave 1 should be A8 momentum and CFG momentum, with RAVE momentum simply maintained as the live anchor instead of relaunching it.",
        "A8 is the cleanest replacement case because its active RSI lane is already losing and momentum has real 30d confirmation.",
        "PRL and FARTCOIN should not consume the next launch slots because their active RSI lanes still own the router.",
        "DOGE and XRP piranha should be treated as health-check probes, not reasons to delay the confirmed momentum wave.",
    ]
    breakout_queue_coins = [str(row.get("coin") or "") for row in breakout.get("queue") or []]
    if breakout_queue_coins:
        leadership_read.append(
            "NOM and SUP now belong in the next breakout-shadow stack, with BAL trailing behind them and PRL still blocked by router conflict."
        )
    if any(str(row.get("coin") or "") == "SUP-USD" and str(row.get("launch_wave") or "") == "launch_after_wave_1" for row in rows):
        leadership_read.append("SUP has now cleared 30d validation strongly enough to join the second launch wave.")
    if any(str(row.get("coin") or "") == "TROLL-USD" and str(row.get("launch_wave") or "") == "debug_hold" for row in rows):
        leadership_read.append("TROLL failed the first 30d validation pass and should stop being pitched off its 7d screenshot.")

    return {
        "generated_at": now.isoformat(),
        "leadership_read": leadership_read,
        "rows": rows,
    }


def write_reports(payload: dict[str, Any], *, md_path: Path = MD_PATH, json_path: Path = JSON_PATH) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Coinbase Spot Next Launch Wave",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Launch Sequence",
            "",
            "| Coin | Strategy | Launch Wave | Recon 30d $ | Recon Closes | Runtime $ | Router | Precondition | Reason |",
            "| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        runtime_value = "" if row["runtime_realized_usd"] is None else f"{float(row['runtime_realized_usd']):.4f}"
        lines.append(
            "| {coin} | {strategy} | {launch_wave} | {reconciliation_30d_net_usd:.4f} | {reconciliation_30d_closes} | {runtime} | {router_decision} | {precondition} | {reason} |".format(
                coin=row["coin"],
                strategy=row["strategy"],
                launch_wave=row["launch_wave"],
                reconciliation_30d_net_usd=float(row["reconciliation_30d_net_usd"]),
                reconciliation_30d_closes=row["reconciliation_30d_closes"],
                runtime=runtime_value,
                router_decision=row["router_decision"] or "",
                precondition=row["precondition"],
                reason=row["reason"],
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
