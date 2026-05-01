#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kraken_spot_client import KrakenSpotClient, parse_pair, to_float


DEFAULT_PRODUCTS = ["HOUSE-USD", "FOLKS-USD", "BTR-USD"]
DEFAULT_EVENT_PATH = ROOT / "reports" / "kraken_maker_microfill_calibration_events.jsonl"
DEFAULT_SUMMARY_PATH = ROOT / "reports" / "kraken_maker_microfill_calibration_summary.json"
DEFAULT_OPPORTUNITY_BOARD_PATH = ROOT / "reports" / "kraken_maker_opportunity_board.json"


@dataclass(frozen=True)
class BookTop:
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    ts_utc: str

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0 if self.bid > 0.0 and self.ask > 0.0 else 0.0

    @property
    def spread_bps(self) -> float:
        return ((self.ask - self.bid) / self.mid * 10000.0) if self.mid > 0.0 else 0.0


@dataclass(frozen=True)
class PairCalibrationInfo:
    rest_pair: str
    tick_size: float


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def product_to_ws(product: str) -> str:
    value = str(product or "").upper().replace("-", "/")
    return value


def load_pair_info(client: KrakenSpotClient, products: list[str]) -> dict[str, PairCalibrationInfo]:
    wanted_ws = {product_to_ws(product): product for product in products}
    wanted_flat = {product.upper().replace("-", ""): product for product in products}
    out: dict[str, PairCalibrationInfo] = {}
    payload = client.asset_pairs()
    for rest_pair, raw in payload.items():
        if not isinstance(raw, dict):
            continue
        pair = parse_pair(str(rest_pair), raw)
        if pair is None or pair.status != "online":
            continue
        product = wanted_ws.get(pair.wsname.upper()) or wanted_flat.get(pair.altname.upper())
        if product:
            out[product] = PairCalibrationInfo(rest_pair=pair.rest_pair, tick_size=pair.tick_size)
    for product in products:
        out.setdefault(product, PairCalibrationInfo(rest_pair=product.replace("-", ""), tick_size=0.0))
    return out


def load_pair_map(client: KrakenSpotClient, products: list[str]) -> dict[str, str]:
    return {product: info.rest_pair for product, info in load_pair_info(client, products).items()}


def fetch_top(client: KrakenSpotClient, rest_pair: str) -> BookTop | None:
    payload = client.depth(rest_pair, count=1)
    if not isinstance(payload, dict) or not payload:
        return None
    book = next(iter(payload.values()))
    bids = book.get("bids") if isinstance(book, dict) else None
    asks = book.get("asks") if isinstance(book, dict) else None
    if not bids or not asks:
        return None
    bid = to_float(bids[0][0])
    bid_size = to_float(bids[0][1])
    ask = to_float(asks[0][0])
    ask_size = to_float(asks[0][1])
    if bid <= 0.0 or ask <= 0.0:
        return None
    return BookTop(bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size, ts_utc=utc_now_iso())


def infer_post_only_fill_proxy(
    side: str, 
    order_price: float, 
    initial: BookTop, 
    current: BookTop, 
    ghost_penalty_bps: float = 0.0
) -> tuple[str, str]:
    side = side.lower()
    penalty = max(0.0, ghost_penalty_bps / 10000.0 * order_price)
    if side == "buy":
        # Taker must clear our price PLUS the ghost penalty to count as a fill
        if current.ask <= (order_price - penalty):
            return "hard_cross_fill_proxy", "ask_crossed_bid_order"
        if order_price <= initial.bid and current.bid < (order_price - penalty):
            return "probable_queue_depletion_fill_proxy", "best_bid_traded_below_order"
        if order_price > initial.bid and current.bid >= order_price:
            return "joined_or_improved_bid_visible_unfilled", "inside_bid_visible_without_cross"
        if current.spread_bps < initial.spread_bps * 0.35:
            return "spread_decay_unfilled", "spread_collapsed_before_fill_proxy"
        return "unfilled_active", "bid_order_still_working"
    if side == "sell":
        # Taker must clear our price PLUS the ghost penalty to count as a fill
        if current.bid >= (order_price + penalty):
            return "hard_cross_fill_proxy", "bid_crossed_ask_order"
        if order_price >= initial.ask and current.ask > (order_price + penalty):
            return "probable_queue_depletion_fill_proxy", "best_ask_lifted_above_order"
        if order_price < initial.ask and current.ask <= order_price:
            return "joined_or_improved_ask_visible_unfilled", "inside_ask_visible_without_cross"
        if current.spread_bps < initial.spread_bps * 0.35:
            return "spread_decay_unfilled", "spread_collapsed_before_fill_proxy"
        return "unfilled_active", "ask_order_still_working"
    raise ValueError(f"unknown side {side!r}")


def maker_price_at_offset(side: str, book: BookTop, offset_frac: float) -> float:
    spread = max(0.0, book.ask - book.bid)
    offset = max(0.0, min(0.99, float(offset_frac)))
    if side.lower() == "buy":
        return min(book.ask - (spread * 0.01), book.bid + (spread * offset))
    if side.lower() == "sell":
        return max(book.bid + (spread * 0.01), book.ask - (spread * offset))
    raise ValueError(f"unknown side {side!r}")


def maker_price_at_tickback(side: str, book: BookTop, tick_size: float, tick_back: int) -> float:
    tick = max(0.0, float(tick_size))
    back = max(0, int(tick_back))
    if tick <= 0.0:
        return book.bid if side.lower() == "buy" else book.ask
    if side.lower() == "buy":
        return max(tick, book.bid - (back * tick))
    if side.lower() == "sell":
        return max(tick, book.ask + (back * tick))
    raise ValueError(f"unknown side {side!r}")


def apply_order_price_bounds(order_price: float, min_order_price: float | None = None, max_order_price: float | None = None) -> float:
    bounded = float(order_price)
    if min_order_price is not None:
        floor_price = to_float(min_order_price)
        if floor_price > 0.0:
            bounded = max(bounded, floor_price)
    if max_order_price is not None:
        ceiling_price = to_float(max_order_price)
        if ceiling_price > 0.0:
            bounded = min(bounded, ceiling_price)
    return bounded


def offset_key(product: str, side: str, offset_frac: float) -> str:
    return f"{str(product).upper()}|{str(side).lower()}|{float(offset_frac):.4f}"


def tickback_key(product: str, side: str, tick_back: int) -> str:
    return f"{str(product).upper()}|{str(side).lower()}|{int(tick_back)}"


def run_trial(
    *,
    client: KrakenSpotClient,
    product: str,
    rest_pair: str,
    side: str,
    price_offset_frac: float,
    tick_back: int | None = None,
    tick_size: float = 0.0,
    ttl_seconds: float,
    poll_seconds: float,
    ghost_penalty_bps: float = 0.0,
    min_order_price: float | None = None,
    max_order_price: float | None = None,
) -> dict[str, Any]:
    initial = fetch_top(client, rest_pair)
    if initial is None:
        return {
            "ts_utc": utc_now_iso(),
            "action": "microfill_calibration_trial",
            "product_id": product,
            "rest_pair": rest_pair,
            "side": side,
            "price_offset_frac": round(float(price_offset_frac), 6),
            "tick_back": tick_back,
            "tick_size": round(float(tick_size), 12),
            "result": "book_unavailable",
        }
    if tick_back is None:
        price_model = "inside_spread_offset_frac"
        order_price = maker_price_at_offset(side, initial, price_offset_frac)
    else:
        price_model = "l1_tickback"
        order_price = maker_price_at_tickback(side, initial, tick_size, tick_back)
    if min_order_price is not None:
        floor_price = to_float(min_order_price)
        if floor_price > 0.0:
            price_model = f"{price_model}_min_floor"
    if max_order_price is not None:
        ceiling_price = to_float(max_order_price)
        if ceiling_price > 0.0:
            price_model = f"{price_model}_max_ceiling"
    order_price = apply_order_price_bounds(order_price, min_order_price=min_order_price, max_order_price=max_order_price)
    deadline = time.time() + max(0.0, ttl_seconds)
    samples = 0
    last = initial
    final_result = "unfilled_timeout"
    final_reason = "ttl_elapsed_without_fill_proxy"
    while time.time() <= deadline:
        time.sleep(max(0.05, poll_seconds))
        current = fetch_top(client, rest_pair)
        if current is None:
            continue
        samples += 1
        last = current
        result, reason = infer_post_only_fill_proxy(side, order_price, initial, current, ghost_penalty_bps)
        if result != "unfilled_active":
            final_result = result
            final_reason = reason
            break
    elapsed = max(0.0, datetime.fromisoformat(last.ts_utc).timestamp() - datetime.fromisoformat(initial.ts_utc).timestamp())
    return {
        "ts_utc": utc_now_iso(),
        "action": "microfill_calibration_trial",
        "product_id": product,
        "rest_pair": rest_pair,
        "side": side,
        "price_model": price_model,
        "price_offset_frac": round(float(price_offset_frac), 6),
        "price_offset_key": offset_key(product, side, price_offset_frac) if tick_back is None else None,
        "tick_back": tick_back,
        "tick_back_key": tickback_key(product, side, int(tick_back)) if tick_back is not None else None,
        "tick_size": round(float(tick_size), 12),
        "min_order_price": min_order_price,
        "max_order_price": max_order_price,
        "order_price": order_price,
        "result": final_result,
        "reason": final_reason,
        "ghost_penalty_bps": ghost_penalty_bps,
        "is_midpoint": abs(float(price_offset_frac) - 0.5) < 0.001 if tick_back is None else False,
        "ttl_seconds": ttl_seconds,
        "poll_seconds": poll_seconds,
        "samples": samples,
        "elapsed_seconds": round(elapsed, 3),
        "initial_bid": initial.bid,
        "initial_ask": initial.ask,
        "initial_bid_size": initial.bid_size,
        "initial_ask_size": initial.ask_size,
        "initial_spread_bps": round(initial.spread_bps, 6),
        "last_bid": last.bid,
        "last_ask": last.ask,
        "last_bid_size": last.bid_size,
        "last_ask_size": last.ask_size,
        "last_spread_bps": round(last.spread_bps, 6),
        "read": "Public-book proxy only. No private endpoints or live orders used.",
    }


def summarize_events(rows: list[dict[str, Any]]) -> dict[str, Any]:
    trials = [row for row in rows if row.get("action") == "microfill_calibration_trial"]
    by_product: dict[str, Counter] = defaultdict(Counter)
    by_side: dict[str, Counter] = defaultdict(Counter)
    by_product_side: dict[str, Counter] = defaultdict(Counter)
    by_product_side_offset: dict[str, Counter] = defaultdict(Counter)
    by_product_side_tick_offset: dict[str, Counter] = defaultdict(Counter)
    fill_like = {"hard_cross_fill_proxy", "probable_queue_depletion_fill_proxy"}
    for row in trials:
        product = str(row.get("product_id") or "")
        side = str(row.get("side") or "")
        result = str(row.get("result") or "")
        by_product[product][result] += 1
        by_side[side][result] += 1
        by_product_side[f"{product}|{side}"][result] += 1
        tick_key = str(row.get("tick_back_key") or "")
        if tick_key:
            by_product_side_tick_offset[tick_key][result] += 1
        else:
            by_product_side_offset[
                str(row.get("price_offset_key") or offset_key(product, side, to_float(row.get("price_offset_frac"))))
            ][result] += 1
    fill_like_count = sum(1 for row in trials if row.get("result") in fill_like)
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_maker_microfill_calibration",
        "trials": len(trials),
        "fill_like_trials": fill_like_count,
        "fill_like_rate": round(fill_like_count / len(trials), 6) if trials else 0.0,
        "by_product": {key: dict(value) for key, value in sorted(by_product.items())},
        "by_side": {key: dict(value) for key, value in sorted(by_side.items())},
        "by_product_side": {key: dict(value) for key, value in sorted(by_product_side.items())},
        "by_product_side_offset": {key: dict(value) for key, value in sorted(by_product_side_offset.items())},
        "by_product_side_tick_offset": {
            key: dict(value) for key, value in sorted(by_product_side_tick_offset.items())
        },
        "read": "Use this to calibrate shadow maker fill probabilities. It is still weaker than actual live order telemetry.",
    }


def write_summary(path: Path, event_path: Path) -> dict[str, Any]:
    summary = summarize_events(load_jsonl(event_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def load_opportunity_products(
    path: Path,
    *,
    top_products: int,
    min_mer: float,
    min_spread_bps: float,
    playbook: str,
) -> list[str]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    rows = payload.get("rows") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        product = str(row.get("product_id") or "").upper()
        if not product or product in seen:
            continue
        if playbook and str(row.get("playbook") or "") != playbook:
            continue
        if to_float(row.get("mer")) < min_mer:
            continue
        if to_float(row.get("spread_bps")) < min_spread_bps:
            continue
        out.append(product)
        seen.add(product)
        if len(out) >= max(1, int(top_products)):
            break
    return out


def products_for_cycle(args: argparse.Namespace) -> list[str]:
    explicit = [str(product).upper() for product in (args.products or [])]
    if args.product_source == "explicit":
        return explicit
    priority = [str(product).upper() for product in (args.priority_products or [])]
    board_products = load_opportunity_products(
        args.opportunity_board_path,
        top_products=args.top_products,
        min_mer=args.min_mer,
        min_spread_bps=args.min_spread_bps,
        playbook=args.playbook,
    )
    merged: list[str] = []
    seen: set[str] = set()
    for product in [*priority, *board_products, *explicit]:
        if product and product not in seen:
            merged.append(product)
            seen.add(product)
    return merged


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Public-data Kraken maker microfill calibration loop.")
    parser.add_argument("--products", nargs="+", default=DEFAULT_PRODUCTS)
    parser.add_argument("--priority-products", nargs="*", default=[])
    parser.add_argument("--product-source", choices=["explicit", "opportunity-board"], default="explicit")
    parser.add_argument("--opportunity-board-path", type=Path, default=DEFAULT_OPPORTUNITY_BOARD_PATH)
    parser.add_argument("--top-products", type=int, default=12)
    parser.add_argument("--min-mer", type=float, default=3.5)
    parser.add_argument("--min-spread-bps", type=float, default=100.0)
    parser.add_argument("--playbook", default="maker_harvest")
    parser.add_argument("--ttl-seconds", type=float, default=20.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument(
        "--price-offset-fracs",
        default="0.0",
        help="Comma-separated maker offset fractions inside the spread. 0.0=bid/ask, 0.5=mid-spread, clamped post-only.",
    )
    parser.add_argument(
        "--price-tick-backs",
        default="",
        help="Comma-separated L1 tick-back distances to calibrate separately. 0=L1 bid/ask, 1=one tick behind, 2=two ticks behind.",
    )
    parser.add_argument("--ghost-penalty-bps", type=float, default=0.0, help="Ghosting penalty in bps. Taker must clear price by this much.")
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--event-path", type=Path, default=DEFAULT_EVENT_PATH)
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY_PATH)
    return parser.parse_args()


def parse_price_offset_fracs(raw: str) -> list[float]:
    out: list[float] = []
    seen: set[float] = set()
    for chunk in str(raw or "0.0").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            value = max(0.0, min(0.99, float(chunk)))
        except ValueError:
            continue
        key = round(value, 4)
        if key not in seen:
            out.append(value)
            seen.add(key)
    return out or [0.0]


def parse_price_tick_backs(raw: str) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for chunk in str(raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            value = max(0, int(float(chunk)))
        except ValueError:
            continue
        if value not in seen:
            out.append(value)
            seen.add(value)
    return out


def main():
    print("Microfill Calibrator Starting...")
    args = parse_args()
    print(f"Arguments: {args}")
    price_offset_fracs = parse_price_offset_fracs(args.price_offset_fracs)
    price_tick_backs = parse_price_tick_backs(args.price_tick_backs)
    client = KrakenSpotClient()
    pair_info: dict[str, PairCalibrationInfo] = {}
    for cycle in range(args.cycles):
        print(f"Cycle {cycle+1}/{args.cycles}")
        products = products_for_cycle(args)
        if not products:
            write_summary(args.summary_path, args.event_path)
            print(json.dumps({"warning": "no_products_for_cycle"}, sort_keys=True))
            continue
        missing_products = [product for product in products if product not in pair_info]
        if missing_products:
            pair_info.update(load_pair_info(client, missing_products))
        for product in products:
            print(f"  Validating {product}...")
            info = pair_info.get(product, PairCalibrationInfo(rest_pair=product.replace("-", ""), tick_size=0.0))
            rest_pair = info.rest_pair
            for side in ("buy", "sell"):
                for price_offset_frac in price_offset_fracs:
                    event = run_trial(
                        client=client,
                        product=product,
                        rest_pair=rest_pair,
                        side=side,
                        price_offset_frac=price_offset_frac,
                        ttl_seconds=float(args.ttl_seconds),
                        poll_seconds=float(args.poll_seconds),
                        ghost_penalty_bps=float(args.ghost_penalty_bps),
                    )
                    append_jsonl(args.event_path, event)
                    write_summary(args.summary_path, args.event_path)
                    print(json.dumps(event, sort_keys=True))
                for tick_back in price_tick_backs:
                    event = run_trial(
                        client=client,
                        product=product,
                        rest_pair=rest_pair,
                        side=side,
                        price_offset_frac=0.0,
                        tick_back=tick_back,
                        tick_size=info.tick_size,
                        ttl_seconds=float(args.ttl_seconds),
                        poll_seconds=float(args.poll_seconds),
                        ghost_penalty_bps=float(args.ghost_penalty_bps),
                    )
                    append_jsonl(args.event_path, event)
                    write_summary(args.summary_path, args.event_path)
                    print(json.dumps(event, sort_keys=True))
    summary = write_summary(args.summary_path, args.event_path)
    print(json.dumps({"summary": summary, "event_path": str(args.event_path)}, indent=2))


if __name__ == "__main__":
    main()
