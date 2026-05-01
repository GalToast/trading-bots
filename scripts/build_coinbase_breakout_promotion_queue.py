#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

SWEEP_PATH = REPORTS / "coinbase_range_breakout_sweep.json"
ROUTER_CONFLICT_PATH = REPORTS / "coinbase_spot_router_conflict_board.json"

MD_PATH = REPORTS / "coinbase_breakout_promotion_queue.md"
JSON_PATH = REPORTS / "coinbase_breakout_promotion_queue.json"


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


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
    sweep = load_json(SWEEP_PATH)
    conflicts = load_json(ROUTER_CONFLICT_PATH)
    conflict_map = {str(row.get("coin") or ""): row for row in conflicts.get("rows") or []}

    queue: list[dict[str, Any]] = []
    blocked_or_deferred: list[dict[str, Any]] = []

    for row in sweep.get("coin_rows") or []:
        coin = str(row.get("coin") or "")
        best_net = round(to_float(row.get("best_net_pnl")), 4)
        profitable_rate = round(to_float(row.get("profitable_rate")), 1)
        uplift_vs_momentum = round(to_float(row.get("uplift_vs_default_momentum")), 4)
        conflict_row = conflict_map.get(coin) or {}

        base = {
            "coin": coin,
            "strategy": "range_breakout_shadow",
            "best_range_lookback": to_int(row.get("best_range_lookback")),
            "best_tp_pct": round(to_float(row.get("best_tp_pct")), 4),
            "best_sl_pct": round(to_float(row.get("best_sl_pct")), 4),
            "best_max_hold": to_int(row.get("best_max_hold")),
            "reconciliation_30d_net_usd": best_net,
            "reconciliation_30d_closes": to_int(row.get("best_trades")),
            "reconciliation_30d_win_rate": round(to_float(row.get("best_win_rate")), 1),
            "reconciliation_30d_max_dd": round(to_float(row.get("best_max_drawdown")), 1),
            "profitable_rate": profitable_rate,
            "uplift_vs_default_momentum": uplift_vs_momentum,
            "score": round(best_net + uplift_vs_momentum * 0.1 + profitable_rate * 0.05, 4),
        }

        if coin in conflict_map:
            blocked_or_deferred.append(
                {
                    **base,
                    "action": "resolve_router_conflict",
                    "router_conflict_action": str(conflict_row.get("conflict_action") or ""),
                    "note": str(conflict_row.get("rationale") or "existing product router must be arbitrated first"),
                }
            )
            continue

        if best_net >= 40.0 and profitable_rate >= 75.0:
            queue.append(
                {
                    **base,
                    "action": "launch_shadow_after_top_batch",
                    "note": "optimized breakout lane is strong enough to enter the next promotion stack after the current momentum batch",
                }
            )
        else:
            blocked_or_deferred.append(
                {
                    **base,
                    "action": "watch_probe_only",
                    "router_conflict_action": "",
                    "note": "positive breakout exists, but the edge is not strong enough yet to outrank the main queue",
                }
            )

    queue.sort(key=lambda row: (-to_float(row["score"]), -to_float(row["reconciliation_30d_net_usd"]), row["coin"]))
    blocked_or_deferred.sort(
        key=lambda row: (
            0 if str(row.get("action") or "") == "resolve_router_conflict" else 1,
            -to_float(row["reconciliation_30d_net_usd"]),
            row["coin"],
        )
    )

    leadership_read = [
        "NOM and SUP are the serious breakout-shadow promotions because the optimized breakout lane materially outperforms both the default breakout spec and the shared momentum baseline.",
        "BAL is also positive enough to stay in the promotion stack, but it belongs behind NOM and SUP rather than ahead of them.",
        "PRL breakout is real, but it is still a router conflict because PRL already has an active positive RSI lane.",
        "The room should treat these as breakout-shadow lanes, not as permission to erase existing momentum or RSI evidence on the same products.",
    ]

    return {
        "generated_at": now.isoformat(),
        "leadership_read": leadership_read,
        "queue": queue,
        "blocked_or_deferred": blocked_or_deferred,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)

    lines = [
        "# Coinbase Breakout Promotion Queue",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Queue",
            "",
            "| Coin | Strategy | Action | Best 30d $ | Closes | WR | DD | Profitable Rate | Uplift vs Momentum | Best Params | Note |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for section in ("queue", "blocked_or_deferred"):
        for row in payload[section]:
            lines.append(
                "| {coin} | {strategy} | {action} | {reconciliation_30d_net_usd:.4f} | {reconciliation_30d_closes} | {reconciliation_30d_win_rate:.1f} | {reconciliation_30d_max_dd:.1f} | {profitable_rate:.1f}% | {uplift_vs_default_momentum:.4f} | lb={best_range_lookback},tp={best_tp_pct},sl={best_sl_pct},hold={best_max_hold} | {note} |".format(
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
