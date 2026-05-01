#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from kraken_spot_client import KrakenPair, KrakenSpotClient, parse_pair, to_float  # noqa: E402


REPORTS = ROOT / "reports"
DEFAULT_TAIL_AUDIT_PATH = REPORTS / "coinbase_spot_tail_fastgreen_compression_audit.json"
DEFAULT_COINBASE_RADAR_PATH = REPORTS / "coinbase_spot_live_radar.json"
DEFAULT_COINBASE_STRATEGY_PATH = REPORTS / "coinbase_spot_machinegun_strategy_board.json"
DEFAULT_KRAKEN_RADAR_PATH = REPORTS / "kraken_spot_live_radar.json"
DEFAULT_KRAKEN_VELOCITY_PATH = REPORTS / "kraken_spot_money_velocity_board.json"
DEFAULT_JSON_PATH = REPORTS / "coinbase_kraken_spot_overlap_board.json"
DEFAULT_CSV_PATH = REPORTS / "coinbase_kraken_spot_overlap_board.csv"
DEFAULT_MD_PATH = REPORTS / "coinbase_kraken_spot_overlap_board.md"


QUOTE_PRIORITY = {"USD": 0, "USDC": 1, "USDT": 2}
ASSET_ALIASES = {
    "XBT": "BTC",
    "XXBT": "BTC",
    "XDG": "DOGE",
    "XXDG": "DOGE",
    "ZUSD": "USD",
    "ZUSDC": "USDC",
    "USDC.M": "USDC",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def normalize_asset(value: Any) -> str:
    text = str(value or "").upper().strip()
    return ASSET_ALIASES.get(text, text)


def split_product_id(product_id: Any) -> tuple[str, str]:
    text = str(product_id or "").upper().replace("/", "-").strip()
    if "-" in text:
        base, quote = text.rsplit("-", 1)
        return normalize_asset(base), normalize_asset(quote)
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if text.endswith(quote) and len(text) > len(quote):
            return normalize_asset(text[: -len(quote)]), normalize_asset(quote)
    return normalize_asset(text), ""


def product_id_from_base_quote(base: Any, quote: Any) -> str:
    base_norm = normalize_asset(base)
    quote_norm = normalize_asset(quote)
    return f"{base_norm}-{quote_norm}" if base_norm and quote_norm else base_norm


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Coinbase spot candidates to the executable Kraken spot universe.")
    parser.add_argument("--tail-audit-path", default=str(DEFAULT_TAIL_AUDIT_PATH))
    parser.add_argument("--coinbase-radar-path", default=str(DEFAULT_COINBASE_RADAR_PATH))
    parser.add_argument("--coinbase-strategy-path", default=str(DEFAULT_COINBASE_STRATEGY_PATH))
    parser.add_argument("--kraken-radar-path", default=str(DEFAULT_KRAKEN_RADAR_PATH))
    parser.add_argument("--kraken-velocity-path", default=str(DEFAULT_KRAKEN_VELOCITY_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--quotes", default="USD,USDC,USDT")
    parser.add_argument("--max-tail-rows", type=int, default=200)
    return parser.parse_args()


def quote_set(value: str) -> set[str]:
    return {normalize_asset(item) for item in str(value or "").split(",") if item.strip()}


def kraken_pairs_from_client(quotes: set[str]) -> list[KrakenPair]:
    client = KrakenSpotClient()
    pairs: list[KrakenPair] = []
    for rest_pair, row in client.asset_pairs().items():
        pair = parse_pair(rest_pair, row)
        if not pair:
            continue
        if pair.status not in {"online", ""}:
            continue
        if normalize_asset(pair.quote) not in quotes:
            continue
        pairs.append(pair)
    return pairs


def kraken_catalog_from_pairs(pairs: list[KrakenPair]) -> dict[str, list[dict[str, Any]]]:
    by_base: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pair in pairs:
        base = normalize_asset(pair.base)
        quote = normalize_asset(pair.quote)
        if not base or not quote:
            continue
        by_base[base].append(
            {
                "product_id": product_id_from_base_quote(base, quote),
                "wsname": pair.wsname,
                "rest_pair": pair.rest_pair,
                "base": base,
                "quote": quote,
                "order_min": pair.order_min,
                "cost_min": pair.cost_min,
            }
        )
    for rows in by_base.values():
        rows.sort(key=lambda row: (QUOTE_PRIORITY.get(str(row.get("quote")), 99), str(row.get("product_id"))))
    return dict(by_base)


def rows_by_product(payload: Any) -> dict[str, dict[str, Any]]:
    rows = payload.get("rows") if isinstance(payload, dict) else []
    out: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        product_id = str(row.get("product_id") or "")
        if product_id:
            out[product_id.upper()] = row
    return out


def candidate_records(
    *,
    tail_audit: dict[str, Any],
    coinbase_radar: dict[str, Any],
    coinbase_strategy: dict[str, Any],
    max_tail_rows: int,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for idx, row in enumerate((tail_audit.get("top_one_per_time") or [])[: max(0, int(max_tail_rows))], start=1):
        product_id = str(row.get("product_id") or "")
        if not product_id:
            continue
        records.append(
            {
                "source": "tail_fastgreen_top_one_per_time",
                "source_rank": idx,
                "product_id": product_id,
                "score": round(to_float(row.get("combined_prob")), 8),
                "tail_prob": round(to_float(row.get("tail_prob")), 8),
                "fast_green_prob": round(to_float(row.get("fast_green_prob")), 8),
                "coinbase_net_pct": round(to_float(row.get("net_pct")), 6),
                "signal_state": "historical_compressed",
            }
        )
    for idx, row in enumerate(coinbase_radar.get("rows") or [], start=1):
        state = str(row.get("signal_state") or "")
        if state not in {"live_hot", "building"}:
            continue
        product_id = str(row.get("product_id") or "")
        if product_id:
            records.append(
                {
                    "source": "coinbase_live_radar",
                    "source_rank": idx,
                    "product_id": product_id,
                    "score": round(to_float(row.get("velocity_score")), 8),
                    "coinbase_net_pct": 0.0,
                    "signal_state": state,
                }
            )
    for idx, row in enumerate(coinbase_strategy.get("rows") or [], start=1):
        product_id = str(row.get("product_id") or "")
        if product_id:
            records.append(
                {
                    "source": "coinbase_machinegun_strategy_board",
                    "source_rank": idx,
                    "product_id": product_id,
                    "score": round(to_float(row.get("machinegun_score")), 8),
                    "coinbase_net_pct": round(to_float(row.get("edge_over_hurdle_pct")), 6),
                    "signal_state": str(row.get("hurdle_state") or ""),
                }
            )
    return records


def merge_candidates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_product: dict[str, dict[str, Any]] = {}
    sources_by_product: dict[str, set[str]] = defaultdict(set)
    for row in records:
        product_id = str(row.get("product_id") or "").upper()
        if not product_id:
            continue
        sources_by_product[product_id].add(str(row.get("source") or "unknown"))
        current = by_product.get(product_id)
        if current is None or to_float(row.get("score")) > to_float(current.get("score")):
            by_product[product_id] = dict(row)
    merged = []
    for product_id, row in by_product.items():
        base, quote = split_product_id(product_id)
        out = dict(row)
        out["product_id"] = product_id
        out["base"] = base
        out["quote"] = quote
        out["sources"] = ",".join(sorted(sources_by_product[product_id]))
        merged.append(out)
    merged.sort(key=lambda row: (to_float(row.get("score")), to_float(row.get("coinbase_net_pct"))), reverse=True)
    return merged


def build_rows(
    candidates: list[dict[str, Any]],
    kraken_catalog: dict[str, list[dict[str, Any]]],
    kraken_radar: dict[str, dict[str, Any]],
    kraken_velocity: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, candidate in enumerate(candidates, start=1):
        base = str(candidate.get("base") or "")
        matches = kraken_catalog.get(base) or []
        preferred = matches[0] if matches else {}
        kraken_product_id = str(preferred.get("product_id") or "")
        radar_row = kraken_radar.get(kraken_product_id) if kraken_product_id else None
        velocity_row = kraken_velocity.get(kraken_product_id) if kraken_product_id else None
        if velocity_row:
            route_state = "kraken_velocity_board"
        elif radar_row:
            route_state = "kraken_live_radar"
        elif matches:
            route_state = "kraken_listed_not_in_current_radar"
        else:
            route_state = "coinbase_only_no_kraken_spot_match"
        rows.append(
            {
                "rank": idx,
                "product_id": candidate.get("product_id") or "",
                "base": base,
                "quote": candidate.get("quote") or "",
                "sources": candidate.get("sources") or candidate.get("source") or "",
                "candidate_score": round(to_float(candidate.get("score")), 8),
                "tail_prob": round(to_float(candidate.get("tail_prob")), 8),
                "fast_green_prob": round(to_float(candidate.get("fast_green_prob")), 8),
                "coinbase_net_pct": round(to_float(candidate.get("coinbase_net_pct")), 6),
                "coinbase_signal_state": candidate.get("signal_state") or "",
                "kraken_route_state": route_state,
                "kraken_product_id": kraken_product_id,
                "kraken_quotes": ",".join(str(item.get("quote") or "") for item in matches),
                "kraken_signal_state": (radar_row or {}).get("signal_state") or "",
                "kraken_spread_bps": round(to_float((radar_row or velocity_row or {}).get("spread_bps")), 4),
                "kraken_edge_bps": round(to_float((velocity_row or {}).get("kraken_edge_bps")), 6),
                "kraken_verdict": (velocity_row or {}).get("verdict") or "",
                "can_trade_100": bool((radar_row or {}).get("can_trade_starting_cash")) if radar_row else bool(matches),
                "min_notional_usd": round(to_float((radar_row or preferred or {}).get("min_notional_usd") or preferred.get("cost_min")), 6),
            }
        )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    kraken_listed = [row for row in rows if str(row.get("kraken_route_state") or "").startswith("kraken_")]
    radar = [row for row in rows if row.get("kraken_route_state") in {"kraken_live_radar", "kraken_velocity_board"}]
    velocity = [row for row in rows if row.get("kraken_route_state") == "kraken_velocity_board"]
    coinbase_only = [row for row in rows if row.get("kraken_route_state") == "coinbase_only_no_kraken_spot_match"]
    fee_flip = [row for row in velocity if row.get("kraken_verdict") == "kraken_fee_flip_candidate"]
    source_breakdown: dict[str, dict[str, Any]] = {}
    for row in rows:
        for source in [item for item in str(row.get("sources") or "").split(",") if item]:
            bucket = source_breakdown.setdefault(
                source,
                {
                    "candidate_products": 0,
                    "kraken_listed_products": 0,
                    "kraken_current_radar_products": 0,
                    "kraken_velocity_board_products": 0,
                    "coinbase_only_products": 0,
                },
            )
            bucket["candidate_products"] += 1
            route_state = str(row.get("kraken_route_state") or "")
            if route_state.startswith("kraken_"):
                bucket["kraken_listed_products"] += 1
            if route_state in {"kraken_live_radar", "kraken_velocity_board"}:
                bucket["kraken_current_radar_products"] += 1
            if route_state == "kraken_velocity_board":
                bucket["kraken_velocity_board_products"] += 1
            if route_state == "coinbase_only_no_kraken_spot_match":
                bucket["coinbase_only_products"] += 1
    return {
        "total_candidate_products": total,
        "kraken_listed_products": len(kraken_listed),
        "kraken_listed_pct": round((len(kraken_listed) / total) * 100.0, 4) if total else 0.0,
        "kraken_current_radar_products": len(radar),
        "kraken_velocity_board_products": len(velocity),
        "kraken_fee_flip_products": len(fee_flip),
        "coinbase_only_products": len(coinbase_only),
        "source_breakdown": source_breakdown,
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    tail_audit = load_json(Path(str(args.tail_audit_path)))
    coinbase_radar = load_json(Path(str(args.coinbase_radar_path)))
    coinbase_strategy = load_json(Path(str(args.coinbase_strategy_path)))
    kraken_radar_payload = load_json(Path(str(args.kraken_radar_path)))
    kraken_velocity_payload = load_json(Path(str(args.kraken_velocity_path)))
    kraken_catalog = kraken_catalog_from_pairs(kraken_pairs_from_client(quote_set(str(args.quotes))))
    candidates = merge_candidates(
        candidate_records(
            tail_audit=tail_audit if isinstance(tail_audit, dict) else {},
            coinbase_radar=coinbase_radar if isinstance(coinbase_radar, dict) else {},
            coinbase_strategy=coinbase_strategy if isinstance(coinbase_strategy, dict) else {},
            max_tail_rows=int(args.max_tail_rows),
        )
    )
    rows = build_rows(candidates, kraken_catalog, rows_by_product(kraken_radar_payload), rows_by_product(kraken_velocity_payload))
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_kraken_spot_overlap_board",
        "shadow_only": True,
        "parameters": {
            "tail_audit_path": str(args.tail_audit_path),
            "coinbase_radar_path": str(args.coinbase_radar_path),
            "coinbase_strategy_path": str(args.coinbase_strategy_path),
            "kraken_radar_path": str(args.kraken_radar_path),
            "kraken_velocity_path": str(args.kraken_velocity_path),
            "quotes": sorted(quote_set(str(args.quotes))),
            "max_tail_rows": int(args.max_tail_rows),
        },
        "read": [
            "This is a routeability board, not live trade permission.",
            "A Coinbase historical or live signal is only Kraken-actionable when the base asset has an online Kraken USD/USDC/USDT spot pair.",
            "Rows marked coinbase_only_no_kraken_spot_match can still be researched on Coinbase, but they cannot validate the Kraken lower-fee path.",
        ],
        "summary": summarize(rows),
        "rows": rows,
    }
    write_reports(payload, Path(str(args.json_path)), Path(str(args.csv_path)), Path(str(args.md_path)))
    return payload


def write_reports(payload: dict[str, Any], json_path: Path, csv_path: Path, md_path: Path) -> None:
    write_json(json_path, payload)
    rows = payload.get("rows") or []
    columns = [
        "rank",
        "product_id",
        "sources",
        "candidate_score",
        "tail_prob",
        "fast_green_prob",
        "coinbase_net_pct",
        "coinbase_signal_state",
        "kraken_route_state",
        "kraken_product_id",
        "kraken_quotes",
        "kraken_signal_state",
        "kraken_spread_bps",
        "kraken_edge_bps",
        "kraken_verdict",
        "can_trade_100",
        "min_notional_usd",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in columns})
    summary = payload.get("summary") or {}
    md = [
        "# Coinbase/Kraken Spot Overlap Board",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Shadow only: `{payload.get('shadow_only')}`",
        "",
        "## Read",
        "",
    ]
    for item in payload.get("read") or []:
        md.append(f"- {item}")
    md.extend(
        [
            "",
            "## Summary",
            "",
            f"- Candidate products: `{summary.get('total_candidate_products', 0)}`",
            f"- Kraken listed products: `{summary.get('kraken_listed_products', 0)}` / `{summary.get('kraken_listed_pct', 0.0)}%`",
            f"- In current Kraken radar/velocity surfaces: `{summary.get('kraken_current_radar_products', 0)}`",
            f"- On Kraken velocity board: `{summary.get('kraken_velocity_board_products', 0)}`",
            f"- Coinbase-only candidates: `{summary.get('coinbase_only_products', 0)}`",
            "",
            "## Source Breakdown",
            "",
            "| Source | Candidates | Kraken Listed | Kraken Current | Kraken Velocity | Coinbase Only |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for source, stats in sorted((summary.get("source_breakdown") or {}).items()):
        md.append(
            "| {source} | {candidate_products} | {kraken_listed_products} | {kraken_current_radar_products} | {kraken_velocity_board_products} | {coinbase_only_products} |".format(
                source=source,
                **stats,
            )
        )
    md.extend(
        [
            "",
            "## Top Routes",
            "",
            "| Rank | Coinbase Product | Sources | Coinbase Signal | Kraken Route | Kraken Product | Kraken Verdict | Edge bps | Spread bps |",
            "| ---: | --- | --- | --- | --- | --- | --- | ---: | ---: |",
        ]
    )
    for row in rows[:40]:
        md.append(
            "| {rank} | {product_id} | {sources} | {coinbase_signal_state} | {kraken_route_state} | {kraken_product_id} | {kraken_verdict} | {kraken_edge_bps:.4f} | {kraken_spread_bps:.2f} |".format(
                **row
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> None:
    build(parse_args())


if __name__ == "__main__":
    main()
