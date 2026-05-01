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
DEFAULT_COINBASE_SIBLING_PATH = REPORTS / "coinbase_spot_tick_jump_sibling_board.json"
DEFAULT_KRAKEN_RADAR_PATH = REPORTS / "kraken_spot_live_radar.json"
DEFAULT_JSON_PATH = REPORTS / "kraken_spot_tick_jump_route_board.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_spot_tick_jump_route_board.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_spot_tick_jump_route_board.md"

QUOTE_PRIORITY = {"USD": 0, "USDC": 1, "USDT": 2}
BASE_ALIASES = {
    "XBT": "BTC",
    "XXBT": "BTC",
    "XDG": "DOGE",
    "XXDG": "DOGE",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def split_product(product_id: Any) -> tuple[str, str]:
    text = str(product_id or "").strip().upper().replace("/", "-")
    if "-" not in text:
        return BASE_ALIASES.get(text, text), ""
    base, quote = text.rsplit("-", 1)
    return BASE_ALIASES.get(base, base), quote


def best_move(row: dict[str, Any]) -> tuple[str, float]:
    candidates = [
        ("last", to_float(row.get("move_last_bps"))),
        ("30s", to_float(row.get("ret_30s_bps"))),
        ("60s", to_float(row.get("ret_60s_bps"))),
        ("5m", to_float(row.get("ret_5m_bps"))),
        ("15m", to_float(row.get("ret_15m_bps"))),
        ("best_short", to_float(row.get("best_short_bps"))),
    ]
    return max(candidates, key=lambda item: item[1])


def rows_by_base(radar_payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in radar_payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        base, quote = split_product(row.get("product_id"))
        if not base:
            continue
        enriched = dict(row)
        enriched["_base"] = base
        enriched["_quote"] = quote
        out.setdefault(base, []).append(enriched)
    return out


def choose_kraken_row(rows: list[dict[str, Any]], *, kraken_fee_bps_per_side: float, target_net_bps: float) -> dict[str, Any] | None:
    if not rows:
        return None

    def sort_key(row: dict[str, Any]) -> tuple[float, float, float, float, str]:
        _, move_bps = best_move(row)
        spread = max(0.0, to_float(row.get("spread_bps")))
        edge = move_bps - (kraken_fee_bps_per_side * 2.0 + target_net_bps + spread)
        can_trade = 1.0 if bool(row.get("can_trade_starting_cash")) else 0.0
        samples = to_float(row.get("samples"))
        quote_rank = -float(QUOTE_PRIORITY.get(str(row.get("_quote") or ""), 99))
        return (can_trade, edge, samples, quote_rank, str(row.get("product_id") or ""))

    return max(rows, key=sort_key)


def classify(row: dict[str, Any], *, min_samples: int, max_spread_bps: float) -> str:
    if not row["kraken_product_id"]:
        return "missing_kraken_radar_route"
    if not row["can_trade_starting_cash"]:
        return "blocked_min_size"
    if row["kraken_spread_bps"] > max_spread_bps:
        return "reject_wide_spread"
    if row["kraken_samples"] < min_samples:
        return "warming_samples"
    if row["kraken_edge_bps"] >= 0 and row["coinbase_edge_bps"] < 0:
        return "kraken_fee_flip_candidate"
    if row["kraken_edge_bps"] >= 0:
        return "clears_kraken_hurdle"
    if row["kraken_edge_bps"] >= -50:
        return "near_kraken_hurdle"
    return "below_kraken_hurdle"


def build_rows(
    *,
    coinbase_sibling_path: Path = DEFAULT_COINBASE_SIBLING_PATH,
    kraken_radar_path: Path = DEFAULT_KRAKEN_RADAR_PATH,
    kraken_fee_bps_per_side: float = 40.0,
    coinbase_fee_bps_per_side: float = 120.0,
    target_net_pct: float = 0.5,
    min_samples: int = 2,
    max_spread_bps: float = 100.0,
    include_rejects: bool = False,
) -> list[dict[str, Any]]:
    sibling_payload = load_json(coinbase_sibling_path)
    radar_payload = load_json(kraken_radar_path)
    by_base = rows_by_base(radar_payload)
    target_net_bps = float(target_net_pct) * 100.0
    rows: list[dict[str, Any]] = []
    for cb in sibling_payload.get("rows") or []:
        if not isinstance(cb, dict):
            continue
        cb_verdict = str(cb.get("verdict") or "")
        if not include_rejects and cb_verdict.startswith("reject_"):
            continue
        cb_product = str(cb.get("product_id") or "").upper()
        base, cb_quote = split_product(cb_product)
        kraken = choose_kraken_row(by_base.get(base) or [], kraken_fee_bps_per_side=kraken_fee_bps_per_side, target_net_bps=target_net_bps)
        move_window = ""
        move_bps = 0.0
        spread_bps = 0.0
        kraken_product = ""
        quote = ""
        samples = 0
        can_trade = False
        min_notional = 0.0
        if kraken:
            move_window, move_bps = best_move(kraken)
            spread_bps = max(0.0, to_float(kraken.get("spread_bps")))
            kraken_product = str(kraken.get("product_id") or "")
            quote = str(kraken.get("_quote") or kraken.get("quote_currency") or "")
            samples = int(to_float(kraken.get("samples")))
            can_trade = bool(kraken.get("can_trade_starting_cash"))
            min_notional = to_float(kraken.get("min_notional_usd"))
        kraken_hurdle = kraken_fee_bps_per_side * 2.0 + target_net_bps + spread_bps
        coinbase_hurdle = coinbase_fee_bps_per_side * 2.0 + target_net_bps + to_float(cb.get("spread_bps"))
        route = {
            "coinbase_product_id": cb_product,
            "base_currency": base,
            "coinbase_quote": cb_quote,
            "coinbase_sibling_verdict": cb_verdict,
            "coinbase_score": round(to_float(cb.get("score")), 4),
            "coinbase_close_hit_rate_pct": round(to_float(cb.get("fee_clear_close_hit_rate_pct")), 4),
            "coinbase_high_hit_rate_pct": round(to_float(cb.get("fee_clear_high_hit_rate_pct")), 4),
            "coinbase_step_pct": round(to_float(cb.get("observed_step_pct")), 4),
            "coinbase_hurdle_bps": round(coinbase_hurdle, 4),
            "coinbase_edge_bps": round(to_float(cb.get("best_forward_close_pct")) * 100.0 - coinbase_hurdle, 4),
            "kraken_product_id": kraken_product,
            "kraken_quote": quote,
            "kraken_samples": samples,
            "kraken_signal_state": str((kraken or {}).get("signal_state") or ""),
            "kraken_best_move_window": move_window,
            "kraken_best_move_bps": round(move_bps, 4),
            "kraken_spread_bps": round(spread_bps, 4),
            "kraken_hurdle_bps": round(kraken_hurdle, 4),
            "kraken_edge_bps": round(move_bps - kraken_hurdle, 4),
            "fee_relief_bps": round((coinbase_fee_bps_per_side - kraken_fee_bps_per_side) * 2.0, 4),
            "can_trade_starting_cash": can_trade,
            "min_notional_usd": round(min_notional, 6),
        }
        route["route_verdict"] = classify(route, min_samples=min_samples, max_spread_bps=max_spread_bps)
        route["route_score"] = round(
            to_float(route["coinbase_score"])
            + max(0.0, to_float(route["kraken_edge_bps"])) * 0.25
            + to_float(route["coinbase_close_hit_rate_pct"])
            - max(0.0, to_float(route["kraken_spread_bps"]) - 25.0) / 25.0,
            4,
        )
        rows.append(route)
    verdict_rank = {
        "kraken_fee_flip_candidate": 0,
        "clears_kraken_hurdle": 1,
        "near_kraken_hurdle": 2,
        "warming_samples": 3,
        "below_kraken_hurdle": 4,
        "reject_wide_spread": 5,
        "blocked_min_size": 6,
        "missing_kraken_radar_route": 7,
    }
    rows.sort(key=lambda item: (verdict_rank.get(str(item.get("route_verdict")), 99), -to_float(item.get("route_score")), -to_float(item.get("coinbase_score"))))
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_md(path: Path, payload: dict[str, Any], *, limit: int = 40) -> None:
    lines = [
        "# Kraken Spot Tick-Jump Route Board",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Coinbase sibling source: `{payload['parameters']['coinbase_sibling_path']}`",
        f"- Kraken radar source: `{payload['parameters']['kraken_radar_path']}`",
        f"- Kraken fee model: `{payload['parameters']['kraken_fee_bps_per_side']}` bps per side + `{payload['parameters']['target_net_pct']}`% target net + spread",
        "",
        "| Product | Kraken Route | Verdict | Route Score | CB Verdict | CB Close Hit % | Kraken Move bps | Kraken Hurdle bps | Kraken Edge bps | Spread bps | Samples | Min Notional |",
        "| --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["rows"][:limit]:
        lines.append(
            "| {coinbase_product_id} | {kraken_product_id} | {route_verdict} | {route_score:.4f} | {coinbase_sibling_verdict} | "
            "{coinbase_close_hit_rate_pct:.2f} | {kraken_best_move_bps:.2f} | {kraken_hurdle_bps:.2f} | "
            "{kraken_edge_bps:.2f} | {kraken_spread_bps:.2f} | {kraken_samples} | {min_notional_usd:.4f} |".format(**row)
        )
    lines.extend(
        [
            "",
            "## Read",
            "",
            "- `kraken_fee_flip_candidate` means the current Kraken radar move clears the lower-fee Kraken hurdle while the Coinbase-style hurdle does not.",
            "- `warming_samples` means the asset is routeable and tradable, but the current Kraken radar has too few rolling samples for a decision.",
            "- This board does not place orders and does not promote a lane; it only narrows which lower-fee route deserves shadow proof.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Route Coinbase tick-jump sibling candidates through Kraken lower-fee radar reality.")
    parser.add_argument("--coinbase-sibling-path", default=str(DEFAULT_COINBASE_SIBLING_PATH))
    parser.add_argument("--kraken-radar-path", default=str(DEFAULT_KRAKEN_RADAR_PATH))
    parser.add_argument("--kraken-fee-bps-per-side", type=float, default=40.0)
    parser.add_argument("--coinbase-fee-bps-per-side", type=float, default=120.0)
    parser.add_argument("--target-net-pct", type=float, default=0.5)
    parser.add_argument("--min-samples", type=int, default=2)
    parser.add_argument("--max-spread-bps", type=float, default=100.0)
    parser.add_argument("--include-rejects", action="store_true")
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = build_rows(
        coinbase_sibling_path=Path(args.coinbase_sibling_path),
        kraken_radar_path=Path(args.kraken_radar_path),
        kraken_fee_bps_per_side=args.kraken_fee_bps_per_side,
        coinbase_fee_bps_per_side=args.coinbase_fee_bps_per_side,
        target_net_pct=args.target_net_pct,
        min_samples=args.min_samples,
        max_spread_bps=args.max_spread_bps,
        include_rejects=args.include_rejects,
    )
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "kraken_spot_tick_jump_route_board",
        "parameters": {
            "coinbase_sibling_path": str(args.coinbase_sibling_path),
            "kraken_radar_path": str(args.kraken_radar_path),
            "kraken_fee_bps_per_side": args.kraken_fee_bps_per_side,
            "coinbase_fee_bps_per_side": args.coinbase_fee_bps_per_side,
            "target_net_pct": args.target_net_pct,
            "min_samples": args.min_samples,
            "max_spread_bps": args.max_spread_bps,
            "include_rejects": bool(args.include_rejects),
        },
        "summary": {
            "rows": len(rows),
            "kraken_fee_flip_candidates": sum(1 for row in rows if row["route_verdict"] == "kraken_fee_flip_candidate"),
            "clears_kraken_hurdle": sum(1 for row in rows if row["route_verdict"] == "clears_kraken_hurdle"),
            "near_kraken_hurdle": sum(1 for row in rows if row["route_verdict"] == "near_kraken_hurdle"),
            "warming_samples": sum(1 for row in rows if row["route_verdict"] == "warming_samples"),
            "missing_kraken_radar_route": sum(1 for row in rows if row["route_verdict"] == "missing_kraken_radar_route"),
        },
        "rows": rows,
    }
    write_json(Path(args.json_path), payload)
    write_csv(Path(args.csv_path), rows)
    write_md(Path(args.md_path), payload)
    print(json.dumps({"json_path": args.json_path, "csv_path": args.csv_path, "md_path": args.md_path, "summary": payload["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
