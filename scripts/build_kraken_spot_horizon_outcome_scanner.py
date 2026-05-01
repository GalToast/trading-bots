#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import kraken_config as cfg  # noqa: E402
from build_kraken_vulture_reversal_replay import (  # noqa: E402
    DEFAULT_CACHE_PATH,
    Sample,
    load_pairs,
    load_samples,
    min_size_blockers,
    normalize_asset,
    parse_products,
    spread_bps,
    to_float,
)
from kraken_spot_client import KrakenPair, KrakenSpotClient  # noqa: E402


DEFAULT_JSON_PATH = REPORTS / "kraken_spot_horizon_outcome_scanner.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_spot_horizon_outcome_scanner.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_spot_horizon_outcome_scanner.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def parse_float_list(raw: str) -> list[float]:
    values: list[float] = []
    for part in str(raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part:
            values.append(float(part))
    return values


def find_at_or_before(samples: list[Sample], target_ts: float, *, start_index: int = 0, end_index: int | None = None) -> int | None:
    end = len(samples) - 1 if end_index is None else min(int(end_index), len(samples) - 1)
    best: int | None = None
    for idx in range(max(0, int(start_index)), end + 1):
        if samples[idx].ts <= target_ts:
            best = idx
            continue
        break
    return best


def find_at_or_after(samples: list[Sample], target_ts: float, *, start_index: int = 0) -> int | None:
    for idx in range(max(0, int(start_index)), len(samples)):
        if samples[idx].ts >= target_ts:
            return idx
    return None


def taker_net_bps(entry_ask: float, exit_bid: float, taker_fee_bps: float) -> float:
    if entry_ask <= 0.0 or exit_bid <= 0.0:
        return 0.0
    fee = float(taker_fee_bps) / 10000.0
    return (((exit_bid / entry_ask) * ((1.0 - fee) ** 2)) - 1.0) * 10000.0


def path_metrics(
    *,
    path: list[Sample],
    entry_ask: float,
    taker_fee_bps: float,
    target_net_bps: float,
    stop_loss_bps: float,
    entry_ts: float,
) -> dict[str, Any]:
    nets = [taker_net_bps(entry_ask, sample.bid, taker_fee_bps) for sample in path]
    mfe_bps = max(nets) if nets else 0.0
    mae_bps = min(nets) if nets else 0.0
    first_target_seconds = None
    first_stop_seconds = None
    for sample, net_bps in zip(path, nets):
        if first_target_seconds is None and net_bps >= float(target_net_bps):
            first_target_seconds = sample.ts - entry_ts
        if first_stop_seconds is None and net_bps <= -abs(float(stop_loss_bps)):
            first_stop_seconds = sample.ts - entry_ts
        if first_target_seconds is not None and first_stop_seconds is not None:
            break
    target_before_stop = (
        first_target_seconds is not None
        and (first_stop_seconds is None or float(first_target_seconds) <= float(first_stop_seconds))
    )
    stop_before_target = (
        first_stop_seconds is not None
        and (first_target_seconds is None or float(first_stop_seconds) < float(first_target_seconds))
    )
    return {
        "mfe_bps": round(mfe_bps, 6),
        "mae_bps": round(mae_bps, 6),
        "first_target_seconds": None if first_target_seconds is None else round(first_target_seconds, 3),
        "first_stop_seconds": None if first_stop_seconds is None else round(first_stop_seconds, 3),
        "target_before_stop": target_before_stop,
        "stop_before_target": stop_before_target,
    }


def replay_product(
    *,
    product_id: str,
    pair: KrakenPair,
    samples: list[Sample],
    signal_lookbacks: list[float],
    horizons: list[float],
    min_signal_bps: float,
    max_spread_bps: float,
    taker_fee_bps: float,
    start_usd: float,
    target_net_bps: float,
    stop_loss_bps: float,
    cooldown_seconds: float,
    max_horizon_lag_seconds: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if len(samples) < 3:
        return rows
    next_allowed_by_lookback = {float(lookback): samples[0].ts for lookback in signal_lookbacks}
    max_horizon = max(horizons) if horizons else 0.0
    for signal_index in range(1, len(samples) - 1):
        signal = samples[signal_index]
        if signal.ts + max_horizon > samples[-1].ts:
            continue
        for lookback in signal_lookbacks:
            lookback = float(lookback)
            if signal.ts < next_allowed_by_lookback[lookback]:
                continue
            prior_index = find_at_or_before(samples, signal.ts - lookback, end_index=signal_index - 1)
            if prior_index is None:
                continue
            prior = samples[prior_index]
            if prior.bid <= 0.0:
                continue
            signal_bps = ((signal.bid / prior.bid) - 1.0) * 10000.0
            if signal_bps < float(min_signal_bps):
                continue
            entry_index = signal_index + 1
            entry = samples[entry_index]
            entry_spread_bps = spread_bps(entry)
            if entry_spread_bps > float(max_spread_bps):
                continue
            next_allowed_by_lookback[lookback] = signal.ts + max(1.0, float(cooldown_seconds))
            for horizon in horizons:
                exit_index = find_at_or_after(samples, entry.ts + float(horizon), start_index=entry_index + 1)
                if exit_index is None:
                    continue
                exit_sample = samples[exit_index]
                horizon_lag_seconds = exit_sample.ts - (entry.ts + float(horizon))
                if horizon_lag_seconds > float(max_horizon_lag_seconds):
                    continue
                path = samples[entry_index : exit_index + 1]
                net_bps = taker_net_bps(entry.ask, exit_sample.bid, taker_fee_bps)
                metrics = path_metrics(
                    path=path,
                    entry_ask=entry.ask,
                    taker_fee_bps=taker_fee_bps,
                    target_net_bps=target_net_bps,
                    stop_loss_bps=stop_loss_bps,
                    entry_ts=entry.ts,
                )
                blockers = ["depth_unavailable_in_radar_cache", "fillability_unproven"]
                blockers.extend(min_size_blockers(pair, entry.ask, start_usd))
                if net_bps < float(target_net_bps):
                    blockers.append("net_edge_below_target")
                if to_float(metrics.get("mfe_bps")) < float(target_net_bps):
                    blockers.append("never_fee_green_to_target")
                if metrics.get("stop_before_target"):
                    blockers.append("stop_before_target")
                rows.append(
                    {
                        "product_id": product_id,
                        "lookback_seconds": float(lookback),
                        "horizon_seconds": float(horizon),
                        "prior_index": prior_index,
                        "signal_index": signal_index,
                        "entry_index": entry_index,
                        "exit_index": exit_index,
                        "prior_ts": prior.ts,
                        "signal_ts": signal.ts,
                        "entry_ts": entry.ts,
                        "exit_ts": exit_sample.ts,
                        "elapsed_seconds": round(exit_sample.ts - entry.ts, 3),
                        "horizon_lag_seconds": round(horizon_lag_seconds, 3),
                        "prior_bid": round(prior.bid, 12),
                        "signal_bid": round(signal.bid, 12),
                        "signal_bps": round(signal_bps, 6),
                        "entry_bid": round(entry.bid, 12),
                        "entry_ask": round(entry.ask, 12),
                        "entry_spread_bps": round(entry_spread_bps, 6),
                        "exit_bid": round(exit_sample.bid, 12),
                        "net_bps": round(net_bps, 6),
                        "target_net_bps": float(target_net_bps),
                        "stop_loss_bps": float(stop_loss_bps),
                        "start_usd": round(float(start_usd), 6),
                        "start_base_qty": round(start_usd / entry.ask if entry.ask > 0.0 else 0.0, 12),
                        "price_positive": net_bps >= float(target_net_bps),
                        "ever_target_green": to_float(metrics.get("mfe_bps")) >= float(target_net_bps),
                        "executable_positive": False,
                        "blockers": blockers,
                        "blocker_text": ", ".join(blockers),
                        **metrics,
                    }
                )
    return rows


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((float(pct) / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


def summarize_rows(rows: list[dict[str, Any]], *, horizons: list[float], signal_lookbacks: list[float], target_net_bps: float) -> dict[str, Any]:
    def stats(bucket: list[dict[str, Any]]) -> dict[str, Any]:
        nets = [to_float(row.get("net_bps")) for row in bucket]
        mfes = [to_float(row.get("mfe_bps")) for row in bucket]
        maes = [to_float(row.get("mae_bps")) for row in bucket]
        return {
            "events": len(bucket),
            "net_positive_price_only": sum(1 for row in bucket if to_float(row.get("net_bps")) >= float(target_net_bps)),
            "ever_target_green_price_only": sum(1 for row in bucket if row.get("ever_target_green")),
            "target_before_stop": sum(1 for row in bucket if row.get("target_before_stop")),
            "stop_before_target": sum(1 for row in bucket if row.get("stop_before_target")),
            "win_rate_pct": round((sum(1 for net in nets if net >= float(target_net_bps)) / len(nets)) * 100.0, 6) if nets else 0.0,
            "ever_green_rate_pct": round((sum(1 for row in bucket if row.get("ever_target_green")) / len(bucket)) * 100.0, 6) if bucket else 0.0,
            "avg_net_bps": round(sum(nets) / len(nets), 6) if nets else 0.0,
            "median_net_bps": round(statistics.median(nets), 6) if nets else 0.0,
            "p90_net_bps": round(percentile(nets, 90), 6),
            "p10_net_bps": round(percentile(nets, 10), 6),
            "avg_mfe_bps": round(sum(mfes) / len(mfes), 6) if mfes else 0.0,
            "avg_mae_bps": round(sum(maes) / len(maes), 6) if maes else 0.0,
            "best_net_bps": round(max(nets), 6) if nets else 0.0,
            "best_mfe_bps": round(max(mfes), 6) if mfes else 0.0,
            "worst_mae_bps": round(min(maes), 6) if maes else 0.0,
        }

    by_horizon = {}
    for horizon in horizons:
        bucket = [row for row in rows if float(row.get("horizon_seconds", 0.0)) == float(horizon)]
        by_horizon[str(int(horizon))] = stats(bucket)
    by_lookback = {}
    for lookback in signal_lookbacks:
        bucket = [row for row in rows if float(row.get("lookback_seconds", 0.0)) == float(lookback)]
        by_lookback[str(int(lookback))] = stats(bucket)
    by_product: dict[str, dict[str, Any]] = {}
    for row in rows:
        product_id = str(row.get("product_id") or "")
        by_product.setdefault(product_id, []).append(row)
    product_stats = {product_id: stats(bucket) for product_id, bucket in by_product.items()}
    product_stats = dict(sorted(product_stats.items(), key=lambda item: (item[1]["avg_net_bps"], item[1]["best_mfe_bps"], item[1]["events"]), reverse=True))
    return {
        "events_scored": len(rows),
        "net_positive_price_only": sum(1 for row in rows if to_float(row.get("net_bps")) >= float(target_net_bps)),
        "ever_target_green_price_only": sum(1 for row in rows if row.get("ever_target_green")),
        "executable_positive": 0,
        "best_net_bps": max((to_float(row.get("net_bps")) for row in rows), default=0.0),
        "best_mfe_bps": max((to_float(row.get("mfe_bps")) for row in rows), default=0.0),
        "by_horizon": by_horizon,
        "by_lookback": by_lookback,
        "by_product": product_stats,
    }


def build_payload(
    *,
    client: KrakenSpotClient,
    cache_path: Path,
    products: set[str],
    quote_currencies: set[str],
    signal_lookbacks: list[float],
    horizons: list[float],
    min_signal_bps: float,
    max_spread_bps: float,
    taker_fee_bps: float,
    start_usd: float,
    target_net_bps: float,
    stop_loss_bps: float,
    cooldown_seconds: float,
    max_horizon_lag_seconds: float,
) -> dict[str, Any]:
    pairs = load_pairs(client, products, quote_currencies)
    cache = load_json(cache_path)
    sample_map = load_samples(cache, pairs)
    rows: list[dict[str, Any]] = []
    for product_id, samples in sample_map.items():
        rows.extend(
            replay_product(
                product_id=product_id,
                pair=pairs[product_id],
                samples=samples,
                signal_lookbacks=signal_lookbacks,
                horizons=horizons,
                min_signal_bps=min_signal_bps,
                max_spread_bps=max_spread_bps,
                taker_fee_bps=taker_fee_bps,
                start_usd=start_usd,
                target_net_bps=target_net_bps,
                stop_loss_bps=stop_loss_bps,
                cooldown_seconds=cooldown_seconds,
                max_horizon_lag_seconds=max_horizon_lag_seconds,
            )
        )
    rows.sort(
        key=lambda row: (
            to_float(row.get("net_bps")),
            to_float(row.get("mfe_bps")),
            -to_float(row.get("entry_spread_bps")),
        ),
        reverse=True,
    )
    summary = summarize_rows(rows, horizons=horizons, signal_lookbacks=signal_lookbacks, target_net_bps=target_net_bps)
    summary["products_loaded"] = len(pairs)
    summary["products_with_cache"] = len(sample_map)
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_spot_horizon_outcome_scanner",
        "venue": "kraken",
        "shadow_only": True,
        "places_orders": False,
        "parameters": {
            "cache_path": str(cache_path),
            "products": sorted(products),
            "quote_currencies": sorted(quote_currencies),
            "signal_lookbacks": signal_lookbacks,
            "horizons": horizons,
            "min_signal_bps": float(min_signal_bps),
            "max_spread_bps": float(max_spread_bps),
            "taker_fee_bps": float(taker_fee_bps),
            "start_usd": float(start_usd),
            "target_net_bps": float(target_net_bps),
            "stop_loss_bps": float(stop_loss_bps),
            "cooldown_seconds": float(cooldown_seconds),
            "max_horizon_lag_seconds": float(max_horizon_lag_seconds),
        },
        "summary": summary,
        "read": [
            "Read-only cache replay: no private endpoints and no order placement.",
            "Signal is positive bid momentum over the configured lookback, with next-sample ask entry and future bid exits.",
            "Economics are taker-style ask entry to bid exit with fees on both sides, so spreads and fees are included.",
            "Rows are still price-only until trigger-time depth and live fillability are captured forward.",
        ],
        "rows": rows[:500],
        "worst_rows": sorted(rows, key=lambda row: to_float(row.get("mae_bps")))[:50],
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, csv_path: Path, md_path: Path) -> None:
    write_json(json_path, payload)
    columns = [
        "product_id",
        "lookback_seconds",
        "horizon_seconds",
        "signal_bps",
        "entry_spread_bps",
        "net_bps",
        "mfe_bps",
        "mae_bps",
        "first_target_seconds",
        "first_stop_seconds",
        "target_before_stop",
        "stop_before_target",
        "entry_ask",
        "exit_bid",
        "blocker_text",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload.get("rows") or []:
            writer.writerow({column: row.get(column, "") for column in columns})

    summary = payload.get("summary") or {}
    lines = [
        "# Kraken Spot Horizon Outcome Scanner",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Shadow only: `{payload.get('shadow_only')}`",
        f"- Products with cache: `{summary.get('products_with_cache')}`",
        f"- Events scored: `{summary.get('events_scored')}`",
        f"- Net-positive price-only: `{summary.get('net_positive_price_only')}`",
        f"- Ever target-green price-only: `{summary.get('ever_target_green_price_only')}`",
        f"- Executable positive: `{summary.get('executable_positive')}`",
        f"- Best net bps: `{to_float(summary.get('best_net_bps')):.4f}`",
        f"- Best MFE bps: `{to_float(summary.get('best_mfe_bps')):.4f}`",
        "",
        "## Read",
        "",
    ]
    lines.extend(f"- {item}" for item in payload.get("read") or [])
    lines.extend(
        [
            "",
            "## Horizon Results",
            "",
            "| Horizon | Events | Net+ | Ever Green | Target Before Stop | Stop Before Target | Win % | Avg Net bps | Median Net bps | P90 Net bps | Avg MFE bps | Avg MAE bps |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for horizon, row in (summary.get("by_horizon") or {}).items():
        lines.append(
            "| {horizon}s | {events} | {net_positive_price_only} | {ever_target_green_price_only} | {target_before_stop} | {stop_before_target} | {win_rate_pct:.2f} | {avg_net_bps:.4f} | {median_net_bps:.4f} | {p90_net_bps:.4f} | {avg_mfe_bps:.4f} | {avg_mae_bps:.4f} |".format(
                horizon=horizon,
                **row,
            )
        )
    lines.extend(
        [
            "",
            "## Product Leaders",
            "",
            "| Product | Events | Net+ | Ever Green | Win % | Avg Net bps | Best Net bps | Best MFE bps | Worst MAE bps |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for product_id, row in list((summary.get("by_product") or {}).items())[:30]:
        lines.append(
            "| {product_id} | {events} | {net_positive_price_only} | {ever_target_green_price_only} | {win_rate_pct:.2f} | {avg_net_bps:.4f} | {best_net_bps:.4f} | {best_mfe_bps:.4f} | {worst_mae_bps:.4f} |".format(
                product_id=product_id,
                **row,
            )
        )
    lines.extend(
        [
            "",
            "## Top Events",
            "",
            "| Rank | Product | Lookback | Horizon | Signal | Spread | Net | MFE | MAE | First Target | First Stop | Blockers |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for idx, row in enumerate((payload.get("rows") or [])[:50], start=1):
        lines.append(
            "| {idx} | {product_id} | {lookback_seconds:.0f}s | {horizon_seconds:.0f}s | {signal_bps:.4f} | {entry_spread_bps:.4f} | {net_bps:.4f} | {mfe_bps:.4f} | {mae_bps:.4f} | {first_target} | {first_stop} | {blocker_text} |".format(
                idx=idx,
                product_id=row.get("product_id"),
                lookback_seconds=to_float(row.get("lookback_seconds")),
                horizon_seconds=to_float(row.get("horizon_seconds")),
                signal_bps=to_float(row.get("signal_bps")),
                entry_spread_bps=to_float(row.get("entry_spread_bps")),
                net_bps=to_float(row.get("net_bps")),
                mfe_bps=to_float(row.get("mfe_bps")),
                mae_bps=to_float(row.get("mae_bps")),
                first_target=row.get("first_target_seconds", ""),
                first_stop=row.get("first_stop_seconds", ""),
                blocker_text=row.get("blocker_text", ""),
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay Kraken spot momentum signals across 5m/10m/30m/60m horizons from cached bid/ask samples.")
    parser.add_argument("--cache-path", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--json-path", type=Path, default=DEFAULT_JSON_PATH)
    parser.add_argument("--csv-path", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--md-path", type=Path, default=DEFAULT_MD_PATH)
    parser.add_argument("--products", default="", help="Comma-separated products. Empty string scans by quote currencies.")
    parser.add_argument("--quote-currencies", default="USD", help="Comma-separated quote filters used when --products is empty.")
    parser.add_argument("--signal-lookbacks", default="60,180,300")
    parser.add_argument("--horizons", default="300,600,1800,3600")
    parser.add_argument("--min-signal-bps", type=float, default=25.0)
    parser.add_argument("--max-spread-bps", type=float, default=100.0)
    parser.add_argument("--taker-fee-bps", type=float, default=cfg.DEFAULT_TAKER_FEE_BPS)
    parser.add_argument("--start-usd", type=float, default=50.0)
    parser.add_argument("--target-net-bps", type=float, default=50.0)
    parser.add_argument("--stop-loss-bps", type=float, default=150.0)
    parser.add_argument("--cooldown-seconds", type=float, default=600.0)
    parser.add_argument("--max-horizon-lag-seconds", type=float, default=120.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_payload(
        client=KrakenSpotClient(),
        cache_path=Path(args.cache_path),
        products=parse_products(args.products),
        quote_currencies={normalize_asset(part.strip()) for part in str(args.quote_currencies or "").split(",") if part.strip()},
        signal_lookbacks=parse_float_list(args.signal_lookbacks),
        horizons=parse_float_list(args.horizons),
        min_signal_bps=float(args.min_signal_bps),
        max_spread_bps=float(args.max_spread_bps),
        taker_fee_bps=float(args.taker_fee_bps),
        start_usd=float(args.start_usd),
        target_net_bps=float(args.target_net_bps),
        stop_loss_bps=float(args.stop_loss_bps),
        cooldown_seconds=float(args.cooldown_seconds),
        max_horizon_lag_seconds=float(args.max_horizon_lag_seconds),
    )
    write_reports(payload, json_path=Path(args.json_path), csv_path=Path(args.csv_path), md_path=Path(args.md_path))
    print(json.dumps({"json_path": str(Path(args.json_path).resolve()), "summary": payload.get("summary")}, indent=2))


if __name__ == "__main__":
    main()
