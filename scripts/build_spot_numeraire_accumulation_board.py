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
from kraken_spot_client import KrakenPair, KrakenSpotClient, normalize_asset, parse_pair, parse_ticker, to_float  # noqa: E402


DEFAULT_JSON_PATH = REPORTS / "spot_numeraire_accumulation_board.json"
DEFAULT_CSV_PATH = REPORTS / "spot_numeraire_accumulation_board.csv"
DEFAULT_MD_PATH = REPORTS / "spot_numeraire_accumulation_board.md"


@dataclass(frozen=True)
class DirectedEdge:
    from_asset: str
    to_asset: str
    product_id: str
    rest_pair: str
    action: str
    price: float
    rate_before_fee: float
    max_input_amount: float
    min_input_amount: float
    l1_depth_usd: float


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_csv_set(raw: str) -> set[str]:
    return {normalize_asset(part.strip()) for part in str(raw or "").split(",") if part.strip()}


def product_id_for_pair(pair: KrakenPair) -> str:
    return f"{pair.base}-{pair.quote}".upper()


def load_pairs(client: KrakenSpotClient, allowed_quotes: set[str]) -> dict[str, KrakenPair]:
    rows: dict[str, KrakenPair] = {}
    for rest_pair, payload in client.asset_pairs().items():
        if not isinstance(payload, dict):
            continue
        pair = parse_pair(str(rest_pair), payload)
        if pair is None:
            continue
        if pair.status.lower() not in {"online", "post_only", ""}:
            continue
        if allowed_quotes and pair.quote not in allowed_quotes:
            continue
        rows[product_id_for_pair(pair)] = pair
    return rows


def fetch_tickers(client: KrakenSpotClient, pairs: dict[str, KrakenPair], chunk_size: int) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    pair_values = list(pairs.values())
    by_rest = {pair.rest_pair: pair for pair in pair_values}
    by_alt = {pair.altname: pair for pair in pair_values}
    by_ws = {pair.wsname: pair for pair in pair_values}
    size = max(1, int(chunk_size))
    for idx in range(0, len(pair_values), size):
        chunk = pair_values[idx : idx + size]
        payload = client.ticker([pair.rest_pair for pair in chunk])
        for returned_pair, row in payload.items():
            pair = by_rest.get(returned_pair) or by_alt.get(returned_pair) or by_ws.get(returned_pair)
            if pair is None or not isinstance(row, dict):
                continue
            parsed = parse_ticker(pair.rest_pair, pair.wsname, row)
            if parsed is None:
                continue
            out[product_id_for_pair(pair)] = {
                "bid": parsed.bid,
                "ask": parsed.ask,
                "bid_size": parsed.bid_size,
                "ask_size": parsed.ask_size,
                "last": parsed.last,
                "volume_24h": parsed.volume_24h,
            }
    return out


def mid_price(book: dict[str, float]) -> float:
    bid = to_float(book.get("bid"))
    ask = to_float(book.get("ask"))
    return (bid + ask) / 2.0 if bid > 0.0 and ask > 0.0 else 0.0


def infer_usd_rates(
    pairs: dict[str, KrakenPair],
    books: dict[str, dict[str, float]],
    stable_assets: set[str],
) -> dict[str, float]:
    rates = {asset: 1.0 for asset in stable_assets}
    rates.setdefault("USD", 1.0)
    for _ in range(8):
        changed = False
        for product_id, pair in pairs.items():
            mid = mid_price(books.get(product_id, {}))
            if mid <= 0.0:
                continue
            base_known = pair.base in rates
            quote_known = pair.quote in rates
            if quote_known and not base_known:
                rates[pair.base] = mid * rates[pair.quote]
                changed = True
            elif base_known and not quote_known:
                rates[pair.quote] = rates[pair.base] / mid
                changed = True
        if not changed:
            break
    return rates


def build_edges(
    pairs: dict[str, KrakenPair],
    books: dict[str, dict[str, float]],
    usd_rates: dict[str, float],
) -> list[DirectedEdge]:
    edges: list[DirectedEdge] = []
    for product_id, pair in pairs.items():
        book = books.get(product_id, {})
        bid = to_float(book.get("bid"))
        ask = to_float(book.get("ask"))
        bid_size = to_float(book.get("bid_size"))
        ask_size = to_float(book.get("ask_size"))
        base_usd = to_float(usd_rates.get(pair.base))
        quote_usd = to_float(usd_rates.get(pair.quote))
        if bid <= 0.0 or ask <= 0.0:
            continue

        buy_quote_depth = ask * ask_size
        buy_min_quote = max(pair.cost_min, pair.order_min * ask)
        edges.append(
            DirectedEdge(
                from_asset=pair.quote,
                to_asset=pair.base,
                product_id=product_id,
                rest_pair=pair.rest_pair,
                action="buy_at_ask",
                price=ask,
                rate_before_fee=1.0 / ask,
                max_input_amount=buy_quote_depth,
                min_input_amount=buy_min_quote,
                l1_depth_usd=buy_quote_depth * quote_usd if quote_usd > 0.0 else 0.0,
            )
        )

        sell_min_base = max(pair.order_min, pair.cost_min / bid if bid > 0.0 else 0.0)
        edges.append(
            DirectedEdge(
                from_asset=pair.base,
                to_asset=pair.quote,
                product_id=product_id,
                rest_pair=pair.rest_pair,
                action="sell_at_bid",
                price=bid,
                rate_before_fee=bid,
                max_input_amount=bid_size,
                min_input_amount=sell_min_base,
                l1_depth_usd=bid_size * base_usd if base_usd > 0.0 else 0.0,
            )
        )
    return edges


def apply_route(
    route: list[DirectedEdge],
    *,
    start_units: float,
    start_usd: float,
    taker_fee_bps: float,
) -> dict[str, Any]:
    amount = float(start_units)
    blockers: list[str] = []
    min_l1_depth_usd = min((edge.l1_depth_usd for edge in route), default=0.0)
    fee_multiplier = 1.0 - (float(taker_fee_bps) / 10000.0)
    for idx, edge in enumerate(route, start=1):
        if amount < edge.min_input_amount:
            blockers.append(f"leg{idx}_below_min:{edge.product_id}")
        if edge.max_input_amount > 0.0 and amount > edge.max_input_amount:
            blockers.append(f"leg{idx}_exceeds_l1_depth:{edge.product_id}")
        amount = amount * edge.rate_before_fee * fee_multiplier
    return {
        "final_units": amount,
        "min_l1_depth_usd": min_l1_depth_usd,
        "blockers": blockers,
        "start_usd": start_usd,
    }


def route_key(route: list[DirectedEdge]) -> str:
    assets = [route[0].from_asset]
    assets.extend(edge.to_asset for edge in route)
    return "->".join(assets)


def route_leg_text(route: list[DirectedEdge]) -> str:
    return " | ".join(f"{edge.action}:{edge.product_id}@{edge.price:.12g}" for edge in route)


def candidate_routes(edges: list[DirectedEdge], numeraires: set[str], max_routes: int) -> list[list[DirectedEdge]]:
    by_from: dict[str, list[DirectedEdge]] = {}
    for edge in edges:
        by_from.setdefault(edge.from_asset, []).append(edge)

    routes: list[list[DirectedEdge]] = []
    seen: set[tuple[str, ...]] = set()
    for start in sorted(numeraires):
        for edge1 in by_from.get(start, []):
            for edge2 in by_from.get(edge1.to_asset, []):
                if edge2.to_asset == start:
                    key = tuple(f"{edge.rest_pair}:{edge.action}" for edge in (edge1, edge2))
                    if key not in seen:
                        seen.add(key)
                        routes.append([edge1, edge2])
                for edge3 in by_from.get(edge2.to_asset, []):
                    if edge3.to_asset != start:
                        continue
                    if len({edge1.rest_pair, edge2.rest_pair, edge3.rest_pair}) < 3:
                        continue
                    key = tuple(f"{edge.rest_pair}:{edge.action}" for edge in (edge1, edge2, edge3))
                    if key in seen:
                        continue
                    seen.add(key)
                    routes.append([edge1, edge2, edge3])
                    if len(routes) >= max_routes:
                        return routes
    return routes


def score_routes(
    routes: list[list[DirectedEdge]],
    *,
    usd_rates: dict[str, float],
    start_usd: float,
    taker_fee_bps: float,
    min_net_bps: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for route in routes:
        start_asset = route[0].from_asset
        start_rate = to_float(usd_rates.get(start_asset))
        if start_rate <= 0.0:
            continue
        start_units = float(start_usd) / start_rate
        result = apply_route(route, start_units=start_units, start_usd=start_usd, taker_fee_bps=taker_fee_bps)
        final_units = to_float(result.get("final_units"))
        numeraire_edge_bps = ((final_units / start_units) - 1.0) * 10000.0 if start_units > 0.0 else 0.0
        final_usd_mark = final_units * start_rate
        usd_mark_edge_bps = ((final_usd_mark / float(start_usd)) - 1.0) * 10000.0 if start_usd > 0.0 else 0.0
        blockers = list(result.get("blockers") or [])
        if numeraire_edge_bps < float(min_net_bps):
            blockers.append("net_edge_below_threshold")
        rows.append(
            {
                "start_numeraire": start_asset,
                "route": route_key(route),
                "legs": route_leg_text(route),
                "leg_count": len(route),
                "start_units": round(start_units, 12),
                "final_units": round(final_units, 12),
                "numeraire_edge_bps": round(numeraire_edge_bps, 6),
                "usd_mark_edge_bps": round(usd_mark_edge_bps, 6),
                "start_usd": round(float(start_usd), 6),
                "final_usd_mark": round(final_usd_mark, 6),
                "min_l1_depth_usd": round(to_float(result.get("min_l1_depth_usd")), 6),
                "fee_model": f"taker_bid_ask_{float(taker_fee_bps):.4f}bps_per_leg",
                "executable_positive": not blockers,
                "blockers": blockers,
                "blocker_text": ", ".join(blockers),
            }
        )
    rows.sort(
        key=lambda row: (
            bool(row.get("executable_positive")),
            to_float(row.get("numeraire_edge_bps")),
            to_float(row.get("min_l1_depth_usd")),
        ),
        reverse=True,
    )
    return rows


def build_payload(
    *,
    client: KrakenSpotClient,
    numeraires: set[str],
    quotes: set[str],
    stable_assets: set[str],
    start_usd: float,
    taker_fee_bps: float,
    min_net_bps: float,
    chunk_size: int,
    max_routes: int,
) -> dict[str, Any]:
    pairs = load_pairs(client, quotes)
    books = fetch_tickers(client, pairs, chunk_size)
    usd_rates = infer_usd_rates(pairs, books, stable_assets)
    edges = build_edges(pairs, books, usd_rates)
    routes = candidate_routes(edges, numeraires, max_routes)
    rows = score_routes(
        routes,
        usd_rates=usd_rates,
        start_usd=start_usd,
        taker_fee_bps=taker_fee_bps,
        min_net_bps=min_net_bps,
    )
    positive_rows = [row for row in rows if to_float(row.get("numeraire_edge_bps")) > 0.0]
    executable_rows = [row for row in rows if row.get("executable_positive")]
    return {
        "generated_at": utc_now_iso(),
        "mode": "spot_numeraire_accumulation_board",
        "venue": "kraken",
        "shadow_only": True,
        "places_orders": False,
        "parameters": {
            "numeraires": sorted(numeraires),
            "quotes": sorted(quotes),
            "stable_assets": sorted(stable_assets),
            "start_usd": float(start_usd),
            "taker_fee_bps": float(taker_fee_bps),
            "min_net_bps": float(min_net_bps),
            "chunk_size": int(chunk_size),
            "max_routes": int(max_routes),
        },
        "summary": {
            "pairs_scanned": len(pairs),
            "books_loaded": len(books),
            "directed_edges": len(edges),
            "routes_scored": len(rows),
            "positive_routes_after_fees": len(positive_rows),
            "executable_positive_routes": len(executable_rows),
            "best_numeraire_edge_bps": max((to_float(row.get("numeraire_edge_bps")) for row in rows), default=0.0),
        },
        "leadership_read": [
            "This is read-only public-market accounting: no private endpoints and no order placement.",
            "Positive rows are instantaneous same-venue route math only; stale books, queue position, and leg timing can erase them live.",
            "USD mark is shown separately from target-numeraire units so accumulation does not masquerade as USD profit.",
        ],
        "usd_rates": {key: round(value, 12) for key, value in sorted(usd_rates.items()) if key in numeraires or key in stable_assets},
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
        "leg_count",
        "numeraire_edge_bps",
        "usd_mark_edge_bps",
        "start_usd",
        "final_usd_mark",
        "min_l1_depth_usd",
        "executable_positive",
        "blocker_text",
        "legs",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload.get("rows") or []:
            writer.writerow({column: row.get(column, "") for column in columns})

    summary = payload.get("summary") or {}
    lines = [
        "# Spot Numeraire Accumulation Board",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Venue: `{payload.get('venue')}`",
        f"- Shadow only: `{payload.get('shadow_only')}`",
        f"- Pairs scanned: `{summary.get('pairs_scanned')}`",
        f"- Routes scored: `{summary.get('routes_scored')}`",
        f"- Positive after fees: `{summary.get('positive_routes_after_fees')}`",
        f"- Executable positive: `{summary.get('executable_positive_routes')}`",
        f"- Best net edge bps: `{to_float(summary.get('best_numeraire_edge_bps')):.4f}`",
        "",
        "## Read",
        "",
    ]
    lines.extend(f"- {item}" for item in payload.get("leadership_read") or [])
    lines.extend(
        [
            "",
            "## Top Routes",
            "",
            "| Rank | Start | Route | Legs | Net bps | USD mark bps | L1 depth USD | Executable | Blockers |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for idx, row in enumerate((payload.get("rows") or [])[:50], start=1):
        lines.append(
            "| {idx} | {start_numeraire} | {route} | {leg_count} | {numeraire_edge_bps:.4f} | {usd_mark_edge_bps:.4f} | {min_l1_depth_usd:.2f} | {executable_positive} | {blocker_text} |".format(
                idx=idx,
                **row,
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only spot route board for USD/BTC/ETH/SOL unit accumulation.")
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--numeraires", default="USD,USDT,USDC,BTC,ETH,SOL")
    parser.add_argument("--quotes", default="USD,USDT,USDC,BTC,ETH,SOL")
    parser.add_argument("--stable-assets", default="USD,USDT,USDC")
    parser.add_argument("--start-usd", type=float, default=50.0)
    parser.add_argument("--taker-fee-bps", type=float, default=cfg.DEFAULT_TAKER_FEE_BPS)
    parser.add_argument("--min-net-bps", type=float, default=1.0)
    parser.add_argument("--chunk-size", type=int, default=50)
    parser.add_argument("--max-routes", type=int, default=20000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_payload(
        client=KrakenSpotClient(),
        numeraires=parse_csv_set(args.numeraires),
        quotes=parse_csv_set(args.quotes),
        stable_assets=parse_csv_set(args.stable_assets),
        start_usd=float(args.start_usd),
        taker_fee_bps=float(args.taker_fee_bps),
        min_net_bps=float(args.min_net_bps),
        chunk_size=int(args.chunk_size),
        max_routes=int(args.max_routes),
    )
    write_reports(
        payload,
        json_path=Path(str(args.json_path)),
        csv_path=Path(str(args.csv_path)),
        md_path=Path(str(args.md_path)),
    )
    print(
        json.dumps(
            {
                "json_path": str(Path(str(args.json_path)).resolve()),
                "md_path": str(Path(str(args.md_path)).resolve()),
                "summary": payload.get("summary"),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
