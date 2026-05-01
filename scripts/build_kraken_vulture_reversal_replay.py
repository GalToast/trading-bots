#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import kraken_config as cfg  # noqa: E402
from build_spot_numeraire_accumulation_board import product_id_for_pair  # noqa: E402
from kraken_spot_client import KrakenPair, KrakenSpotClient, normalize_asset, parse_pair, to_float  # noqa: E402


DEFAULT_CACHE_PATH = REPORTS / "cache" / "kraken_spot_live_radar_ticks.json"
DEFAULT_JSON_PATH = REPORTS / "kraken_vulture_reversal_replay.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_vulture_reversal_replay.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_vulture_reversal_replay.md"
DEFAULT_PRODUCTS = "HOUSE-USD,CQT-USD,VELVET-USD,EPT-USD,HDX-USD,VOOI-USD,STRD-USD"


@dataclass(frozen=True)
class Sample:
    ts: float
    bid: float
    ask: float


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_product(raw: str) -> str:
    value = str(raw or "").upper().replace("/", "-").replace("_", "-")
    if "-" in value:
        parts = [normalize_asset(part) for part in value.split("-") if part]
        return "-".join(parts)
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH", "SOL", "EUR"):
        if value.endswith(quote) and len(value) > len(quote):
            return f"{normalize_asset(value[:-len(quote)])}-{normalize_asset(quote)}"
    return normalize_asset(value)


def parse_products(raw: str) -> set[str]:
    return {normalize_product(part.strip()) for part in str(raw or "").split(",") if part.strip()}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_pairs(client: KrakenSpotClient, products: set[str], quote_currencies: set[str]) -> dict[str, KrakenPair]:
    pairs: dict[str, KrakenPair] = {}
    for rest_pair, payload in client.asset_pairs().items():
        if not isinstance(payload, dict):
            continue
        pair = parse_pair(str(rest_pair), payload)
        if pair is None:
            continue
        product_id = product_id_for_pair(pair)
        if products and product_id not in products:
            continue
        if quote_currencies and pair.quote not in quote_currencies:
            continue
        if pair.status.lower() not in {"online", "post_only", ""}:
            continue
        pairs[product_id] = pair
    return pairs


def load_samples(cache: dict[str, Any], pairs: dict[str, KrakenPair]) -> dict[str, list[Sample]]:
    raw_samples = cache.get("samples") if isinstance(cache, dict) else {}
    if not isinstance(raw_samples, dict):
        return {}
    rest_to_product = {pair.rest_pair: product_id for product_id, pair in pairs.items()}
    out: dict[str, list[Sample]] = {}
    for rest_pair, rows in raw_samples.items():
        product_id = rest_to_product.get(str(rest_pair))
        if product_id is None or not isinstance(rows, list):
            continue
        samples: list[Sample] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ts = to_float(row.get("ts"))
            bid = to_float(row.get("bid"))
            ask = to_float(row.get("ask"))
            if ts > 0.0 and bid > 0.0 and ask > 0.0:
                samples.append(Sample(ts=ts, bid=bid, ask=ask))
        samples.sort(key=lambda sample: sample.ts)
        if samples:
            out[product_id] = samples
    return out


def find_future_index(samples: list[Sample], start_index: int, horizon_seconds: float) -> int | None:
    target = samples[start_index].ts + float(horizon_seconds)
    best_idx: int | None = None
    best_delta: float | None = None
    for idx in range(start_index + 1, len(samples)):
        delta = abs(samples[idx].ts - target)
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_idx = idx
        if samples[idx].ts >= target:
            break
    return best_idx


def taker_net_bps(entry_ask: float, exit_bid: float, taker_fee_bps: float) -> float:
    if entry_ask <= 0.0 or exit_bid <= 0.0:
        return 0.0
    return ((exit_bid / entry_ask) - 1.0) * 10000.0 - (2.0 * float(taker_fee_bps))


def spread_bps(sample: Sample) -> float:
    return ((sample.ask - sample.bid) / sample.bid) * 10000.0 if sample.bid > 0.0 and sample.ask >= sample.bid else 0.0


def min_size_blockers(pair: KrakenPair, entry_ask: float, start_usd: float) -> list[str]:
    blockers: list[str] = []
    if start_usd < pair.cost_min:
        blockers.append("below_cost_min")
    base_qty = start_usd / entry_ask if entry_ask > 0.0 else 0.0
    if base_qty < pair.order_min:
        blockers.append("below_order_min")
    return blockers


def replay_product(
    *,
    product_id: str,
    pair: KrakenPair,
    samples: list[Sample],
    horizons: list[float],
    lookback_samples: int,
    min_dump_bps: float,
    max_spread_bps: float,
    taker_fee_bps: float,
    start_usd: float,
    min_net_bps: float,
    cooldown_samples: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if len(samples) < lookback_samples + 2:
        return rows
    next_allowed = lookback_samples
    for signal_index in range(lookback_samples, len(samples) - 1):
        if signal_index < next_allowed:
            continue
        prior = samples[signal_index - lookback_samples : signal_index]
        prior_high = max(sample.bid for sample in prior)
        signal = samples[signal_index]
        if prior_high <= 0.0:
            continue
        dump_bps = ((signal.bid / prior_high) - 1.0) * 10000.0
        if dump_bps > -abs(float(min_dump_bps)):
            continue
        entry_index = signal_index + 1
        entry = samples[entry_index]
        entry_spread_bps = spread_bps(entry)
        if entry_spread_bps > float(max_spread_bps):
            continue
        next_allowed = signal_index + max(1, int(cooldown_samples))
        for horizon in horizons:
            exit_index = find_future_index(samples, entry_index, horizon)
            if exit_index is None:
                continue
            exit_sample = samples[exit_index]
            net_bps = taker_net_bps(entry.ask, exit_sample.bid, taker_fee_bps)
            path = samples[entry_index : exit_index + 1]
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
                    "signal_index": signal_index,
                    "entry_index": entry_index,
                    "exit_index": exit_index,
                    "signal_ts": signal.ts,
                    "entry_ts": entry.ts,
                    "exit_ts": exit_sample.ts,
                    "elapsed_seconds": round(exit_sample.ts - entry.ts, 3),
                    "horizon_seconds": float(horizon),
                    "prior_high_bid": round(prior_high, 12),
                    "signal_bid": round(signal.bid, 12),
                    "signal_ask": round(signal.ask, 12),
                    "dump_bps": round(dump_bps, 6),
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


def sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows.sort(
        key=lambda row: (
            to_float(row.get("net_bps")),
            to_float(row.get("mfe_bps")),
            -abs(to_float(row.get("dump_bps"))),
        ),
        reverse=True,
    )
    return rows


def build_payload(
    *,
    client: KrakenSpotClient,
    cache_path: Path,
    products: set[str],
    quote_currencies: set[str],
    horizons: list[float],
    lookback_samples: int,
    min_dump_bps: float,
    max_spread_bps: float,
    taker_fee_bps: float,
    start_usd: float,
    min_net_bps: float,
    cooldown_samples: int,
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
                lookback_samples=lookback_samples,
                min_dump_bps=min_dump_bps,
                max_spread_bps=max_spread_bps,
                taker_fee_bps=taker_fee_bps,
                start_usd=start_usd,
                min_net_bps=min_net_bps,
                cooldown_samples=cooldown_samples,
            )
        )
    rows = sort_rows(rows)
    net_positive = [row for row in rows if to_float(row.get("net_bps")) >= float(min_net_bps)]
    ever_green = [row for row in rows if to_float(row.get("mfe_bps")) >= float(min_net_bps)]
    by_product: dict[str, dict[str, Any]] = {}
    for product_id in sorted(sample_map):
        product_rows = [row for row in rows if row.get("product_id") == product_id]
        by_product[product_id] = {
            "samples": len(sample_map[product_id]),
            "events": len(product_rows),
            "net_positive": sum(1 for row in product_rows if to_float(row.get("net_bps")) >= float(min_net_bps)),
            "ever_fee_green": sum(1 for row in product_rows if to_float(row.get("mfe_bps")) >= float(min_net_bps)),
            "best_net_bps": max((to_float(row.get("net_bps")) for row in product_rows), default=0.0),
            "best_mfe_bps": max((to_float(row.get("mfe_bps")) for row in product_rows), default=0.0),
        }
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_vulture_reversal_replay",
        "venue": "kraken",
        "shadow_only": True,
        "places_orders": False,
        "parameters": {
            "cache_path": str(cache_path),
            "products": sorted(products),
            "quote_currencies": sorted(quote_currencies),
            "horizons": horizons,
            "lookback_samples": int(lookback_samples),
            "min_dump_bps": float(min_dump_bps),
            "max_spread_bps": float(max_spread_bps),
            "taker_fee_bps": float(taker_fee_bps),
            "start_usd": float(start_usd),
            "min_net_bps": float(min_net_bps),
            "cooldown_samples": int(cooldown_samples),
        },
        "summary": {
            "products_requested": len(products),
            "products_loaded": len(pairs),
            "products_with_cache": len(sample_map),
            "events_scored": len(rows),
            "net_positive_price_only": len(net_positive),
            "ever_fee_green_price_only": len(ever_green),
            "executable_positive": sum(1 for row in rows if row.get("executable_positive")),
            "best_net_bps": max((to_float(row.get("net_bps")) for row in rows), default=0.0),
            "best_mfe_bps": max((to_float(row.get("mfe_bps")) for row in rows), default=0.0),
        },
        "by_product": by_product,
        "leadership_read": [
            "Read-only cache replay: no private endpoints and no order placement.",
            "Signal is causal: rolling prior high first, dump signal second, entry at the next cached ask.",
            "Rows are price-only until depth/fillability is joined; radar cache has bid/ask but not full order-book depth.",
        ],
        "rows": rows,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def write_reports(payload: dict[str, Any], *, json_path: Path, csv_path: Path, md_path: Path) -> None:
    write_json(json_path, payload)
    columns = [
        "product_id",
        "horizon_seconds",
        "elapsed_seconds",
        "dump_bps",
        "entry_spread_bps",
        "net_bps",
        "mfe_bps",
        "mae_bps",
        "first_green_seconds",
        "entry_ask",
        "exit_bid",
        "executable_positive",
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
        "# Kraken Vulture Reversal Replay",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Shadow only: `{payload.get('shadow_only')}`",
        f"- Products with cache: `{summary.get('products_with_cache')}`",
        f"- Events scored: `{summary.get('events_scored')}`",
        f"- Net-positive price-only: `{summary.get('net_positive_price_only')}`",
        f"- Ever fee-green price-only: `{summary.get('ever_fee_green_price_only')}`",
        f"- Executable positive: `{summary.get('executable_positive')}`",
        f"- Best net bps: `{to_float(summary.get('best_net_bps')):.4f}`",
        f"- Best MFE bps: `{to_float(summary.get('best_mfe_bps')):.4f}`",
        "",
        "## Read",
        "",
    ]
    lines.extend(f"- {item}" for item in payload.get("leadership_read") or [])
    lines.extend(
        [
            "",
            "## Product Summary",
            "",
            "| Product | Samples | Events | Net+ | Ever Green | Best Net bps | Best MFE bps |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for product_id, row in (payload.get("by_product") or {}).items():
        lines.append(
            "| {product_id} | {samples} | {events} | {net_positive} | {ever_fee_green} | {best_net_bps:.4f} | {best_mfe_bps:.4f} |".format(
                product_id=product_id,
                **row,
            )
        )
    lines.extend(
        [
            "",
            "## Top Events",
            "",
            "| Rank | Product | Horizon | Dump bps | Spread bps | Net bps | MFE bps | MAE bps | Blockers |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for idx, row in enumerate((payload.get("rows") or [])[:50], start=1):
        lines.append(
            "| {idx} | {product_id} | {horizon_seconds:.0f}s | {dump_bps:.4f} | {entry_spread_bps:.4f} | {net_bps:.4f} | {mfe_bps:.4f} | {mae_bps:.4f} | {blocker_text} |".format(
                idx=idx,
                **row,
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_float_list(raw: str) -> list[float]:
    out: list[float] = []
    for part in str(raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part:
            out.append(float(part))
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay causal Kraken spot dump-recovery vulture entries from cached bid/ask samples.")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--products", default=DEFAULT_PRODUCTS, help="Comma-separated products. Pass empty string to scan by quote currencies.")
    parser.add_argument("--quote-currencies", default="", help="Comma-separated quote filters used when --products is empty, e.g. USD.")
    parser.add_argument("--horizons", default="30,60,300,900")
    parser.add_argument("--lookback-samples", type=int, default=20)
    parser.add_argument("--min-dump-bps", type=float, default=500.0)
    parser.add_argument("--max-spread-bps", type=float, default=500.0)
    parser.add_argument("--taker-fee-bps", type=float, default=cfg.DEFAULT_TAKER_FEE_BPS)
    parser.add_argument("--start-usd", type=float, default=50.0)
    parser.add_argument("--min-net-bps", type=float, default=10.0)
    parser.add_argument("--cooldown-samples", type=int, default=5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_payload(
        client=KrakenSpotClient(),
        cache_path=Path(str(args.cache_path)),
        products=parse_products(args.products),
        quote_currencies={normalize_asset(part.strip()) for part in str(args.quote_currencies or "").split(",") if part.strip()},
        horizons=parse_float_list(args.horizons),
        lookback_samples=int(args.lookback_samples),
        min_dump_bps=float(args.min_dump_bps),
        max_spread_bps=float(args.max_spread_bps),
        taker_fee_bps=float(args.taker_fee_bps),
        start_usd=float(args.start_usd),
        min_net_bps=float(args.min_net_bps),
        cooldown_samples=int(args.cooldown_samples),
    )
    write_reports(
        payload,
        json_path=Path(str(args.json_path)),
        csv_path=Path(str(args.csv_path)),
        md_path=Path(str(args.md_path)),
    )
    print(json.dumps({"json_path": str(Path(str(args.json_path)).resolve()), "summary": payload.get("summary")}, indent=2))


if __name__ == "__main__":
    main()
