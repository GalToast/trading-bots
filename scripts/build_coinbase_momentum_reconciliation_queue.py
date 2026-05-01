#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

MD_PATH = REPORTS / "coinbase_momentum_reconciliation_queue.md"
JSON_PATH = REPORTS / "coinbase_momentum_reconciliation_queue.json"

SWEEP_PARTIAL_PATH = REPORTS / "coinbase_opportunity_sweep_partial.json"
EVIDENCE_MATRIX_PATH = REPORTS / "coinbase_spot_evidence_matrix.json"

ALREADY_RECONCILED_MOMENTUM = {
    ("RAVE-USD", "mom_10"),
    ("BAL-USD", "mom_50"),
    ("BLUR-USD", "mom_25"),
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


def evidence_map() -> dict[tuple[str, str], dict[str, Any]]:
    payload = load_json(EVIDENCE_MATRIX_PATH)
    return {
        (str(row.get("coin") or ""), str(row.get("strategy") or "")): row
        for row in payload.get("rows") or []
    }


def priority_from_metrics(net_pnl: float, closes: int, max_dd: float) -> str:
    if net_pnl >= 5.0 and closes >= 15 and max_dd <= 20.0:
        return "reconcile_next"
    if net_pnl >= 2.0 and closes >= 10 and max_dd <= 25.0:
        return "reconcile_later"
    return "watch_only"


def build_payload() -> dict[str, Any]:
    sweep = load_json(SWEEP_PARTIAL_PATH)
    evidence = evidence_map()
    queue: list[dict[str, Any]] = []
    already_covered: list[dict[str, Any]] = []

    for row in sweep.get("profitable_combos") or []:
        coin = str(row.get("coin") or "")
        strategy = str(row.get("strategy") or "")
        if not strategy.startswith("mom_"):
            continue

        key = (coin, strategy)
        net_pnl = round(to_float(row.get("net_pnl")), 4)
        closes = to_int(row.get("closes"))
        max_dd = round(to_float(row.get("max_dd")), 4)
        win_rate = round(to_float(row.get("win_rate")), 1)

        if key in ALREADY_RECONCILED_MOMENTUM:
            already_covered.append(
                {
                    "coin": coin,
                    "strategy": strategy,
                    "library_sweep_partial_14d_net_usd": net_pnl,
                    "closes": closes,
                    "max_dd": max_dd,
                    "note": "already represented in evidence matrix / reconciliation set",
                }
            )
            continue

        evidence_row = evidence.get(key) or {}
        verdict = str(evidence_row.get("verdict") or "")
        if verdict in {"deployable_priority", "bench_positive_wait_runtime"}:
            already_covered.append(
                {
                    "coin": coin,
                    "strategy": strategy,
                    "library_sweep_partial_14d_net_usd": net_pnl,
                    "closes": closes,
                    "max_dd": max_dd,
                    "note": f"already covered via verdict={verdict}",
                }
            )
            continue

        priority = priority_from_metrics(net_pnl, closes, max_dd)
        score = round(net_pnl + min(closes, 40) * 0.15 - max_dd * 0.25, 4)
        queue.append(
            {
                "coin": coin,
                "strategy": strategy,
                "priority": priority,
                "score": score,
                "library_sweep_partial_14d_net_usd": net_pnl,
                "closes": closes,
                "win_rate": win_rate,
                "max_dd": max_dd,
                "reason": (
                    "positive library-backed momentum result without 30d reconciliation yet"
                ),
            }
        )

    priority_order = {"reconcile_next": 0, "reconcile_later": 1, "watch_only": 2}
    queue.sort(key=lambda row: (priority_order[row["priority"]], -row["score"], -row["library_sweep_partial_14d_net_usd"], row["coin"], row["strategy"]))
    already_covered.sort(key=lambda row: (-row["library_sweep_partial_14d_net_usd"], row["coin"], row["strategy"]))

    leadership_read = [
        "Momentum is now the main expansion lane, so the next bottleneck is 30-day snapshot reconciliation on the best unreconciled momentum combos.",
        "IOTX momentum is the highest-signal fresh reconciliation candidate from the current library-backed sweep partial.",
        "CFG, PRL, A8, DASH, and ALEPH have enough partial evidence to deserve queued reconciliation before weaker one-off names.",
        "Tiny-sample names like MOG and razor-thin names like COMP should stay watch-only until more sweep depth exists.",
    ]

    return {
        "generated_at": str(sweep.get("run_at") or ""),
        "leadership_read": leadership_read,
        "queue": queue,
        "already_covered": already_covered,
    }


def write_reports(payload: dict[str, Any], *, md_path: Path = MD_PATH, json_path: Path = JSON_PATH) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Coinbase Momentum Reconciliation Queue",
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
            "| Coin | Strategy | Priority | Score | Sweep Partial 14d $ | Closes | WR | Max DD | Reason |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in payload["queue"]:
        lines.append(
            "| {coin} | {strategy} | {priority} | {score:.4f} | {library_sweep_partial_14d_net_usd:.4f} | {closes} | {win_rate:.1f} | {max_dd:.1f} | {reason} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Already Covered",
            "",
            "| Coin | Strategy | Sweep Partial 14d $ | Closes | Max DD | Note |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in payload["already_covered"]:
        lines.append(
            "| {coin} | {strategy} | {library_sweep_partial_14d_net_usd:.4f} | {closes} | {max_dd:.1f} | {note} |".format(
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
