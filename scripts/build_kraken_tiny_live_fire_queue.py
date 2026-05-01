#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kraken_spot_client import KrakenPair, KrakenSpotClient, normalize_asset, parse_pair, to_float  # noqa: E402
from run_kraken_tiny_live_maker_roundtrip_probe import (  # noqa: E402
    exit_floor_above_ask_bps,
    legal_price,
    legal_maker_buy_price,
    legal_volume,
    maker_exit_floor_price,
)


DEFAULT_VALIDATE_EVENT_PATH = (
    REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_fast_cooldown_ab_events.jsonl"
)
DEFAULT_TINY_LIVE_EVENT_PATH = REPORTS / "kraken_tiny_live_maker_roundtrip_events.jsonl"
DEFAULT_BTC_QUOTE_TINY_LIVE_EVENT_PATH = REPORTS / "kraken_tiny_live_btc_quote_roundtrip_events.jsonl"
DEFAULT_PARALLEL_USD_TINY_LIVE_EVENT_PATH = REPORTS / "kraken_tiny_live_parallel_usd_roundtrip_events.jsonl"
DEFAULT_JSON_PATH = REPORTS / "kraken_tiny_live_fire_queue.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_tiny_live_fire_queue.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_tiny_live_fire_queue.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_ts_utc(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_jsonl(path: Path, *, max_rows: int = 20000) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()[-max_rows:]:
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


def normalize_product_id(raw: str) -> str:
    product_id = str(raw or "").upper().replace("/", "-")
    parts = [normalize_asset(part) for part in product_id.split("-") if part]
    return "-".join(parts)


def parse_csv_set(raw: str) -> set[str]:
    return {normalize_asset(part.strip()) for part in str(raw or "").split(",") if part.strip()}


def parse_float_csv(raw: str) -> list[float]:
    values: list[float] = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        values.append(float(part))
    return values


def offset_key(product: str, side: str, offset_frac: float) -> str:
    return f"{normalize_product_id(product)}|{side.lower()}|{float(offset_frac):.4f}"


def normalize_microfill_offset_key(raw_key: str) -> str:
    parts = str(raw_key or "").split("|")
    if len(parts) != 3:
        return str(raw_key or "")
    return offset_key(parts[0], parts[1], to_float(parts[2]))


def legal_maker_buy_price_at_offset(bid: float, ask: float, tick_size: float, offset_frac: float) -> float:
    """Return a legal post-only buy inside the spread at a fractional bid->ask offset."""
    if bid <= 0.0 or ask <= 0.0:
        return 0.0
    if ask <= bid:
        return legal_price(bid, tick_size, side="buy")
    offset = max(0.0, min(0.99, float(offset_frac)))
    raw = bid + ((ask - bid) * offset)
    if tick_size > 0.0:
        raw = min(raw, ask - tick_size)
    if raw <= 0.0 or raw < bid:
        raw = bid
    return legal_price(raw, tick_size, side="buy")


def microfill_fill_like_stats(path_rows: dict[str, Any]) -> dict[str, dict[str, float]]:
    stats: dict[str, dict[str, float]] = {}
    success_keys = {"hard_cross_fill_proxy", "probable_queue_depletion_fill_proxy"}
    for raw_key, counts in path_rows.items():
        if not isinstance(counts, dict):
            continue
        trials = sum(to_float(value) for value in counts.values())
        fill_like = sum(to_float(value) for key, value in counts.items() if str(key) in success_keys)
        stats[normalize_microfill_offset_key(str(raw_key))] = {
            "trials": trials,
            "fill_like": fill_like,
            "rate": fill_like / trials if trials > 0.0 else 0.0,
        }
    return stats


def load_microfill_offset_stats(paths: list[Path]) -> dict[str, dict[str, float]]:
    stats: dict[str, dict[str, float]] = {}
    for path in paths:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else payload
        if not isinstance(summary, dict):
            continue
        offset_rows = summary.get("by_product_side_offset")
        if not isinstance(offset_rows, dict):
            continue
        for key, value in microfill_fill_like_stats(offset_rows).items():
            existing = stats.get(key, {"trials": 0.0, "fill_like": 0.0, "rate": 0.0})
            trials = existing["trials"] + value["trials"]
            fill_like = existing["fill_like"] + value["fill_like"]
            stats[key] = {
                "trials": trials,
                "fill_like": fill_like,
                "rate": fill_like / trials if trials > 0.0 else 0.0,
            }
    return stats


def pair_map_for_quotes(asset_pairs_payload: dict[str, Any], quote_currencies: set[str]) -> dict[str, KrakenPair]:
    out: dict[str, KrakenPair] = {}
    for rest_pair, payload in asset_pairs_payload.items():
        if not isinstance(payload, dict):
            continue
        pair = parse_pair(str(rest_pair), payload)
        if pair is None:
            continue
        if pair.quote not in quote_currencies:
            continue
        if pair.status.lower() not in {"online", "post_only"}:
            continue
        out[product_id_for_pair(pair)] = pair
    return out


def quote_usd_rates(client: KrakenSpotClient, all_pairs: dict[str, KrakenPair], quote_currencies: set[str]) -> dict[str, float]:
    rates: dict[str, float] = {"USD": 1.0}
    request_products: dict[str, KrakenPair] = {}
    for quote in quote_currencies:
        if quote == "USD":
            continue
        direct = all_pairs.get(f"{quote}-USD")
        inverse = all_pairs.get(f"USD-{quote}")
        if direct:
            request_products[f"{quote}-USD"] = direct
        elif inverse:
            request_products[f"USD-{quote}"] = inverse
    tickers = fetch_tickers(client, request_products) if request_products else {}
    for product_id, pair in request_products.items():
        metrics = ticker_metrics(tickers.get(product_id, {}))
        mid = (metrics["bid"] + metrics["ask"]) / 2.0 if metrics["bid"] > 0.0 and metrics["ask"] > 0.0 else metrics["last"]
        if mid <= 0.0:
            continue
        if pair.quote == "USD":
            rates[pair.base] = mid
        elif pair.base == "USD":
            rates[pair.quote] = 1.0 / mid
    return rates


def latest_validate_evidence(paths: list[Path]) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for path in paths:
        for row in load_jsonl(path):
            if str(row.get("action") or "") != "kraken_validate_order":
                continue
            product_id = str(row.get("product_id") or "").upper()
            if not product_id:
                continue
            evidence[product_id] = {
                "ok": bool(row.get("ok")),
                "status": str(row.get("status") or ""),
                "ts_utc": str(row.get("ts_utc") or ""),
                "path": str(path),
                "error": str(row.get("error") or row.get("exception") or ""),
            }
    return evidence


def latest_live_entry_evidence(paths: list[Path]) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    txid_to_product: dict[str, str] = {}
    txid_exec: dict[str, float] = {}
    for path in paths:
        for row in load_jsonl(path):
            action = str(row.get("action") or "")
            product_id = normalize_product_id(str(row.get("product_id") or ""))
            txid = str(row.get("txid") or "")
            ts_utc = str(row.get("ts_utc") or "")
            if product_id and action in {"live_roundtrip_entry_submit_attempt", "live_roundtrip_entry_submitted"}:
                if txid:
                    txid_to_product[txid] = product_id
                evidence[product_id] = {
                    "outcome": "entry_submitted",
                    "ok": False,
                    "ts_utc": ts_utc,
                    "path": str(path),
                    "txid": txid,
                }
                continue
            if action == "live_roundtrip_entry_status" and txid:
                product_for_txid = txid_to_product.get(txid)
                vol_exec = to_float(row.get("vol_exec"))
                txid_exec[txid] = max(txid_exec.get(txid, 0.0), vol_exec)
                if product_for_txid and vol_exec > 0.0:
                    evidence[product_for_txid] = {
                        "outcome": "entry_filled_or_partial",
                        "ok": True,
                        "ts_utc": ts_utc,
                        "path": str(path),
                        "txid": txid,
                        "vol_exec": vol_exec,
                    }
                continue
            if action == "live_roundtrip_entry_cancel_requested":
                chain = row.get("txid_chain") if isinstance(row.get("txid_chain"), list) else []
                chain_ids = [str(item) for item in chain if str(item)]
                if txid:
                    chain_ids.append(txid)
                products = {txid_to_product.get(item, "") for item in chain_ids}
                products.discard("")
                if len(products) != 1:
                    continue
                product_for_chain = next(iter(products))
                max_exec = max((txid_exec.get(item, 0.0) for item in chain_ids), default=0.0)
                if max_exec <= 0.0:
                    evidence[product_for_chain] = {
                        "outcome": "entry_canceled_unfilled",
                        "ok": False,
                        "ts_utc": ts_utc,
                        "path": str(path),
                        "txid_chain": chain_ids,
                    }
    return evidence


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[idx : idx + size] for idx in range(0, len(items), max(1, size))]


def fetch_tickers(client: KrakenSpotClient, pairs: dict[str, KrakenPair], *, chunk_size: int = 45) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    rest_to_product = {pair.rest_pair: product_id for product_id, pair in pairs.items()}
    for rest_chunk in chunked([pair.rest_pair for pair in pairs.values()], chunk_size):
        payload = client.ticker(rest_chunk)
        if not isinstance(payload, dict):
            continue
        for rest_pair, row in payload.items():
            product_id = rest_to_product.get(str(rest_pair))
            if not product_id and rest_chunk:
                # Kraken may answer with altname keys. Fall back to order when batch size is one.
                product_id = rest_to_product.get(rest_chunk[0]) if len(rest_chunk) == 1 else ""
            if product_id and isinstance(row, dict):
                out[product_id] = row
    return out


def ticker_metrics(row: dict[str, Any], *, quote_usd_rate: float = 1.0) -> dict[str, float]:
    bid = to_float((row.get("b") or [None])[0])
    ask = to_float((row.get("a") or [None])[0])
    last = to_float((row.get("c") or [None])[0])
    open_24h = to_float(row.get("o"))
    high = to_float((row.get("h") or [None, None])[1])
    low = to_float((row.get("l") or [None, None])[1])
    volume_base = to_float((row.get("v") or [None, None])[1])
    trade_count_24h = to_float((row.get("t") or [None, None])[1])
    mid = (bid + ask) / 2.0 if bid > 0.0 and ask > 0.0 else last
    return {
        "bid": bid,
        "ask": ask,
        "last": last,
        "open_24h": open_24h,
        "spread_bps": ((ask - bid) / bid * 10000.0) if bid > 0.0 and ask > 0.0 else 0.0,
        "ret_24h_bps": ((last - open_24h) / open_24h * 10000.0) if last > 0.0 and open_24h > 0.0 else 0.0,
        "range_24h_bps": ((high - low) / mid * 10000.0) if high > 0.0 and low > 0.0 and mid > 0.0 else 0.0,
        "volume_24h_base": volume_base,
        "volume_24h_quote": volume_base * mid if volume_base > 0.0 and mid > 0.0 else 0.0,
        "volume_24h_usd": volume_base * mid * quote_usd_rate if volume_base > 0.0 and mid > 0.0 else 0.0,
        "trade_count_24h": trade_count_24h,
    }


def parse_depth_side(rows: Any) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    if not isinstance(rows, list):
        return out
    for item in rows:
        if not isinstance(item, list) or len(item) < 2:
            continue
        price = to_float(item[0])
        volume = to_float(item[1])
        if price > 0.0 and volume > 0.0:
            out.append((price, volume))
    return out


def depth_metrics(client: KrakenSpotClient, pair: KrakenPair, *, entry_price: float, exit_floor: float) -> dict[str, Any]:
    payload = client.depth(pair.rest_pair, count=20)
    row = payload.get(pair.rest_pair) if isinstance(payload, dict) else None
    if not isinstance(row, dict) and isinstance(payload, dict) and payload:
        row = next(iter(payload.values()))
    if not isinstance(row, dict):
        return {"depth_ok": False, "depth_error": "missing_depth_row"}
    bids = parse_depth_side(row.get("bids"))
    asks = parse_depth_side(row.get("asks"))
    bid_queue_base = sum(volume for price, volume in bids if price >= entry_price)
    ask_wall_base = sum(volume for price, volume in asks if price <= exit_floor)
    ask_wall_usd = sum(price * volume for price, volume in asks if price <= exit_floor)
    return {
        "depth_ok": True,
        "bid_queue_base_at_or_above_entry": round(bid_queue_base, 10),
        "ask_wall_base_at_or_below_exit_floor": round(ask_wall_base, 10),
        "ask_wall_usd_at_or_below_exit_floor": round(ask_wall_usd, 6),
        "top_bid_depth_base": round(bids[0][1], 10) if bids else 0.0,
        "top_ask_depth_base": round(asks[0][1], 10) if asks else 0.0,
    }


def live_exposure_snapshot(
    client: KrakenSpotClient,
    *,
    quote_usd: float,
    quote_amounts: dict[str, float],
    block_on_open_orders: bool,
) -> dict[str, Any]:
    balance = client.balance()
    open_orders = client._request("POST", "/0/private/OpenOrders", private=True)
    open_order_rows = open_orders.get("open") if isinstance(open_orders, dict) else {}
    open_order_rows = open_order_rows if isinstance(open_order_rows, dict) else {}
    normalized_balance: dict[str, float] = {}
    for asset, value in balance.items():
        normalized_balance[normalize_asset(str(asset))] = normalized_balance.get(normalize_asset(str(asset)), 0.0) + to_float(value)
    usd_free = normalized_balance.get("USD", 0.0)
    non_usd = {
        asset: value
        for asset, value in normalized_balance.items()
        if asset != "USD" and abs(value) > 0.0
    }
    hard_blockers: list[str] = []
    warnings: list[str] = []
    quote_balance: dict[str, float] = {}
    quote_balance_blockers: list[str] = []
    for quote, needed in quote_amounts.items():
        available = normalized_balance.get(quote, 0.0)
        quote_balance[quote] = round(available, 12)
        if available < needed * 1.01:
            quote_balance_blockers.append(f"insufficient_{quote}_for_probe")
    if open_order_rows and block_on_open_orders:
        hard_blockers.append("live_open_orders_present")
    if non_usd:
        warnings.append("non_usd_inventory_present")
    return {
        "queried": True,
        "usd_free": round(usd_free, 8),
        "quote_balances": quote_balance,
        "quote_balance_blockers": quote_balance_blockers,
        "open_order_count": len(open_order_rows),
        "open_order_ids": list(open_order_rows.keys()),
        "non_usd_balances": non_usd,
        "hard_blockers": hard_blockers,
        "warnings": warnings,
    }


def local_exposure_snapshot() -> dict[str, Any]:
    return {
        "queried": False,
        "usd_free": 0.0,
        "quote_balances": {},
        "quote_balance_blockers": [],
        "open_order_count": None,
        "open_order_ids": [],
        "non_usd_balances": {},
        "hard_blockers": ["private_exposure_not_checked"],
        "warnings": [],
    }


def candidate_row(
    *,
    product_id: str,
    pair: KrakenPair,
    ticker: dict[str, Any],
    validate_evidence: dict[str, Any] | None,
    live_entry_evidence: dict[str, Any] | None,
    quote_usd: float,
    max_quote_usd: float,
    quote_amount: float,
    max_quote_amount: float,
    quote_usd_rate: float,
    quote_balance: float | None,
    maker_fee_bps: float,
    target_net_pct: float,
    entry_improve_ticks: int,
    entry_offset_frac: float | None,
    max_entry_concession_bps: float,
    min_entry_spread_cushion_bps: float,
    microfill_offset_stats: dict[str, dict[str, float]],
    min_entry_microfill_rate: float,
    min_entry_microfill_trials: float,
    min_exit_microfill_rate: float,
    min_exit_microfill_trials: float,
    max_exit_floor_above_ask_bps: float,
    min_volume_24h_usd: float,
    min_trades_24h: float,
    max_spread_bps: float,
    min_ret_24h_bps: float,
    entry_miss_cooldown_minutes: float,
    now: datetime,
) -> dict[str, Any]:
    metrics = ticker_metrics(ticker, quote_usd_rate=quote_usd_rate)
    bid = metrics["bid"]
    ask = metrics["ask"]
    blockers: list[str] = []
    warnings: list[str] = []
    if bid <= 0.0 or ask <= 0.0:
        blockers.append("bad_bid_ask")
        entry_price = 0.0
        volume = 0.0
        estimated_notional = 0.0
        estimated_entry_fee = 0.0
        exit_floor = 0.0
        exit_floor_raw = 0.0
        floor_above_ask_bps = 0.0
    else:
        if entry_offset_frac is None:
            entry_price = legal_maker_buy_price(bid, ask, pair.tick_size, improve_ticks=max(0, entry_improve_ticks))
            entry_price_model = "improve_ticks"
        else:
            entry_price = legal_maker_buy_price_at_offset(bid, ask, pair.tick_size, entry_offset_frac)
            entry_price_model = "spread_offset_frac"
        volume = legal_volume(quote_amount / entry_price, pair.lot_decimals)
        estimated_notional = volume * entry_price
        estimated_entry_fee = estimated_notional * (maker_fee_bps / 10000.0)
        exit_floor, exit_floor_raw = maker_exit_floor_price(
            entry_cost=estimated_notional,
            entry_fee=estimated_entry_fee,
            volume=volume,
            maker_fee_bps=maker_fee_bps,
            target_net_pct=target_net_pct,
            tick_size=pair.tick_size,
        )
        floor_above_ask_bps = exit_floor_above_ask_bps(exit_floor, ask)
    if bid <= 0.0 or ask <= 0.0 or entry_price <= 0.0:
        entry_concession_bps = 0.0
        entry_spread_cushion_bps = 0.0
        target_exit_move_bps_from_entry = 0.0
    else:
        entry_concession_bps = max(0.0, (entry_price - bid) / bid * 10000.0)
        entry_spread_cushion_bps = max(0.0, (ask - entry_price) / entry_price * 10000.0)
        target_exit_move_bps_from_entry = max(0.0, (exit_floor - entry_price) / entry_price * 10000.0)
    if "entry_price_model" not in locals():
        entry_price_model = "improve_ticks"

    if volume <= 0.0:
        blockers.append("zero_legal_volume")
    if estimated_notional < pair.cost_min:
        blockers.append("below_pair_cost_min")
    estimated_notional_usd = estimated_notional * quote_usd_rate
    if estimated_notional > max_quote_amount:
        blockers.append("above_max_quote_amount")
    if estimated_notional_usd > max_quote_usd:
        blockers.append("above_max_quote_usd_equivalent")
    if quote_balance is not None and estimated_notional > quote_balance * 0.99:
        blockers.append("insufficient_quote_balance")
    if metrics["volume_24h_usd"] < min_volume_24h_usd:
        blockers.append("low_24h_usd_volume")
    if metrics["trade_count_24h"] < min_trades_24h:
        blockers.append("low_24h_trade_count")
    if metrics["spread_bps"] > max_spread_bps:
        blockers.append("spread_too_wide")
    if max_entry_concession_bps >= 0.0 and entry_concession_bps > max_entry_concession_bps:
        blockers.append("entry_concession_too_high")
    if min_entry_spread_cushion_bps > 0.0 and entry_spread_cushion_bps < min_entry_spread_cushion_bps:
        blockers.append("entry_spread_cushion_too_low")
    if floor_above_ask_bps > max_exit_floor_above_ask_bps:
        blockers.append("exit_floor_unreachable")
    if metrics["ret_24h_bps"] < min_ret_24h_bps:
        warnings.append("weak_24h_momentum")

    if validate_evidence is None:
        blockers.append("validate_only_missing")
        validate_status = "missing"
        validate_ok = False
    else:
        validate_ok = bool(validate_evidence.get("ok"))
        validate_status = str(validate_evidence.get("status") or "")
        if not validate_ok:
            blockers.append("validate_only_failed")

    entry_microfill_key = offset_key(product_id, "buy", entry_offset_frac) if entry_offset_frac is not None else ""
    entry_microfill = microfill_offset_stats.get(entry_microfill_key, {}) if entry_microfill_key else {}
    entry_microfill_trials = to_float(entry_microfill.get("trials"))
    entry_microfill_rate = to_float(entry_microfill.get("rate"))
    entry_microfill_fill_like = to_float(entry_microfill.get("fill_like"))
    if entry_offset_frac is not None and (min_entry_microfill_rate > 0.0 or min_entry_microfill_trials > 0.0):
        if entry_microfill_trials < min_entry_microfill_trials:
            blockers.append("entry_microfill_insufficient_trials")
        elif entry_microfill_rate < min_entry_microfill_rate:
            blockers.append("entry_microfill_rate_too_low")

    exit_microfill_key = offset_key(product_id, "sell", entry_offset_frac) if entry_offset_frac is not None else ""
    exit_microfill = microfill_offset_stats.get(exit_microfill_key, {}) if exit_microfill_key else {}
    exit_microfill_trials = to_float(exit_microfill.get("trials"))
    exit_microfill_rate = to_float(exit_microfill.get("rate"))
    exit_microfill_fill_like = to_float(exit_microfill.get("fill_like"))
    if entry_offset_frac is not None and (min_exit_microfill_rate > 0.0 or min_exit_microfill_trials > 0.0):
        if exit_microfill_trials < min_exit_microfill_trials:
            blockers.append("exit_microfill_insufficient_trials")
        elif exit_microfill_rate < min_exit_microfill_rate:
            blockers.append("exit_microfill_rate_too_low")

    live_entry_outcome = str((live_entry_evidence or {}).get("outcome") or "")
    live_entry_ts = str((live_entry_evidence or {}).get("ts_utc") or "")
    live_entry_dt = parse_ts_utc(live_entry_ts)
    live_entry_age_minutes = (
        max(0.0, (now - live_entry_dt).total_seconds() / 60.0)
        if live_entry_dt is not None and live_entry_dt.tzinfo is not None
        else None
    )
    if (
        entry_miss_cooldown_minutes > 0.0
        and live_entry_outcome == "entry_canceled_unfilled"
        and live_entry_age_minutes is not None
        and live_entry_age_minutes <= entry_miss_cooldown_minutes
    ):
        blockers.append("recent_live_entry_miss")

    readiness = "fire_candidate" if not blockers else "blocked"
    if blockers == ["validate_only_missing"]:
        readiness = "needs_validate_only"
    elif blockers and set(blockers) <= {"validate_only_missing"}:
        readiness = "needs_validate_only"
    elif "exit_floor_unreachable" in blockers:
        readiness = "blocked_exit_floor"

    score = (
        max(0.0, max_exit_floor_above_ask_bps - floor_above_ask_bps) * 4.0
        + min(metrics["spread_bps"], 80.0)
        + max(0.0, metrics["ret_24h_bps"]) / 20.0
        + math.log10(max(metrics["volume_24h_usd"], 1.0))
    )
    return {
        "product_id": product_id,
        "quote_currency": pair.quote,
        "readiness": readiness,
        "blockers": blockers,
        "warnings": warnings,
        "validate_ok": validate_ok,
        "validate_status": validate_status,
        "validate_ts_utc": str((validate_evidence or {}).get("ts_utc") or ""),
        "live_entry_outcome": live_entry_outcome,
        "live_entry_ts_utc": live_entry_ts,
        "live_entry_age_minutes": round(live_entry_age_minutes, 3) if live_entry_age_minutes is not None else None,
        "bid": round(bid, 12),
        "ask": round(ask, 12),
        "last": round(metrics["last"], 12),
        "entry_price_model": entry_price_model,
        "entry_offset_frac": round(entry_offset_frac, 6) if entry_offset_frac is not None else None,
        "entry_improve_ticks": entry_improve_ticks if entry_offset_frac is None else None,
        "entry_price": entry_price,
        "entry_concession_bps": round(entry_concession_bps, 6),
        "entry_spread_cushion_bps": round(entry_spread_cushion_bps, 6),
        "volume": round(volume, 10),
        "quote_usd_rate": round(quote_usd_rate, 8),
        "quote_balance": round(quote_balance, 12) if quote_balance is not None else None,
        "quote_amount": round(quote_amount, 12),
        "max_quote_amount": round(max_quote_amount, 12),
        "estimated_notional": round(estimated_notional, 12),
        "estimated_notional_quote": round(estimated_notional, 12),
        "estimated_notional_usd": round(estimated_notional_usd, 8),
        "estimated_entry_fee": round(estimated_entry_fee, 8),
        "estimated_entry_fee_quote": round(estimated_entry_fee, 12),
        "estimated_entry_fee_usd": round(estimated_entry_fee * quote_usd_rate, 8),
        "estimated_required_exit_price": exit_floor,
        "estimated_required_exit_price_raw": exit_floor_raw,
        "exit_floor_above_ask_bps": floor_above_ask_bps,
        "target_exit_move_bps_from_entry": round(target_exit_move_bps_from_entry, 6),
        "entry_microfill_key": entry_microfill_key,
        "entry_microfill_trials": round(entry_microfill_trials, 6),
        "entry_microfill_fill_like": round(entry_microfill_fill_like, 6),
        "entry_microfill_rate": round(entry_microfill_rate, 6),
        "exit_microfill_key": exit_microfill_key,
        "exit_microfill_trials": round(exit_microfill_trials, 6),
        "exit_microfill_fill_like": round(exit_microfill_fill_like, 6),
        "exit_microfill_rate": round(exit_microfill_rate, 6),
        "spread_bps": round(metrics["spread_bps"], 6),
        "ret_24h_bps": round(metrics["ret_24h_bps"], 6),
        "range_24h_bps": round(metrics["range_24h_bps"], 6),
        "volume_24h_quote": round(metrics["volume_24h_quote"], 12),
        "volume_24h_usd": round(metrics["volume_24h_usd"], 2),
        "trade_count_24h": round(metrics["trade_count_24h"], 6),
        "cost_min": pair.cost_min,
        "order_min": pair.order_min,
        "tick_size": pair.tick_size,
        "score": round(score, 6),
    }


def build_payload(
    *,
    client: KrakenSpotClient,
    quote_usd: float,
    max_quote_usd: float,
    maker_fee_bps: float,
    target_net_pct: float,
    entry_improve_ticks: int,
    entry_offset_fracs: list[float],
    max_entry_concession_bps: float,
    min_entry_spread_cushion_bps: float,
    microfill_summary_paths: list[Path],
    min_entry_microfill_rate: float,
    min_entry_microfill_trials: float,
    min_exit_microfill_rate: float,
    min_exit_microfill_trials: float,
    quote_currencies: set[str],
    max_exit_floor_above_ask_bps: float,
    min_volume_24h_usd: float,
    min_trades_24h: float,
    max_spread_bps: float,
    min_ret_24h_bps: float,
    validate_paths: list[Path],
    live_entry_paths: list[Path],
    entry_miss_cooldown_minutes: float,
    query_private: bool,
    block_on_open_orders: bool,
    depth_top_n: int,
) -> dict[str, Any]:
    all_pairs: dict[str, KrakenPair] = {}
    asset_pairs = client.asset_pairs()
    for rest_pair, payload in asset_pairs.items():
        if isinstance(payload, dict):
            pair = parse_pair(str(rest_pair), payload)
            if pair and pair.status.lower() in {"online", "post_only"}:
                all_pairs[product_id_for_pair(pair)] = pair
    pair_map = pair_map_for_quotes(asset_pairs, quote_currencies)
    rates = quote_usd_rates(client, all_pairs, quote_currencies)
    pair_map = {product_id: pair for product_id, pair in pair_map.items() if rates.get(pair.quote, 0.0) > 0.0}
    quote_amounts = {quote: quote_usd / rates[quote] for quote in quote_currencies if rates.get(quote, 0.0) > 0.0}
    max_quote_amounts = {quote: max_quote_usd / rates[quote] for quote in quote_currencies if rates.get(quote, 0.0) > 0.0}
    validate = latest_validate_evidence(validate_paths)
    live_entry = latest_live_entry_evidence(live_entry_paths)
    microfill_offset_stats = load_microfill_offset_stats(microfill_summary_paths)
    now = datetime.now(timezone.utc)
    exposure = (
        live_exposure_snapshot(
            client,
            quote_usd=quote_usd,
            quote_amounts=quote_amounts,
            block_on_open_orders=block_on_open_orders,
        )
        if query_private
        else local_exposure_snapshot()
    )
    quote_balances = exposure.get("quote_balances") if isinstance(exposure.get("quote_balances"), dict) else {}
    tickers = fetch_tickers(client, pair_map)
    offsets: list[float | None] = list(entry_offset_fracs) if entry_offset_fracs else [None]
    rows: list[dict[str, Any]] = []
    for product_id, pair in pair_map.items():
        if product_id not in tickers or pair.quote not in quote_amounts:
            continue
        for entry_offset_frac in offsets:
            rows.append(
                candidate_row(
                    product_id=product_id,
                    pair=pair,
                    ticker=tickers.get(product_id, {}),
                    validate_evidence=validate.get(product_id),
                    live_entry_evidence=live_entry.get(product_id),
                    quote_usd=quote_usd,
                    max_quote_usd=max_quote_usd,
                    quote_amount=quote_amounts[pair.quote],
                    max_quote_amount=max_quote_amounts[pair.quote],
                    quote_usd_rate=rates[pair.quote],
                    quote_balance=to_float(quote_balances.get(pair.quote)) if query_private else None,
                    maker_fee_bps=maker_fee_bps,
                    target_net_pct=target_net_pct,
                    entry_improve_ticks=entry_improve_ticks,
                    entry_offset_frac=entry_offset_frac,
                    max_entry_concession_bps=max_entry_concession_bps,
                    min_entry_spread_cushion_bps=min_entry_spread_cushion_bps,
                    microfill_offset_stats=microfill_offset_stats,
                    min_entry_microfill_rate=min_entry_microfill_rate,
                    min_entry_microfill_trials=min_entry_microfill_trials,
                    min_exit_microfill_rate=min_exit_microfill_rate,
                    min_exit_microfill_trials=min_exit_microfill_trials,
                    max_exit_floor_above_ask_bps=max_exit_floor_above_ask_bps,
                    min_volume_24h_usd=min_volume_24h_usd,
                    min_trades_24h=min_trades_24h,
                    max_spread_bps=max_spread_bps,
                    min_ret_24h_bps=min_ret_24h_bps,
                    entry_miss_cooldown_minutes=entry_miss_cooldown_minutes,
                    now=now,
                )
            )
    rows.sort(
        key=lambda row: (
            0 if row["readiness"] == "fire_candidate" else 1 if row["readiness"] == "needs_validate_only" else 2,
            -to_float(row.get("score")),
            to_float(row.get("exit_floor_above_ask_bps")),
        )
    )
    for row in rows[: max(0, depth_top_n)]:
        pair = pair_map.get(str(row.get("product_id")))
        if not pair:
            continue
        try:
            row.update(
                depth_metrics(
                    client,
                    pair,
                    entry_price=to_float(row.get("entry_price")),
                    exit_floor=to_float(row.get("estimated_required_exit_price")),
                )
            )
        except Exception as exc:
            row.update({"depth_ok": False, "depth_error": str(exc)})

    counts = Counter(str(row.get("readiness") or "") for row in rows)
    fire_candidates = [row for row in rows if row.get("readiness") == "fire_candidate"]
    needs_validate = [row for row in rows if row.get("readiness") == "needs_validate_only"]
    global_blockers = list(exposure.get("hard_blockers") or [])
    if global_blockers:
        next_action = "wait_flat_or_fund_usd_before_live_probe"
        live_probe_allowed = False
    elif fire_candidates:
        next_action = "manual_review_then_single_tiny_live_probe"
        live_probe_allowed = True
    elif needs_validate:
        next_action = "run_validate_only_on_top_needs_validate"
        live_probe_allowed = False
    else:
        next_action = "no_fire_candidate_refresh_market"
        live_probe_allowed = False

    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_tiny_live_fire_queue",
        "parameters": {
            "quote_usd": quote_usd,
            "max_quote_usd": max_quote_usd,
            "maker_fee_bps": maker_fee_bps,
            "target_net_pct": target_net_pct,
            "entry_improve_ticks": entry_improve_ticks,
            "entry_offset_fracs": [round(float(value), 6) for value in entry_offset_fracs],
            "max_entry_concession_bps": max_entry_concession_bps,
            "min_entry_spread_cushion_bps": min_entry_spread_cushion_bps,
            "microfill_summary_paths": [str(path) for path in microfill_summary_paths],
            "microfill_offset_stats": len(microfill_offset_stats),
            "min_entry_microfill_rate": min_entry_microfill_rate,
            "min_entry_microfill_trials": min_entry_microfill_trials,
            "min_exit_microfill_rate": min_exit_microfill_rate,
            "min_exit_microfill_trials": min_exit_microfill_trials,
            "quote_currencies": sorted(quote_currencies),
            "quote_usd_rates": {key: round(value, 8) for key, value in sorted(rates.items()) if key in quote_currencies},
            "quote_amounts": {key: round(value, 12) for key, value in sorted(quote_amounts.items())},
            "max_quote_amounts": {key: round(value, 12) for key, value in sorted(max_quote_amounts.items())},
            "max_exit_floor_above_ask_bps": max_exit_floor_above_ask_bps,
            "min_volume_24h_usd": min_volume_24h_usd,
            "min_trades_24h": min_trades_24h,
            "max_spread_bps": max_spread_bps,
            "min_ret_24h_bps": min_ret_24h_bps,
            "entry_miss_cooldown_minutes": entry_miss_cooldown_minutes,
            "depth_top_n": depth_top_n,
            "validate_paths": [str(path) for path in validate_paths],
            "live_entry_paths": [str(path) for path in live_entry_paths],
            "query_private": query_private,
            "block_on_open_orders": block_on_open_orders,
        },
        "live_exposure": exposure,
        "summary": {
            "pairs_scanned": len(rows),
            "usd_pairs_scanned": sum(1 for row in rows if row.get("quote_currency") == "USD"),
            "quote_counts": dict(Counter(str(row.get("quote_currency") or "") for row in rows)),
            "validate_evidence_products": len(validate),
            "live_entry_evidence_products": len(live_entry),
            "microfill_offset_keys": len(microfill_offset_stats),
            "readiness_counts": dict(counts),
            "global_blockers": global_blockers,
            "global_warnings": list(exposure.get("warnings") or []),
            "live_probe_allowed": live_probe_allowed,
            "next_action": next_action,
            "fire_candidates": [row["product_id"] for row in fire_candidates[:10]],
            "needs_validate_only": [row["product_id"] for row in needs_validate[:10]],
            "read": (
                "Read-only selector. A fire candidate is not autonomous permission; it means the product clears "
                "the current tiny-live math and existing validate evidence. Live probes still require explicit approval."
            ),
        },
        "rows": rows,
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, csv_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    fieldnames = [
        "product_id",
        "quote_currency",
        "readiness",
        "blockers",
        "warnings",
        "validate_ok",
        "live_entry_outcome",
        "bid",
        "ask",
        "entry_price_model",
        "entry_offset_frac",
        "entry_price",
        "entry_concession_bps",
        "entry_spread_cushion_bps",
        "estimated_required_exit_price",
        "exit_floor_above_ask_bps",
        "target_exit_move_bps_from_entry",
        "entry_microfill_trials",
        "entry_microfill_rate",
        "exit_microfill_trials",
        "exit_microfill_rate",
        "estimated_notional_quote",
        "estimated_notional_usd",
        "spread_bps",
        "ret_24h_bps",
        "volume_24h_usd",
        "trade_count_24h",
        "score",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            serial = dict(row)
            serial["blockers"] = ",".join(row.get("blockers") or [])
            serial["warnings"] = ",".join(row.get("warnings") or [])
            writer.writerow(serial)

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    exposure = payload.get("live_exposure") if isinstance(payload.get("live_exposure"), dict) else {}
    lines = [
        "# Kraken Tiny Live Fire Queue",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Pairs scanned: `{summary.get('pairs_scanned', summary.get('usd_pairs_scanned'))}`",
        f"- Quote counts: `{summary.get('quote_counts')}`",
        f"- Readiness counts: `{summary.get('readiness_counts')}`",
        f"- Global blockers: `{summary.get('global_blockers')}`",
        f"- Global warnings: `{summary.get('global_warnings')}`",
        f"- Free USD: `{exposure.get('usd_free')}`",
        f"- Quote balances: `{exposure.get('quote_balances')}`",
        f"- Quote balance blockers: `{exposure.get('quote_balance_blockers')}`",
        f"- Open order IDs: `{exposure.get('open_order_ids')}`",
        f"- Live probe allowed: `{summary.get('live_probe_allowed')}`",
        f"- Next action: `{summary.get('next_action')}`",
        f"- Read: {summary.get('read')}",
        "",
        "## Top Queue",
        "",
        "| Product | Quote | Readiness | Blockers | Offset | Entry concession bps | Entry cushion bps | Entry microfill | Exit microfill | Exit floor bps | Exit move bps | Spread bps | 24h trades | Entry | Exit floor | Notional $ | Score |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows[:40]:
        lines.append(
            "| {product_id} | {quote_currency} | {readiness} | {blockers} | {entry_offset_frac} | {entry_concession_bps:.3f} | {entry_spread_cushion_bps:.3f} | {entry_microfill_rate:.3f}/{entry_microfill_trials:.0f} | {exit_microfill_rate:.3f}/{exit_microfill_trials:.0f} | {exit_floor_above_ask_bps:.3f} | {target_exit_move_bps_from_entry:.3f} | {spread_bps:.2f} | {trade_count_24h:.0f} | {entry_price:.12g} | {estimated_required_exit_price:.12g} | {estimated_notional_usd:.2f} | {score:.2f} |".format(
                **{**row, "blockers": ", ".join(row.get("blockers") or []), "entry_offset_frac": "" if row.get("entry_offset_frac") is None else row.get("entry_offset_frac")}
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a read-only Kraken tiny-live fire/no-fire queue.")
    parser.add_argument("--quote-usd", type=float, default=9.0)
    parser.add_argument("--max-quote-usd", type=float, default=9.25)
    parser.add_argument("--maker-fee-bps", type=float, default=25.0)
    parser.add_argument("--target-net-pct", type=float, default=0.10)
    parser.add_argument("--entry-improve-ticks", type=int, default=1000)
    parser.add_argument("--entry-offset-fracs", default="", help="Optional comma-separated bid-to-ask spread offsets to test instead of improve ticks, e.g. 0.1,0.25,0.4")
    parser.add_argument("--max-entry-concession-bps", type=float, default=-1.0, help="Block offset rows that pay more than this many bps above bid; negative disables")
    parser.add_argument("--min-entry-spread-cushion-bps", type=float, default=0.0, help="Block offset rows that leave less than this many bps between entry and ask")
    parser.add_argument("--microfill-summary-path", action="append", type=Path, default=None)
    parser.add_argument("--min-entry-microfill-rate", type=float, default=0.0)
    parser.add_argument("--min-entry-microfill-trials", type=float, default=0.0)
    parser.add_argument("--min-exit-microfill-rate", type=float, default=0.0)
    parser.add_argument("--min-exit-microfill-trials", type=float, default=0.0)
    parser.add_argument("--quote-currencies", default="USD")
    parser.add_argument("--max-exit-floor-above-ask-bps", type=float, default=15.0)
    parser.add_argument("--min-volume-24h-usd", type=float, default=5000.0)
    parser.add_argument("--min-trades-24h", type=float, default=0.0)
    parser.add_argument("--max-spread-bps", type=float, default=250.0)
    parser.add_argument("--min-ret-24h-bps", type=float, default=-500.0)
    parser.add_argument("--validate-event-path", action="append", type=Path, default=None)
    parser.add_argument("--live-entry-event-path", action="append", type=Path, default=None)
    parser.add_argument("--entry-miss-cooldown-minutes", type=float, default=60.0)
    parser.add_argument("--no-query-private", action="store_true")
    parser.add_argument("--no-block-on-open-orders", action="store_true")
    parser.add_argument("--depth-top-n", type=int, default=40)
    parser.add_argument("--json-path", type=Path, default=DEFAULT_JSON_PATH)
    parser.add_argument("--csv-path", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--md-path", type=Path, default=DEFAULT_MD_PATH)
    args = parser.parse_args()

    validate_paths = args.validate_event_path or [DEFAULT_VALIDATE_EVENT_PATH, DEFAULT_TINY_LIVE_EVENT_PATH]
    live_entry_paths = args.live_entry_event_path or [
        DEFAULT_TINY_LIVE_EVENT_PATH,
        DEFAULT_BTC_QUOTE_TINY_LIVE_EVENT_PATH,
        DEFAULT_PARALLEL_USD_TINY_LIVE_EVENT_PATH,
    ]
    client = KrakenSpotClient()
    payload = build_payload(
        client=client,
        quote_usd=args.quote_usd,
        max_quote_usd=args.max_quote_usd,
        maker_fee_bps=args.maker_fee_bps,
        target_net_pct=args.target_net_pct,
        entry_improve_ticks=args.entry_improve_ticks,
        entry_offset_fracs=parse_float_csv(args.entry_offset_fracs),
        max_entry_concession_bps=args.max_entry_concession_bps,
        min_entry_spread_cushion_bps=args.min_entry_spread_cushion_bps,
        microfill_summary_paths=args.microfill_summary_path or [],
        min_entry_microfill_rate=args.min_entry_microfill_rate,
        min_entry_microfill_trials=args.min_entry_microfill_trials,
        min_exit_microfill_rate=args.min_exit_microfill_rate,
        min_exit_microfill_trials=args.min_exit_microfill_trials,
        quote_currencies=parse_csv_set(args.quote_currencies),
        max_exit_floor_above_ask_bps=args.max_exit_floor_above_ask_bps,
        min_volume_24h_usd=args.min_volume_24h_usd,
        min_trades_24h=args.min_trades_24h,
        max_spread_bps=args.max_spread_bps,
        min_ret_24h_bps=args.min_ret_24h_bps,
        validate_paths=validate_paths,
        live_entry_paths=live_entry_paths,
        entry_miss_cooldown_minutes=args.entry_miss_cooldown_minutes,
        query_private=not args.no_query_private,
        block_on_open_orders=not args.no_block_on_open_orders,
        depth_top_n=args.depth_top_n,
    )
    write_reports(payload, json_path=args.json_path, csv_path=args.csv_path, md_path=args.md_path)
    print(json.dumps({"summary": payload["summary"], "md_path": str(args.md_path)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
