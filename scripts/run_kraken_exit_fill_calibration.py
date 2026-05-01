#!/usr/bin/env python3
"""Exit-fill calibration probe for Kraken maker lane.

Places a real post-only buy, then on fill attempts post-only exits at
multiple profit targets.  Pure telemetry — no taker fallback.  Records
fill times, miss rates, and spread conditions so we can answer:
*At what profit target does the maker exit fill reliably?*

Usage (dry-run, no orders placed):
    python scripts/run_kraken_exit_fill_calibration.py --products HOUSE-USD,BTR-USD --dry-run

Usage (live probe, requires user approval):
    python scripts/run_kraken_exit_fill_calibration.py --products HOUSE-USD,BTR-USD --live --event-path reports/kraken_exit_fill_calibration_events.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
import time
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

DEFAULT_PRODUCTS = ["HOUSE-USD", "BTR-USD"]
DEFAULT_EVENT_PATH = ROOT / "reports" / "kraken_exit_fill_calibration_events.jsonl"
DEFAULT_JSON_PATH = ROOT / "reports" / "kraken_exit_fill_calibration.json"
DEFAULT_MD_PATH = ROOT / "reports" / "kraken_exit_fill_calibration.md"
SWARM_BRAIN_PATH = ROOT / "reports" / "swarm_brain_features.json"

# Exit targets to test (as % above entry)
EXIT_TARGETS_PCT = [0.10, 0.15, 0.20, 0.25, 0.50]
# Max time to wait for entry fill (seconds)
ENTRY_FILL_TIMEOUT = 300.0
# Max time to wait for each exit target (seconds)
EXIT_FILL_TIMEOUT = 180.0
# Poll interval for order status (seconds)
POLL_INTERVAL = 3.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_global_veto_active() -> bool:
    """Check Swarm Brain for global liquidity veto."""
    if not SWARM_BRAIN_PATH.exists():
        return False
    try:
        brain = json.loads(SWARM_BRAIN_PATH.read_text(encoding="utf-8"))
        return brain.get("global_veto_active", False)
    except:
        return False


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


@dataclass
class ExitCalibrationResult:
    product_id: str
    entry_txid: str | None
    entry_fill_sec: float | None
    entry_price: float | None
    entry_fee: float | None
    exit_target_pct: float
    exit_txid: str | None
    exit_fill_sec: float | None
    exit_price: float | None
    exit_fee: float | None
    exit_miss: bool
    spread_at_entry_bps: float
    spread_at_exit_submit_bps: float | None
    spread_at_exit_fill_bps: float | None
    taker_proof_margin_bps: float | None
    total_hold_sec: float | None
    net_usd: float | None
    net_pct: float | None
    status: str  # "filled", "missed", "entry_failed", "veto"
    staggered_mode: bool = False
    exit_l1_txid: str | None = None
    exit_l1_fill_sec: float | None = None
    exit_stagger_txid: str | None = None
    exit_stagger_fill_sec: float | None = None
    error: str | None = None


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


def compute_spread_bps(bid: float, ask: float) -> float:
    if bid <= 0 or ask <= 0:
        return 0.0
    mid = (bid + ask) / 2.0
    return ((ask - bid) / mid) * 10000.0


def place_post_only_buy(
    client: KrakenSpotClient, pair: KrakenPair, bid: float, quote_usd: float, *, validate: bool = False
) -> dict[str, Any]:
    cost_min = float(pair.cost_min)
    order_min = float(pair.order_min)
    # Size order to respect min notional
    if quote_usd < cost_min:
        quote_usd = cost_min * 1.02
    volume = quote_usd / bid
    if volume < order_min:
        volume = order_min
        quote_usd = volume * bid

    resp = client.add_order(
        rest_pair=pair.rest_pair,
        side="buy",
        order_type="limit",
        volume=volume,
        price=bid,
        post_only=True,
        validate=validate,
    )
    return resp


def cancel_order(client: KrakenSpotClient, txid: str) -> dict[str, Any]:
    return client._request("POST", "/0/private/CancelOrder", params={"txid": txid}, private=True)


def query_open_orders(client: KrakenSpotClient) -> dict[str, Any]:
    return client._request("POST", "/0/private/OpenOrders", params={}, private=True)


def query_closed_orders(client: KrakenSpotClient, txid: str) -> dict[str, Any]:
    return client._request("POST", "/0/private/QueryOrders", params={"txid": txid}, private=True)


def wait_for_entry_fill(
    client: KrakenSpotClient, txid: str, timeout: float, poll: float
) -> tuple[bool, dict[str, Any] | None]:
    """Poll order status until filled or timeout. Returns (filled, order_info)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            info = query_closed_orders(client, txid)
            order = info.get(txid, {})
            status = order.get("status", "")
            if status == "closed":
                return True, order
            if status in {"canceled", "expired"}:
                return False, order
        except Exception:
            pass
        # Also check open orders
        try:
            open_info = query_open_orders(client)
            open_orders = open_info.get("open", {})
            if txid not in open_orders:
                # Not in open orders — check closed
                try:
                    closed_info = query_closed_orders(client, txid)
                    closed_order = closed_info.get(txid, {})
                    if closed_order.get("status") == "closed":
                        return True, closed_order
                    if closed_order.get("status") in {"canceled", "expired"}:
                        return False, closed_order
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(poll)
    return False, None


def run_exit_calibration_trial(
    *,
    client: KrakenSpotClient,
    product_id: str,
    pair: KrakenPair,
    exit_target_pct: float,
    event_path: Path,
    dry_run: bool = False,
    max_quote_usd: float = 10.0,
) -> ExitCalibrationResult:
    bid, ask = fetch_bid_ask(client, pair)
    spread_at_entry = compute_spread_bps(bid, ask)

    result = ExitCalibrationResult(
        product_id=product_id,
        entry_txid=None,
        entry_fill_sec=None,
        entry_price=None,
        entry_fee=None,
        exit_target_pct=exit_target_pct,
        exit_txid=None,
        exit_fill_sec=None,
        exit_price=None,
        exit_fee=None,
        exit_miss=False,
        spread_at_entry_bps=spread_at_entry,
        spread_at_exit_submit_bps=None,
        spread_at_exit_fill_bps=None,
        taker_proof_margin_bps=None,
        total_hold_sec=None,
        net_usd=None,
        net_pct=None,
        status="entry_failed",
    )

    if not dry_run and is_global_veto_active():
        result.status = "veto"
        append_jsonl(event_path, {
            "ts_utc": utc_now_iso(),
            "action": "exit_fill_calibration_veto",
            "product_id": product_id,
            "reason": "global_liquidity_veto_active",
        })
        return result

    if dry_run:
        result.status = "dry_run"
        result.entry_price = bid
        append_jsonl(event_path, {
            "ts_utc": utc_now_iso(),
            "action": "exit_fill_calibration_dry_run",
            "product_id": product_id,
            "exit_target_pct": exit_target_pct,
            "simulated_entry_price": round(bid, 12),
            "simulated_exit_price": round(bid * (1 + exit_target_pct / 100.0), 12),
            "spread_bps": round(spread_at_entry, 6),
            "status": "dry_run",
        })
        return result

    # Place entry
    t0 = time.time()
    try:
        entry_resp = place_post_only_buy(client, pair, bid, max_quote_usd)
        descr = entry_resp.get("descr", {})
        entry_txid = entry_resp.get("txid", [None])[0] if isinstance(entry_resp.get("txid"), list) else entry_resp.get("txid")
        if not entry_txid and isinstance(entry_resp.get("txid"), list) and entry_resp["txid"]:
            entry_txid = entry_resp["txid"][0]
        result.entry_txid = entry_txid
    except Exception as exc:
        result.error = str(exc)
        result.status = "entry_failed"
        append_jsonl(event_path, {
            "ts_utc": utc_now_iso(),
            "action": "exit_fill_calibration",
            "product_id": product_id,
            "exit_target_pct": exit_target_pct,
            "status": "entry_failed",
            "error": str(exc),
        })
        return result

    append_jsonl(event_path, {
        "ts_utc": utc_now_iso(),
        "action": "entry_order_placed",
        "product_id": product_id,
        "txid": result.entry_txid,
        "exit_target_pct": exit_target_pct,
        "bid": round(bid, 12),
        "ask": round(ask, 12),
        "spread_bps": round(spread_at_entry, 6),
    })

    # Wait for entry fill
    filled, order_info = wait_for_entry_fill(client, result.entry_txid, ENTRY_FILL_TIMEOUT, POLL_INTERVAL)
    entry_fill_time = time.time() - t0

    if not filled:
        # Cancel entry
        try:
            cancel_order(client, result.entry_txid)
        except Exception:
            pass
        result.status = "entry_not_filled"
        result.entry_fill_sec = entry_fill_time
        append_jsonl(event_path, {
            "ts_utc": utc_now_iso(),
            "action": "exit_fill_calibration",
            "product_id": product_id,
            "exit_target_pct": exit_target_pct,
            "status": "entry_not_filled",
            "entry_fill_sec": round(entry_fill_time, 3),
            "entry_txid": result.entry_txid,
        })
        return result

    result.entry_fill_sec = entry_fill_time
    result.entry_price = to_float(order_info.get("price")) if order_info else bid
    result.entry_fee = to_float(order_info.get("fee")) if order_info else None
    vol_filled = to_float(order_info.get("vol_exec")) if order_info else 0.0
    cost_usd = result.entry_price * vol_filled if result.entry_price and vol_filled else max_quote_usd

    append_jsonl(event_path, {
        "ts_utc": utc_now_iso(),
        "action": "entry_filled",
        "product_id": product_id,
        "txid": result.entry_txid,
        "exit_target_pct": exit_target_pct,
        "entry_price": round(result.entry_price, 12) if result.entry_price else None,
        "entry_fee": result.entry_fee,
        "entry_fill_sec": round(entry_fill_time, 3),
        "volume": round(vol_filled, 8),
        "cost_usd": round(cost_usd, 6),
    })

    # Place exit at target
    exit_price = result.entry_price * (1 + exit_target_pct / 100.0)
    exit_price = round(exit_price, int(pair.pair_decimals))

    bid2, ask2 = fetch_bid_ask(client, pair)
    result.spread_at_exit_submit_bps = compute_spread_bps(bid2, ask2)

    t1 = time.time()
    try:
        exit_resp = client.add_order(
            rest_pair=pair.rest_pair,
            side="sell",
            order_type="limit",
            volume=vol_filled,
            price=exit_price,
            post_only=True,
        )
        exit_txid = exit_resp.get("txid", [None])[0] if isinstance(exit_resp.get("txid"), list) else exit_resp.get("txid")
        if not exit_txid and isinstance(exit_resp.get("txid"), list) and exit_resp["txid"]:
            exit_txid = exit_resp["txid"][0]
        result.exit_txid = exit_txid
    except Exception as exc:
        result.error = str(exc)
        result.status = "exit_order_failed"
        result.exit_miss = True
        append_jsonl(event_path, {
            "ts_utc": utc_now_iso(),
            "action": "exit_fill_calibration",
            "product_id": product_id,
            "exit_target_pct": exit_target_pct,
            "status": "exit_order_failed",
            "error": str(exc),
            "entry_txid": result.entry_txid,
            "entry_fill_sec": round(entry_fill_time, 3),
        })
        return result

    # Wait for exit fill
    exit_filled, exit_order_info = wait_for_entry_fill(client, result.exit_txid, EXIT_FILL_TIMEOUT, POLL_INTERVAL)
    exit_fill_time = time.time() - t1

    if exit_filled:
        result.exit_fill_sec = exit_fill_time
        result.exit_price = to_float(exit_order_info.get("price")) if exit_order_info else exit_price
        result.exit_fee = to_float(exit_order_info.get("fee")) if exit_order_info else None
        result.status = "filled"
        result.total_hold_sec = entry_fill_time + exit_fill_time

        # Calculate net
        gross = (result.exit_price * vol_filled) if result.exit_price else cost_usd
        total_fees = (result.entry_fee or 0) + (result.exit_fee or 0)
        result.net_usd = gross - cost_usd - total_fees
        result.net_pct = (result.net_usd / cost_usd * 100.0) if cost_usd > 0 else 0.0

        bid3, ask3 = fetch_bid_ask(client, pair)
        result.spread_at_exit_fill_bps = compute_spread_bps(bid3, ask3)
        if bid3 > 0:
            result.taker_proof_margin_bps = ((result.exit_price - bid3) / bid3) * 10000.0

        append_jsonl(event_path, {
            "ts_utc": utc_now_iso(),
            "action": "exit_fill_calibration",
            "product_id": product_id,
            "exit_target_pct": exit_target_pct,
            "status": "filled",
            "entry_txid": result.entry_txid,
            "exit_txid": result.exit_txid,
            "entry_fill_sec": round(entry_fill_time, 3),
            "exit_fill_sec": round(exit_fill_time, 3),
            "total_hold_sec": round(result.total_hold_sec, 3),
            "entry_price": round(result.entry_price, 12) if result.entry_price else None,
            "exit_price": round(result.exit_price, 12) if result.exit_price else None,
            "entry_fee": result.entry_fee,
            "exit_fee": result.exit_fee,
            "net_usd": round(result.net_usd, 6) if result.net_usd is not None else None,
            "net_pct": round(result.net_pct, 6) if result.net_pct is not None else None,
            "spread_at_entry_bps": round(spread_at_entry, 6),
            "spread_at_exit_submit_bps": round(result.spread_at_exit_submit_bps, 6) if result.spread_at_exit_submit_bps else None,
            "spread_at_exit_fill_bps": round(result.spread_at_exit_fill_bps, 6) if result.spread_at_exit_fill_bps else None,
            "taker_proof_margin_bps": round(result.taker_proof_margin_bps, 2) if result.taker_proof_margin_bps is not None else None,
        })
    else:
        # Cancel exit
        try:
            cancel_order(client, result.exit_txid)
        except Exception:
            pass
        result.exit_miss = True
        result.status = "exit_miss"
        result.exit_fill_sec = exit_fill_time
        result.total_hold_sec = entry_fill_time + exit_fill_time

        append_jsonl(event_path, {
            "ts_utc": utc_now_iso(),
            "action": "exit_fill_calibration",
            "product_id": product_id,
            "exit_target_pct": exit_target_pct,
            "status": "exit_miss",
            "entry_txid": result.entry_txid,
            "exit_txid": result.exit_txid,
            "entry_fill_sec": round(entry_fill_time, 3),
            "exit_miss_after_sec": round(exit_fill_time, 3),
            "total_hold_sec": round(result.total_hold_sec, 3),
            "spread_at_entry_bps": round(spread_at_entry, 6),
            "spread_at_exit_submit_bps": round(result.spread_at_exit_submit_bps, 6) if result.spread_at_exit_submit_bps else None,
        })

    return result


def run_staggered_exit_calibration_trial(
    *,
    client: KrakenSpotClient,
    product_id: str,
    pair: KrakenPair,
    exit_target_pct: float,
    event_path: Path,
    dry_run: bool = False,
    max_quote_usd: float = 10.0,
) -> ExitCalibrationResult:
    """Specialized trial that tests 50/50 split between L1 and L1-1t."""
    bid, ask = fetch_bid_ask(client, pair)
    spread_at_entry = compute_spread_bps(bid, ask)

    result = ExitCalibrationResult(
        product_id=product_id,
        entry_txid=None,
        entry_fill_sec=None,
        entry_price=None,
        entry_fee=None,
        exit_target_pct=exit_target_pct,
        exit_txid=None,
        exit_fill_sec=None,
        exit_price=None,
        exit_fee=None,
        exit_miss=False,
        spread_at_entry_bps=spread_at_entry,
        spread_at_exit_submit_bps=None,
        spread_at_exit_fill_bps=None,
        taker_proof_margin_bps=None,
        total_hold_sec=None,
        net_usd=None,
        net_pct=None,
        status="entry_failed",
        staggered_mode=True,
    )

    if not dry_run and is_global_veto_active():
        result.status = "veto"
        append_jsonl(event_path, {
            "ts_utc": utc_now_iso(),
            "action": "exit_fill_calibration_veto",
            "product_id": product_id,
            "reason": "global_liquidity_veto_active",
            "mode": "staggered"
        })
        return result

    if dry_run:
        result.status = "dry_run"
        result.entry_price = bid
        append_jsonl(event_path, {
            "ts_utc": utc_now_iso(),
            "action": "exit_fill_calibration_dry_run",
            "product_id": product_id,
            "exit_target_pct": exit_target_pct,
            "mode": "staggered",
            "status": "dry_run",
        })
        return result

    # Place entry
    t0 = time.time()
    try:
        entry_resp = place_post_only_buy(client, pair, bid, max_quote_usd)
        entry_txid = entry_resp.get("txid", [None])[0] if isinstance(entry_resp.get("txid"), list) else entry_resp.get("txid")
        if not entry_txid and isinstance(entry_resp.get("txid"), list) and entry_resp["txid"]:
            entry_txid = entry_resp["txid"][0]
        result.entry_txid = entry_txid
    except Exception as exc:
        result.error = str(exc)
        result.status = "entry_failed"
        return result

    # Wait for entry fill
    filled, order_info = wait_for_entry_fill(client, result.entry_txid, ENTRY_FILL_TIMEOUT, POLL_INTERVAL)
    entry_fill_time = time.time() - t0

    if not filled:
        try: cancel_order(client, result.entry_txid)
        except Exception: pass
        result.status = "entry_not_filled"
        return result

    result.entry_fill_sec = entry_fill_time
    result.entry_price = to_float(order_info.get("price")) if order_info else bid
    result.entry_fee = to_float(order_info.get("fee")) if order_info else None
    vol_filled = to_float(order_info.get("vol_exec")) if order_info else 0.0
    
    # SPLIT QUANTITY 50/50
    vol_l1 = vol_filled / 2.0
    vol_stagger = vol_filled - vol_l1
    
    tick_size = float(pair.ordermin_step) # Approximation for tick size
    # Let's re-verify tick size if possible
    
    bid2, ask2 = fetch_bid_ask(client, pair)
    result.spread_at_exit_submit_bps = compute_spread_bps(bid2, ask2)
    
    # Prices
    price_l1 = result.entry_price * (1 + exit_target_pct / 100.0)
    price_l1 = round(price_l1, int(pair.pair_decimals))
    
    # Staggered price (L1-1t)
    price_stagger = price_l1 - tick_size # Note: exit_target is above entry, so -1t is mid-spread
    price_stagger = round(price_stagger, int(pair.pair_decimals))
    
    t1 = time.time()
    try:
        # Submit L1 order
        resp_l1 = client.add_order(rest_pair=pair.rest_pair, side="sell", order_type="limit", volume=vol_l1, price=price_l1, post_only=True)
        result.exit_l1_txid = resp_l1.get("txid", [None])[0]
        
        # Submit Staggered order
        resp_stagger = client.add_order(rest_pair=pair.rest_pair, side="sell", order_type="limit", volume=vol_stagger, price=price_stagger, post_only=True)
        result.exit_stagger_txid = resp_stagger.get("txid", [None])[0]
    except Exception as exc:
        result.error = str(exc)
        result.status = "exit_order_failed"
        return result

    # Wait for both
    l1_filled = False
    stagger_filled = False
    deadline = time.time() + EXIT_FILL_TIMEOUT
    
    while time.time() < deadline and not (l1_filled and stagger_filled):
        if not l1_filled:
            f, info = wait_for_entry_fill(client, result.exit_l1_txid, 1.0, 1.0)
            if f:
                l1_filled = True
                result.exit_l1_fill_sec = time.time() - t1
        if not stagger_filled:
            f, info = wait_for_entry_fill(client, result.exit_stagger_txid, 1.0, 1.0)
            if f:
                stagger_filled = True
                result.exit_stagger_fill_sec = time.time() - t1
        time.sleep(POLL_INTERVAL)

    # Cleanup and finalize result
    if not l1_filled:
        try: cancel_order(client, result.exit_l1_txid)
        except Exception: pass
    if not stagger_filled:
        try: cancel_order(client, result.exit_stagger_txid)
        except Exception: pass
        
    result.status = "filled" if (l1_filled or stagger_filled) else "exit_miss"
    result.exit_fill_sec = (result.exit_l1_fill_sec or 0.0 + (result.exit_stagger_fill_sec or 0.0)) / 2.0 # Avg
    
    append_jsonl(event_path, {
        "ts_utc": utc_now_iso(),
        "action": "exit_fill_calibration_staggered",
        "product_id": product_id,
        "l1_status": "filled" if l1_filled else "missed",
        "stagger_status": "filled" if stagger_filled else "missed",
        "l1_fill_sec": result.exit_l1_fill_sec,
        "stagger_fill_sec": result.exit_stagger_fill_sec,
        "total_filled": (vol_l1 if l1_filled else 0) + (vol_stagger if stagger_filled else 0),
    })

    return result


def write_reports(events_path: Path, json_path: Path, md_path: Path) -> None:
    events = []
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    calibrations = [e for e in events if e.get("action") == "exit_fill_calibration"]
    dry_runs = [e for e in events if e.get("action") == "exit_fill_calibration_dry_run"]

    summary = {
        "generated_at": utc_now_iso(),
        "total_events": len(events),
        "calibrations": len(calibrations),
        "dry_runs": len(dry_runs),
        "filled": sum(1 for e in calibrations if e.get("status") == "filled"),
        "exit_miss": sum(1 for e in calibrations if e.get("status") == "exit_miss"),
        "entry_failed": sum(1 for e in calibrations if e.get("status") in {"entry_failed", "entry_not_filled"}),
    }

    # Per-product, per-target summary
    by_product_target: dict[str, dict[float, dict[str, Any]]] = {}
    for e in calibrations:
        pid = e.get("product_id", "")
        target = float(e.get("exit_target_pct", 0))
        if pid not in by_product_target:
            by_product_target[pid] = {}
        if target not in by_product_target[pid]:
            by_product_target[pid][target] = {"attempts": 0, "filled": 0, "missed": 0, "entry_failed": 0, "avg_exit_fill_sec": [], "avg_net_pct": []}
        by_product_target[pid][target]["attempts"] += 1
        if e.get("status") == "filled":
            by_product_target[pid][target]["filled"] += 1
            if e.get("exit_fill_sec") is not None:
                by_product_target[pid][target]["avg_exit_fill_sec"].append(e["exit_fill_sec"])
            if e.get("net_pct") is not None:
                by_product_target[pid][target]["avg_net_pct"].append(e["net_pct"])
        elif e.get("status") == "exit_miss":
            by_product_target[pid][target]["missed"] += 1
        else:
            by_product_target[pid][target]["entry_failed"] += 1

    summary["by_product_target"] = {}
    for pid in sorted(by_product_target):
        summary["by_product_target"][pid] = {}
        for target in sorted(by_product_target[pid]):
            d = by_product_target[pid][target]
            summary["by_product_target"][pid][str(target)] = {
                "attempts": d["attempts"],
                "filled": d["filled"],
                "missed": d["missed"],
                "entry_failed": d["entry_failed"],
                "fill_rate": round(d["filled"] / d["attempts"], 4) if d["attempts"] > 0 else 0.0,
                "avg_exit_fill_sec": round(sum(d["avg_exit_fill_sec"]) / len(d["avg_exit_fill_sec"]), 3) if d["avg_exit_fill_sec"] else None,
                "avg_net_pct": round(sum(d["avg_net_pct"]) / len(d["avg_net_pct"]), 4) if d["avg_net_pct"] else None,
            }

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    # Markdown
    lines = [
        "# Kraken Exit-Fill Calibration",
        "",
        f"- Generated: `{summary['generated_at']}`",
        f"- Total events: `{summary['total_events']}`",
        f"- Calibrations: `{summary['calibrations']}`",
        f"- Filled: `{summary['filled']}`",
        f"- Exit missed: `{summary['exit_miss']}`",
        f"- Entry failed: `{summary['entry_failed']}`",
        "",
        "## By Product / Target",
        "",
        "| Product | Target % | Attempts | Filled | Missed | Fill Rate | Avg Exit Sec | Avg Net % |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for pid in sorted(summary.get("by_product_target", {})):
        for target_str in sorted(summary["by_product_target"][pid], key=lambda x: float(x)):
            d = summary["by_product_target"][pid][target_str]
            lines.append(
                f"| {pid} | {target_str} | {d['attempts']} | {d['filled']} | {d['missed']} | {d['fill_rate']:.1%} | {d['avg_exit_fill_sec'] or 'N/A'} | {d['avg_net_pct'] or 'N/A'} |"
            )

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exit-fill calibration probe: places real post-only buys then tests exit fillability at multiple targets."
    )
    parser.add_argument("--products", default="", help="Comma-separated products. Defaults to HOUSE-USD,BTR-USD.")
    parser.add_argument("--event-path", type=Path, default=DEFAULT_EVENT_PATH)
    parser.add_argument("--json-path", type=Path, default=DEFAULT_JSON_PATH)
    parser.add_argument("--md-path", type=Path, default=DEFAULT_MD_PATH)
    parser.add_argument("--max-quote-usd", type=float, default=10.0)
    parser.add_argument("--exit-targets", default="", help="Comma-separated exit target percentages (e.g., 0.10,0.25,0.50).")
    parser.add_argument("--staggered", action="store_true", help="Enable 50/50 L1/L1-1t staggered exit trial.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate only — no orders placed.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    products = [p.strip().upper() for p in args.products.split(",") if p.strip()] if args.products.strip() else DEFAULT_PRODUCTS
    exit_targets = [float(x.strip()) for x in args.exit_targets.split(",") if x.strip()] if args.exit_targets.strip() else EXIT_TARGETS_PCT

    if args.dry_run:
        print(f"DRY RUN — no orders will be placed for {products}")
    else:
        print(f"LIVE PROBE — real orders will be placed for {products}")
        print(f"Exit targets: {exit_targets}%")
        print(f"Max quote: ${args.max_quote_usd}")
        print(f"Entry timeout: {ENTRY_FILL_TIMEOUT}s, Exit timeout: {EXIT_FILL_TIMEOUT}s")
        input("Press Enter to proceed (or Ctrl+C to cancel)...")

    client = KrakenSpotClient()
    pair_map = build_pair_map(client.asset_pairs())

    results = []
    for product in products:
        pair = pair_map.get(product)
        if pair is None:
            print(f"  SKIP {product}: pair not found")
            append_jsonl(args.event_path, {
                "ts_utc": utc_now_iso(),
                "action": "exit_fill_calibration",
                "product_id": product,
                "status": "pair_not_found",
            })
            continue

        for target in exit_targets:
            print(f"  {product} @ {target}% target...")
            if args.staggered:
                result = run_staggered_exit_calibration_trial(
                    client=client,
                    product_id=product,
                    pair=pair,
                    exit_target_pct=target,
                    event_path=args.event_path,
                    dry_run=args.dry_run,
                    max_quote_usd=args.max_quote_usd,
                )
            else:
                result = run_exit_calibration_trial(
                    client=client,
                    product_id=product,
                    pair=pair,
                    exit_target_pct=target,
                    event_path=args.event_path,
                    dry_run=args.dry_run,
                    max_quote_usd=args.max_quote_usd,
                )
            results.append(result)
            print(f"    -> {result.status} | entry={result.entry_fill_sec}s | exit={result.exit_fill_sec}s | net={result.net_usd}")

    summary = write_reports(args.event_path, args.json_path, args.md_path)
    print(f"\nSummary: {summary['filled']} filled, {summary['exit_miss']} missed, {summary['entry_failed']} entry_failed")
    print(f"Reports: {args.json_path}, {args.md_path}")


if __name__ == "__main__":
    main()
