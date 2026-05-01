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
DEFAULT_RADAR_PATH = REPORTS / "kraken_spot_live_radar.json"
DEFAULT_JSON_PATH = REPORTS / "kraken_spot_money_velocity_board.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_spot_money_velocity_board.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_spot_money_velocity_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rank Kraken spot radar rows by after-fee money velocity.")
    parser.add_argument("--radar-path", default=str(DEFAULT_RADAR_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--starting-cash", type=float, default=100.0)
    parser.add_argument("--deploy-pct", type=float, default=0.8)
    parser.add_argument("--kraken-taker-round-trip-bps", type=float, default=80.0)
    parser.add_argument("--coinbase-taker-round-trip-bps", type=float, default=240.0)
    parser.add_argument("--profit-buffer-bps", type=float, default=50.0)
    parser.add_argument("--min-samples", type=int, default=2)
    return parser.parse_args()


def best_move(row: dict[str, Any]) -> tuple[str, float]:
    candidates = [
        ("last", to_float(row.get("move_last_bps"))),
        ("30s", to_float(row.get("ret_30s_bps"))),
        ("60s", to_float(row.get("ret_60s_bps"))),
        ("5m", to_float(row.get("ret_5m_bps"))),
        ("15m", to_float(row.get("ret_15m_bps"))),
    ]
    return max(candidates, key=lambda item: item[1])


def classify(edge_bps: float, coinbase_edge_bps: float, samples: int, can_trade: bool) -> str:
    if not can_trade:
        return "blocked_min_size"
    if samples < 2:
        return "warming_samples"
    if edge_bps >= 0 and coinbase_edge_bps < 0:
        return "kraken_fee_flip_candidate"
    if edge_bps >= 0 and coinbase_edge_bps >= 0:
        return "clears_both_fee_models"
    if edge_bps >= -50:
        return "near_kraken_hurdle"
    return "below_hurdle"


def build(args: argparse.Namespace) -> dict[str, Any]:
    radar = load_json(Path(str(args.radar_path)))
    rows: list[dict[str, Any]] = []
    deploy_usd = float(args.starting_cash) * float(args.deploy_pct)
    for row in radar.get("rows") or []:
        samples = int(to_float(row.get("samples")))
        can_trade = bool(row.get("can_trade_starting_cash"))
        move_window, move_bps = best_move(row)
        spread_bps = max(0.0, to_float(row.get("spread_bps")))
        kraken_hurdle_bps = float(args.kraken_taker_round_trip_bps) + spread_bps + float(args.profit_buffer_bps)
        coinbase_hurdle_bps = float(args.coinbase_taker_round_trip_bps) + spread_bps + float(args.profit_buffer_bps)
        kraken_edge_bps = move_bps - kraken_hurdle_bps
        coinbase_edge_bps = move_bps - coinbase_hurdle_bps
        kraken_net_usd = deploy_usd * kraken_edge_bps / 10000.0
        coinbase_net_usd = deploy_usd * coinbase_edge_bps / 10000.0
        rows.append(
            {
                "product_id": row.get("product_id"),
                "quote_currency": row.get("quote_currency"),
                "signal_state": row.get("signal_state"),
                "bid": row.get("bid"),
                "ask": row.get("ask"),
                "best_move_window": move_window,
                "best_move_bps": round(move_bps, 6),
                "spread_bps": round(spread_bps, 4),
                "kraken_hurdle_bps": round(kraken_hurdle_bps, 4),
                "coinbase_hurdle_bps": round(coinbase_hurdle_bps, 4),
                "kraken_edge_bps": round(kraken_edge_bps, 6),
                "coinbase_edge_bps": round(coinbase_edge_bps, 6),
                "kraken_net_usd_on_deploy": round(kraken_net_usd, 6),
                "coinbase_net_usd_on_deploy": round(coinbase_net_usd, 6),
                "fee_savings_usd_on_deploy": round(kraken_net_usd - coinbase_net_usd, 6),
                "deploy_usd": round(deploy_usd, 6),
                "min_notional_usd": row.get("min_notional_usd"),
                "can_trade_starting_cash": can_trade,
                "samples": samples,
                "source": row.get("source"),
                "verdict": classify(kraken_edge_bps, coinbase_edge_bps, samples, can_trade),
            }
        )
    rows.sort(
        key=lambda item: (
            item.get("verdict") == "kraken_fee_flip_candidate",
            item.get("verdict") == "clears_both_fee_models",
            to_float(item.get("kraken_edge_bps")),
        ),
        reverse=True,
    )
    payload = {
        "generated_at": utc_now_iso(),
        "radar_generated_at": radar.get("generated_at"),
        "mode": "kraken_spot_money_velocity_board",
        "shadow_only": True,
        "parameters": {
            "starting_cash": float(args.starting_cash),
            "deploy_pct": float(args.deploy_pct),
            "deploy_usd": deploy_usd,
            "kraken_taker_round_trip_bps": float(args.kraken_taker_round_trip_bps),
            "coinbase_taker_round_trip_bps": float(args.coinbase_taker_round_trip_bps),
            "profit_buffer_bps": float(args.profit_buffer_bps),
            "min_samples": int(args.min_samples),
        },
        "summary": {
            "rows": len(rows),
            "warming_samples": sum(1 for row in rows if row.get("verdict") == "warming_samples"),
            "kraken_fee_flip_candidates": sum(1 for row in rows if row.get("verdict") == "kraken_fee_flip_candidate"),
            "clears_both_fee_models": sum(1 for row in rows if row.get("verdict") == "clears_both_fee_models"),
            "near_kraken_hurdle": sum(1 for row in rows if row.get("verdict") == "near_kraken_hurdle"),
            "tradable_with_deploy_cash": sum(1 for row in rows if row.get("can_trade_starting_cash")),
        },
        "leadership_read": [
            "This is the after-fee money-velocity surface for Texas-available Kraken spot candidates.",
            "A kraken_fee_flip_candidate is a live move that clears Kraken starter taker fees plus spread/profit buffer but would not clear Coinbase's current taker drag.",
            "Rows are not live permission; they are the next shadow candidates once the radar has enough rolling samples.",
        ],
        "rows": rows,
    }
    write_reports(payload, Path(str(args.json_path)), Path(str(args.csv_path)), Path(str(args.md_path)))
    return payload


def write_reports(payload: dict[str, Any], json_path: Path, csv_path: Path, md_path: Path) -> None:
    write_json(json_path, payload)
    columns = [
        "product_id",
        "quote_currency",
        "verdict",
        "signal_state",
        "bid",
        "ask",
        "best_move_window",
        "best_move_bps",
        "spread_bps",
        "kraken_hurdle_bps",
        "coinbase_hurdle_bps",
        "kraken_edge_bps",
        "coinbase_edge_bps",
        "kraken_net_usd_on_deploy",
        "coinbase_net_usd_on_deploy",
        "fee_savings_usd_on_deploy",
        "min_notional_usd",
        "samples",
        "source",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload.get("rows") or []:
            writer.writerow({column: row.get(column, "") for column in columns})
    lines = [
        "# Kraken Spot Money Velocity Board",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Radar generated: `{payload.get('radar_generated_at')}`",
        f"- Shadow only: `{payload.get('shadow_only')}`",
        f"- Rows: `{payload.get('summary', {}).get('rows')}`",
        f"- Tradable with deploy cash: `{payload.get('summary', {}).get('tradable_with_deploy_cash')}`",
        f"- Kraken fee-flip candidates: `{payload.get('summary', {}).get('kraken_fee_flip_candidates')}`",
        f"- Clears both fee models: `{payload.get('summary', {}).get('clears_both_fee_models')}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in payload.get("leadership_read") or []])
    lines.extend(
        [
            "",
            "## Top After-Fee Rows",
            "",
            "| Rank | Product | Verdict | Window | Move bps | Spread | Kraken Edge bps | Kraken Net $ | Coinbase Net $ | Samples |",
            "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for idx, row in enumerate((payload.get("rows") or [])[:50], start=1):
        lines.append(
            "| {idx} | {product_id} | {verdict} | {best_move_window} | {best_move_bps:.4f} | {spread_bps:.2f} | {kraken_edge_bps:.4f} | {kraken_net_usd_on_deploy:.4f} | {coinbase_net_usd_on_deploy:.4f} | {samples} |".format(
                idx=idx,
                **row,
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    payload = build(args)
    print(json.dumps({"json_path": str(Path(args.json_path).resolve()), "md_path": str(Path(args.md_path).resolve()), "rows": len(payload.get("rows") or [])}, indent=2))


if __name__ == "__main__":
    main()
