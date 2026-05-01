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

from benchmark_coinbase_spot_burst_lab import _load_candles
from coinbase_advanced_client import CoinbaseAdvancedClient


DEFAULT_CSV_PATH = ROOT / "reports" / "coinbase_spot_edge_lab_24h.csv"
DEFAULT_MD_PATH = ROOT / "reports" / "coinbase_spot_edge_lab_24h.md"


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
    net_return_pct: float
    gross_return_pct: float
    bars_held: int
    outcome: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Third-generation Coinbase spot edge lab.")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--granularity", default="FIVE_MINUTE")
    parser.add_argument("--min-volume-24h", type=float, default=250000.0)
    parser.add_argument("--top-n", type=int, default=80)
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


def _select_products(client: CoinbaseAdvancedClient, *, min_volume_24h: float, top_n: int) -> tuple[list[str], dict[str, dict[str, float]]]:
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
        if volume_24h < min_volume_24h:
            continue
        candidates.append((abs(pct24h), volume_24h, product_id))
        meta[product_id] = {"pct24h": pct24h, "volume_24h": volume_24h}
    candidates.sort(reverse=True)
    return [product_id for _, _, product_id in candidates[:top_n]], meta


def _load_market(
    client: CoinbaseAdvancedClient,
    product_ids: list[str],
    *,
    start_ts: int,
    end_ts: int,
    granularity: str,
) -> tuple[dict[str, list[Candle]], dict[str, dict[int, Candle]], list[int]]:
    by_product: dict[str, list[Candle]] = {}
    by_time: dict[str, dict[int, Candle]] = {}
    common_times: set[int] | None = None
    for product_id in product_ids:
        raw_candles = _load_candles(client, product_id, start_ts=start_ts, end_ts=end_ts, granularity=granularity)
        candles = [
            Candle(
                start=int(candle.start),
                low=float(candle.low),
                high=float(candle.high),
                open=float(candle.open),
                close=float(candle.close),
                volume=float(candle.volume),
            )
            for candle in raw_candles
        ]
        if len(candles) < 30:
            continue
        by_product[product_id] = candles
        product_by_time = {candle.start: candle for candle in candles}
        by_time[product_id] = product_by_time
        if common_times is None:
            common_times = set(product_by_time.keys())
        else:
            common_times &= set(product_by_time.keys())
    ordered_times = sorted(common_times or [])
    return by_product, by_time, ordered_times


def _round_trip_fee_pct(fee_bps_per_side: float) -> float:
    return 2.0 * (fee_bps_per_side / 10000.0)


def _simulate_two_stage_exit(
    candles: list[Candle],
    entry_idx: int,
    *,
    initial_stop_pct: float,
    target1_pct: float,
    target2_pct: float,
    max_hold_bars: int,
    fee_bps_per_side: float,
) -> EventResult:
    entry = candles[entry_idx].close
    stop_px = entry * (1.0 - initial_stop_pct)
    target1_px = entry * (1.0 + target1_pct)
    target2_px = entry * (1.0 + target2_pct)
    remaining_fraction = 1.0
    realized_gross = 0.0
    first_target_hit = False
    last_idx = min(len(candles) - 1, entry_idx + max_hold_bars)
    break_even_px = entry

    for idx in range(entry_idx + 1, last_idx + 1):
        candle = candles[idx]
        if not first_target_hit:
            if candle.low <= stop_px and candle.high >= target1_px:
                gross = -initial_stop_pct
                return EventResult(net_return_pct=gross - _round_trip_fee_pct(fee_bps_per_side), gross_return_pct=gross, bars_held=idx - entry_idx, outcome="stop_first_ambiguous")
            if candle.low <= stop_px:
                gross = -initial_stop_pct
                return EventResult(net_return_pct=gross - _round_trip_fee_pct(fee_bps_per_side), gross_return_pct=gross, bars_held=idx - entry_idx, outcome="stop")
            if candle.high >= target1_px:
                realized_gross += 0.5 * target1_pct
                remaining_fraction = 0.5
                first_target_hit = True

        if first_target_hit:
            if candle.low <= break_even_px and candle.high >= target2_px:
                gross = realized_gross
                return EventResult(net_return_pct=gross - _round_trip_fee_pct(fee_bps_per_side), gross_return_pct=gross, bars_held=idx - entry_idx, outcome="breakeven_first_ambiguous")
            if candle.high >= target2_px:
                realized_gross += remaining_fraction * target2_pct
                gross = realized_gross
                return EventResult(net_return_pct=gross - _round_trip_fee_pct(fee_bps_per_side), gross_return_pct=gross, bars_held=idx - entry_idx, outcome="target2")
            if candle.low <= break_even_px:
                gross = realized_gross
                return EventResult(net_return_pct=gross - _round_trip_fee_pct(fee_bps_per_side), gross_return_pct=gross, bars_held=idx - entry_idx, outcome="breakeven")

    exit_close = candles[last_idx].close
    if first_target_hit:
        realized_gross += remaining_fraction * ((exit_close / entry) - 1.0)
    else:
        realized_gross = (exit_close / entry) - 1.0
    return EventResult(net_return_pct=realized_gross - _round_trip_fee_pct(fee_bps_per_side), gross_return_pct=realized_gross, bars_held=last_idx - entry_idx, outcome="time_exit")


def _is_pullback_resume(candles: list[Candle], idx: int) -> bool:
    if idx < 2:
        return False
    burst = candles[idx - 1]
    trigger = candles[idx]
    pre = candles[idx - 2]
    if min(pre.close, burst.open, trigger.open) <= 0.0:
        return False
    burst_return = (burst.close / pre.close) - 1.0
    burst_range = (burst.high / burst.low) - 1.0 if burst.low > 0.0 else 0.0
    retrace = (trigger.low / burst.close) - 1.0
    regained = (trigger.close / burst.close) - 1.0
    close_location = 0.0 if trigger.high <= trigger.low else (trigger.close - trigger.low) / (trigger.high - trigger.low)
    return burst_return >= 0.02 and burst_range >= 0.025 and retrace <= -0.006 and regained >= -0.0015 and close_location >= 0.65


def _is_flush_reclaim(candles: list[Candle], idx: int) -> bool:
    if idx < 2:
        return False
    flush = candles[idx - 1]
    reclaim = candles[idx]
    pre = candles[idx - 2]
    if min(pre.close, flush.low, reclaim.low) <= 0.0:
        return False
    flush_move = (flush.low / pre.close) - 1.0
    reclaim_move = (reclaim.close / flush.low) - 1.0
    close_location = 0.0 if reclaim.high <= reclaim.low else (reclaim.close - reclaim.low) / (reclaim.high - reclaim.low)
    return flush_move <= -0.025 and reclaim_move >= 0.018 and close_location >= 0.7


def _simulate_product_tactic(
    candles: list[Candle],
    *,
    signal_name: str,
    fee_bps_per_side: float,
) -> list[EventResult]:
    events: list[EventResult] = []
    if signal_name == "pullback_resume":
        signal_fn = _is_pullback_resume
        kwargs = {"initial_stop_pct": 0.01, "target1_pct": 0.018, "target2_pct": 0.04, "max_hold_bars": 12}
    elif signal_name == "flush_reclaim":
        signal_fn = _is_flush_reclaim
        kwargs = {"initial_stop_pct": 0.012, "target1_pct": 0.02, "target2_pct": 0.05, "max_hold_bars": 12}
    else:
        raise ValueError(signal_name)
    for idx in range(len(candles)):
        if signal_fn(candles, idx):
            events.append(_simulate_two_stage_exit(candles, idx, fee_bps_per_side=fee_bps_per_side, **kwargs))
    return events


def _simulate_leader_emergence(
    by_time: dict[str, dict[int, Candle]],
    ordered_times: list[int],
    *,
    fee_bps_per_side: float,
) -> dict[str, list[EventResult]]:
    events_by_product: dict[str, list[EventResult]] = {}
    if len(ordered_times) < 12:
        return events_by_product

    for idx in range(6, len(ordered_times) - 12):
        current_time = ordered_times[idx]
        prev_time = ordered_times[idx - 3]
        old_time = ordered_times[idx - 6]
        scores: list[tuple[float, str]] = []
        previous_scores: list[tuple[float, str]] = []
        for product_id, series in by_time.items():
            now = series[current_time]
            prev = series[prev_time]
            older = series[old_time]
            if min(now.close, prev.close, older.close) <= 0.0:
                continue
            scores.append((((now.close / prev.close) - 1.0), product_id))
            previous_scores.append((((prev.close / older.close) - 1.0), product_id))
        if len(scores) < 4 or len(previous_scores) < 4:
            continue
        scores.sort(reverse=True)
        previous_scores.sort(reverse=True)
        current_rank = {product_id: rank for rank, (_, product_id) in enumerate(scores, start=1)}
        previous_rank = {product_id: rank for rank, (_, product_id) in enumerate(previous_scores, start=1)}
        strength_by_product = {product_id: strength for strength, product_id in scores}

        for product_id, rank in current_rank.items():
            prev_rank = previous_rank.get(product_id, len(previous_rank) + 1)
            rank_jump = prev_rank - rank
            strength = strength_by_product[product_id]
            if rank > 3 or rank_jump < 5 or strength < 0.012:
                continue
            candles = [by_time[product_id][t] for t in ordered_times if t in by_time[product_id]]
            position_idx = next((i for i, candle in enumerate(candles) if candle.start == current_time), None)
            if position_idx is None:
                continue
            event = _simulate_two_stage_exit(
                candles,
                position_idx,
                initial_stop_pct=0.01,
                target1_pct=0.02,
                target2_pct=0.05,
                max_hold_bars=12,
                fee_bps_per_side=fee_bps_per_side,
            )
            events_by_product.setdefault(product_id, []).append(event)
    return events_by_product


def _summarize(events: list[EventResult]) -> dict[str, float]:
    if not events:
        return {"signals": 0, "win_rate_pct": 0.0, "avg_net_return_pct": 0.0, "median_hold_bars": 0.0}
    return {
        "signals": len(events),
        "win_rate_pct": (sum(1 for event in events if event.net_return_pct > 0.0) / len(events)) * 100.0,
        "avg_net_return_pct": statistics.fmean(event.net_return_pct for event in events) * 100.0,
        "median_hold_bars": statistics.median(event.bars_held for event in events),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "tactic",
        "signals",
        "win_rate_pct",
        "avg_net_return_pct",
        "median_hold_bars",
        "positive_products",
        "cumulative_net_pct",
        "top_products",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in columns})


def _write_md(path: Path, *, hours: int, fee_bps_per_side: float, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Coinbase Spot Edge Lab",
        "",
        f"- Window: last `{hours}h`",
        "- Candle granularity: `FIVE_MINUTE`",
        f"- Fee assumption: `{fee_bps_per_side:.1f}` bps per side",
        "- Third-generation tactics: pullback-resume, flush-reclaim, leader-emergence",
        "- Exit model: two-stage asymmetric exit with first scale-out and second runner target.",
        "",
        "| Tactic | Signals | Win Rate | Avg Net % | Median Hold Bars | Positive Products | Cumulative Net % | Top Products |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {tactic} | {signals} | {win_rate_pct:.1f}% | {avg_net_return_pct:.3f}% | {median_hold_bars} | {positive_products} | {cumulative_net_pct:.3f}% | {top_products} |".format(
                **row
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    client = CoinbaseAdvancedClient()
    end_ts = int(time.time())
    start_ts = end_ts - int(args.hours) * 3600

    product_ids, _meta = _select_products(client, min_volume_24h=float(args.min_volume_24h), top_n=int(args.top_n))
    by_product, by_time, ordered_times = _load_market(
        client,
        product_ids,
        start_ts=start_ts,
        end_ts=end_ts,
        granularity=str(args.granularity),
    )

    tactic_events: dict[str, dict[str, list[EventResult]]] = {
        "pullback_resume": {},
        "flush_reclaim": {},
        "leader_emergence": {},
    }

    for product_id, candles in by_product.items():
        tactic_events["pullback_resume"][product_id] = _simulate_product_tactic(
            candles, signal_name="pullback_resume", fee_bps_per_side=float(args.fee_bps_per_side)
        )
        tactic_events["flush_reclaim"][product_id] = _simulate_product_tactic(
            candles, signal_name="flush_reclaim", fee_bps_per_side=float(args.fee_bps_per_side)
        )
    tactic_events["leader_emergence"] = _simulate_leader_emergence(by_time, ordered_times, fee_bps_per_side=float(args.fee_bps_per_side))

    rows: list[dict[str, Any]] = []
    for tactic_name, product_events in tactic_events.items():
        flat = [event for events in product_events.values() for event in events]
        summary = _summarize(flat)
        product_scores = {product_id: sum(event.net_return_pct for event in events) * 100.0 for product_id, events in product_events.items()}
        positive_products = [product_id for product_id, score in product_scores.items() if score > 0.0]
        ranked_products = sorted(product_scores.items(), key=lambda item: item[1], reverse=True)
        rows.append(
            {
                "tactic": tactic_name,
                "signals": summary["signals"],
                "win_rate_pct": summary["win_rate_pct"],
                "avg_net_return_pct": summary["avg_net_return_pct"],
                "median_hold_bars": summary["median_hold_bars"],
                "positive_products": len(positive_products),
                "cumulative_net_pct": sum(product_scores.values()),
                "top_products": ", ".join(product_id for product_id, score in ranked_products[:5] if score != 0.0),
            }
        )

    rows.sort(key=lambda row: (row["positive_products"], row["cumulative_net_pct"], row["avg_net_return_pct"]), reverse=True)
    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    _write_csv(csv_path, rows)
    _write_md(md_path, hours=int(args.hours), fee_bps_per_side=float(args.fee_bps_per_side), rows=rows)
    print(json.dumps({"csv_path": str(csv_path), "md_path": str(md_path), "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
