#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kraken_spot_client import KrakenPair, KrakenSpotClient, KrakenSpotClientError, parse_pair, to_float
from live_penetration_lattice_shadow import append_jsonl


DEFAULT_EVENT_PATH = (
    ROOT
    / "reports"
    / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_fast_cooldown_ab_events.jsonl"
)
DEFAULT_STATE_PATH = (
    ROOT
    / "reports"
    / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_fast_cooldown_ab_state.json"
)
DEFAULT_JSON_PATH = ROOT / "reports" / "kraken_maker_validate_only_probe.json"
DEFAULT_MD_PATH = ROOT / "reports" / "kraken_maker_validate_only_probe.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_jsonl(path: Path, *, max_rows: int = 5000) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for line in lines[-max_rows:]:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def product_id_for_pair(pair: KrakenPair) -> str:
    return f"{pair.base}-{pair.quote}".upper()


def build_pair_map(asset_pairs_payload: dict[str, Any]) -> dict[str, KrakenPair]:
    out: dict[str, KrakenPair] = {}
    for rest_pair, payload in asset_pairs_payload.items():
        if not isinstance(payload, dict):
            continue
        pair = parse_pair(str(rest_pair), payload)
        if pair is None:
            continue
        if pair.status and pair.status.lower() not in {"online", "cancel_only", "post_only"}:
            continue
        out[product_id_for_pair(pair)] = pair
    return out


def infer_recent_products(*, state_path: Path, event_path: Path, limit: int) -> list[str]:
    state = load_json(state_path).get("state")
    state = state if isinstance(state, dict) else {}
    products: list[str] = []
    active = state.get("active_positions")
    if isinstance(active, dict):
        products.extend(str(pid).upper() for pid in active if str(pid).strip())
    events = load_jsonl(event_path)
    for row in reversed(events):
        action = str(row.get("action") or "")
        if action not in {"open_maker_shadow", "close_maker_shadow", "maker_exit_miss"}:
            continue
        pid = str(row.get("product_id") or "").upper()
        if pid:
            products.append(pid)
        if len(dict.fromkeys(products)) >= limit:
            break
    return list(dict.fromkeys(products))[:limit]


def decimal_places_from_step(step: float, fallback: int) -> int:
    if step <= 0:
        return fallback
    text = f"{step:.16f}".rstrip("0").rstrip(".")
    if "." not in text:
        return 0
    return min(12, max(0, len(text.split(".", 1)[1])))


def round_price(price: float, pair: KrakenPair) -> float:
    places = max(int(pair.pair_decimals), decimal_places_from_step(pair.tick_size, int(pair.pair_decimals)))
    return round(float(price), places)


def ceil_volume(volume: float, pair: KrakenPair) -> float:
    places = max(0, int(pair.lot_decimals))
    scale = 10**places
    return math.ceil(float(volume) * scale) / scale


@dataclass
class ValidateOrderPlan:
    product_id: str
    rest_pair: str
    quote_currency: str
    side: str
    price: float
    volume: float
    quote_amount: float
    min_quote_amount: float
    max_quote_amount: float
    quote_usd: float
    min_quote_usd: float
    max_quote_usd: float
    order_min_base: float
    cost_min: float
    post_only: bool = True
    validate_only: bool = True


def build_validate_order_plan(
    *,
    product_id: str,
    pair: KrakenPair,
    bid: float,
    ask: float,
    max_quote_usd: float,
    max_quote_amount: float | None = None,
    min_quote_cushion: float,
) -> ValidateOrderPlan:
    if bid <= 0.0 or ask <= 0.0:
        raise ValueError(f"{product_id} has invalid bid/ask: bid={bid}, ask={ask}")
    max_quote = float(max_quote_usd if max_quote_amount is None else max_quote_amount)
    min_quote = max(float(pair.cost_min), float(pair.order_min) * bid)
    target_quote = max(min_quote * float(min_quote_cushion), max_quote)
    if target_quote > max_quote + 1e-9:
        raise ValueError(
            f"{product_id} requires quote {target_quote:.12f} {pair.quote}, above max_quote_amount {max_quote:.12f}"
        )
    price = round_price(bid, pair)
    volume = ceil_volume(max(float(pair.order_min), target_quote / price), pair)
    quote_amount = volume * price
    if quote_amount > max_quote * 1.001:
        raise ValueError(
            f"{product_id} rounded quote {quote_amount:.12f} {pair.quote}, above max_quote_amount {max_quote:.12f}"
        )
    return ValidateOrderPlan(
        product_id=product_id,
        rest_pair=pair.rest_pair,
        quote_currency=pair.quote,
        side="buy",
        price=price,
        volume=volume,
        quote_amount=quote_amount,
        min_quote_amount=min_quote,
        max_quote_amount=max_quote,
        quote_usd=quote_amount if pair.quote == "USD" else 0.0,
        min_quote_usd=min_quote if pair.quote == "USD" else 0.0,
        max_quote_usd=float(max_quote_usd),
        order_min_base=float(pair.order_min),
        cost_min=float(pair.cost_min),
    )


def fetch_bid_ask(client: KrakenSpotClient, pair: KrakenPair) -> tuple[float, float]:
    payload = client.ticker([pair.rest_pair])
    if not isinstance(payload, dict) or not payload:
        raise KrakenSpotClientError(f"No ticker payload for {pair.rest_pair}")
    row = next(iter(payload.values()))
    if not isinstance(row, dict):
        raise KrakenSpotClientError(f"Malformed ticker payload for {pair.rest_pair}: {row!r}")
    bid = to_float((row.get("b") or [None])[0])
    ask = to_float((row.get("a") or [None])[0])
    return bid, ask


def run_probe(
    *,
    client: KrakenSpotClient,
    products: list[str],
    event_path: Path,
    max_quote_usd: float,
    max_quote_amount: float | None = None,
    min_quote_cushion: float,
    allow_non_usd_quote: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    pair_map = build_pair_map(client.asset_pairs())
    results: list[dict[str, Any]] = []
    for product in products:
        product_id = product.upper()
        pair = pair_map.get(product_id)
        if pair is None:
            result = {
                "ts_utc": utc_now_iso(),
                "action": "kraken_validate_order",
                "product_id": product_id,
                "ok": False,
                "status": "pair_not_found",
                "validate_only": True,
                "post_only": True,
            }
            append_jsonl(event_path, result)
            results.append(result)
            continue
        if pair.quote != "USD" and not allow_non_usd_quote:
            result = {
                "ts_utc": utc_now_iso(),
                "action": "kraken_validate_order",
                "product_id": product_id,
                "ok": False,
                "status": "non_usd_quote_requires_explicit_allow",
                "validate_only": True,
                "post_only": True,
                "quote_currency": pair.quote,
            }
            append_jsonl(event_path, result)
            results.append(result)
            continue
        try:
            bid, ask = fetch_bid_ask(client, pair)
            plan = build_validate_order_plan(
                product_id=product_id,
                pair=pair,
                bid=bid,
                ask=ask,
                max_quote_usd=max_quote_usd,
                max_quote_amount=max_quote_amount,
                min_quote_cushion=min_quote_cushion,
            )
            response: dict[str, Any] = {}
            if not dry_run:
                response = client.add_order(
                    rest_pair=plan.rest_pair,
                    side=plan.side,
                    order_type="limit",
                    volume=plan.volume,
                    price=plan.price,
                    post_only=True,
                    validate=True,
                )
            result = {
                "ts_utc": utc_now_iso(),
                "action": "kraken_validate_order",
                "product_id": product_id,
                "ok": True,
                "status": "dry_run_validated_locally" if dry_run else "validated",
                "dry_run": dry_run,
                **asdict(plan),
                "bid": round(bid, 12),
                "ask": round(ask, 12),
                "spread_bps": round(((ask - bid) / bid) * 10000.0, 6) if bid > 0.0 else 0.0,
                "response": response,
            }
        except Exception as exc:
            result = {
                "ts_utc": utc_now_iso(),
                "action": "kraken_validate_order",
                "product_id": product_id,
                "ok": False,
                "status": "validate_failed",
                "dry_run": dry_run,
                "validate_only": True,
                "post_only": True,
                "error": str(exc),
            }
        append_jsonl(event_path, result)
        results.append(result)
    ok_count = sum(1 for row in results if row.get("ok") is True)
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_maker_validate_only_probe",
        "dry_run": dry_run,
        "event_path": str(event_path),
        "summary": {
            "products": products,
            "probed": len(results),
            "validated": ok_count,
            "failed": len(results) - ok_count,
            "max_quote_usd": max_quote_usd,
            "max_quote_amount": max_quote_amount,
            "min_quote_cushion": min_quote_cushion,
            "allow_non_usd_quote": allow_non_usd_quote,
            "safety": "validate_only_post_only_no_live_orders",
        },
        "results": results,
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    summary = payload["summary"]
    lines = [
        "# Kraken Maker Validate-Only Probe",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Dry run: `{payload['dry_run']}`",
        f"- Safety: `{summary['safety']}`",
        f"- Event path: `{payload['event_path']}`",
        f"- Products: `{summary['products']}`",
        f"- Validated: `{summary['validated']}`",
        f"- Failed: `{summary['failed']}`",
        "",
        "| Product | OK | Status | Rest Pair | Quote | Side | Price | Volume | Quote Amount | Quote $ | Spread bps |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["results"]:
        lines.append(
            "| {product_id} | {ok} | {status} | {rest_pair} | {quote_currency} | {side} | {price:.12g} | {volume:.12g} | {quote_amount:.12g} | {quote_usd:.6f} | {spread_bps:.6f} |".format(
                product_id=row.get("product_id", ""),
                ok=row.get("ok", False),
                status=row.get("status", ""),
                rest_pair=row.get("rest_pair", ""),
                quote_currency=row.get("quote_currency", ""),
                side=row.get("side", ""),
                price=to_float(row.get("price")),
                volume=to_float(row.get("volume")),
                quote_amount=to_float(row.get("quote_amount")),
                quote_usd=to_float(row.get("quote_usd")),
                spread_bps=to_float(row.get("spread_bps")),
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_products(raw: str, *, state_path: Path, event_path: Path, limit: int) -> list[str]:
    if raw.strip():
        return [part.strip().upper() for part in raw.split(",") if part.strip()]
    return infer_recent_products(state_path=state_path, event_path=event_path, limit=limit)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate Kraken post-only AddOrder payloads for the current maker shadow lane. Never submits live orders."
    )
    parser.add_argument("--products", default="", help="Comma-separated products. Defaults to active/recent champion products.")
    parser.add_argument("--product-limit", type=int, default=5)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--event-path", type=Path, default=DEFAULT_EVENT_PATH)
    parser.add_argument("--json-path", type=Path, default=DEFAULT_JSON_PATH)
    parser.add_argument("--md-path", type=Path, default=DEFAULT_MD_PATH)
    parser.add_argument("--max-quote-usd", type=float, default=10.0)
    parser.add_argument("--max-quote-amount", type=float, default=None, help="Quote-currency cap for non-USD pairs, e.g. BTC amount for *-BTC.")
    parser.add_argument("--min-quote-cushion", type=float, default=1.02)
    parser.add_argument("--allow-non-usd-quote", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Resolve pairs/tickers and write local evidence without private AddOrder.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    products = parse_products(
        args.products,
        state_path=args.state_path,
        event_path=args.event_path,
        limit=max(1, int(args.product_limit)),
    )
    if not products:
        raise SystemExit("No products to probe. Pass --products or provide a state/event tape with recent products.")
    payload = run_probe(
        client=KrakenSpotClient(),
        products=products,
        event_path=args.event_path,
        max_quote_usd=float(args.max_quote_usd),
        max_quote_amount=args.max_quote_amount,
        min_quote_cushion=float(args.min_quote_cushion),
        allow_non_usd_quote=bool(args.allow_non_usd_quote),
        dry_run=bool(args.dry_run),
    )
    write_reports(payload, json_path=args.json_path, md_path=args.md_path)
    print(json.dumps({"summary": payload["summary"], "json_path": str(args.json_path), "md_path": str(args.md_path)}, indent=2))


if __name__ == "__main__":
    main()
