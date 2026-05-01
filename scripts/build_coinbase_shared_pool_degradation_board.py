#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

ALLOCATOR_PATH = REPORTS / "coinbase_isolated_sleeve_allocator.json"
BACKFILL_PATH = REPORTS / "multi_coin_runner_backfill.json"

JSON_PATH = REPORTS / "coinbase_shared_pool_degradation_board.json"
MD_PATH = REPORTS / "coinbase_shared_pool_degradation_board.md"

STATUS_RANK = {
    "shared_survivor": 0,
    "shared_thin_positive": 1,
    "shared_pool_flattened": 2,
    "shared_pool_negative": 3,
}


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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_row(isolated_net: float, shared_net: float) -> tuple[str, str]:
    retention_pct = (shared_net / isolated_net * 100.0) if isolated_net > 0 else 0.0
    flat_threshold = max(1.0, isolated_net * 0.01)

    if abs(shared_net) <= flat_threshold:
        return (
            "shared_pool_flattened",
            "pooled capital nearly flattens the lane, so a weak shared result should not be used to demote the isolated sleeve thesis",
        )
    if shared_net > 0 and retention_pct >= 1.0:
        return (
            "shared_survivor",
            "lane survives pooled contention, but isolated sleeves still capture far more of the edge",
        )
    if shared_net > 0:
        return (
            "shared_thin_positive",
            "lane stays barely positive under the shared pool, which is not strong enough to treat pooled performance as the deployment target",
        )
    return (
        "shared_pool_negative",
        "pooled contention pushes the lane negative, so this runner is the wrong validation environment for the isolated sleeve thesis",
    )


def build_rows() -> list[dict[str, Any]]:
    allocator = load_json(ALLOCATOR_PATH)
    backfill = load_json(BACKFILL_PATH)

    shared_rows = {
        str(coin): row
        for coin, row in dict((backfill.get("runner_result") or {}).get("coins") or {}).items()
    }

    rows: list[dict[str, Any]] = []
    for sleeve in list(allocator.get("primary_sleeves") or []):
        coin = str(sleeve.get("coin") or "")
        shared = dict(shared_rows.get(coin) or {})
        isolated_net = round(to_float(sleeve.get("reconciliation_30d_net_usd")), 2)
        shared_net = round(to_float(shared.get("net_pnl")), 2)
        status, deployment_read = classify_row(isolated_net, shared_net)
        retention_pct = round((shared_net / isolated_net * 100.0), 2) if isolated_net > 0 else 0.0
        gap_usd = round(isolated_net - shared_net, 2)

        if status == "shared_survivor":
            action = "may stay visible in pooled monitoring, but keep isolated sleeves as the real deployment model"
        elif status == "shared_thin_positive":
            action = "do not use the pooled result as proof of robustness; prioritize isolated runtime proof next"
        elif status == "shared_pool_flattened":
            action = "treat the shared runner as capital-architecture drag, not as a reason to demote the lane"
        else:
            action = "do not judge this lane inside the shared pool; validate and run it as an isolated sleeve instead"

        rows.append(
            {
                "coin": coin,
                "strategy": str(sleeve.get("strategy") or ""),
                "family": str(sleeve.get("family") or ""),
                "launch_wave": str(sleeve.get("launch_wave") or ""),
                "sleeve_rank": int(sleeve.get("sleeve_rank") or 0),
                "isolated_30d_net_usd": isolated_net,
                "isolated_30d_closes": int(sleeve.get("reconciliation_30d_closes") or 0),
                "shared_runner_30d_net_usd": shared_net,
                "shared_runner_closes": int(shared.get("closes") or 0),
                "shared_runner_signals": int(shared.get("signals") or 0),
                "shared_retention_pct": retention_pct,
                "net_gap_usd": gap_usd,
                "degradation_status": status,
                "deployment_read": deployment_read,
                "recommended_action": action,
            }
        )

    rows.sort(
        key=lambda row: (
            STATUS_RANK.get(str(row.get("degradation_status") or ""), 99),
            int(row.get("sleeve_rank") or 99),
            str(row.get("coin") or ""),
        )
    )
    return rows


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    isolated_total = round(sum(to_float(row.get("isolated_30d_net_usd")) for row in rows), 2)
    shared_total = round(sum(to_float(row.get("shared_runner_30d_net_usd")) for row in rows), 2)
    return {
        "primary_lane_count": len(rows),
        "isolated_total_30d_net_usd": isolated_total,
        "shared_total_30d_net_usd": shared_total,
        "shared_vs_isolated_retention_pct": round(shared_total / isolated_total * 100.0, 2) if isolated_total > 0 else 0.0,
        "shared_survivors": [row["coin"] for row in rows if row["degradation_status"] == "shared_survivor"],
    }


def build_leadership_read(rows: list[dict[str, Any]], summary: dict[str, Any]) -> list[str]:
    flattened = [row["coin"] for row in rows if row["degradation_status"] == "shared_pool_flattened"]
    negative = [row["coin"] for row in rows if row["degradation_status"] == "shared_pool_negative"]
    survivors = list(summary.get("shared_survivors") or [])
    return [
        "This board is a cross-architecture inference: it compares isolated sleeve bench strength to the same coin's result inside the fresh 15-coin shared runner, so it measures deployment fit under pooled capital rather than proving the isolated edge itself.",
        (
            f"Across the seven primary sleeves, isolated evidence totals {to_float(summary.get('isolated_total_30d_net_usd')):+.2f} "
            f"while the same lanes only contribute {to_float(summary.get('shared_total_30d_net_usd')):+.2f} inside the shared runner "
            f"({to_float(summary.get('shared_vs_isolated_retention_pct')):.2f}% retention)."
        ),
        f"{', '.join(survivors) if survivors else 'No primary lanes'} are the only clear pooled-capital survivors; they can stay visible in shared monitoring, but they still retain only a small fraction of their isolated edge.",
        f"{', '.join(flattened + negative) if (flattened or negative) else 'No lanes'} should not be demoted just because the shared runner compresses or reverses them; the pooled architecture is the likely failure mode there.",
    ]


def build_payload() -> dict[str, Any]:
    rows = build_rows()
    summary = build_summary(rows)
    return {
        "generated_at": utc_now_iso(),
        "leadership_read": build_leadership_read(rows, summary),
        "summary": summary,
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Shared Pool Degradation Board",
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
            f"- Primary lanes compared: `{payload['summary']['primary_lane_count']}`",
            f"- Isolated total 30d net: `${to_float(payload['summary']['isolated_total_30d_net_usd']):.2f}`",
            f"- Shared-runner total 30d net: `${to_float(payload['summary']['shared_total_30d_net_usd']):.2f}`",
            f"- Shared retention vs isolated: `{to_float(payload['summary']['shared_vs_isolated_retention_pct']):.2f}%`",
            "",
            "## Rows",
            "",
            "| Rank | Coin | Strategy | Wave | Isolated 30d Net $ | Shared 30d Net $ | Retention % | Gap $ | Status | Recommended Action |",
            "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {sleeve_rank} | {coin} | {strategy} | {launch_wave} | {isolated_30d_net_usd:.2f} | {shared_runner_30d_net_usd:.2f} | {shared_retention_pct:.2f} | {net_gap_usd:.2f} | {degradation_status} | {recommended_action} |".format(
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
