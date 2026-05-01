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
from build_spot_numeraire_accumulation_board import (  # noqa: E402
    DirectedEdge,
    candidate_routes,
    parse_csv_set,
    product_id_for_pair,
)
from kraken_spot_client import KrakenPair, KrakenSpotClient, normalize_asset, parse_pair, to_float  # noqa: E402


DEFAULT_CACHE_PATH = REPORTS / "cache" / "kraken_spot_live_radar_ticks.json"
DEFAULT_JSON_PATH = REPORTS / "spot_route_convergence_replay.json"
DEFAULT_CSV_PATH = REPORTS / "spot_route_convergence_replay.csv"
DEFAULT_MD_PATH = REPORTS / "spot_route_convergence_replay.md"


@dataclass(frozen=True)
class CachedBook:
    bid: float
    ask: float
    ts: float


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_pairs(client: KrakenSpotClient, quotes: set[str]) -> dict[str, KrakenPair]:
    out: dict[str, KrakenPair] = {}
    for rest_pair, payload in client.asset_pairs().items():
        if not isinstance(payload, dict):
            continue
        pair = parse_pair(str(rest_pair), payload)
        if pair is None:
            continue
        if pair.status.lower() not in {"online", "post_only", ""}:
            continue
        if quotes and pair.quote not in quotes:
            continue
        out[pair.rest_pair] = pair
    return out


def cached_books_by_time(cache: dict[str, Any], pairs: dict[str, KrakenPair]) -> dict[float, dict[str, CachedBook]]:
    samples = cache.get("samples") if isinstance(cache, dict) else {}
    if not isinstance(samples, dict):
        return {}
    by_time: dict[float, dict[str, CachedBook]] = {}
    for rest_pair, rows in samples.items():
        pair = pairs.get(str(rest_pair))
        if pair is None or not isinstance(rows, list):
            continue
        product_id = product_id_for_pair(pair)
        for row in rows:
            if not isinstance(row, dict):
                continue
            bid = to_float(row.get("bid"))
            ask = to_float(row.get("ask"))
            ts = to_float(row.get("ts"))
            if bid <= 0.0 or ask <= 0.0 or ts <= 0.0:
                continue
            by_time.setdefault(ts, {})[product_id] = CachedBook(bid=bid, ask=ask, ts=ts)
    return by_time


def infer_usd_rates_at(pairs_by_product: dict[str, KrakenPair], books: dict[str, CachedBook], stable_assets: set[str]) -> dict[str, float]:
    rates = {asset: 1.0 for asset in stable_assets}
    rates.setdefault("USD", 1.0)
    for _ in range(8):
        changed = False
        for product_id, pair in pairs_by_product.items():
            book = books.get(product_id)
            if book is None:
                continue
            mid = (book.bid + book.ask) / 2.0 if book.bid > 0.0 and book.ask > 0.0 else 0.0
            if mid <= 0.0:
                continue
            if pair.quote in rates and pair.base not in rates:
                rates[pair.base] = mid * rates[pair.quote]
                changed = True
            elif pair.base in rates and pair.quote not in rates:
                rates[pair.quote] = rates[pair.base] / mid
                changed = True
        if not changed:
            break
    return rates


def build_edges_at(
    pairs_by_product: dict[str, KrakenPair],
    books: dict[str, CachedBook],
    usd_rates: dict[str, float],
) -> list[DirectedEdge]:
    edges: list[DirectedEdge] = []
    for product_id, pair in pairs_by_product.items():
        book = books.get(product_id)
        if book is None:
            continue
        base_usd = to_float(usd_rates.get(pair.base))
        quote_usd = to_float(usd_rates.get(pair.quote))
        buy_quote_depth = 0.0
        sell_base_depth = 0.0
        buy_min_quote = max(pair.cost_min, pair.order_min * book.ask)
        sell_min_base = max(pair.order_min, pair.cost_min / book.bid if book.bid > 0.0 else 0.0)
        edges.append(
            DirectedEdge(
                from_asset=pair.quote,
                to_asset=pair.base,
                product_id=product_id,
                rest_pair=pair.rest_pair,
                action="buy_at_ask",
                price=book.ask,
                rate_before_fee=1.0 / book.ask,
                max_input_amount=buy_quote_depth,
                min_input_amount=buy_min_quote,
                l1_depth_usd=0.0 * quote_usd,
            )
        )
        edges.append(
            DirectedEdge(
                from_asset=pair.base,
                to_asset=pair.quote,
                product_id=product_id,
                rest_pair=pair.rest_pair,
                action="sell_at_bid",
                price=book.bid,
                rate_before_fee=book.bid,
                max_input_amount=sell_base_depth,
                min_input_amount=sell_min_base,
                l1_depth_usd=0.0 * base_usd,
            )
        )
    return edges


def route_key(route: list[DirectedEdge]) -> str:
    assets = [route[0].from_asset]
    assets.extend(edge.to_asset for edge in route)
    return "->".join(assets)


def leg_key(edge: DirectedEdge) -> str:
    return f"{edge.action}:{edge.product_id}"


def apply_edges(start_units: float, edges: list[DirectedEdge], fee_bps: float) -> float:
    amount = float(start_units)
    fee_mult = 1.0 - (float(fee_bps) / 10000.0)
    for edge in edges:
        amount = amount * edge.rate_before_fee * fee_mult
    return amount


def route_edge_bps(route: list[DirectedEdge], fee_bps: float) -> float:
    final = apply_edges(1.0, route, fee_bps)
    return (final - 1.0) * 10000.0


def find_future_time(times: list[float], start_index: int, horizon_seconds: float) -> int | None:
    target = times[start_index] + float(horizon_seconds)
    best_idx: int | None = None
    best_delta: float | None = None
    for idx in range(start_index + 1, len(times)):
        delta = abs(times[idx] - target)
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_idx = idx
        if times[idx] >= target:
            break
    return best_idx


def keyed_edges(edges: list[DirectedEdge]) -> dict[str, DirectedEdge]:
    return {leg_key(edge): edge for edge in edges}


def route_from_keys(edge_map: dict[str, DirectedEdge], keys: list[str]) -> list[DirectedEdge] | None:
    out: list[DirectedEdge] = []
    for key in keys:
        edge = edge_map.get(key)
        if edge is None:
            return None
        out.append(edge)
    return out


def replay_convergence(
    *,
    pairs_by_product: dict[str, KrakenPair],
    by_time: dict[float, dict[str, CachedBook]],
    numeraires: set[str],
    stable_assets: set[str],
    horizons: list[float],
    start_usd: float,
    taker_fee_bps: float,
    signal_fee_bps: float,
    min_signal_gap_bps: float,
    min_net_bps: float,
    max_events: int,
) -> list[dict[str, Any]]:
    times = sorted(by_time)
    rows: list[dict[str, Any]] = []
    for time_index, ts in enumerate(times):
        books = by_time[ts]
        rates = infer_usd_rates_at(pairs_by_product, books, stable_assets)
        edges = build_edges_at(pairs_by_product, books, rates)
        routes = candidate_routes(edges, numeraires, max_routes=50000)
        for route in routes:
            signal_gap_bps = route_edge_bps(route, signal_fee_bps)
            if signal_gap_bps < float(min_signal_gap_bps):
                continue
            start_asset = route[0].from_asset
            start_rate = to_float(rates.get(start_asset))
            if start_rate <= 0.0:
                continue
            start_units = float(start_usd) / start_rate
            first_leg = route[0]
            remaining_keys = [leg_key(edge) for edge in route[1:]]
            after_entry_units = apply_edges(start_units, [first_leg], taker_fee_bps)
            instant_net_bps = route_edge_bps(route, taker_fee_bps)
            route_keys = [leg_key(edge) for edge in route]
            for horizon in horizons:
                future_index = find_future_time(times, time_index, horizon)
                if future_index is None:
                    continue
                future_ts = times[future_index]
                future_books = by_time[future_ts]
                future_rates = infer_usd_rates_at(pairs_by_product, future_books, stable_assets)
                future_edges = build_edges_at(pairs_by_product, future_books, future_rates)
                future_edge_map = keyed_edges(future_edges)
                remaining_route = route_from_keys(future_edge_map, remaining_keys)
                full_future_route = route_from_keys(future_edge_map, route_keys)
                if remaining_route is None or full_future_route is None:
                    continue
                final_units = apply_edges(after_entry_units, remaining_route, taker_fee_bps)
                net_bps = ((final_units / start_units) - 1.0) * 10000.0 if start_units > 0.0 else 0.0
                full_future_signal_bps = route_edge_bps(full_future_route, signal_fee_bps)
                convergence_bps = signal_gap_bps - full_future_signal_bps
                blockers: list[str] = ["depth_unavailable_in_radar_cache", "fillability_unproven"]
                if net_bps < float(min_net_bps):
                    blockers.append("net_edge_below_threshold")
                if convergence_bps <= 0.0:
                    blockers.append("no_gap_convergence")
                rows.append(
                    {
                        "signal_ts": ts,
                        "future_ts": future_ts,
                        "elapsed_seconds": round(future_ts - ts, 3),
                        "horizon_seconds": float(horizon),
                        "start_numeraire": start_asset,
                        "route": route_key(route),
                        "entry_leg": leg_key(first_leg),
                        "exit_legs": " | ".join(remaining_keys),
                        "signal_gap_bps": round(signal_gap_bps, 6),
                        "instant_net_bps": round(instant_net_bps, 6),
                        "future_signal_gap_bps": round(full_future_signal_bps, 6),
                        "gap_convergence_bps": round(convergence_bps, 6),
                        "staged_net_bps": round(net_bps, 6),
                        "start_units": round(start_units, 12),
                        "final_units": round(final_units, 12),
                        "start_usd": round(float(start_usd), 6),
                        "executable_positive": not blockers,
                        "blockers": blockers,
                        "blocker_text": ", ".join(blockers),
                    }
                )
                if len(rows) >= int(max_events):
                    return sort_rows(rows)
    return sort_rows(rows)


def sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows.sort(
        key=lambda row: (
            bool(row.get("executable_positive")),
            to_float(row.get("staged_net_bps")),
            to_float(row.get("gap_convergence_bps")),
            to_float(row.get("signal_gap_bps")),
        ),
        reverse=True,
    )
    return rows


def build_payload(
    *,
    client: KrakenSpotClient,
    cache_path: Path,
    numeraires: set[str],
    quotes: set[str],
    stable_assets: set[str],
    horizons: list[float],
    start_usd: float,
    taker_fee_bps: float,
    signal_fee_bps: float,
    min_signal_gap_bps: float,
    min_net_bps: float,
    max_events: int,
) -> dict[str, Any]:
    pairs_by_rest = load_pairs(client, quotes)
    pairs_by_product = {product_id_for_pair(pair): pair for pair in pairs_by_rest.values()}
    cache = load_json(cache_path)
    by_time = cached_books_by_time(cache, pairs_by_rest)
    rows = replay_convergence(
        pairs_by_product=pairs_by_product,
        by_time=by_time,
        numeraires=numeraires,
        stable_assets=stable_assets,
        horizons=horizons,
        start_usd=start_usd,
        taker_fee_bps=taker_fee_bps,
        signal_fee_bps=signal_fee_bps,
        min_signal_gap_bps=min_signal_gap_bps,
        min_net_bps=min_net_bps,
        max_events=max_events,
    )
    net_positive = [row for row in rows if to_float(row.get("staged_net_bps")) >= float(min_net_bps)]
    converged = [row for row in rows if to_float(row.get("gap_convergence_bps")) > 0.0]
    return {
        "generated_at": utc_now_iso(),
        "mode": "spot_route_convergence_replay",
        "venue": "kraken",
        "shadow_only": True,
        "places_orders": False,
        "parameters": {
            "cache_path": str(cache_path),
            "numeraires": sorted(numeraires),
            "quotes": sorted(quotes),
            "stable_assets": sorted(stable_assets),
            "horizons": horizons,
            "start_usd": float(start_usd),
            "taker_fee_bps": float(taker_fee_bps),
            "signal_fee_bps": float(signal_fee_bps),
            "min_signal_gap_bps": float(min_signal_gap_bps),
            "min_net_bps": float(min_net_bps),
            "max_events": int(max_events),
        },
        "summary": {
            "cache_times": len(by_time),
            "pairs_loaded": len(pairs_by_rest),
            "events_scored": len(rows),
            "net_positive_price_only": len(net_positive),
            "gap_converged": len(converged),
            "executable_positive": sum(1 for row in rows if row.get("executable_positive")),
            "best_staged_net_bps": max((to_float(row.get("staged_net_bps")) for row in rows), default=0.0),
            "best_gap_convergence_bps": max((to_float(row.get("gap_convergence_bps")) for row in rows), default=0.0),
        },
        "leadership_read": [
            "Read-only cache replay: no private endpoints and no order placement.",
            "Rows are price-only until depth/fillability is joined; radar cache has bid/ask but not full L1 size.",
            "A net-positive row is a candidate for deeper tape capture, not a live signal.",
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
        "start_numeraire",
        "route",
        "entry_leg",
        "exit_legs",
        "horizon_seconds",
        "elapsed_seconds",
        "signal_gap_bps",
        "instant_net_bps",
        "future_signal_gap_bps",
        "gap_convergence_bps",
        "staged_net_bps",
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
        "# Spot Route Convergence Replay",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Venue: `{payload.get('venue')}`",
        f"- Shadow only: `{payload.get('shadow_only')}`",
        f"- Cache times: `{summary.get('cache_times')}`",
        f"- Events scored: `{summary.get('events_scored')}`",
        f"- Price-only net positive: `{summary.get('net_positive_price_only')}`",
        f"- Executable positive: `{summary.get('executable_positive')}`",
        f"- Best staged net bps: `{to_float(summary.get('best_staged_net_bps')):.4f}`",
        f"- Best gap convergence bps: `{to_float(summary.get('best_gap_convergence_bps')):.4f}`",
        "",
        "## Read",
        "",
    ]
    lines.extend(f"- {item}" for item in payload.get("leadership_read") or [])
    lines.extend(
        [
            "",
            "## Top Price-Only Candidates",
            "",
            "| Rank | Start | Route | Horizon | Signal bps | Future bps | Convergence bps | Staged net bps | Blockers |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for idx, row in enumerate((payload.get("rows") or [])[:50], start=1):
        lines.append(
            "| {idx} | {start_numeraire} | {route} | {horizon_seconds:.0f}s | {signal_gap_bps:.4f} | {future_signal_gap_bps:.4f} | {gap_convergence_bps:.4f} | {staged_net_bps:.4f} | {blocker_text} |".format(
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
    parser = argparse.ArgumentParser(description="Replay cached Kraken route gaps to see whether they converge into fee-paid unit gains.")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--numeraires", default="USD,USDT,USDC,BTC,ETH,SOL")
    parser.add_argument("--quotes", default="USD,USDT,USDC,BTC,ETH,SOL")
    parser.add_argument("--stable-assets", default="USD,USDT,USDC")
    parser.add_argument("--horizons", default="5,15,30,60")
    parser.add_argument("--start-usd", type=float, default=50.0)
    parser.add_argument("--taker-fee-bps", type=float, default=cfg.DEFAULT_TAKER_FEE_BPS)
    parser.add_argument("--signal-fee-bps", type=float, default=0.0, help="Fee bps used only for detecting route gap events.")
    parser.add_argument("--min-signal-gap-bps", type=float, default=1.0)
    parser.add_argument("--min-net-bps", type=float, default=1.0)
    parser.add_argument("--max-events", type=int, default=20000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_payload(
        client=KrakenSpotClient(),
        cache_path=Path(str(args.cache_path)),
        numeraires=parse_csv_set(args.numeraires),
        quotes=parse_csv_set(args.quotes),
        stable_assets=parse_csv_set(args.stable_assets),
        horizons=parse_float_list(args.horizons),
        start_usd=float(args.start_usd),
        taker_fee_bps=float(args.taker_fee_bps),
        signal_fee_bps=float(args.signal_fee_bps),
        min_signal_gap_bps=float(args.min_signal_gap_bps),
        min_net_bps=float(args.min_net_bps),
        max_events=int(args.max_events),
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
