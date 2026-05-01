#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
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
    DEFAULT_PRODUCTS,
    Sample,
    find_future_index,
    load_pairs,
    load_samples,
    min_size_blockers,
    normalize_asset,
    parse_products,
    sort_rows,
    spread_bps,
    taker_net_bps,
    to_float,
)
from kraken_spot_client import KrakenPair, KrakenSpotClient  # noqa: E402


DEFAULT_JSON_PATH = REPORTS / "kraken_vulture_reclaim_replay.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_vulture_reclaim_replay.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_vulture_reclaim_replay.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_float_csv(raw: str) -> list[float]:
    return [float(part.strip()) for part in str(raw or "").split(",") if part.strip()]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def find_reclaim_entry(
    samples: list[Sample],
    *,
    signal_index: int,
    confirm_bps: float,
    entry_timeout_samples: int,
) -> tuple[int | None, float, float, int | None]:
    low_bid = samples[signal_index].bid
    low_index = signal_index
    max_idx = min(len(samples) - 2, signal_index + max(1, int(entry_timeout_samples)))
    for idx in range(signal_index + 1, max_idx + 1):
        sample = samples[idx]
        if sample.bid < low_bid:
            low_bid = sample.bid
            low_index = idx
        if low_bid > 0.0:
            reclaim_bps = ((sample.bid / low_bid) - 1.0) * 10000.0
            if reclaim_bps >= float(confirm_bps):
                return idx + 1, low_bid, reclaim_bps, low_index
    return None, low_bid, 0.0, low_index


def replay_product(
    *,
    product_id: str,
    pair: KrakenPair,
    samples: list[Sample],
    horizons: list[float],
    confirm_bps_values: list[float],
    lookback_samples: int,
    min_dump_bps: float,
    max_spread_bps: float,
    taker_fee_bps: float,
    start_usd: float,
    min_net_bps: float,
    cooldown_samples: int,
    entry_timeout_samples: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if len(samples) < lookback_samples + 4:
        return rows
    next_allowed_by_confirm = {float(confirm): lookback_samples for confirm in confirm_bps_values}
    for signal_index in range(lookback_samples, len(samples) - 2):
        prior = samples[signal_index - lookback_samples : signal_index]
        prior_high = max(sample.bid for sample in prior)
        signal = samples[signal_index]
        if prior_high <= 0.0:
            continue
        dump_bps = ((signal.bid / prior_high) - 1.0) * 10000.0
        if dump_bps > -abs(float(min_dump_bps)):
            continue
        for confirm_bps in confirm_bps_values:
            confirm_bps = float(confirm_bps)
            if signal_index < next_allowed_by_confirm[confirm_bps]:
                continue
            entry_index, low_bid, reclaim_bps, low_index = find_reclaim_entry(
                samples,
                signal_index=signal_index,
                confirm_bps=confirm_bps,
                entry_timeout_samples=entry_timeout_samples,
            )
            if entry_index is None:
                continue
            if entry_index >= len(samples):
                continue
            entry = samples[entry_index]
            entry_spread_bps = spread_bps(entry)
            if entry_spread_bps > float(max_spread_bps):
                continue
            next_allowed_by_confirm[confirm_bps] = signal_index + max(1, int(cooldown_samples))
            for horizon in horizons:
                exit_index = find_future_index(samples, entry_index, horizon)
                if exit_index is None:
                    continue
                exit_sample = samples[exit_index]
                path = samples[entry_index : exit_index + 1]
                net_bps = taker_net_bps(entry.ask, exit_sample.bid, taker_fee_bps)
                mfe_bps = max(taker_net_bps(entry.ask, sample.bid, taker_fee_bps) for sample in path)
                mae_bps = min(taker_net_bps(entry.ask, sample.bid, taker_fee_bps) for sample in path)
                first_green_seconds = None
                for sample in path:
                    if taker_net_bps(entry.ask, sample.bid, taker_fee_bps) >= float(min_net_bps):
                        first_green_seconds = sample.ts - entry.ts
                        break
                blockers = ["depth_unavailable_in_radar_cache", "fillability_unproven"]
                blockers.extend(min_size_blockers(pair, entry.ask, start_usd))
                if net_bps < float(min_net_bps):
                    blockers.append("net_edge_below_threshold")
                if mfe_bps < float(min_net_bps):
                    blockers.append("never_fee_green")
                rows.append(
                    {
                        "product_id": product_id,
                        "confirm_bps": confirm_bps,
                        "horizon_seconds": float(horizon),
                        "signal_index": signal_index,
                        "low_index": low_index,
                        "entry_index": entry_index,
                        "exit_index": exit_index,
                        "signal_ts": signal.ts,
                        "entry_ts": entry.ts,
                        "exit_ts": exit_sample.ts,
                        "elapsed_seconds": round(exit_sample.ts - entry.ts, 3),
                        "prior_high_bid": round(prior_high, 12),
                        "signal_bid": round(signal.bid, 12),
                        "low_bid": round(low_bid, 12),
                        "dump_bps": round(dump_bps, 6),
                        "reclaim_bps": round(reclaim_bps, 6),
                        "entry_ask": round(entry.ask, 12),
                        "entry_spread_bps": round(entry_spread_bps, 6),
                        "exit_bid": round(exit_sample.bid, 12),
                        "net_bps": round(net_bps, 6),
                        "mfe_bps": round(mfe_bps, 6),
                        "mae_bps": round(mae_bps, 6),
                        "first_green_seconds": None if first_green_seconds is None else round(first_green_seconds, 3),
                        "start_usd": round(float(start_usd), 6),
                        "start_base_qty": round(start_usd / entry.ask if entry.ask > 0.0 else 0.0, 12),
                        "executable_positive": not blockers,
                        "blockers": blockers,
                        "blocker_text": ", ".join(blockers),
                    }
                )
    return rows


def build_payload(
    *,
    client: KrakenSpotClient,
    cache_path: Path,
    products: set[str],
    quote_currencies: set[str],
    horizons: list[float],
    confirm_bps_values: list[float],
    lookback_samples: int,
    min_dump_bps: float,
    max_spread_bps: float,
    taker_fee_bps: float,
    start_usd: float,
    min_net_bps: float,
    cooldown_samples: int,
    entry_timeout_samples: int,
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
                horizons=horizons,
                confirm_bps_values=confirm_bps_values,
                lookback_samples=lookback_samples,
                min_dump_bps=min_dump_bps,
                max_spread_bps=max_spread_bps,
                taker_fee_bps=taker_fee_bps,
                start_usd=start_usd,
                min_net_bps=min_net_bps,
                cooldown_samples=cooldown_samples,
                entry_timeout_samples=entry_timeout_samples,
            )
        )
    rows = sort_rows(rows)
    net_positive = [row for row in rows if to_float(row.get("net_bps")) >= float(min_net_bps)]
    ever_green = [row for row in rows if to_float(row.get("mfe_bps")) >= float(min_net_bps)]
    by_confirm: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = f"{to_float(row.get('confirm_bps')):.4f}"
        bucket = by_confirm.setdefault(key, {"confirm_bps": to_float(row.get("confirm_bps")), "events": 0, "net_positive": 0, "best_net_bps": 0.0})
        bucket["events"] += 1
        if to_float(row.get("net_bps")) >= float(min_net_bps):
            bucket["net_positive"] += 1
        bucket["best_net_bps"] = max(to_float(bucket.get("best_net_bps")), to_float(row.get("net_bps")))
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_vulture_reclaim_replay",
        "shadow_only": True,
        "parameters": {
            "cache_path": str(cache_path),
            "products": sorted(products),
            "quote_currencies": sorted(quote_currencies),
            "horizons": horizons,
            "confirm_bps_values": confirm_bps_values,
            "lookback_samples": int(lookback_samples),
            "min_dump_bps": float(min_dump_bps),
            "max_spread_bps": float(max_spread_bps),
            "taker_fee_bps": float(taker_fee_bps),
            "start_usd": float(start_usd),
            "min_net_bps": float(min_net_bps),
            "cooldown_samples": int(cooldown_samples),
            "entry_timeout_samples": int(entry_timeout_samples),
        },
        "summary": {
            "products_loaded": len(pairs),
            "products_with_cache": len(sample_map),
            "events_scored": len(rows),
            "net_positive_price_only": len(net_positive),
            "ever_fee_green_price_only": len(ever_green),
            "executable_positive": sum(1 for row in rows if row.get("executable_positive")),
            "best_net_bps": max((to_float(row.get("net_bps")) for row in rows), default=0.0),
            "best_mfe_bps": max((to_float(row.get("mfe_bps")) for row in rows), default=0.0),
            "by_confirm": sorted(by_confirm.values(), key=lambda row: (to_float(row.get("best_net_bps")), int(row.get("net_positive", 0))), reverse=True),
        },
        "read": [
            "Causal dump-reclaim replay: prior high -> dump -> reclaim confirmation -> next ask entry.",
            "Economics are taker ask entry to taker bid exit with taker fees on both sides.",
            "Rows remain price-only until trigger-time order-book depth is captured forward.",
        ],
        "top_rows": rows[:50],
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "product_id",
        "confirm_bps",
        "horizon_seconds",
        "dump_bps",
        "reclaim_bps",
        "entry_spread_bps",
        "net_bps",
        "mfe_bps",
        "mae_bps",
        "first_green_seconds",
        "entry_ask",
        "exit_bid",
        "blocker_text",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def write_md(path: Path, payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    lines = [
        "# Kraken Vulture Reclaim Replay",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Events scored: `{summary['events_scored']}`",
        f"- Net-positive price-only: `{summary['net_positive_price_only']}`",
        f"- Ever fee-green price-only: `{summary['ever_fee_green_price_only']}`",
        f"- Executable positive: `{summary['executable_positive']}`",
        f"- Best net bps: `{to_float(summary['best_net_bps']):.4f}`",
        "",
        "## Confirm Grid",
        "",
        "| Confirm bps | Events | Net+ | Best net bps |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for row in summary.get("by_confirm") or []:
        lines.append(
            f"| {to_float(row.get('confirm_bps')):.4f} | {int(row.get('events', 0))} | {int(row.get('net_positive', 0))} | {to_float(row.get('best_net_bps')):.4f} |"
        )
    lines.extend(["", "## Top Rows", "", "| # | Product | Confirm | Horizon | Dump | Reclaim | Spread | Net | MFE | MAE | Blockers |", "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"])
    for idx, row in enumerate(payload.get("top_rows") or [], start=1):
        lines.append(
            "| {idx} | {product_id} | {confirm_bps:.1f} | {horizon_seconds:.0f}s | {dump_bps:.4f} | {reclaim_bps:.4f} | {entry_spread_bps:.4f} | {net_bps:.4f} | {mfe_bps:.4f} | {mae_bps:.4f} | {blocker_text} |".format(
                idx=idx,
                product_id=row.get("product_id"),
                confirm_bps=to_float(row.get("confirm_bps")),
                horizon_seconds=to_float(row.get("horizon_seconds")),
                dump_bps=to_float(row.get("dump_bps")),
                reclaim_bps=to_float(row.get("reclaim_bps")),
                entry_spread_bps=to_float(row.get("entry_spread_bps")),
                net_bps=to_float(row.get("net_bps")),
                mfe_bps=to_float(row.get("mfe_bps")),
                mae_bps=to_float(row.get("mae_bps")),
                blocker_text=row.get("blocker_text", ""),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay causal Kraken spot dump-reclaim vulture entries from cached bid/ask samples.")
    parser.add_argument("--cache-path", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--products", default=DEFAULT_PRODUCTS, help="Comma-separated product ids. Empty string means all matching quote currencies.")
    parser.add_argument("--quote-currencies", default="", help="Comma-separated quote currencies used when products is empty.")
    parser.add_argument("--horizons", default="30,60,300,900")
    parser.add_argument("--confirm-bps", default="20,40,80,120")
    parser.add_argument("--lookback-samples", type=int, default=20)
    parser.add_argument("--min-dump-bps", type=float, default=500.0)
    parser.add_argument("--entry-timeout-samples", type=int, default=12)
    parser.add_argument("--max-spread-bps", type=float, default=800.0)
    parser.add_argument("--taker-fee-bps", type=float, default=cfg.DEFAULT_TAKER_FEE_BPS)
    parser.add_argument("--start-usd", type=float, default=50.0)
    parser.add_argument("--min-net-bps", type=float, default=10.0)
    parser.add_argument("--cooldown-samples", type=int, default=5)
    parser.add_argument("--json-path", type=Path, default=DEFAULT_JSON_PATH)
    parser.add_argument("--csv-path", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--md-path", type=Path, default=DEFAULT_MD_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    products = parse_products(args.products)
    quote_currencies = {normalize_asset(part.strip().upper()) for part in str(args.quote_currencies or "").split(",") if part.strip()}
    payload = build_payload(
        client=KrakenSpotClient(),
        cache_path=args.cache_path,
        products=products,
        quote_currencies=quote_currencies,
        horizons=parse_float_csv(args.horizons),
        confirm_bps_values=parse_float_csv(args.confirm_bps),
        lookback_samples=int(args.lookback_samples),
        min_dump_bps=float(args.min_dump_bps),
        max_spread_bps=float(args.max_spread_bps),
        taker_fee_bps=float(args.taker_fee_bps),
        start_usd=float(args.start_usd),
        min_net_bps=float(args.min_net_bps),
        cooldown_samples=int(args.cooldown_samples),
        entry_timeout_samples=int(args.entry_timeout_samples),
    )
    args.json_path.parent.mkdir(parents=True, exist_ok=True)
    args.json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(args.csv_path, payload.get("top_rows") or [])
    write_md(args.md_path, payload)
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
