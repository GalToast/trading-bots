#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_CACHE_PATH = REPORTS / "cache" / "coinbase_spot_pulse_candles.json"
DEFAULT_PULSE_PATH = REPORTS / "coinbase_spot_pulse_board.json"
DEFAULT_RADAR_PATH = REPORTS / "coinbase_spot_live_radar.json"
DEFAULT_JSON_PATH = REPORTS / "coinbase_spot_tick_jump_sibling_board.json"
DEFAULT_CSV_PATH = REPORTS / "coinbase_spot_tick_jump_sibling_board.csv"
DEFAULT_MD_PATH = REPORTS / "coinbase_spot_tick_jump_sibling_board.md"
STABLE_QUOTES = {"USD", "USDC", "USDT"}


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


def product_quote(product_id: str) -> str:
    text = str(product_id or "").upper()
    return text.rsplit("-", 1)[-1] if "-" in text else ""


def product_base(product_id: str) -> str:
    text = str(product_id or "").upper()
    return text.rsplit("-", 1)[0] if "-" in text else text


def as_decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if number <= 0:
        return None
    return number


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(math.ceil((pct / 100.0) * len(ordered))) - 1))
    return ordered[idx]


def observed_min_step_pct(candles: list[dict[str, Any]]) -> tuple[float, float, int]:
    prices: set[Decimal] = set()
    for candle in candles:
        for key in ("open", "high", "low", "close"):
            number = as_decimal(candle.get(key))
            if number is not None:
                prices.add(number)
    ordered = sorted(prices)
    if len(ordered) < 2:
        price = float(ordered[0]) if ordered else 0.0
        return 0.0, price, len(ordered)
    diffs = [ordered[i] - ordered[i - 1] for i in range(1, len(ordered)) if ordered[i] > ordered[i - 1]]
    min_diff = min(diffs) if diffs else Decimal("0")
    median_price = float(ordered[len(ordered) // 2])
    step_pct = float((min_diff / Decimal(str(median_price))) * Decimal("100")) if median_price > 0 else 0.0
    return step_pct, float(min_diff), len(ordered)


def movement_stats(candles: list[dict[str, Any]], hurdle_pct: float, lookahead_bars: int) -> dict[str, Any]:
    ranges: list[float] = []
    close_moves: list[float] = []
    high_hits = 0
    close_hits = 0
    best_forward_high = 0.0
    best_forward_close = 0.0
    for index, candle in enumerate(candles):
        close = to_float(candle.get("close"))
        high = to_float(candle.get("high"))
        low = to_float(candle.get("low"))
        if close > 0 and high > 0 and low > 0:
            ranges.append(((high - low) / close) * 100.0)
        if close <= 0 or index >= len(candles) - 1:
            continue
        future = candles[index + 1 : index + 1 + lookahead_bars]
        if not future:
            continue
        max_high = max(to_float(row.get("high")) for row in future)
        max_close = max(to_float(row.get("close")) for row in future)
        high_move = ((max_high - close) / close) * 100.0 if max_high > 0 else 0.0
        close_move = ((max_close - close) / close) * 100.0 if max_close > 0 else 0.0
        best_forward_high = max(best_forward_high, high_move)
        best_forward_close = max(best_forward_close, close_move)
        close_moves.append(close_move)
        if high_move >= hurdle_pct:
            high_hits += 1
        if close_move >= hurdle_pct:
            close_hits += 1
    opportunities = max(0, len(candles) - 1)
    return {
        "p50_range_pct": round(statistics.median(ranges), 4) if ranges else 0.0,
        "p90_range_pct": round(percentile(ranges, 90), 4),
        "best_forward_high_pct": round(best_forward_high, 4),
        "best_forward_close_pct": round(best_forward_close, 4),
        "p90_forward_close_pct": round(percentile(close_moves, 90), 4),
        "fee_clear_high_hits": high_hits,
        "fee_clear_close_hits": close_hits,
        "fee_clear_high_hit_rate_pct": round((high_hits / opportunities) * 100.0, 2) if opportunities else 0.0,
        "fee_clear_close_hit_rate_pct": round((close_hits / opportunities) * 100.0, 2) if opportunities else 0.0,
    }


def load_rows_by_product(path: Path) -> dict[str, dict[str, Any]]:
    payload = load_json(path)
    rows = payload.get("rows") or []
    return {str(row.get("product_id") or "").upper(): row for row in rows if isinstance(row, dict)}


def iter_cache_entries(cache_payload: dict[str, Any]) -> list[dict[str, Any]]:
    entries = cache_payload.get("entries")
    if not isinstance(entries, dict):
        return []
    results: list[dict[str, Any]] = []
    for value in entries.values():
        if isinstance(value, dict):
            results.append(value)
    return results


def classify_row(row: dict[str, Any], *, min_hit_rate_pct: float, max_spread_bps: float) -> str:
    if row["candles"] < 10:
        return "reject_insufficient_candles"
    if row["spread_bps"] > max_spread_bps:
        return "reject_wide_spread"
    if row["observed_step_pct"] <= 0:
        return "reject_no_observed_step"
    if row["net_one_step_after_hurdle_pct"] >= 0 and row["fee_clear_close_hit_rate_pct"] >= min_hit_rate_pct:
        return "mog_like_tick_jump_candidate"
    if row["fee_clear_close_hit_rate_pct"] >= min_hit_rate_pct:
        return "movement_candidate_needs_multi_tick"
    if row["fee_clear_high_hit_rate_pct"] >= min_hit_rate_pct:
        return "wick_candidate_needs_execution_proof"
    return "reject_fee_wall"


def build_rows(
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    pulse_path: Path = DEFAULT_PULSE_PATH,
    radar_path: Path = DEFAULT_RADAR_PATH,
    fee_bps_per_side: float = 120.0,
    target_net_pct: float = 0.5,
    lookahead_bars: int = 10,
    quotes: set[str] | None = None,
    min_hit_rate_pct: float = 1.0,
    max_spread_bps: float = 100.0,
) -> list[dict[str, Any]]:
    quote_filter = {quote.upper() for quote in (quotes or STABLE_QUOTES)}
    cache = load_json(cache_path)
    pulse_rows = load_rows_by_product(pulse_path)
    radar_rows = load_rows_by_product(radar_path)
    fee_roundtrip_pct = (float(fee_bps_per_side) * 2.0) / 100.0
    base_hurdle_pct = fee_roundtrip_pct + float(target_net_pct)
    rows_by_product: dict[str, dict[str, Any]] = {}
    for entry in iter_cache_entries(cache):
        product_id = str(entry.get("product_id") or "").upper()
        if not product_id or product_quote(product_id) not in quote_filter:
            continue
        candles = entry.get("candles") or []
        if not isinstance(candles, list) or not candles:
            continue
        pulse = pulse_rows.get(product_id, {})
        radar = radar_rows.get(product_id, {})
        spread_bps = to_float(radar.get("spread_bps")) or to_float(pulse.get("spread_bps"))
        spread_pct = spread_bps / 100.0
        hurdle_pct = base_hurdle_pct + spread_pct
        step_pct, min_step, unique_prices = observed_min_step_pct(candles)
        move = movement_stats(candles, hurdle_pct, lookahead_bars)
        ticks_to_clear = math.ceil(hurdle_pct / step_pct) if step_pct > 0 else 0
        row = {
            "product_id": product_id,
            "base_currency": str(pulse.get("base_currency") or product_base(product_id)),
            "quote_currency": product_quote(product_id),
            "candles": len(candles),
            "cache_fetched_at": str(entry.get("fetched_at") or ""),
            "price": round(to_float(pulse.get("price")) or to_float(candles[-1].get("close")), 12),
            "spread_bps": round(spread_bps, 4),
            "quote_volume_native": round(to_float(pulse.get("quote_volume_native") or radar.get("quote_volume_native")), 4),
            "pulse_state": str(pulse.get("pulse_state") or ""),
            "radar_signal_state": str(radar.get("signal_state") or ""),
            "ret_15m_pct": round(to_float(pulse.get("ret_15m_pct")) or to_float(radar.get("ret_15m_bps")) / 100.0, 4),
            "observed_min_price_step": round(min_step, 12),
            "observed_unique_prices": unique_prices,
            "observed_step_pct": round(step_pct, 4),
            "fee_roundtrip_pct": round(fee_roundtrip_pct, 4),
            "target_net_pct": round(target_net_pct, 4),
            "spread_pct": round(spread_pct, 4),
            "hurdle_pct": round(hurdle_pct, 4),
            "net_one_step_after_hurdle_pct": round(step_pct - hurdle_pct, 4),
            "ticks_to_clear_hurdle": ticks_to_clear,
            **move,
        }
        row["verdict"] = classify_row(row, min_hit_rate_pct=min_hit_rate_pct, max_spread_bps=max_spread_bps)
        row["score"] = round(
            max(0.0, row["net_one_step_after_hurdle_pct"]) * 4.0
            + row["fee_clear_close_hit_rate_pct"] * 2.0
            + max(0.0, row["p90_forward_close_pct"] - row["hurdle_pct"])
            - max(0.0, row["spread_bps"] - 25.0) / 25.0,
            4,
        )
        prior = rows_by_product.get(product_id)
        if prior is None or (row["score"], row["candles"]) > (prior["score"], prior["candles"]):
            rows_by_product[product_id] = row
    rows = list(rows_by_product.values())
    verdict_rank = {
        "mog_like_tick_jump_candidate": 0,
        "movement_candidate_needs_multi_tick": 1,
        "wick_candidate_needs_execution_proof": 2,
        "reject_fee_wall": 3,
        "reject_wide_spread": 4,
        "reject_no_observed_step": 5,
        "reject_insufficient_candles": 6,
    }
    rows.sort(key=lambda row: (verdict_rank.get(row["verdict"], 9), -row["score"], row["ticks_to_clear_hurdle"], -row["quote_volume_native"]))
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_md(path: Path, payload: dict[str, Any], *, limit: int = 30) -> None:
    rows = payload["rows"][:limit]
    lines = [
        "# Coinbase Spot Tick-Jump Sibling Board",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Fee hurdle: `{payload['parameters']['fee_bps_per_side']}` bps per side + `{payload['parameters']['target_net_pct']}`% target net + live/cached spread",
        f"- Lookahead: `{payload['parameters']['lookahead_bars']}` cached one-minute bars",
        "",
        "| Product | Verdict | Score | Step % | Hurdle % | Ticks | Close Hit % | High Hit % | Spread bps | Ret 15m % | Volume |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {product_id} | {verdict} | {score:.4f} | {observed_step_pct:.4f} | {hurdle_pct:.4f} | "
            "{ticks_to_clear_hurdle} | {fee_clear_close_hit_rate_pct:.2f} | {fee_clear_high_hit_rate_pct:.2f} | "
            "{spread_bps:.2f} | {ret_15m_pct:.4f} | {quote_volume_native:.2f} |".format(**row)
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `mog_like_tick_jump_candidate` means the observed minimum price step can clear the full fee/spread/profit hurdle by itself and recent cached closes have repeated fee-clearing follow-through.",
            "- `movement_candidate_needs_multi_tick` means the product can clear the hurdle in the lookahead window, but not from one observed step.",
            "- `wick_candidate_needs_execution_proof` means highs reached the hurdle, but close-to-close evidence did not; this needs bid/ask fill proof before it can guide allocation.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find Coinbase spot products with MOG-like fee-clearing tick geometry.")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--pulse-path", default=str(DEFAULT_PULSE_PATH))
    parser.add_argument("--radar-path", default=str(DEFAULT_RADAR_PATH))
    parser.add_argument("--fee-bps-per-side", type=float, default=120.0)
    parser.add_argument("--target-net-pct", type=float, default=0.5)
    parser.add_argument("--lookahead-bars", type=int, default=10)
    parser.add_argument("--quotes", default="USD,USDC,USDT")
    parser.add_argument("--min-hit-rate-pct", type=float, default=1.0)
    parser.add_argument("--max-spread-bps", type=float, default=100.0)
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    quotes = {part.strip().upper() for part in str(args.quotes).split(",") if part.strip()}
    rows = build_rows(
        cache_path=Path(args.cache_path),
        pulse_path=Path(args.pulse_path),
        radar_path=Path(args.radar_path),
        fee_bps_per_side=args.fee_bps_per_side,
        target_net_pct=args.target_net_pct,
        lookahead_bars=args.lookahead_bars,
        quotes=quotes,
        min_hit_rate_pct=args.min_hit_rate_pct,
        max_spread_bps=args.max_spread_bps,
    )
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_tick_jump_sibling_board",
        "parameters": {
            "cache_path": str(args.cache_path),
            "pulse_path": str(args.pulse_path),
            "radar_path": str(args.radar_path),
            "fee_bps_per_side": args.fee_bps_per_side,
            "target_net_pct": args.target_net_pct,
            "lookahead_bars": args.lookahead_bars,
            "quotes": sorted(quotes),
            "min_hit_rate_pct": args.min_hit_rate_pct,
            "max_spread_bps": args.max_spread_bps,
        },
        "summary": {
            "rows": len(rows),
            "mog_like_tick_jump_candidates": sum(1 for row in rows if row["verdict"] == "mog_like_tick_jump_candidate"),
            "movement_candidates_needing_multi_tick": sum(1 for row in rows if row["verdict"] == "movement_candidate_needs_multi_tick"),
            "wick_candidates_needing_execution_proof": sum(1 for row in rows if row["verdict"] == "wick_candidate_needs_execution_proof"),
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
