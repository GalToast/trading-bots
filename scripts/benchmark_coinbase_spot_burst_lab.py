#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from coinbase_advanced_client import CoinbaseAdvancedClient


DEFAULT_CSV_PATH = ROOT / "reports" / "coinbase_spot_burst_lab_24h.csv"
DEFAULT_MD_PATH = ROOT / "reports" / "coinbase_spot_burst_lab_24h.md"


@dataclass(frozen=True)
class Candle:
    start: int
    low: float
    high: float
    open: float
    close: float
    volume: float


@dataclass(frozen=True)
class EventResult:
    gross_return_pct: float
    net_return_pct: float
    bars_held: int
    outcome: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Coinbase spot burst lab across the USD spot universe.")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--granularity", default="FIVE_MINUTE")
    parser.add_argument("--min-volume-24h", type=float, default=250000.0)
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--fee-bps-per-side", type=float, default=40.0)
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def _to_candles(rows: list[dict[str, Any]]) -> list[Candle]:
    return [
        Candle(
            start=int(row["start"]),
            low=float(row["low"]),
            high=float(row["high"]),
            open=float(row["open"]),
            close=float(row["close"]),
            volume=float(row.get("volume", 0.0) or 0.0),
        )
        for row in rows
    ]


def _load_candles(
    client: CoinbaseAdvancedClient,
    product_id: str,
    *,
    start_ts: int,
    end_ts: int,
    granularity: str,
    chunk_seconds: int = 300 * 300,
) -> list[Candle]:
    rows: list[dict[str, Any]] = []
    cursor = start_ts
    while cursor < end_ts:
        chunk_end = min(end_ts, cursor + chunk_seconds)
        payload = client.market_candles(product_id, start=cursor, end=chunk_end, granularity=granularity, limit=350)
        rows.extend(payload.get("candles") or [])
        cursor = chunk_end
    dedup: dict[int, dict[str, Any]] = {}
    for row in rows:
        dedup[int(row["start"])] = row
    return _to_candles([dedup[key] for key in sorted(dedup.keys())])


def evaluate_forward_path(
    candles: list[Candle],
    entry_idx: int,
    *,
    target_pct: float,
    stop_pct: float,
    max_hold_bars: int,
    fee_bps_per_side: float,
) -> EventResult:
    entry = candles[entry_idx].close
    target_px = entry * (1.0 + target_pct)
    stop_px = entry * (1.0 - stop_pct)
    fee_pct = 2.0 * (fee_bps_per_side / 10000.0)
    last_idx = min(len(candles) - 1, entry_idx + max_hold_bars)

    for idx in range(entry_idx + 1, last_idx + 1):
        candle = candles[idx]
        stop_hit = candle.low <= stop_px
        target_hit = candle.high >= target_px
        if stop_hit and target_hit:
            gross = -stop_pct
            return EventResult(gross_return_pct=gross, net_return_pct=gross - fee_pct, bars_held=idx - entry_idx, outcome="stop_first_ambiguous")
        if stop_hit:
            gross = -stop_pct
            return EventResult(gross_return_pct=gross, net_return_pct=gross - fee_pct, bars_held=idx - entry_idx, outcome="stop")
        if target_hit:
            gross = target_pct
            return EventResult(gross_return_pct=gross, net_return_pct=gross - fee_pct, bars_held=idx - entry_idx, outcome="target")

    exit_close = candles[last_idx].close
    gross = (exit_close / entry) - 1.0
    return EventResult(gross_return_pct=gross, net_return_pct=gross - fee_pct, bars_held=last_idx - entry_idx, outcome="time_exit")


def is_burst_continuation(candles: list[Candle], idx: int) -> bool:
    if idx < 1:
        return False
    cur = candles[idx]
    prev = candles[idx - 1]
    if cur.open <= 0.0 or prev.close <= 0.0:
        return False
    close_jump = (cur.close / prev.close) - 1.0
    intrabar_range = (cur.high / cur.low) - 1.0 if cur.low > 0.0 else 0.0
    close_location = 0.0 if cur.high <= cur.low else (cur.close - cur.low) / (cur.high - cur.low)
    return close_jump >= 0.012 and intrabar_range >= 0.015 and close_location >= 0.7


def is_panic_reclaim(candles: list[Candle], idx: int) -> bool:
    if idx < 1:
        return False
    cur = candles[idx]
    prev = candles[idx - 1]
    if prev.close <= 0.0 or cur.low <= 0.0:
        return False
    flush = (cur.low / prev.close) - 1.0
    reclaim = (cur.close / cur.low) - 1.0
    close_location = 0.0 if cur.high <= cur.low else (cur.close - cur.low) / (cur.high - cur.low)
    return flush <= -0.018 and reclaim >= 0.012 and close_location >= 0.65


def is_compression_breakout(candles: list[Candle], idx: int) -> bool:
    if idx < 12:
        return False
    cur = candles[idx]
    recent = candles[idx - 12 : idx]
    ranges = [((bar.high / bar.low) - 1.0) for bar in recent if bar.low > 0.0]
    if not ranges:
        return False
    median_range = statistics.median(ranges)
    cur_range = (cur.high / cur.low) - 1.0 if cur.low > 0.0 else 0.0
    body = (cur.close / cur.open) - 1.0 if cur.open > 0.0 else 0.0
    close_location = 0.0 if cur.high <= cur.low else (cur.close - cur.low) / (cur.high - cur.low)
    return median_range <= 0.004 and cur_range >= 0.012 and body >= 0.008 and close_location >= 0.75


def summarize_events(events: list[EventResult]) -> dict[str, float]:
    if not events:
        return {
            "signals": 0,
            "win_rate_pct": 0.0,
            "avg_net_return_pct": 0.0,
            "median_hold_bars": 0.0,
            "target_hits": 0,
        }
    wins = [event for event in events if event.net_return_pct > 0.0]
    return {
        "signals": len(events),
        "win_rate_pct": (len(wins) / len(events)) * 100.0,
        "avg_net_return_pct": statistics.fmean(event.net_return_pct for event in events) * 100.0,
        "median_hold_bars": statistics.median(event.bars_held for event in events),
        "target_hits": sum(1 for event in events if event.outcome == "target"),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "product_id",
        "volume_24h",
        "pct24h",
        "median_range_pct",
        "burst_signals",
        "burst_win_rate_pct",
        "burst_avg_net_return_pct",
        "reclaim_signals",
        "reclaim_win_rate_pct",
        "reclaim_avg_net_return_pct",
        "compression_signals",
        "compression_win_rate_pct",
        "compression_avg_net_return_pct",
        "best_tactic",
        "best_tactic_score",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in columns})


def _write_md(path: Path, *, hours: int, fee_bps_per_side: float, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Coinbase Spot Burst Lab",
        "",
        f"- Window: last `{hours}h`",
        f"- Candle granularity: `FIVE_MINUTE`",
        f"- Fee assumption: `{fee_bps_per_side:.1f}` bps per side",
        "- Universe: top USD spot names by 24h move among products above the volume floor",
        "- Tactics scored: burst continuation, panic reclaim, compression breakout",
        "- Returns are net of fee assumption and use conservative candle-path evaluation.",
        "",
        "| Product | 24h % | Med 5m Range % | Burst | Burst Avg Net % | Reclaim | Reclaim Avg Net % | Compression | Compression Avg Net % | Best |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows[:20]:
        lines.append(
            "| {product_id} | {pct24h:.2f}% | {median_range_pct:.3f}% | {burst_signals} | {burst_avg_net_return_pct:.3f}% | {reclaim_signals} | {reclaim_avg_net_return_pct:.3f}% | {compression_signals} | {compression_avg_net_return_pct:.3f}% | {best_tactic} |".format(
                **row
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    client = CoinbaseAdvancedClient()
    end_ts = int(time.time())
    start_ts = end_ts - int(args.hours) * 3600

    products_payload = client.list_products(get_all_products=True, product_type="SPOT", limit=1000)
    candidates: list[tuple[float, float, str]] = []
    meta: dict[str, dict[str, float]] = {}
    for product in (products_payload.get("products") or []):
        product_id = str(product.get("product_id") or "")
        base = str(product.get("base_currency_id") or "")
        quote = str(product.get("quote_currency_id") or "")
        if quote != "USD" or not product_id or base in {"USD", "USDC", "USDT"} or product_id.startswith("USDC-"):
            continue
        try:
            pct24h = float(product.get("price_percentage_change_24h") or 0.0)
            volume_24h = float(product.get("volume_24h") or 0.0)
        except Exception:
            continue
        if volume_24h < float(args.min_volume_24h):
            continue
        candidates.append((abs(pct24h), volume_24h, product_id))
        meta[product_id] = {"pct24h": pct24h, "volume_24h": volume_24h}

    candidates.sort(reverse=True)
    selected = [product_id for _, _, product_id in candidates[: int(args.top_n)]]
    rows: list[dict[str, Any]] = []

    for product_id in selected:
        candles = _load_candles(
            client,
            product_id,
            start_ts=start_ts,
            end_ts=end_ts,
            granularity=str(args.granularity),
        )
        if len(candles) < 20:
            continue
        ranges = [((candle.high / candle.low) - 1.0) * 100.0 for candle in candles if candle.low > 0.0]
        burst_events = [
            evaluate_forward_path(candles, idx, target_pct=0.02, stop_pct=0.01, max_hold_bars=3, fee_bps_per_side=float(args.fee_bps_per_side))
            for idx in range(len(candles))
            if is_burst_continuation(candles, idx)
        ]
        reclaim_events = [
            evaluate_forward_path(candles, idx, target_pct=0.02, stop_pct=0.01, max_hold_bars=6, fee_bps_per_side=float(args.fee_bps_per_side))
            for idx in range(len(candles))
            if is_panic_reclaim(candles, idx)
        ]
        compression_events = [
            evaluate_forward_path(candles, idx, target_pct=0.018, stop_pct=0.008, max_hold_bars=6, fee_bps_per_side=float(args.fee_bps_per_side))
            for idx in range(len(candles))
            if is_compression_breakout(candles, idx)
        ]

        burst_summary = summarize_events(burst_events)
        reclaim_summary = summarize_events(reclaim_events)
        compression_summary = summarize_events(compression_events)
        tactic_scores = {
            "burst": burst_summary["signals"] * burst_summary["avg_net_return_pct"],
            "reclaim": reclaim_summary["signals"] * reclaim_summary["avg_net_return_pct"],
            "compression": compression_summary["signals"] * compression_summary["avg_net_return_pct"],
        }
        best_tactic = max(tactic_scores, key=tactic_scores.get)

        rows.append(
            {
                "product_id": product_id,
                "volume_24h": meta[product_id]["volume_24h"],
                "pct24h": meta[product_id]["pct24h"],
                "median_range_pct": statistics.median(ranges) if ranges else 0.0,
                "burst_signals": burst_summary["signals"],
                "burst_win_rate_pct": burst_summary["win_rate_pct"],
                "burst_avg_net_return_pct": burst_summary["avg_net_return_pct"],
                "reclaim_signals": reclaim_summary["signals"],
                "reclaim_win_rate_pct": reclaim_summary["win_rate_pct"],
                "reclaim_avg_net_return_pct": reclaim_summary["avg_net_return_pct"],
                "compression_signals": compression_summary["signals"],
                "compression_win_rate_pct": compression_summary["win_rate_pct"],
                "compression_avg_net_return_pct": compression_summary["avg_net_return_pct"],
                "best_tactic": best_tactic,
                "best_tactic_score": tactic_scores[best_tactic],
            }
        )

    rows.sort(key=lambda row: row["best_tactic_score"], reverse=True)
    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    _write_csv(csv_path, rows)
    _write_md(md_path, hours=int(args.hours), fee_bps_per_side=float(args.fee_bps_per_side), rows=rows)
    print(json.dumps({"csv_path": str(csv_path), "md_path": str(md_path), "rows": rows[:20]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
