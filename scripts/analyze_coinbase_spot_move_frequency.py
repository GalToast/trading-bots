#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_CACHE_PATH = REPORTS / "cache" / "coinbase_spot_pulse_candles.json"
DEFAULT_PULSE_PATH = REPORTS / "coinbase_spot_pulse_board.json"
DEFAULT_JSON_PATH = REPORTS / "coinbase_spot_move_frequency.json"
DEFAULT_CSV_PATH = REPORTS / "coinbase_spot_move_frequency.csv"
DEFAULT_MD_PATH = REPORTS / "coinbase_spot_move_frequency.md"


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
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_quotes(value: str) -> set[str]:
    return {part.strip().upper() for part in value.split(",") if part.strip()}


def product_meta(path: Path) -> dict[str, dict[str, Any]]:
    payload = load_json(path)
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    return {str(row.get("product_id") or "").upper(): row for row in rows if isinstance(row, dict)}


def candle_entries(cache_path: Path, *, hours: int, granularity: str) -> list[tuple[str, list[dict[str, Any]]]]:
    payload = load_json(cache_path)
    entries = payload.get("entries") if isinstance(payload.get("entries"), dict) else {}
    suffix = f"|{granularity.upper()}|{int(hours)}H"
    out: list[tuple[str, list[dict[str, Any]]]] = []
    for key, entry in entries.items():
        if not str(key).upper().endswith(suffix) or not isinstance(entry, dict):
            continue
        product_id = str(entry.get("product_id") or str(key).split("|", 1)[0]).upper()
        candles = entry.get("candles") if isinstance(entry.get("candles"), list) else []
        clean = [row for row in candles if isinstance(row, dict) and to_float(row.get("close")) > 0.0]
        clean.sort(key=lambda row: to_float(row.get("start")))
        if clean:
            out.append((product_id, clean))
    return out


def analyze_product(
    product_id: str,
    candles: list[dict[str, Any]],
    meta: dict[str, Any],
    *,
    forward_minutes: int,
    move_threshold_pct: float,
    net_profit_threshold_pct: float,
    fee_bps_per_side: float,
    max_spread_bps: float,
) -> dict[str, Any]:
    spread_bps = min(max(0.0, to_float(meta.get("spread_bps"))), max_spread_bps)
    cost_pct = ((fee_bps_per_side * 2.0) + spread_bps) / 100.0
    gross_hits = 0
    net_hits = 0
    fee_clear_hits = 0
    windows = 0
    best_forward_pct = 0.0
    sum_forward_pct = 0.0
    for index, candle in enumerate(candles[:-1]):
        close = to_float(candle.get("close"))
        if close <= 0.0:
            continue
        forward = candles[index + 1 : min(len(candles), index + 1 + forward_minutes)]
        if not forward:
            continue
        high = max(to_float(row.get("high")) for row in forward)
        gross_pct = ((high / close) - 1.0) * 100.0
        net_pct = gross_pct - cost_pct
        windows += 1
        sum_forward_pct += gross_pct
        best_forward_pct = max(best_forward_pct, gross_pct)
        if gross_pct >= move_threshold_pct:
            gross_hits += 1
        if net_pct >= net_profit_threshold_pct:
            net_hits += 1
        if net_pct > 0.0:
            fee_clear_hits += 1
    return {
        "product_id": product_id,
        "quote_currency": str(meta.get("quote_currency") or product_id.rsplit("-", 1)[-1]).upper(),
        "windows": windows,
        "gross_move_hits": gross_hits,
        "fee_clear_hits": fee_clear_hits,
        "net_profit_hits": net_hits,
        "gross_move_hit_rate_pct": round((gross_hits / windows) * 100.0, 6) if windows else 0.0,
        "fee_clear_hit_rate_pct": round((fee_clear_hits / windows) * 100.0, 6) if windows else 0.0,
        "net_profit_hit_rate_pct": round((net_hits / windows) * 100.0, 6) if windows else 0.0,
        "best_forward_pct": round(best_forward_pct, 6),
        "avg_forward_pct": round((sum_forward_pct / windows), 6) if windows else 0.0,
        "spread_bps": round(spread_bps, 4),
        "cost_pct": round(cost_pct, 6),
        "pulse_score": to_float(meta.get("pulse_score")),
        "ret_15m_pct": to_float(meta.get("ret_15m_pct")),
        "ret_60m_pct": to_float(meta.get("ret_60m_pct")),
        "quote_volume_native": to_float(meta.get("quote_volume_native")),
    }


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    quotes = parse_quotes(str(args.quote_currencies))
    meta_by_product = product_meta(Path(args.pulse_path))
    rows: list[dict[str, Any]] = []
    totals = {
        "windows": 0,
        "gross_move_hits": 0,
        "fee_clear_hits": 0,
        "net_profit_hits": 0,
    }
    for product_id, candles in candle_entries(Path(args.cache_path), hours=int(args.hours), granularity=str(args.granularity)):
        meta = meta_by_product.get(product_id, {})
        quote = str(meta.get("quote_currency") or product_id.rsplit("-", 1)[-1]).upper()
        if not bool(args.include_non_usd_quotes) and quote not in quotes:
            continue
        if meta and not bool(meta.get("live_tradable", False)):
            continue
        if len(candles) < int(args.min_candles):
            continue
        row = analyze_product(
            product_id,
            candles,
            meta,
            forward_minutes=int(args.forward_minutes),
            move_threshold_pct=float(args.move_threshold_pct),
            net_profit_threshold_pct=float(args.net_profit_threshold_pct),
            fee_bps_per_side=float(args.fee_bps_per_side),
            max_spread_bps=float(args.max_spread_bps),
        )
        rows.append(row)
        for key in totals:
            totals[key] += int(row[key])
    rows.sort(key=lambda row: (row["net_profit_hits"], row["fee_clear_hits"], row["best_forward_pct"]), reverse=True)
    windows = totals["windows"]
    summary = {
        **totals,
        "products": len(rows),
        "gross_move_hit_rate_pct": round((totals["gross_move_hits"] / windows) * 100.0, 6) if windows else 0.0,
        "fee_clear_hit_rate_pct": round((totals["fee_clear_hits"] / windows) * 100.0, 6) if windows else 0.0,
        "net_profit_hit_rate_pct": round((totals["net_profit_hits"] / windows) * 100.0, 6) if windows else 0.0,
    }
    return {
        "mode": "coinbase_spot_move_frequency",
        "parameters": {
            "hours": int(args.hours),
            "granularity": str(args.granularity),
            "forward_minutes": int(args.forward_minutes),
            "move_threshold_pct": float(args.move_threshold_pct),
            "net_profit_threshold_pct": float(args.net_profit_threshold_pct),
            "fee_bps_per_side": float(args.fee_bps_per_side),
            "quote_currencies": sorted(quotes),
            "include_non_usd_quotes": bool(args.include_non_usd_quotes),
        },
        "summary": summary,
        "rows": rows,
    }


def write_outputs(payload: dict[str, Any], *, json_path: Path, csv_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    columns = list(payload["rows"][0].keys()) if payload["rows"] else []
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        if columns:
            writer.writeheader()
            for row in payload["rows"]:
                writer.writerow(row)
    params = payload["parameters"]
    summary = payload["summary"]
    lines = [
        "# Coinbase Spot Move Frequency",
        "",
        f"- Products: `{summary['products']}`",
        f"- Window: `{params['hours']}h` `{params['granularity']}` candles",
        f"- Forward horizon: `{params['forward_minutes']}` minutes",
        f"- Gross move threshold: `{params['move_threshold_pct']}`%",
        f"- Net profit threshold after fees/spread: `{params['net_profit_threshold_pct']}`%",
        f"- Fee: `{params['fee_bps_per_side']}` bps per side",
        "",
        f"- Product-minute windows: `{summary['windows']}`",
        f"- Gross move hits: `{summary['gross_move_hits']}` (`{summary['gross_move_hit_rate_pct']}`%)",
        f"- Fee-clear hits: `{summary['fee_clear_hits']}` (`{summary['fee_clear_hit_rate_pct']}`%)",
        f"- Net-profit hits: `{summary['net_profit_hits']}` (`{summary['net_profit_hit_rate_pct']}`%)",
        "",
        "| Product | Windows | Gross Hits | Fee Clear | Net Hits | Best Forward % | Spread bps | Pulse | 15m % | 60m % |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["rows"][:30]:
        lines.append(
            "| {product_id} | {windows} | {gross_move_hits} | {fee_clear_hits} | {net_profit_hits} | {best_forward_pct:.3f} | {spread_bps:.2f} | {pulse_score:.2f} | {ret_15m_pct:.2f} | {ret_60m_pct:.2f} |".format(
                **row
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Count Coinbase spot forward move frequency from cached pulse candles.")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--pulse-path", default=str(DEFAULT_PULSE_PATH))
    parser.add_argument("--hours", type=int, default=3)
    parser.add_argument("--granularity", default="ONE_MINUTE")
    parser.add_argument("--forward-minutes", type=int, default=60)
    parser.add_argument("--move-threshold-pct", type=float, default=5.0)
    parser.add_argument("--net-profit-threshold-pct", type=float, default=1.0)
    parser.add_argument("--fee-bps-per-side", type=float, default=120.0)
    parser.add_argument("--max-spread-bps", type=float, default=100.0)
    parser.add_argument("--quote-currencies", default="USD,USDC")
    parser.add_argument("--include-non-usd-quotes", action="store_true")
    parser.add_argument("--min-candles", type=int, default=20)
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_payload(args)
    write_outputs(payload, json_path=Path(args.json_path), csv_path=Path(args.csv_path), md_path=Path(args.md_path))
    print(json.dumps({"json_path": args.json_path, "csv_path": args.csv_path, "md_path": args.md_path, "summary": payload["summary"], "top": payload["rows"][:10]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
