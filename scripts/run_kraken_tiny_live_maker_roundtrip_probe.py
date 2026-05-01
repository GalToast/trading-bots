#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from kraken_spot_client import KrakenPair, KrakenSpotClient, normalize_asset, parse_pair, to_float  # noqa: E402


DEFAULT_EVENTS_PATH = ROOT / "reports" / "kraken_tiny_live_maker_roundtrip_events.jsonl"
DEFAULT_REPORT_PATH = ROOT / "reports" / "kraken_tiny_live_maker_roundtrip_latest.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def legal_price(price: float, tick_size: float, *, side: str) -> float:
    if tick_size <= 0.0:
        return price
    steps = price / tick_size
    if side == "buy":
        return round(int(steps + 1e-9) * tick_size, 12)
    floor_steps = int(steps + 1e-9)
    return round((floor_steps if abs(steps - floor_steps) < 1e-9 else floor_steps + 1) * tick_size, 12)


def legal_maker_buy_price(bid: float, ask: float, tick_size: float, *, improve_ticks: int = 0) -> float:
    """Return a post-only buy price at bid or inside the spread without crossing ask."""
    if improve_ticks <= 0 or tick_size <= 0.0:
        return legal_price(bid, tick_size, side="buy")
    inside_cap = ask - tick_size
    candidate = min(bid + (improve_ticks * tick_size), inside_cap)
    if candidate <= 0.0 or candidate < bid:
        candidate = bid
    return legal_price(candidate, tick_size, side="buy")


def legal_maker_sell_price(
    bid: float,
    ask: float,
    tick_size: float,
    *,
    minimum_price: float,
    inside_spread: bool = False,
) -> float:
    """Return a post-only sell price that does not cross bid and respects a profit floor."""
    if not inside_spread or tick_size <= 0.0:
        return legal_price(max(ask, minimum_price), tick_size, side="sell")
    inside_floor = bid + tick_size
    return legal_price(max(inside_floor, minimum_price), tick_size, side="sell")


def maker_exit_floor_price(
    *,
    entry_cost: float,
    entry_fee: float,
    volume: float,
    maker_fee_bps: float,
    target_net_pct: float,
    tick_size: float,
) -> tuple[float, float]:
    fee_rate = maker_fee_bps / 10000.0
    target_net = target_net_pct / 100.0
    raw_price = ((entry_cost + entry_fee) * (1.0 + target_net)) / max(
        volume * (1.0 - fee_rate),
        1e-12,
    )
    return legal_price(raw_price, tick_size, side="sell"), raw_price


def exit_floor_above_ask_bps(required_exit_price: float, ask: float) -> float:
    if ask <= 0.0:
        return 0.0
    return round(max(0.0, (required_exit_price - ask) / ask * 10000.0), 6)


def legal_volume(volume: float, lot_decimals: int) -> float:
    scale = 10 ** max(0, int(lot_decimals))
    return int(volume * scale) / scale


def load_pair(client: KrakenSpotClient, product_id: str) -> KrakenPair:
    wanted = str(product_id or "").upper().replace("-", "/")
    for rest_pair, payload in client.asset_pairs().items():
        pair = parse_pair(rest_pair, payload)
        if not pair or pair.status != "online":
            continue
        if pair.wsname.upper() == wanted or f"{pair.base}/{pair.quote}" == wanted:
            return pair
    raise RuntimeError(f"Could not find online Kraken pair for {product_id}")


def fetch_bid_ask(client: KrakenSpotClient, pair: KrakenPair) -> tuple[float, float]:
    ticker = client.ticker([pair.rest_pair])
    row = ticker.get(pair.rest_pair) or next(iter(ticker.values()), {})
    bid = to_float((row.get("b") or [0])[0])
    ask = to_float((row.get("a") or [0])[0])
    if bid <= 0.0 or ask <= 0.0:
        raise RuntimeError(f"Bad ticker for {pair.wsname}: bid={bid} ask={ask}")
    return bid, ask


def parse_depth_levels(raw_levels: Any, *, reverse: bool) -> list[tuple[float, float]]:
    levels: list[tuple[float, float]] = []
    for raw in raw_levels or []:
        if not isinstance(raw, list) or len(raw) < 2:
            continue
        price = to_float(raw[0])
        size = to_float(raw[1])
        if price > 0.0 and size > 0.0:
            levels.append((price, size))
    levels.sort(key=lambda item: item[0], reverse=reverse)
    return levels


def notional_sum(levels: list[tuple[float, float]], count: int) -> float:
    return sum(price * size for price, size in levels[: max(0, int(count))])


def depth_vwap_for_quote(levels: list[tuple[float, float]], quote_amount: float) -> tuple[float, float]:
    remaining = max(0.0, quote_amount)
    if remaining <= 0.0:
        return 0.0, 0.0
    filled_base = 0.0
    spent_quote = 0.0
    for price, size in levels:
        level_notional = price * size
        take_quote = min(remaining, level_notional)
        if take_quote <= 0.0:
            continue
        filled_base += take_quote / price
        spent_quote += take_quote
        remaining -= take_quote
        if remaining <= 1e-12:
            break
    if filled_base <= 0.0:
        return 0.0, remaining
    return spent_quote / filled_base, max(0.0, remaining)


def depth_vwap_for_base(levels: list[tuple[float, float]], base_volume: float) -> tuple[float, float]:
    remaining = max(0.0, base_volume)
    if remaining <= 0.0:
        return 0.0, 0.0
    filled_base = 0.0
    received_quote = 0.0
    for price, size in levels:
        take_base = min(remaining, size)
        if take_base <= 0.0:
            continue
        filled_base += take_base
        received_quote += take_base * price
        remaining -= take_base
        if remaining <= 1e-12:
            break
    if filled_base <= 0.0:
        return 0.0, remaining
    return received_quote / filled_base, max(0.0, remaining)


def book_snapshot(
    client: KrakenSpotClient,
    pair: KrakenPair,
    *,
    quote_amount: float,
    base_volume: float = 0.0,
    depth_count: int = 10,
) -> dict[str, Any]:
    payload = client.depth(pair.rest_pair, count=max(10, int(depth_count)))
    raw_book = payload.get(pair.rest_pair) if isinstance(payload, dict) else None
    if not isinstance(raw_book, dict):
        raw_book = next(iter(payload.values()), {}) if isinstance(payload, dict) and payload else {}
    bids = parse_depth_levels(raw_book.get("bids"), reverse=True)
    asks = parse_depth_levels(raw_book.get("asks"), reverse=False)
    if not bids or not asks:
        return {"book_snapshot_error": "empty_depth"}
    bid = bids[0][0]
    ask = asks[0][0]
    mid = (bid + ask) / 2.0
    l10_bid_notional = notional_sum(bids, 10)
    l10_ask_notional = notional_sum(asks, 10)
    buy_vwap, buy_unfilled_quote = depth_vwap_for_quote(asks, quote_amount)
    sell_vwap, sell_unfilled_base = depth_vwap_for_base(bids, base_volume) if base_volume > 0.0 else (0.0, 0.0)
    return {
        "book_bid": bid,
        "book_ask": ask,
        "book_mid": mid,
        "book_spread_bps": round((ask - bid) / mid * 10000.0, 6) if mid > 0.0 else 0.0,
        "book_l1_bid_notional": round(notional_sum(bids, 1), 8),
        "book_l1_ask_notional": round(notional_sum(asks, 1), 8),
        "book_l5_bid_notional": round(notional_sum(bids, 5), 8),
        "book_l5_ask_notional": round(notional_sum(asks, 5), 8),
        "book_l10_bid_notional": round(l10_bid_notional, 8),
        "book_l10_ask_notional": round(l10_ask_notional, 8),
        "book_l10_imbalance_ratio": round(l10_bid_notional / l10_ask_notional, 6) if l10_ask_notional > 0.0 else 999999.0,
        "book_l10_obi": round(l10_bid_notional / (l10_bid_notional + l10_ask_notional), 6) if l10_bid_notional + l10_ask_notional > 0.0 else 0.5,
        "book_depth_count": max(10, int(depth_count)),
        "book_buy_quote_amount": round(quote_amount, 12),
        "book_buy_vwap": round(buy_vwap, 12),
        "book_buy_depth_unfilled_quote": round(buy_unfilled_quote, 12),
        "book_buy_depth_ok": buy_unfilled_quote <= 1e-9,
        "book_buy_vwap_slippage_bps_vs_ask": round((buy_vwap - ask) / ask * 10000.0, 6) if buy_vwap > 0.0 and ask > 0.0 else 0.0,
        "book_sell_base_volume": round(base_volume, 12),
        "book_sell_vwap": round(sell_vwap, 12),
        "book_sell_depth_unfilled_base": round(sell_unfilled_base, 12),
        "book_sell_depth_ok": sell_unfilled_base <= 1e-12 if base_volume > 0.0 else None,
        "book_sell_vwap_slippage_bps_vs_bid": round((bid - sell_vwap) / bid * 10000.0, 6) if sell_vwap > 0.0 and bid > 0.0 else 0.0,
    }


def append_book_snapshot_event(
    client: KrakenSpotClient,
    *,
    pair: KrakenPair,
    event_path: Path,
    label: str,
    quote_amount: float,
    base_volume: float = 0.0,
    txid: str = "",
    depth_count: int = 10,
) -> dict[str, Any]:
    try:
        snapshot = book_snapshot(
            client,
            pair,
            quote_amount=quote_amount,
            base_volume=base_volume,
            depth_count=depth_count,
        )
    except Exception as exc:
        snapshot = {"book_snapshot_error": str(exc)}
    row = {
        "ts_utc": utc_now_iso(),
        "action": "live_roundtrip_book_snapshot",
        "snapshot_label": label,
        "product_id": pair.wsname.replace("/", "-"),
        "quote_currency": pair.quote,
        "txid": txid,
        **snapshot,
    }
    append_jsonl(event_path, row)
    return snapshot


def order_status(client: KrakenSpotClient, txid: str) -> dict[str, Any]:
    result = client._request("POST", "/0/private/QueryOrders", params={"txid": txid}, private=True)
    if isinstance(result, dict):
        row = result.get(txid)
        if isinstance(row, dict):
            return row
    return {}


def cancel_order(client: KrakenSpotClient, txid: str) -> dict[str, Any]:
    return client._request("POST", "/0/private/CancelOrder", params={"txid": txid}, private=True)


def submit_order(
    client: KrakenSpotClient,
    *,
    pair: KrakenPair,
    side: str,
    volume: float,
    price: float,
    event_path: Path,
    label: str,
) -> str:
    response = client.add_order(
        rest_pair=pair.rest_pair,
        side=side,
        order_type="limit",
        volume=volume,
        price=price,
        post_only=True,
        validate=False,
    )
    txids = response.get("txid") if isinstance(response, dict) else None
    txid = str((txids or [""])[0])
    if not txid:
        raise RuntimeError(f"Kraken did not return txid for {label}: {response!r}")
    append_jsonl(
        event_path,
        {
            "ts_utc": utc_now_iso(),
            "action": f"live_roundtrip_{label}_submitted",
            "product_id": pair.wsname.replace("/", "-"),
            "txid": txid,
            "side": side,
            "price": price,
            "volume": round(volume, 10),
            "response": response,
        },
    )
    return txid


def status_is_terminal(status: dict[str, Any]) -> bool:
    return str(status.get("status") or "").lower() in {"closed", "canceled", "expired"}


def status_filled(status: dict[str, Any]) -> bool:
    return str(status.get("status") or "").lower() == "closed" and to_float(status.get("vol_exec")) > 0.0


def poll_until_done_or_timeout(
    client: KrakenSpotClient,
    *,
    txid: str,
    pair: KrakenPair,
    event_path: Path,
    label: str,
    max_wait_seconds: float,
    poll_seconds: float,
) -> dict[str, Any]:
    deadline = time.time() + max(0.0, max_wait_seconds)
    last: dict[str, Any] = {}
    while True:
        last = order_status(client, txid)
        bid, ask = fetch_bid_ask(client, pair)
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": f"live_roundtrip_{label}_status",
                "txid": txid,
                "status": last.get("status"),
                "vol": last.get("vol"),
                "vol_exec": last.get("vol_exec"),
                "cost": last.get("cost"),
                "fee": last.get("fee"),
                "price": last.get("price"),
                "bid": bid,
                "ask": ask,
                "spread_bps": round((ask - bid) / bid * 10000.0, 6) if bid > 0.0 else 0.0,
            },
        )
        if status_is_terminal(last):
            return last
        if time.time() >= deadline:
            cancel_response = cancel_order(client, txid)
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": f"live_roundtrip_{label}_cancel_requested",
                    "txid": txid,
                    "response": cancel_response,
                },
            )
            time.sleep(1.0)
            return order_status(client, txid)
        time.sleep(max(1.0, poll_seconds))


def poll_post_only_with_reprice(
    client: KrakenSpotClient,
    *,
    txid: str,
    pair: KrakenPair,
    side: str,
    volume: float,
    current_price: float,
    event_path: Path,
    label: str,
    max_wait_seconds: float,
    poll_seconds: float,
    reprice_seconds: float,
    max_reprices: int,
    price_builder: Any,
) -> tuple[str, dict[str, Any], list[str]]:
    deadline = time.time() + max(0.0, max_wait_seconds)
    txid_chain = [txid]
    last_reprice_at = time.time()
    reprices = 0
    while True:
        last = order_status(client, txid)
        bid, ask = fetch_bid_ask(client, pair)
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": f"live_roundtrip_{label}_status",
                "txid": txid,
                "status": last.get("status"),
                "vol": last.get("vol"),
                "vol_exec": last.get("vol_exec"),
                "cost": last.get("cost"),
                "fee": last.get("fee"),
                "price": last.get("price"),
                "bid": bid,
                "ask": ask,
                "spread_bps": round((ask - bid) / bid * 10000.0, 6) if bid > 0.0 else 0.0,
                "reprice_count": reprices,
            },
        )
        if status_is_terminal(last):
            return txid, last, txid_chain
        now = time.time()
        if (
            reprice_seconds > 0.0
            and reprices < max_reprices
            and now - last_reprice_at >= reprice_seconds
        ):
            new_price = price_builder(bid, ask)
            if new_price > 0.0 and abs(new_price - current_price) > max(pair.tick_size / 2.0, 1e-12):
                cancel_response = cancel_order(client, txid)
                append_jsonl(
                    event_path,
                    {
                        "ts_utc": utc_now_iso(),
                        "action": f"live_roundtrip_{label}_reprice_cancel_requested",
                        "txid": txid,
                        "old_price": current_price,
                        "new_price": new_price,
                        "bid": bid,
                        "ask": ask,
                        "response": cancel_response,
                        "reprice_count": reprices + 1,
                    },
                )
                time.sleep(1.0)
                canceled = order_status(client, txid)
                if status_filled(canceled):
                    return txid, canceled, txid_chain
                txid = submit_order(
                    client,
                    pair=pair,
                    side=side,
                    volume=volume,
                    price=new_price,
                    event_path=event_path,
                    label=f"{label}_reprice",
                )
                txid_chain.append(txid)
                current_price = new_price
                reprices += 1
                last_reprice_at = now
        if time.time() >= deadline:
            cancel_response = cancel_order(client, txid)
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": f"live_roundtrip_{label}_cancel_requested",
                    "txid": txid,
                    "response": cancel_response,
                    "reprice_count": reprices,
                    "txid_chain": txid_chain,
                },
            )
            time.sleep(1.0)
            return txid, order_status(client, txid), txid_chain
        time.sleep(max(1.0, poll_seconds))


def nonzero_balances(client: KrakenSpotClient) -> dict[str, str]:
    return {k: v for k, v in client.balance().items() if to_float(v) != 0.0}


def normalized_balance_amounts(balances: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for asset, value in balances.items():
        normalized = normalize_asset(str(asset))
        out[normalized] = out.get(normalized, 0.0) + to_float(value)
    return out


def pressure_gate_status(
    summary_path: Path,
    *,
    product_id: str,
    min_cycles: int,
    min_two_sided_rate: float,
    require_sell_floor: bool,
) -> dict[str, Any]:
    if not summary_path.exists():
        return {"ok": False, "reason": "pressure_summary_missing", "summary_path": str(summary_path)}
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    parameters = payload.get("parameters") if isinstance(payload, dict) else {}
    if require_sell_floor and not bool((parameters or {}).get("enforce_sell_floor_from_queue")):
        return {
            "ok": False,
            "reason": "pressure_summary_not_sell_floor_enforced",
            "summary_path": str(summary_path),
            "enforce_sell_floor_from_queue": bool((parameters or {}).get("enforce_sell_floor_from_queue")),
        }
    product = str(product_id or "").upper()
    candidates: list[dict[str, Any]] = []
    for row in payload.get("leaders") or []:
        key = str(row.get("key") or "")
        if key.split("|", 1)[0].upper() == product:
            candidates.append(row)
    if not candidates:
        return {"ok": False, "reason": "pressure_product_missing", "summary_path": str(summary_path), "product_id": product}
    candidates.sort(
        key=lambda row: (
            to_float(row.get("two_sided_fill_rate")),
            to_float(row.get("two_sided_depth_ok_rate")),
            to_float(row.get("cycles")),
        ),
        reverse=True,
    )
    best = candidates[0]
    cycles = int(to_float(best.get("cycles")))
    rate = to_float(best.get("two_sided_fill_rate"))
    ok = cycles >= int(min_cycles) and rate >= float(min_two_sided_rate)
    return {
        "ok": ok,
        "reason": "pressure_gate_passed" if ok else "pressure_gate_failed",
        "summary_path": str(summary_path),
        "product_id": product,
        "best_key": best.get("key"),
        "cycles": cycles,
        "two_sided_fill_rate": rate,
        "min_cycles": int(min_cycles),
        "min_two_sided_fill_rate": float(min_two_sided_rate),
        "two_sided_depth_ok_rate": to_float(best.get("two_sided_depth_ok_rate")),
        "require_sell_floor": bool(require_sell_floor),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One tiny live post-only maker buy, then post-only maker sell target. Auto-cancels unfilled orders."
    )
    parser.add_argument("--product", default="TRAC-USD")
    parser.add_argument("--quote-usd", type=float, default=7.0)
    parser.add_argument("--max-quote-usd", type=float, default=7.25)
    parser.add_argument("--quote-amount", type=float, default=None)
    parser.add_argument("--max-quote-amount", type=float, default=None)
    parser.add_argument("--allow-non-usd-quote", action="store_true")
    parser.add_argument("--target-net-pct", type=float, default=0.25)
    parser.add_argument("--maker-fee-bps", type=float, default=25.0)
    parser.add_argument("--entry-wait-seconds", type=float, default=60.0)
    parser.add_argument("--exit-wait-seconds", type=float, default=180.0)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--entry-improve-ticks", type=int, default=0)
    parser.add_argument("--entry-reprice-seconds", type=float, default=0.0)
    parser.add_argument("--entry-max-reprices", type=int, default=0)
    parser.add_argument("--require-exit-floor-reachable", action="store_true")
    parser.add_argument("--max-exit-floor-above-ask-bps", type=float, default=15.0)
    parser.add_argument("--exit-inside-spread", action="store_true")
    parser.add_argument("--exit-reprice-seconds", type=float, default=0.0)
    parser.add_argument("--exit-max-reprices", type=int, default=0)
    parser.add_argument("--record-book-snapshots", action="store_true")
    parser.add_argument("--book-depth-count", type=int, default=10)
    parser.add_argument("--pressure-gate-summary-path", type=Path, default=None)
    parser.add_argument("--pressure-gate-min-cycles", type=int, default=5)
    parser.add_argument("--pressure-gate-min-two-sided-rate", type=float, default=0.25)
    parser.add_argument("--pressure-gate-require-sell-floor", action="store_true")
    parser.add_argument("--events-path", default=str(DEFAULT_EVENTS_PATH))
    parser.add_argument("--report-path", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--i-understand-this-places-live-orders", action="store_true")
    args = parser.parse_args()

    if not args.i_understand_this_places_live_orders:
        raise SystemExit("Refusing to run without --i-understand-this-places-live-orders")
    if args.quote_usd <= 0.0 or args.quote_usd > args.max_quote_usd:
        raise SystemExit("--quote-usd must be positive and <= --max-quote-usd")

    client = KrakenSpotClient()
    event_path = Path(args.events_path)
    report_path = Path(args.report_path)
    pair = load_pair(client, args.product)
    if pair.quote != "USD" and not args.allow_non_usd_quote:
        raise RuntimeError(f"Refusing non-USD quote pair without --allow-non-usd-quote: {pair.wsname}")
    if pair.quote != "USD" and (args.quote_amount is None or args.max_quote_amount is None):
        raise RuntimeError("Non-USD quote pairs require explicit --quote-amount and --max-quote-amount in quote currency")
    quote_amount = float(args.quote_amount if args.quote_amount is not None else args.quote_usd)
    max_quote_amount = float(args.max_quote_amount if args.max_quote_amount is not None else args.max_quote_usd)
    if quote_amount <= 0.0 or quote_amount > max_quote_amount:
        raise SystemExit("--quote-amount must be positive and <= --max-quote-amount")
    if args.pressure_gate_summary_path is not None:
        gate = pressure_gate_status(
            args.pressure_gate_summary_path,
            product_id=pair.wsname.replace("/", "-"),
            min_cycles=max(1, args.pressure_gate_min_cycles),
            min_two_sided_rate=max(0.0, args.pressure_gate_min_two_sided_rate),
            require_sell_floor=bool(args.pressure_gate_require_sell_floor),
        )
        if not gate.get("ok"):
            event_path = Path(args.events_path)
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "live_roundtrip_entry_veto_pressure_gate",
                    **gate,
                },
            )
            raise RuntimeError(f"Pressure gate failed: {gate}")

    balances_before = nonzero_balances(client)
    normalized_before = normalized_balance_amounts(balances_before)
    quote_available = normalized_before.get(pair.quote, 0.0)
    if quote_available < quote_amount * 1.01:
        raise RuntimeError(
            f"Insufficient {pair.quote} for probe: have {quote_available:.12f}, need about {quote_amount:.12f}"
        )

    bid, ask = fetch_bid_ask(client, pair)
    entry_price = legal_maker_buy_price(bid, ask, pair.tick_size, improve_ticks=max(0, args.entry_improve_ticks))
    raw_volume = quote_amount / entry_price
    volume = legal_volume(raw_volume, pair.lot_decimals)
    if volume <= 0.0:
        raise RuntimeError("Computed zero volume")

    def entry_floor_metrics(candidate_entry_price: float) -> dict[str, float]:
        candidate_notional = volume * candidate_entry_price
        candidate_fee = candidate_notional * (args.maker_fee_bps / 10000.0)
        candidate_required_exit_price, candidate_required_exit_price_raw = maker_exit_floor_price(
            entry_cost=candidate_notional,
            entry_fee=candidate_fee,
            volume=volume,
            maker_fee_bps=args.maker_fee_bps,
            target_net_pct=args.target_net_pct,
            tick_size=pair.tick_size,
        )
        return {
            "estimated_notional": candidate_notional,
            "estimated_entry_fee": candidate_fee,
            "estimated_required_exit_price": candidate_required_exit_price,
            "estimated_required_exit_price_raw": candidate_required_exit_price_raw,
            "estimated_exit_floor_above_ask_bps": exit_floor_above_ask_bps(
                candidate_required_exit_price,
                ask,
            ),
        }

    metrics = entry_floor_metrics(entry_price)
    estimated_notional = metrics["estimated_notional"]
    if estimated_notional < pair.cost_min:
        raise RuntimeError(f"Estimated notional {estimated_notional:.6f} is below costmin {pair.cost_min:.6f}")
    if estimated_notional > max_quote_amount:
        raise RuntimeError(f"Estimated notional {estimated_notional:.12f} exceeds max {max_quote_amount:.12f}")
    estimated_entry_fee = metrics["estimated_entry_fee"]
    estimated_required_exit_price = metrics["estimated_required_exit_price"]
    estimated_required_exit_price_raw = metrics["estimated_required_exit_price_raw"]
    estimated_exit_floor_above_ask_bps = metrics["estimated_exit_floor_above_ask_bps"]

    append_jsonl(
        event_path,
        {
            "ts_utc": utc_now_iso(),
            "action": "live_roundtrip_entry_submit_attempt",
            "product_id": pair.wsname.replace("/", "-"),
            "quote_currency": pair.quote,
            "quote_usd": round(args.quote_usd, 6),
            "quote_amount": round(quote_amount, 12),
            "max_quote_amount": round(max_quote_amount, 12),
            "bid": bid,
            "ask": ask,
            "entry_price": entry_price,
            "volume": round(volume, 10),
            "estimated_notional": round(estimated_notional, 8),
            "estimated_entry_fee": round(estimated_entry_fee, 8),
            "estimated_required_exit_price": estimated_required_exit_price,
            "estimated_required_exit_price_raw": estimated_required_exit_price_raw,
            "estimated_exit_floor_above_ask_bps": estimated_exit_floor_above_ask_bps,
            "require_exit_floor_reachable": bool(args.require_exit_floor_reachable),
            "max_exit_floor_above_ask_bps": args.max_exit_floor_above_ask_bps,
            "balances_before": balances_before,
        },
    )
    entry_submit_book: dict[str, Any] = {}
    if args.record_book_snapshots:
        entry_submit_book = append_book_snapshot_event(
            client,
            pair=pair,
            event_path=event_path,
            label="entry_submit_attempt",
            quote_amount=quote_amount,
            depth_count=args.book_depth_count,
        )
    if (
        args.require_exit_floor_reachable
        and estimated_exit_floor_above_ask_bps > args.max_exit_floor_above_ask_bps
    ):
        veto_reason = (
            "required fee-paid target exit is too far above current ask "
            f"({estimated_exit_floor_above_ask_bps:.6f} bps > {args.max_exit_floor_above_ask_bps:.6f} bps)"
        )
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": "live_roundtrip_entry_veto_exit_floor_unreachable",
                "product_id": pair.wsname.replace("/", "-"),
                "quote_currency": pair.quote,
                "quote_usd": round(args.quote_usd, 6),
                "quote_amount": round(quote_amount, 12),
                "max_quote_amount": round(max_quote_amount, 12),
                "bid": bid,
                "ask": ask,
                "entry_price": entry_price,
                "volume": round(volume, 10),
                "estimated_notional": round(estimated_notional, 8),
                "estimated_entry_fee": round(estimated_entry_fee, 8),
                "estimated_required_exit_price": estimated_required_exit_price,
                "estimated_required_exit_price_raw": estimated_required_exit_price_raw,
                "estimated_exit_floor_above_ask_bps": estimated_exit_floor_above_ask_bps,
                "max_exit_floor_above_ask_bps": args.max_exit_floor_above_ask_bps,
                "reason": veto_reason,
            },
        )
        balances_after = nonzero_balances(client)
        report = {
            "ts_utc": utc_now_iso(),
            "product_id": pair.wsname.replace("/", "-"),
            "entry_txid": "",
            "entry_txid_chain": [],
            "entry_status": {
                "status": "vetoed",
                "reason": veto_reason,
                "estimated_exit_floor_above_ask_bps": estimated_exit_floor_above_ask_bps,
                "max_exit_floor_above_ask_bps": args.max_exit_floor_above_ask_bps,
            },
            "entry_submit_book": entry_submit_book,
            "exit_txid": "",
            "exit_txid_chain": [],
            "exit_status": {},
            "balances_before": balances_before,
            "balances_after": balances_after,
            "trade_balance_usd": {},
            "events_path": str(event_path),
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    def build_entry_reprice_price(live_bid: float, live_ask: float) -> float:
        candidate_price = legal_maker_buy_price(
            live_bid,
            live_ask,
            pair.tick_size,
            improve_ticks=max(0, args.entry_improve_ticks),
        )
        candidate_notional = volume * candidate_price
        candidate_fee = candidate_notional * (args.maker_fee_bps / 10000.0)
        candidate_required_exit_price, candidate_required_exit_price_raw = maker_exit_floor_price(
            entry_cost=candidate_notional,
            entry_fee=candidate_fee,
            volume=volume,
            maker_fee_bps=args.maker_fee_bps,
            target_net_pct=args.target_net_pct,
            tick_size=pair.tick_size,
        )
        candidate_floor_above_ask_bps = exit_floor_above_ask_bps(candidate_required_exit_price, live_ask)
        if candidate_notional > max_quote_amount:
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "live_roundtrip_entry_reprice_veto_max_quote",
                    "product_id": pair.wsname.replace("/", "-"),
                    "bid": live_bid,
                    "ask": live_ask,
                    "candidate_entry_price": candidate_price,
                    "volume": round(volume, 10),
                    "candidate_notional": round(candidate_notional, 12),
                    "max_quote_amount": round(max_quote_amount, 12),
                    "quote_currency": pair.quote,
                },
            )
            return 0.0
        if (
            args.require_exit_floor_reachable
            and candidate_floor_above_ask_bps > args.max_exit_floor_above_ask_bps
        ):
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "live_roundtrip_entry_reprice_veto_exit_floor_unreachable",
                    "product_id": pair.wsname.replace("/", "-"),
                    "bid": live_bid,
                    "ask": live_ask,
                    "candidate_entry_price": candidate_price,
                    "volume": round(volume, 10),
                    "candidate_notional": round(candidate_notional, 8),
                    "candidate_entry_fee": round(candidate_fee, 8),
                    "candidate_required_exit_price": candidate_required_exit_price,
                    "candidate_required_exit_price_raw": candidate_required_exit_price_raw,
                    "candidate_exit_floor_above_ask_bps": candidate_floor_above_ask_bps,
                    "max_exit_floor_above_ask_bps": args.max_exit_floor_above_ask_bps,
                },
            )
            return 0.0
        return candidate_price

    entry_txid = submit_order(
        client,
        pair=pair,
        side="buy",
        volume=volume,
        price=entry_price,
        event_path=event_path,
        label="entry",
    )
    if args.record_book_snapshots:
        append_book_snapshot_event(
            client,
            pair=pair,
            event_path=event_path,
            label="entry_order_submitted",
            quote_amount=quote_amount,
            txid=entry_txid,
            depth_count=args.book_depth_count,
        )
    entry_txid, entry_status, entry_txid_chain = poll_post_only_with_reprice(
        client,
        txid=entry_txid,
        pair=pair,
        side="buy",
        volume=volume,
        current_price=entry_price,
        event_path=event_path,
        label="entry",
        max_wait_seconds=args.entry_wait_seconds,
        poll_seconds=args.poll_seconds,
        reprice_seconds=args.entry_reprice_seconds,
        max_reprices=max(0, args.entry_max_reprices),
        price_builder=build_entry_reprice_price,
    )

    filled_volume = to_float(entry_status.get("vol_exec"))
    entry_cost = to_float(entry_status.get("cost"))
    entry_fee = to_float(entry_status.get("fee"))
    entry_avg_price = to_float(entry_status.get("price"), entry_price)
    exit_txid = ""
    exit_txid_chain: list[str] = []
    exit_status: dict[str, Any] = {}
    if filled_volume > 0.0:
        if args.record_book_snapshots:
            append_book_snapshot_event(
                client,
                pair=pair,
                event_path=event_path,
                label="entry_filled_before_exit_submit",
                quote_amount=quote_amount,
                base_volume=filled_volume,
                txid=entry_txid,
                depth_count=args.book_depth_count,
            )
        exit_bid, exit_ask = fetch_bid_ask(client, pair)
        required_price, required_price_raw = maker_exit_floor_price(
            entry_cost=entry_cost,
            entry_fee=entry_fee,
            volume=filled_volume,
            maker_fee_bps=args.maker_fee_bps,
            target_net_pct=args.target_net_pct,
            tick_size=pair.tick_size,
        )
        exit_price = legal_maker_sell_price(
            exit_bid,
            exit_ask,
            pair.tick_size,
            minimum_price=required_price,
            inside_spread=bool(args.exit_inside_spread),
        )
        exit_volume = legal_volume(filled_volume, pair.lot_decimals)
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": "live_roundtrip_exit_submit_attempt",
                "product_id": pair.wsname.replace("/", "-"),
                "entry_txid": entry_txid,
                "filled_volume": round(filled_volume, 10),
                "entry_cost": entry_cost,
                "entry_fee": entry_fee,
                "entry_avg_price": entry_avg_price,
                "bid": exit_bid,
                "ask": exit_ask,
                "spread_bps": round((exit_ask - exit_bid) / exit_bid * 10000.0, 6) if exit_bid > 0.0 else 0.0,
                "exit_price": exit_price,
                "required_min_exit_price": required_price,
                "required_min_exit_price_raw": required_price_raw,
                "exit_floor_above_ask_bps": exit_floor_above_ask_bps(required_price, exit_ask),
                "exit_inside_spread": bool(args.exit_inside_spread),
                "exit_volume": round(exit_volume, 10),
                "target_net_pct": args.target_net_pct,
            },
        )
        exit_txid = submit_order(
            client,
            pair=pair,
            side="sell",
            volume=exit_volume,
            price=exit_price,
            event_path=event_path,
            label="exit",
        )
        if args.record_book_snapshots:
            append_book_snapshot_event(
                client,
                pair=pair,
                event_path=event_path,
                label="exit_order_submitted",
                quote_amount=quote_amount,
                base_volume=exit_volume,
                txid=exit_txid,
                depth_count=args.book_depth_count,
            )
        exit_txid, exit_status, exit_txid_chain = poll_post_only_with_reprice(
            client,
            txid=exit_txid,
            pair=pair,
            side="sell",
            volume=exit_volume,
            current_price=exit_price,
            event_path=event_path,
            label="exit",
            max_wait_seconds=args.exit_wait_seconds,
            poll_seconds=args.poll_seconds,
            reprice_seconds=args.exit_reprice_seconds,
            max_reprices=max(0, args.exit_max_reprices),
            price_builder=lambda live_bid, live_ask: legal_maker_sell_price(
                live_bid,
                live_ask,
                pair.tick_size,
                minimum_price=required_price,
                inside_spread=bool(args.exit_inside_spread),
            ),
        )
        if args.record_book_snapshots:
            append_book_snapshot_event(
                client,
                pair=pair,
                event_path=event_path,
                label="exit_terminal",
                quote_amount=quote_amount,
                base_volume=exit_volume,
                txid=exit_txid,
                depth_count=args.book_depth_count,
            )

    balances_after = nonzero_balances(client)
    trade_balance = client._request("POST", "/0/private/TradeBalance", params={"asset": "ZUSD"}, private=True)
    report = {
        "ts_utc": utc_now_iso(),
        "product_id": pair.wsname.replace("/", "-"),
        "entry_txid": entry_txid,
        "entry_txid_chain": entry_txid_chain,
        "entry_status": entry_status,
        "exit_txid": exit_txid,
        "exit_txid_chain": exit_txid_chain,
        "exit_status": exit_status,
        "entry_submit_book": entry_submit_book,
        "balances_before": balances_before,
        "balances_after": balances_after,
        "trade_balance_usd": trade_balance,
        "events_path": str(event_path),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
