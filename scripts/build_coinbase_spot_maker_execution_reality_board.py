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
DEFAULT_PULSE_PATH = REPORTS / "coinbase_spot_pulse_board.json"
JSON_PATH = REPORTS / "coinbase_spot_maker_execution_reality_board.json"
CSV_PATH = REPORTS / "coinbase_spot_maker_execution_reality_board.csv"
MD_PATH = REPORTS / "coinbase_spot_maker_execution_reality_board.md"


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


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def maker_entry_fill_score(row: dict[str, Any]) -> float:
    """Heuristic only: post-only entry needs a retrace; hot runaway candles can miss."""
    spread_bps = to_float(row.get("spread_bps"))
    ret_15 = to_float(row.get("ret_15m_pct"))
    median_range = to_float(row.get("median_range_60m_pct"))
    p90_range = to_float(row.get("p90_range_60m_pct"))
    volume = to_float(row.get("quote_volume_native"))
    score = 55.0
    score += min(20.0, median_range * 10.0)
    score += min(10.0, p90_range * 2.0)
    if -1.5 <= ret_15 <= 0.25:
        score += 18.0
    elif ret_15 > 1.0:
        score -= min(35.0, ret_15 * 9.0)
    elif ret_15 < -3.0:
        score -= min(25.0, abs(ret_15) * 5.0)
    score -= max(0.0, spread_bps - 15.0) * 0.9
    if volume >= 1_000_000:
        score += 8.0
    elif volume < 50_000:
        score -= 20.0
    return round(clamp(score, 0.0, 100.0), 2)


def scenario_edge(
    row: dict[str, Any],
    *,
    maker_fee_bps: float,
    taker_fee_bps: float,
    profit_buffer_pct: float,
    adverse_spread_mult: float,
    noise_haircut_mult: float,
    missed_fill_haircut_pct: float,
    min_fill_score: float,
) -> dict[str, Any]:
    spread_bps = to_float(row.get("spread_bps"))
    spread_pct = spread_bps / 100.0
    ret_15 = to_float(row.get("ret_15m_pct"))
    ret_60 = to_float(row.get("ret_60m_pct"))
    ret_4h = to_float(row.get("ret_4h_pct"))
    best_move = max(ret_15, ret_60, ret_4h)
    median_range = to_float(row.get("median_range_60m_pct"))
    fill_score = maker_entry_fill_score(row)
    fill_risk_haircut = ((100.0 - fill_score) / 100.0) * missed_fill_haircut_pct
    adverse_haircut = spread_pct * adverse_spread_mult + max(0.0, median_range) * noise_haircut_mult

    taker_taker_hurdle = (2.0 * taker_fee_bps) / 100.0 + spread_pct + profit_buffer_pct
    maker_taker_hurdle = (maker_fee_bps + taker_fee_bps) / 100.0 + (spread_pct * 0.5) + profit_buffer_pct
    maker_maker_hurdle = (2.0 * maker_fee_bps) / 100.0 + profit_buffer_pct

    maker_taker_math_edge = best_move - maker_taker_hurdle
    maker_maker_math_edge = best_move - maker_maker_hurdle
    maker_taker_realistic_edge = maker_taker_math_edge - adverse_haircut - fill_risk_haircut
    maker_maker_realistic_edge = maker_maker_math_edge - adverse_haircut - (fill_risk_haircut * 1.5)

    if fill_score < min_fill_score:
        verdict = "reject_post_only_fill_risk"
    elif maker_taker_realistic_edge >= 0.0:
        verdict = "maker_taker_shadow_probe"
    elif maker_maker_realistic_edge >= 0.0:
        verdict = "maker_maker_only_needs_exit_fill_proof"
    elif maker_taker_math_edge >= 0.0 or maker_maker_math_edge >= 0.0:
        verdict = "fee_math_only_fill_haircut_reject"
    elif best_move - taker_taker_hurdle >= 0.0:
        verdict = "taker_already_clears"
    else:
        verdict = "fee_wall_blocked"

    return {
        "best_move_pct": round(best_move, 4),
        "taker_taker_hurdle_pct": round(taker_taker_hurdle, 4),
        "taker_taker_edge_pct": round(best_move - taker_taker_hurdle, 4),
        "maker_taker_hurdle_pct": round(maker_taker_hurdle, 4),
        "maker_taker_math_edge_pct": round(maker_taker_math_edge, 4),
        "maker_taker_realistic_edge_pct": round(maker_taker_realistic_edge, 4),
        "maker_maker_hurdle_pct": round(maker_maker_hurdle, 4),
        "maker_maker_math_edge_pct": round(maker_maker_math_edge, 4),
        "maker_maker_realistic_edge_pct": round(maker_maker_realistic_edge, 4),
        "maker_entry_fill_score": fill_score,
        "adverse_selection_haircut_pct": round(adverse_haircut, 4),
        "missed_fill_haircut_pct": round(fill_risk_haircut, 4),
        "verdict": verdict,
    }


def build_row(
    row: dict[str, Any],
    *,
    maker_fee_bps: float,
    taker_fee_bps: float,
    zero_maker_fee_bps: float,
    profit_buffer_pct: float,
    max_spread_bps: float,
    min_fill_score: float,
    adverse_spread_mult: float,
    noise_haircut_mult: float,
    missed_fill_haircut_pct: float,
) -> dict[str, Any] | None:
    if not row.get("live_tradable") or str(row.get("status") or "") != "ok":
        return None
    if str(row.get("live_route_state") or "") != "ready_direct_usd_or_stable":
        return None
    spread_bps = to_float(row.get("spread_bps"))
    current = scenario_edge(
        row,
        maker_fee_bps=maker_fee_bps,
        taker_fee_bps=taker_fee_bps,
        profit_buffer_pct=profit_buffer_pct,
        adverse_spread_mult=adverse_spread_mult,
        noise_haircut_mult=noise_haircut_mult,
        missed_fill_haircut_pct=missed_fill_haircut_pct,
        min_fill_score=min_fill_score,
    )
    zero = scenario_edge(
        row,
        maker_fee_bps=zero_maker_fee_bps,
        taker_fee_bps=taker_fee_bps,
        profit_buffer_pct=profit_buffer_pct,
        adverse_spread_mult=adverse_spread_mult,
        noise_haircut_mult=noise_haircut_mult,
        missed_fill_haircut_pct=missed_fill_haircut_pct,
        min_fill_score=min_fill_score,
    )
    if spread_bps > max_spread_bps:
        current["verdict"] = "reject_wide_spread"
        zero["verdict"] = "reject_wide_spread"

    result = {
        "product_id": str(row.get("product_id") or ""),
        "quote_currency": str(row.get("quote_currency") or ""),
        "pulse_state": str(row.get("pulse_state") or ""),
        "pulse_score": round(to_float(row.get("pulse_score")), 4),
        "price": round(to_float(row.get("price")), 12),
        "spread_bps": round(spread_bps, 4),
        "ret_15m_pct": round(to_float(row.get("ret_15m_pct")), 4),
        "ret_60m_pct": round(to_float(row.get("ret_60m_pct")), 4),
        "ret_4h_pct": round(to_float(row.get("ret_4h_pct")), 4),
        "median_range_60m_pct": round(to_float(row.get("median_range_60m_pct")), 4),
        "p90_range_60m_pct": round(to_float(row.get("p90_range_60m_pct")), 4),
        "quote_volume_native": round(to_float(row.get("quote_volume_native")), 4),
        "current_maker_fee_bps": round(maker_fee_bps, 4),
        "zero_maker_fee_bps": round(zero_maker_fee_bps, 4),
        "current_verdict": current["verdict"],
        "zero_maker_verdict": zero["verdict"],
    }
    for key, value in current.items():
        if key != "verdict":
            result[f"current_{key}"] = value
    for key, value in zero.items():
        if key != "verdict":
            result[f"zero_{key}"] = value
    result["score"] = round(
        max(result["current_maker_taker_realistic_edge_pct"], result["current_maker_maker_realistic_edge_pct"])
        + max(0.0, result["current_maker_entry_fill_score"] - min_fill_score) / 25.0
        - max(0.0, spread_bps - 25.0) / 50.0,
        4,
    )
    return result


def build_payload(
    *,
    pulse_path: Path,
    maker_fee_bps: float,
    taker_fee_bps: float,
    zero_maker_fee_bps: float,
    profit_buffer_pct: float,
    max_spread_bps: float,
    min_fill_score: float,
    adverse_spread_mult: float,
    noise_haircut_mult: float,
    missed_fill_haircut_pct: float,
    top: int,
) -> dict[str, Any]:
    pulse = load_json(pulse_path)
    rows: list[dict[str, Any]] = []
    for source_row in pulse.get("rows") or []:
        if not isinstance(source_row, dict):
            continue
        built = build_row(
            source_row,
            maker_fee_bps=maker_fee_bps,
            taker_fee_bps=taker_fee_bps,
            zero_maker_fee_bps=zero_maker_fee_bps,
            profit_buffer_pct=profit_buffer_pct,
            max_spread_bps=max_spread_bps,
            min_fill_score=min_fill_score,
            adverse_spread_mult=adverse_spread_mult,
            noise_haircut_mult=noise_haircut_mult,
            missed_fill_haircut_pct=missed_fill_haircut_pct,
        )
        if built is not None:
            rows.append(built)

    verdict_rank = {
        "maker_taker_shadow_probe": 0,
        "maker_maker_only_needs_exit_fill_proof": 1,
        "taker_already_clears": 2,
        "fee_math_only_fill_haircut_reject": 3,
        "reject_post_only_fill_risk": 4,
        "fee_wall_blocked": 5,
        "reject_wide_spread": 6,
    }
    rows.sort(
        key=lambda row: (
            verdict_rank.get(row["current_verdict"], 9),
            -row["score"],
            -row["current_maker_taker_realistic_edge_pct"],
            -row["zero_maker_taker_realistic_edge_pct"],
        )
    )
    current_counts: dict[str, int] = {}
    zero_counts: dict[str, int] = {}
    for row in rows:
        current_counts[row["current_verdict"]] = current_counts.get(row["current_verdict"], 0) + 1
        zero_counts[row["zero_maker_verdict"]] = zero_counts.get(row["zero_maker_verdict"], 0) + 1
    return {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_maker_execution_reality_board",
        "parameters": {
            "pulse_path": str(pulse_path),
            "taker_fee_bps": taker_fee_bps,
            "maker_fee_bps": maker_fee_bps,
            "zero_maker_fee_bps": zero_maker_fee_bps,
            "profit_buffer_pct": profit_buffer_pct,
            "max_spread_bps": max_spread_bps,
            "min_fill_score": min_fill_score,
            "adverse_spread_mult": adverse_spread_mult,
            "noise_haircut_mult": noise_haircut_mult,
            "missed_fill_haircut_pct": missed_fill_haircut_pct,
        },
        "summary": {
            "rows": len(rows),
            "current_verdict_counts": current_counts,
            "zero_maker_verdict_counts": zero_counts,
            "current_shadow_probe_rows": sum(1 for row in rows if row["current_verdict"] == "maker_taker_shadow_probe"),
            "zero_maker_shadow_probe_rows": sum(1 for row in rows if row["zero_maker_verdict"] == "maker_taker_shadow_probe"),
        },
        "leadership_read": [
            "This is a ceiling-test board, not a live order permission surface.",
            "Post-only maker math is separated from fill reality: every row pays adverse-selection and missed-fill haircuts.",
            "Rows that only clear the zero-maker scenario are broker/fee-tier research, not current Coinbase account opportunities.",
        ],
        "rows": rows[:top],
    }


def write_reports(payload: dict[str, Any]) -> None:
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    columns = [
        "product_id",
        "current_verdict",
        "zero_maker_verdict",
        "pulse_state",
        "pulse_score",
        "spread_bps",
        "ret_15m_pct",
        "ret_60m_pct",
        "ret_4h_pct",
        "current_best_move_pct",
        "current_maker_entry_fill_score",
        "current_maker_taker_realistic_edge_pct",
        "current_maker_maker_realistic_edge_pct",
        "zero_maker_taker_realistic_edge_pct",
        "current_adverse_selection_haircut_pct",
        "current_missed_fill_haircut_pct",
        "quote_volume_native",
    ]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload["rows"]:
            writer.writerow({column: row.get(column, "") for column in columns})

    params = payload["parameters"]
    summary = payload["summary"]
    lines = [
        "# Coinbase Spot Maker Execution Reality Board",
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
            f"- Current taker fee: `{params['taker_fee_bps']}` bps per side",
            f"- Current maker fee: `{params['maker_fee_bps']}` bps per side",
            f"- Hypothetical maker fee: `{params['zero_maker_fee_bps']}` bps per side",
            f"- Profit buffer: `{params['profit_buffer_pct']}`%",
            f"- Min post-only fill score: `{params['min_fill_score']}`",
            f"- Current verdict counts: `{summary['current_verdict_counts']}`",
            f"- Hypothetical maker verdict counts: `{summary['zero_maker_verdict_counts']}`",
            "",
            "## Top Rows",
            "",
            "| Product | Current Verdict | Zero-Maker Verdict | Pulse | Spread bps | Best Move % | Fill Score | Current Maker/Taker Edge % | Current Maker/Maker Edge % | Zero Maker/Taker Edge % | Haircuts % |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload["rows"]:
        haircuts = row["current_adverse_selection_haircut_pct"] + row["current_missed_fill_haircut_pct"]
        lines.append(
            "| {product_id} | {current_verdict} | {zero_maker_verdict} | {pulse_score:.4f} | {spread_bps:.2f} | {current_best_move_pct:.4f} | {current_maker_entry_fill_score:.2f} | {current_maker_taker_realistic_edge_pct:.4f} | {current_maker_maker_realistic_edge_pct:.4f} | {zero_maker_taker_realistic_edge_pct:.4f} | {haircuts:.4f} |".format(
                haircuts=haircuts,
                **row,
            )
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Separate Coinbase post-only maker fee math from fill-risk reality.")
    parser.add_argument("--pulse-path", default=str(DEFAULT_PULSE_PATH))
    parser.add_argument("--taker-fee-bps", type=float, default=120.0)
    parser.add_argument("--maker-fee-bps", type=float, default=60.0)
    parser.add_argument("--zero-maker-fee-bps", type=float, default=0.0)
    parser.add_argument("--profit-buffer-pct", type=float, default=0.75)
    parser.add_argument("--max-spread-bps", type=float, default=75.0)
    parser.add_argument("--min-fill-score", type=float, default=55.0)
    parser.add_argument("--adverse-spread-mult", type=float, default=1.0)
    parser.add_argument("--noise-haircut-mult", type=float, default=0.25)
    parser.add_argument("--missed-fill-haircut-pct", type=float, default=0.5)
    parser.add_argument("--top", type=int, default=80)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_payload(
        pulse_path=Path(args.pulse_path),
        taker_fee_bps=float(args.taker_fee_bps),
        maker_fee_bps=float(args.maker_fee_bps),
        zero_maker_fee_bps=float(args.zero_maker_fee_bps),
        profit_buffer_pct=float(args.profit_buffer_pct),
        max_spread_bps=float(args.max_spread_bps),
        min_fill_score=float(args.min_fill_score),
        adverse_spread_mult=float(args.adverse_spread_mult),
        noise_haircut_mult=float(args.noise_haircut_mult),
        missed_fill_haircut_pct=float(args.missed_fill_haircut_pct),
        top=max(1, int(args.top)),
    )
    write_reports(payload)
    print(json.dumps({"json_path": str(JSON_PATH), "csv_path": str(CSV_PATH), "md_path": str(MD_PATH), "summary": payload["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
