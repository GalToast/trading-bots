#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
LIVE_RADAR_PATH = REPORTS / "coinbase_spot_live_radar.json"
JSON_PATH = REPORTS / "coinbase_spot_dissonance_board.json"
CSV_PATH = REPORTS / "coinbase_spot_dissonance_board.csv"
MD_PATH = REPORTS / "coinbase_spot_dissonance_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return (float(numerator) / float(denominator)) * 100.0


def bps_values(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [to_float(row.get(key)) for row in rows]


def worst_short_bps(row: dict[str, Any]) -> float:
    return min(
        to_float(row.get("move_last_bps")),
        to_float(row.get("ret_30s_bps")),
        to_float(row.get("ret_60s_bps")),
        to_float(row.get("ret_5m_bps")),
    )


def sign_conflicts(row: dict[str, Any]) -> int:
    values = [
        to_float(row.get("ret_30s_bps")),
        to_float(row.get("ret_60s_bps")),
        to_float(row.get("ret_5m_bps")),
        to_float(row.get("ret_15m_bps")),
    ]
    positives = sum(1 for value in values if value > 0.0)
    negatives = sum(1 for value in values if value < 0.0)
    return min(positives, negatives)


def dissonance_score(row: dict[str, Any], broad_toxicity: float) -> float:
    worst = abs(min(worst_short_bps(row), 0.0))
    spread = min(to_float(row.get("spread_bps")), 300.0)
    conflict = float(sign_conflicts(row))
    negative_stack = sum(
        1
        for key in ("move_last_bps", "ret_30s_bps", "ret_60s_bps", "ret_5m_bps", "ret_15m_bps")
        if to_float(row.get(key)) < 0.0
    )
    return round(worst * 0.55 + spread * 0.25 + conflict * 12.0 + negative_stack * 8.0 + broad_toxicity * 0.35, 6)


def row_action(row: dict[str, Any], broad_toxic: bool) -> str:
    spread = to_float(row.get("spread_bps"))
    worst = worst_short_bps(row)
    if spread > 100.0:
        return "avoid_toxic_spread"
    if broad_toxic and worst < 0.0:
        return "avoid_broad_dump_wave"
    if worst <= -25.0 and to_float(row.get("ret_30s_bps")) > 0.0 and spread <= 100.0:
        return "rebound_watch_only"
    if sign_conflicts(row) >= 2:
        return "wait_for_alignment"
    return "not_dissonant"


def build_payload() -> dict[str, Any]:
    radar = load_json(LIVE_RADAR_PATH)
    rows = [row for row in radar.get("rows") or [] if str(row.get("live_route_state") or "") == "ready_direct_usd_or_stable"]
    total = len(rows)
    if total:
        median_60s = median(bps_values(rows, "ret_60s_bps"))
        median_5m = median(bps_values(rows, "ret_5m_bps"))
        median_15m = median(bps_values(rows, "ret_15m_bps"))
        median_spread = median(bps_values(rows, "spread_bps"))
    else:
        median_60s = median_5m = median_15m = median_spread = 0.0
    dumping = [row for row in rows if worst_short_bps(row) <= -25.0]
    wide = [row for row in rows if to_float(row.get("spread_bps")) > 100.0 or str(row.get("signal_state") or "") == "too_wide"]
    live_hot = [row for row in rows if str(row.get("signal_state") or "") == "live_hot"]
    building = [row for row in rows if str(row.get("signal_state") or "") == "building"]
    negative_5m = [row for row in rows if to_float(row.get("ret_5m_bps")) < 0.0]
    positive_5m = [row for row in rows if to_float(row.get("ret_5m_bps")) > 0.0]
    dump_share = pct(len(dumping), total)
    wide_share = pct(len(wide), total)
    negative_5m_share = pct(len(negative_5m), total)
    hot_share = pct(len(live_hot), total)
    building_share = pct(len(building), total)
    positive_5m_share = pct(len(positive_5m), total)
    broad_toxicity = max(0.0, -median_5m) + dump_share * 1.4 + wide_share * 0.8 + max(0.0, negative_5m_share - positive_5m_share) * 0.5
    broad_toxic = broad_toxicity >= 75.0 or dump_share >= 20.0 or (median_5m <= -25.0 and negative_5m_share > positive_5m_share)
    product_rows = []
    for row in rows:
        action = row_action(row, broad_toxic)
        if action == "not_dissonant":
            continue
        product_rows.append(
            {
                "product_id": str(row.get("product_id") or ""),
                "signal_state": str(row.get("signal_state") or ""),
                "action": action,
                "dissonance_score": dissonance_score(row, broad_toxicity),
                "worst_short_bps": round(worst_short_bps(row), 6),
                "ret_30s_bps": round(to_float(row.get("ret_30s_bps")), 6),
                "ret_60s_bps": round(to_float(row.get("ret_60s_bps")), 6),
                "ret_5m_bps": round(to_float(row.get("ret_5m_bps")), 6),
                "ret_15m_bps": round(to_float(row.get("ret_15m_bps")), 6),
                "spread_bps": round(to_float(row.get("spread_bps")), 4),
                "samples": int(to_float(row.get("samples"))),
            }
        )
    product_rows.sort(key=lambda row: to_float(row.get("dissonance_score")), reverse=True)
    return {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_dissonance_board",
        "source": {
            "live_radar_path": str(LIVE_RADAR_PATH),
            "live_radar_generated_at": radar.get("generated_at"),
        },
        "summary": {
            "direct_ready_rows": total,
            "broad_toxic": broad_toxic,
            "broad_toxicity_score": round(broad_toxicity, 6),
            "dump_share_pct": round(dump_share, 4),
            "wide_share_pct": round(wide_share, 4),
            "negative_5m_share_pct": round(negative_5m_share, 4),
            "positive_5m_share_pct": round(positive_5m_share, 4),
            "hot_share_pct": round(hot_share, 4),
            "building_share_pct": round(building_share, 4),
            "median_60s_bps": round(median_60s, 6),
            "median_5m_bps": round(median_5m, 6),
            "median_15m_bps": round(median_15m, 6),
            "median_spread_bps": round(median_spread, 4),
            "dissonant_product_rows": len(product_rows),
        },
        "leadership_read": [
            "Dissonance means long-only spot momentum is fighting a broad correlated loss wave, toxic spreads, or mixed timeframes.",
            "A bearish wave is not directly shortable on Coinbase spot, but it can become a hard no-entry state or a rebound-watch queue.",
            "This board is a blocker and classifier, not a profit claim; strategy rows should only fire when dissonance clears or a rebound rule proves itself.",
        ],
        "rows": product_rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    columns = [
        "product_id",
        "signal_state",
        "action",
        "dissonance_score",
        "worst_short_bps",
        "ret_30s_bps",
        "ret_60s_bps",
        "ret_5m_bps",
        "ret_15m_bps",
        "spread_bps",
        "samples",
    ]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload["rows"]:
            writer.writerow({column: row.get(column, "") for column in columns})
    summary = payload["summary"]
    lines = [
        "# Coinbase Spot Dissonance Board",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Live radar generated: `{payload['source']['live_radar_generated_at']}`",
        f"- Direct-ready rows: `{summary['direct_ready_rows']}`",
        f"- Broad toxic: `{summary['broad_toxic']}`",
        f"- Broad toxicity score: `{summary['broad_toxicity_score']}`",
        f"- Dissonant product rows: `{summary['dissonant_product_rows']}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in payload["leadership_read"]])
    lines.extend(
        [
            "",
            "## Regime Metrics",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Dump share pct | {summary['dump_share_pct']:.4f} |",
            f"| Wide share pct | {summary['wide_share_pct']:.4f} |",
            f"| Negative 5m share pct | {summary['negative_5m_share_pct']:.4f} |",
            f"| Positive 5m share pct | {summary['positive_5m_share_pct']:.4f} |",
            f"| Hot share pct | {summary['hot_share_pct']:.4f} |",
            f"| Building share pct | {summary['building_share_pct']:.4f} |",
            f"| Median 60s bps | {summary['median_60s_bps']:.6f} |",
            f"| Median 5m bps | {summary['median_5m_bps']:.6f} |",
            f"| Median 15m bps | {summary['median_15m_bps']:.6f} |",
            f"| Median spread bps | {summary['median_spread_bps']:.4f} |",
            "",
            "## Product Blocks",
            "",
            "| Rank | Product | Action | Score | Worst bps | 30s bps | 5m bps | Spread bps |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for idx, row in enumerate(payload["rows"][:50], start=1):
        lines.append(
            "| {idx} | {product_id} | {action} | {dissonance_score:.4f} | {worst_short_bps:.4f} | {ret_30s_bps:.4f} | {ret_5m_bps:.4f} | {spread_bps:.2f} |".format(
                idx=idx,
                **row,
            )
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_reports(payload)
    print(json.dumps({"json_path": str(JSON_PATH), "md_path": str(MD_PATH), "summary": payload["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
