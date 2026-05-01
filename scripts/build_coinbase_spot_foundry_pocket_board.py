#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
MATRIX_PATH = REPORTS / "coinbase_spot_gpu_foundry_product_matrix.csv"
JSON_PATH = REPORTS / "coinbase_spot_foundry_pocket_board.json"
CSV_PATH = REPORTS / "coinbase_spot_foundry_pocket_board.csv"
MD_PATH = REPORTS / "coinbase_spot_foundry_pocket_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def load_rows() -> list[dict[str, Any]]:
    if not MATRIX_PATH.exists():
        return []
    with MATRIX_PATH.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def build_payload() -> dict[str, Any]:
    raw_rows = load_rows()
    viable = []
    for row in raw_rows:
        signals = to_int(row.get("signals"))
        cumulative_net = to_float(row.get("cumulative_net_pct"))
        avg_net = to_float(row.get("avg_net_pct"))
        worst_net = to_float(row.get("worst_net_pct"))
        win_rate = to_float(row.get("win_rate_pct"))
        if signals < 2 or cumulative_net <= 0.0:
            continue
        fragility_penalty = 4.0 if signals < 3 else 1.5 if signals < 5 else 0.0
        downside_penalty = max(0.0, -worst_net) * 0.35
        score = cumulative_net + avg_net * 2.0 + win_rate * 0.03 - downside_penalty - fragility_penalty
        out = {
            "product_id": row.get("product_id"),
            "variant_id": to_int(row.get("variant_id")),
            "archetype": row.get("archetype"),
            "trigger": row.get("trigger"),
            "confirmation": row.get("confirmation"),
            "exit": row.get("exit"),
            "sizing": row.get("sizing"),
            "signals": signals,
            "wins": to_int(row.get("wins")),
            "win_rate_pct": round(win_rate, 4),
            "avg_net_pct": round(avg_net, 6),
            "cumulative_net_pct": round(cumulative_net, 6),
            "worst_net_pct": round(worst_net, 6),
            "spread_bps_proxy": round(to_float(row.get("spread_bps_proxy")), 4),
            "pocket_score": round(score, 6),
            "fragility": "tiny_sample" if signals < 5 else "small_sample" if signals < 10 else "larger_sample",
        }
        viable.append(out)
    viable.sort(key=lambda row: (to_float(row["pocket_score"]), to_float(row["cumulative_net_pct"]), to_float(row["signals"])), reverse=True)

    product_summary: dict[str, dict[str, Any]] = {}
    for row in raw_rows:
        product_id = str(row.get("product_id") or "")
        if not product_id:
            continue
        summary = product_summary.setdefault(
            product_id,
            {
                "product_id": product_id,
                "rows": 0,
                "survivors": 0,
                "best_cumulative_net_pct": -999999.0,
                "best_avg_net_pct": -999999.0,
                "total_signals": 0,
            },
        )
        summary["rows"] += 1
        summary["total_signals"] += to_int(row.get("signals"))
        if str(row.get("survived_fees") or "").lower() == "true":
            summary["survivors"] += 1
        summary["best_cumulative_net_pct"] = max(float(summary["best_cumulative_net_pct"]), to_float(row.get("cumulative_net_pct")))
        summary["best_avg_net_pct"] = max(float(summary["best_avg_net_pct"]), to_float(row.get("avg_net_pct")))
    products = list(product_summary.values())
    products.sort(key=lambda row: (to_float(row["best_cumulative_net_pct"]), to_float(row["survivors"])), reverse=True)
    return {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_foundry_pocket_board",
        "source": str(MATRIX_PATH),
        "summary": {
            "matrix_rows": len(raw_rows),
            "positive_pocket_rows": len(viable),
            "positive_products": sum(1 for row in products if to_int(row.get("survivors")) > 0),
        },
        "leadership_read": [
            "The full-universe foundry says broad geometry loses after fees, but sparse product/setup pockets exist.",
            "Most pockets are tiny samples and must be replayed with stricter candle-path and forward shadow proof before trust.",
            "Use this board to seed product-conditioned ML labels and targeted replay, not live orders.",
        ],
        "rows": viable,
        "product_summary": products,
    }


def write_outputs(payload: dict[str, Any]) -> None:
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    columns = [
        "product_id",
        "variant_id",
        "archetype",
        "trigger",
        "confirmation",
        "exit",
        "sizing",
        "signals",
        "wins",
        "win_rate_pct",
        "avg_net_pct",
        "cumulative_net_pct",
        "worst_net_pct",
        "spread_bps_proxy",
        "pocket_score",
        "fragility",
    ]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload["rows"]:
            writer.writerow({column: row.get(column, "") for column in columns})
    lines = [
        "# Coinbase Spot Foundry Pocket Board",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Matrix rows: `{payload['summary']['matrix_rows']}`",
        f"- Positive pocket rows: `{payload['summary']['positive_pocket_rows']}`",
        f"- Products with positive pockets: `{payload['summary']['positive_products']}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {line}" for line in payload["leadership_read"]])
    lines.extend(
        [
            "",
            "## Top Pockets",
            "",
            "| Rank | Product | Variant | Setup | Signals | Win % | Avg Net % | Cum Net % | Worst % | Score | Fragility |",
            "| ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for idx, row in enumerate(payload["rows"][:50], start=1):
        setup = f"{row['trigger']} / {row['confirmation']} / {row['exit']}"
        lines.append(
            "| {idx} | {product_id} | {variant_id} | {setup} | {signals} | {win_rate_pct:.2f} | {avg_net_pct:.4f} | {cumulative_net_pct:.4f} | {worst_net_pct:.4f} | {pocket_score:.4f} | {fragility} |".format(
                idx=idx,
                setup=setup,
                **row,
            )
        )
    lines.extend(
        [
            "",
            "## Product Summary",
            "",
            "| Rank | Product | Survivors | Best Cum Net % | Best Avg Net % | Total Signals |",
            "| ---: | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for idx, row in enumerate(payload["product_summary"][:40], start=1):
        lines.append(
            "| {idx} | {product_id} | {survivors} | {best_cumulative_net_pct:.4f} | {best_avg_net_pct:.4f} | {total_signals} |".format(
                idx=idx,
                **row,
            )
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_outputs(payload)
    print(json.dumps({"json_path": str(JSON_PATH), "md_path": str(MD_PATH), "summary": payload["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
