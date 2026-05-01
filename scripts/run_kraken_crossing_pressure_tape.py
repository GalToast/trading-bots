#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kraken_spot_client import KrakenSpotClient, normalize_asset, parse_pair, parse_ticker, to_float  # noqa: E402
from run_kraken_maker_microfill_calibrator import load_pair_info, run_trial  # noqa: E402


DEFAULT_EVENT_PATH = REPORTS / "kraken_crossing_pressure_tape_events.jsonl"
DEFAULT_SUMMARY_PATH = REPORTS / "kraken_crossing_pressure_tape_summary.json"
DEFAULT_RADAR_PATH = REPORTS / "kraken_spot_live_radar.json"
DEFAULT_RADAR_CACHE_PATH = REPORTS / "cache" / "kraken_spot_live_radar_ticks.json"
DEFAULT_DISLOCATION_LAB_PATH = REPORTS / "kraken_spot_dislocation_reversion_lab_usd_maker_upper.json"
DEFAULT_FIRE_QUEUE_PATH = REPORTS / "kraken_morning_candidates_honey_fire_queue_target002_unfunded.json"
FILL_LIKE_RESULTS = {"hard_cross_fill_proxy", "probable_queue_depletion_fill_proxy"}
USD_EQUIVALENT_QUOTES = {"USD", "USDT", "USDC"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_float_csv(raw: str | list[str]) -> list[float]:
    values: list[float] = []
    parts: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            parts.extend(str(item or "").split(","))
    else:
        parts.extend(str(raw or "").split(","))
    for part in parts:
        clean = part.strip()
        if clean:
            values.append(float(clean))
    return values


def parse_str_csv(raw: str | list[str]) -> list[str]:
    values: list[str] = []
    parts: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            parts.extend(str(item or "").split(","))
    else:
        parts.extend(str(raw or "").split(","))
    for part in parts:
        clean = part.strip().upper()
        if clean:
            values.append(clean)
    return values


def parse_name_csv(raw: str | list[str]) -> list[str]:
    values: list[str] = []
    parts: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            parts.extend(str(item or "").split(","))
    else:
        parts.extend(str(raw or "").split(","))
    for part in parts:
        clean = part.strip()
        if clean:
            values.append(clean)
    return values


def spread_bps(bid: float, ask: float) -> float:
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return 0.0
    return ((ask - bid) / mid) * 10000.0


def infer_product_quote_currency(product: str) -> str:
    value = str(product or "").upper().replace("/", "-")
    if "-" in value:
        return normalize_asset(value.rsplit("-", 1)[1])
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH", "SOL", "EUR", "GBP", "CAD", "AUD", "JPY"):
        if value.endswith(quote) and len(value) > len(quote):
            return normalize_asset(quote)
    return "USD"


def find_quote_usd_pairs(asset_pairs_payload: dict[str, Any], needed_quotes: set[str]) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for rest_pair, raw in asset_pairs_payload.items():
        if not isinstance(raw, dict):
            continue
        pair = parse_pair(str(rest_pair), raw)
        if pair is None or pair.status != "online":
            continue
        if pair.quote == "USD" and pair.base in needed_quotes and pair.base not in out:
            out[pair.base] = (pair.rest_pair, pair.wsname)
    return out


def load_quote_to_usd_rates(client: KrakenSpotClient, products: list[str]) -> dict[str, float]:
    quote_currencies = {infer_product_quote_currency(product) for product in products}
    rates = {quote: 1.0 for quote in quote_currencies if quote in USD_EQUIVALENT_QUOTES}
    needed = {quote for quote in quote_currencies if quote not in USD_EQUIVALENT_QUOTES}
    if not needed:
        return rates
    asset_pairs_payload = client.asset_pairs()
    quote_pairs = find_quote_usd_pairs(asset_pairs_payload, needed)
    if not quote_pairs:
        return rates
    ticker_payload = client.ticker([rest_pair for rest_pair, _wsname in quote_pairs.values()])
    for quote, (rest_pair, wsname) in quote_pairs.items():
        raw = ticker_payload.get(rest_pair)
        if not isinstance(raw, dict):
            continue
        top = parse_ticker(rest_pair, wsname, raw)
        if top is None:
            continue
        mid = (top.bid + top.ask) / 2.0
        if mid > 0.0:
            rates[quote] = mid
    return rates


def load_sell_floor_prices(path: Path | None) -> dict[str, float]:
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else []
    out: dict[str, float] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        product_id = str(row.get("product_id") or "").upper()
        price = to_float(row.get("estimated_required_exit_price"))
        if product_id and price > 0.0:
            out[product_id] = price
    return out


def rank_top_spread_products(
    asset_pairs_payload: dict[str, Any],
    ticker_payload: dict[str, Any],
    *,
    quote_currencies: list[str],
    top_products: int,
    min_spread_bps: float,
    min_volume_24h: float,
) -> list[dict[str, Any]]:
    pairs_by_rest: dict[str, Any] = {}
    for rest_pair, raw in asset_pairs_payload.items():
        pair = parse_pair(str(rest_pair), raw)
        if pair is None or pair.status != "online":
            continue
        if pair.quote.upper() not in quote_currencies:
            continue
        pairs_by_rest[str(rest_pair)] = pair

    rows: list[dict[str, Any]] = []
    for rest_pair, raw_ticker in ticker_payload.items():
        pair = pairs_by_rest.get(str(rest_pair))
        if pair is None:
            continue
        top = parse_ticker(str(rest_pair), pair.wsname, raw_ticker)
        if top is None:
            continue
        current_spread_bps = spread_bps(top.bid, top.ask)
        if current_spread_bps < min_spread_bps or top.volume_24h < min_volume_24h:
            continue
        rows.append(
            {
                "product_id": pair.wsname.replace("/", "-").upper(),
                "rest_pair": str(rest_pair),
                "spread_bps": round(current_spread_bps, 6),
                "volume_24h": top.volume_24h,
                "bid": top.bid,
                "ask": top.ask,
            }
        )
    rows.sort(key=lambda row: (-to_float(row["spread_bps"]), -to_float(row["volume_24h"]), str(row["product_id"])))
    return rows[: max(0, int(top_products))]


def load_top_spread_products(client: KrakenSpotClient, args: argparse.Namespace) -> tuple[list[str], list[dict[str, Any]]]:
    quote_currencies = parse_str_csv(args.quote_currencies)
    asset_pairs_payload = client.asset_pairs()
    rest_pairs: list[str] = []
    for rest_pair, raw in asset_pairs_payload.items():
        pair = parse_pair(str(rest_pair), raw)
        if pair is None or pair.status != "online":
            continue
        if pair.quote.upper() in quote_currencies:
            rest_pairs.append(str(rest_pair))

    ticker_payload: dict[str, Any] = {}
    for start in range(0, len(rest_pairs), 50):
        ticker_payload.update(client.ticker(rest_pairs[start : start + 50]))

    rows = rank_top_spread_products(
        asset_pairs_payload,
        ticker_payload,
        quote_currencies=quote_currencies,
        top_products=args.top_products,
        min_spread_bps=args.min_spread_bps,
        min_volume_24h=args.min_volume_24h,
    )
    return [str(row["product_id"]) for row in rows], rows


def rank_radar_heartbeat_products(
    radar_payload: dict[str, Any],
    *,
    quote_currencies: list[str],
    top_products: int,
    max_spread_bps: float,
    min_best_short_bps: float,
    min_samples: int,
    states: list[str],
) -> list[dict[str, Any]]:
    allowed_states = {state.lower() for state in states}
    rows: list[dict[str, Any]] = []
    for row in radar_payload.get("rows") or []:
        quote = str(row.get("quote_currency") or "").upper()
        state = str(row.get("signal_state") or "").lower()
        if quote_currencies and quote not in quote_currencies:
            continue
        if allowed_states and state not in allowed_states:
            continue
        if to_float(row.get("spread_bps")) > max_spread_bps:
            continue
        if to_float(row.get("best_short_bps")) < min_best_short_bps:
            continue
        if int(to_float(row.get("samples"))) < int(min_samples):
            continue
        rows.append(
            {
                "product_id": str(row.get("product_id") or "").upper(),
                "rest_pair": str(row.get("rest_pair") or ""),
                "signal_state": row.get("signal_state"),
                "velocity_score": to_float(row.get("velocity_score")),
                "best_short_bps": to_float(row.get("best_short_bps")),
                "spread_bps": to_float(row.get("spread_bps")),
                "samples": int(to_float(row.get("samples"))),
                "ret_30s_bps": to_float(row.get("ret_30s_bps")),
                "ret_60s_bps": to_float(row.get("ret_60s_bps")),
            }
        )
    rows.sort(key=lambda row: (-to_float(row["velocity_score"]), -to_float(row["best_short_bps"]), str(row["product_id"])))
    return rows[: max(0, int(top_products))]


def load_radar_heartbeat_products(args: argparse.Namespace) -> tuple[list[str], list[dict[str, Any]]]:
    payload = json.loads(Path(args.radar_path).read_text(encoding="utf-8"))
    rows = rank_radar_heartbeat_products(
        payload,
        quote_currencies=parse_str_csv(args.quote_currencies),
        top_products=args.top_products,
        max_spread_bps=args.max_radar_spread_bps,
        min_best_short_bps=args.min_best_short_bps,
        min_samples=args.min_radar_samples,
        states=parse_str_csv(args.radar_states),
    )
    return [str(row["product_id"]) for row in rows], rows


def side_motion_from_samples(samples: list[dict[str, Any]], *, lookback_seconds: float) -> dict[str, float]:
    if len(samples) < 2:
        return {"ask_down_bps": 0.0, "bid_up_bps": 0.0, "latest_ask_down_bps": 0.0, "latest_bid_up_bps": 0.0, "samples": float(len(samples))}
    latest = samples[-1]
    previous = samples[-2]
    latest_ts = to_float(latest.get("ts"))
    latest_ask = to_float(latest.get("ask"))
    latest_bid = to_float(latest.get("bid"))
    previous_ask = to_float(previous.get("ask"))
    previous_bid = to_float(previous.get("bid"))
    latest_ask_down_bps = ((previous_ask - latest_ask) / previous_ask) * 10000.0 if previous_ask > 0.0 and latest_ask > 0.0 else 0.0
    latest_bid_up_bps = ((latest_bid - previous_bid) / previous_bid) * 10000.0 if previous_bid > 0.0 and latest_bid > 0.0 else 0.0
    ask_down_bps = 0.0
    bid_up_bps = 0.0
    for sample in samples[:-1]:
        if latest_ts > 0.0 and latest_ts - to_float(sample.get("ts")) > lookback_seconds:
            continue
        prior_ask = to_float(sample.get("ask"))
        prior_bid = to_float(sample.get("bid"))
        if prior_ask > 0.0 and latest_ask > 0.0:
            ask_down_bps = max(ask_down_bps, ((prior_ask - latest_ask) / prior_ask) * 10000.0)
        if prior_bid > 0.0 and latest_bid > 0.0:
            bid_up_bps = max(bid_up_bps, ((latest_bid - prior_bid) / prior_bid) * 10000.0)
    return {
        "ask_down_bps": round(ask_down_bps, 6),
        "bid_up_bps": round(bid_up_bps, 6),
        "latest_ask_down_bps": round(max(0.0, latest_ask_down_bps), 6),
        "latest_bid_up_bps": round(max(0.0, latest_bid_up_bps), 6),
        "samples": float(len(samples)),
    }


def rank_radar_side_heartbeat_products(
    radar_payload: dict[str, Any],
    cache_payload: dict[str, Any],
    *,
    quote_currencies: list[str],
    top_products: int,
    max_spread_bps: float,
    min_ask_down_bps: float,
    min_bid_up_bps: float,
    min_latest_ask_down_bps: float = 0.0,
    min_latest_bid_up_bps: float = 0.0,
    min_samples: int,
    lookback_seconds: float,
    side_mode: str,
) -> list[dict[str, Any]]:
    samples_by_rest = cache_payload.get("samples") or {}
    rows: list[dict[str, Any]] = []
    for row in radar_payload.get("rows") or []:
        quote = str(row.get("quote_currency") or "").upper()
        rest_pair = str(row.get("rest_pair") or "")
        if quote_currencies and quote not in quote_currencies:
            continue
        if to_float(row.get("spread_bps")) > max_spread_bps:
            continue
        samples = samples_by_rest.get(rest_pair) or []
        if len(samples) < int(min_samples):
            continue
        motion = side_motion_from_samples(samples, lookback_seconds=lookback_seconds)
        ask_down = to_float(motion.get("ask_down_bps"))
        bid_up = to_float(motion.get("bid_up_bps"))
        latest_ask_down = to_float(motion.get("latest_ask_down_bps"))
        latest_bid_up = to_float(motion.get("latest_bid_up_bps"))
        mode = str(side_mode or "both").lower()
        if mode == "entry" and ask_down < min_ask_down_bps:
            continue
        if mode == "entry" and latest_ask_down < min_latest_ask_down_bps:
            continue
        if mode == "exit" and bid_up < min_bid_up_bps:
            continue
        if mode == "exit" and latest_bid_up < min_latest_bid_up_bps:
            continue
        if mode == "either" and ask_down < min_ask_down_bps and bid_up < min_bid_up_bps:
            continue
        if mode == "either" and latest_ask_down < min_latest_ask_down_bps and latest_bid_up < min_latest_bid_up_bps:
            continue
        if mode not in {"entry", "exit", "either"} and (ask_down < min_ask_down_bps or bid_up < min_bid_up_bps):
            continue
        if mode not in {"entry", "exit", "either"} and (latest_ask_down < min_latest_ask_down_bps or latest_bid_up < min_latest_bid_up_bps):
            continue
        side_score = min(ask_down, bid_up) if mode == "both" else ask_down + bid_up
        rows.append(
            {
                "product_id": str(row.get("product_id") or "").upper(),
                "rest_pair": rest_pair,
                "spread_bps": to_float(row.get("spread_bps")),
                "velocity_score": to_float(row.get("velocity_score")),
                "best_short_bps": to_float(row.get("best_short_bps")),
                "ask_down_bps": ask_down,
                "bid_up_bps": bid_up,
                "latest_ask_down_bps": latest_ask_down,
                "latest_bid_up_bps": latest_bid_up,
                "side_score_bps": round(side_score, 6),
                "samples": int(motion.get("samples", 0.0)),
            }
        )
    rows.sort(key=lambda row: (-to_float(row["side_score_bps"]), -to_float(row["ask_down_bps"]), -to_float(row["bid_up_bps"]), str(row["product_id"])))
    return rows[: max(0, int(top_products))]


def load_radar_side_heartbeat_products(args: argparse.Namespace) -> tuple[list[str], list[dict[str, Any]]]:
    radar_payload = json.loads(Path(args.radar_path).read_text(encoding="utf-8"))
    cache_payload = json.loads(Path(args.radar_cache_path).read_text(encoding="utf-8"))
    rows = rank_radar_side_heartbeat_products(
        radar_payload,
        cache_payload,
        quote_currencies=parse_str_csv(args.quote_currencies),
        top_products=args.top_products,
        max_spread_bps=args.max_radar_spread_bps,
        min_ask_down_bps=args.min_ask_down_bps,
        min_bid_up_bps=args.min_bid_up_bps,
        min_latest_ask_down_bps=args.min_latest_ask_down_bps,
        min_latest_bid_up_bps=args.min_latest_bid_up_bps,
        min_samples=args.min_radar_samples,
        lookback_seconds=args.side_lookback_seconds,
        side_mode=args.side_mode,
    )
    return [str(row["product_id"]) for row in rows], rows


def rank_dislocation_lab_products(
    lab_payload: dict[str, Any],
    *,
    top_products: int,
    horizon_seconds: int,
    min_net_pct: float,
    min_mfe_net_pct: float,
    setup_names: list[str],
    score_mode: str = "either",
) -> list[dict[str, Any]]:
    allowed_setups = {name for name in setup_names if name}
    best_by_product: dict[str, dict[str, Any]] = {}
    horizon_key = str(int(horizon_seconds))
    mode = str(score_mode or "either").lower()
    for event in lab_payload.get("events") or []:
        product_id = str(event.get("product_id") or "").upper()
        if not product_id:
            continue
        setup = str(event.get("setup") or "")
        if allowed_setups and setup not in allowed_setups:
            continue
        mark = (event.get("marks") or {}).get(horizon_key)
        if not isinstance(mark, dict):
            continue
        net_pct = to_float(mark.get("net_pct"))
        mfe_net_pct = to_float(mark.get("mfe_net_pct"))
        realized_pass = net_pct >= min_net_pct
        mfe_pass = mfe_net_pct >= min_mfe_net_pct
        if mode == "realized":
            keep = realized_pass
        elif mode == "mfe":
            keep = mfe_pass
        elif mode == "both":
            keep = realized_pass and mfe_pass
        else:
            keep = realized_pass or mfe_pass
        if not keep:
            continue
        score = max(net_pct - min_net_pct, mfe_net_pct - min_mfe_net_pct)
        row = {
            "product_id": product_id,
            "rest_pair": str(event.get("rest_pair") or ""),
            "setup": setup,
            "entry_ts": to_float(event.get("entry_ts")),
            "dislocation_bps": to_float(event.get("dislocation_bps")),
            "spread_bps": to_float(event.get("spread_bps")),
            "ask_discount_bps": to_float(event.get("ask_discount_bps")),
            "net_pct": net_pct,
            "mfe_net_pct": mfe_net_pct,
            "target_hit": bool(mark.get("target_hit")),
            "score": round(score, 6),
        }
        current = best_by_product.get(product_id)
        if current is None or (to_float(row["score"]), to_float(row["net_pct"]), to_float(row["mfe_net_pct"])) > (
            to_float(current.get("score")),
            to_float(current.get("net_pct")),
            to_float(current.get("mfe_net_pct")),
        ):
            best_by_product[product_id] = row
    rows = list(best_by_product.values())
    rows.sort(key=lambda row: (-to_float(row["score"]), -to_float(row["net_pct"]), -to_float(row["mfe_net_pct"]), str(row["product_id"])))
    return rows[: max(0, int(top_products))]


def load_dislocation_lab_products(args: argparse.Namespace) -> tuple[list[str], list[dict[str, Any]]]:
    payload = json.loads(Path(args.dislocation_lab_path).read_text(encoding="utf-8"))
    rows = rank_dislocation_lab_products(
        payload,
        top_products=args.top_products,
        horizon_seconds=int(args.dislocation_horizon_seconds),
        min_net_pct=float(args.min_dislocation_net_pct),
        min_mfe_net_pct=float(args.min_dislocation_mfe_net_pct),
        setup_names=parse_name_csv(args.dislocation_setups),
        score_mode=str(args.dislocation_score_mode),
    )
    return [str(row["product_id"]) for row in rows], rows


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def is_fill_like(result: Any) -> bool:
    return str(result or "") in FILL_LIKE_RESULTS


def trial_features(
    trial: dict[str, Any],
    *,
    depth_notional_usd: float = 0.0,
    quote_currency: str = "USD",
    quote_to_usd: float = 1.0,
) -> dict[str, Any]:
    quote_currency = normalize_asset(quote_currency)
    quote_to_usd = to_float(quote_to_usd)
    initial_bid = to_float(trial.get("initial_bid"))
    initial_ask = to_float(trial.get("initial_ask"))
    initial_bid_size = to_float(trial.get("initial_bid_size"))
    initial_ask_size = to_float(trial.get("initial_ask_size"))
    last_bid = to_float(trial.get("last_bid"))
    last_ask = to_float(trial.get("last_ask"))
    last_bid_size = to_float(trial.get("last_bid_size"))
    last_ask_size = to_float(trial.get("last_ask_size"))
    initial_spread = to_float(trial.get("initial_spread_bps"))
    last_spread = to_float(trial.get("last_spread_bps"))
    bid_move_bps = ((last_bid - initial_bid) / initial_bid * 10000.0) if initial_bid > 0.0 else 0.0
    ask_move_bps = ((last_ask - initial_ask) / initial_ask * 10000.0) if initial_ask > 0.0 else 0.0
    spread_change_bps = last_spread - initial_spread
    initial_bid_depth_quote = initial_bid * initial_bid_size
    initial_ask_depth_quote = initial_ask * initial_ask_size
    last_bid_depth_quote = last_bid * last_bid_size
    last_ask_depth_quote = last_ask * last_ask_size
    initial_bid_depth_usd = initial_bid_depth_quote * quote_to_usd
    initial_ask_depth_usd = initial_ask_depth_quote * quote_to_usd
    last_bid_depth_usd = last_bid_depth_quote * quote_to_usd
    last_ask_depth_usd = last_ask_depth_quote * quote_to_usd
    same_side_depth_usd = initial_bid_depth_usd if str(trial.get("side") or "").lower() == "buy" else initial_ask_depth_usd
    return {
        "fill_like": is_fill_like(trial.get("result")),
        "result": str(trial.get("result") or ""),
        "reason": str(trial.get("reason") or ""),
        "initial_spread_bps": round(initial_spread, 6),
        "last_spread_bps": round(last_spread, 6),
        "spread_change_bps": round(spread_change_bps, 6),
        "bid_move_bps": round(bid_move_bps, 6),
        "ask_move_bps": round(ask_move_bps, 6),
        "samples": int(to_float(trial.get("samples"))),
        "elapsed_seconds": round(to_float(trial.get("elapsed_seconds")), 3),
        "order_price": to_float(trial.get("order_price")),
        "quote_currency": quote_currency,
        "quote_to_usd": round(quote_to_usd, 8),
        "initial_bid_depth_quote": round(initial_bid_depth_quote, 12),
        "initial_ask_depth_quote": round(initial_ask_depth_quote, 12),
        "last_bid_depth_quote": round(last_bid_depth_quote, 12),
        "last_ask_depth_quote": round(last_ask_depth_quote, 12),
        "initial_bid_depth_usd": round(initial_bid_depth_usd, 6),
        "initial_ask_depth_usd": round(initial_ask_depth_usd, 6),
        "last_bid_depth_usd": round(last_bid_depth_usd, 6),
        "last_ask_depth_usd": round(last_ask_depth_usd, 6),
        "same_side_depth_usd": round(same_side_depth_usd, 6),
        "same_side_depth_ok": bool(depth_notional_usd <= 0.0 or (quote_to_usd > 0.0 and same_side_depth_usd >= depth_notional_usd)),
    }


def cycle_record(
    product: str,
    offset: float,
    cycle_index: int,
    buy_trial: dict[str, Any],
    sell_trial: dict[str, Any],
    *,
    depth_notional_usd: float = 0.0,
    quote_currency: str = "USD",
    quote_to_usd: float = 1.0,
    sell_floor_price: float | None = None,
) -> dict[str, Any]:
    buy = trial_features(buy_trial, depth_notional_usd=depth_notional_usd, quote_currency=quote_currency, quote_to_usd=quote_to_usd)
    sell = trial_features(sell_trial, depth_notional_usd=depth_notional_usd, quote_currency=quote_currency, quote_to_usd=quote_to_usd)
    return {
        "action": "crossing_pressure_cycle",
        "ts_utc": utc_now_iso(),
        "product_id": product.upper(),
        "quote_currency": normalize_asset(quote_currency),
        "quote_to_usd": round(to_float(quote_to_usd), 8),
        "offset_frac": round(float(offset), 6),
        "cycle_index": cycle_index,
        "sell_floor_price": sell_floor_price,
        "buy": buy,
        "sell": sell,
        "buy_fill_like": bool(buy["fill_like"]),
        "sell_fill_like": bool(sell["fill_like"]),
        "two_sided_fill_like": bool(buy["fill_like"] and sell["fill_like"]),
        "buy_depth_ok": bool(buy["same_side_depth_ok"]),
        "sell_depth_ok": bool(sell["same_side_depth_ok"]),
        "two_sided_depth_ok": bool(buy["same_side_depth_ok"] and sell["same_side_depth_ok"]),
        "read": "Public-book pressure tape only. No private endpoints or live orders used.",
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_key: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, dict[str, Any]] = {}
    for record in records:
        key = f"{record.get('product_id')}|{float(to_float(record.get('offset_frac'))):.4f}"
        by_key[key]["cycles"] += 1
        if record.get("buy_fill_like"):
            by_key[key]["buy_fill_like"] += 1
        if record.get("sell_fill_like"):
            by_key[key]["sell_fill_like"] += 1
        if record.get("two_sided_fill_like"):
            by_key[key]["two_sided_fill_like"] += 1
            examples.setdefault(key, record)
        if record.get("buy_depth_ok"):
            by_key[key]["buy_depth_ok"] += 1
        if record.get("sell_depth_ok"):
            by_key[key]["sell_depth_ok"] += 1
        if record.get("two_sided_depth_ok"):
            by_key[key]["two_sided_depth_ok"] += 1
    rows: list[dict[str, Any]] = []
    for key, counts in sorted(by_key.items()):
        cycles = counts["cycles"]
        buy = counts["buy_fill_like"]
        sell = counts["sell_fill_like"]
        dual = counts["two_sided_fill_like"]
        buy_depth = counts["buy_depth_ok"]
        sell_depth = counts["sell_depth_ok"]
        dual_depth = counts["two_sided_depth_ok"]
        rows.append(
            {
                "key": key,
                "cycles": cycles,
                "buy_fill_like": buy,
                "sell_fill_like": sell,
                "two_sided_fill_like": dual,
                "buy_fill_rate": round(buy / cycles, 6) if cycles else 0.0,
                "sell_fill_rate": round(sell / cycles, 6) if cycles else 0.0,
                "two_sided_fill_rate": round(dual / cycles, 6) if cycles else 0.0,
                "buy_depth_ok": buy_depth,
                "sell_depth_ok": sell_depth,
                "two_sided_depth_ok": dual_depth,
                "buy_depth_ok_rate": round(buy_depth / cycles, 6) if cycles else 0.0,
                "sell_depth_ok_rate": round(sell_depth / cycles, 6) if cycles else 0.0,
                "two_sided_depth_ok_rate": round(dual_depth / cycles, 6) if cycles else 0.0,
                "example": examples.get(key),
            }
        )
    rows.sort(key=lambda row: (-to_float(row.get("two_sided_fill_rate")), -to_float(row.get("buy_fill_rate")), -to_float(row.get("sell_fill_rate")), str(row.get("key"))))
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_crossing_pressure_tape",
        "records": len(records),
        "two_sided_records": sum(1 for record in records if record.get("two_sided_fill_like")),
        "two_sided_depth_ok_records": sum(1 for record in records if record.get("two_sided_depth_ok")),
        "leaders": rows,
        "read": "Use leaders with repeated two-sided fill-like evidence as candidates for validate-only review. This is not live-order permission.",
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    client = KrakenSpotClient()
    selected_rows: list[dict[str, Any]] = []
    if args.product_source == "top-spread":
        products, selected_rows = load_top_spread_products(client, args)
    elif args.product_source == "radar-heartbeat":
        products, selected_rows = load_radar_heartbeat_products(args)
    elif args.product_source == "radar-side-heartbeat":
        products, selected_rows = load_radar_side_heartbeat_products(args)
    elif args.product_source == "dislocation-lab":
        products, selected_rows = load_dislocation_lab_products(args)
    else:
        products = [str(product).upper() for product in args.products]
    offsets = parse_float_csv(args.offsets)
    pair_info = load_pair_info(client, products)
    quote_to_usd_rates = load_quote_to_usd_rates(client, products)
    quote_by_product = {product: infer_product_quote_currency(product) for product in products}
    sell_floor_prices = load_sell_floor_prices(args.sell_floor_path) if args.enforce_sell_floor_from_queue else {}
    records: list[dict[str, Any]] = []
    for cycle_index in range(1, int(args.cycles) + 1):
        for product in products:
            info = pair_info[product]
            for offset in offsets:
                buy_trial = run_trial(
                    client=client,
                    product=product,
                    rest_pair=info.rest_pair,
                    side="buy",
                    price_offset_frac=offset,
                    tick_back=None,
                    tick_size=info.tick_size,
                    ttl_seconds=args.ttl_seconds,
                    poll_seconds=args.poll_seconds,
                    ghost_penalty_bps=args.ghost_penalty_bps,
                )
                append_jsonl(args.event_path, buy_trial)
                sell_trial = run_trial(
                    client=client,
                    product=product,
                    rest_pair=info.rest_pair,
                    side="sell",
                    price_offset_frac=offset,
                    tick_back=None,
                    tick_size=info.tick_size,
                    ttl_seconds=args.ttl_seconds,
                    poll_seconds=args.poll_seconds,
                    ghost_penalty_bps=args.ghost_penalty_bps,
                    min_order_price=sell_floor_prices.get(product),
                )
                append_jsonl(args.event_path, sell_trial)
                record = cycle_record(
                    product,
                    offset,
                    cycle_index,
                    buy_trial,
                    sell_trial,
                    depth_notional_usd=float(args.depth_notional_usd),
                    quote_currency=quote_by_product.get(product, "USD"),
                    quote_to_usd=quote_to_usd_rates.get(quote_by_product.get(product, "USD"), 0.0),
                    sell_floor_price=sell_floor_prices.get(product),
                )
                append_jsonl(args.event_path, record)
                records.append(record)
    summary = summarize(records)
    summary["parameters"] = {
        "products": products,
        "offsets": offsets,
        "cycles": int(args.cycles),
        "ttl_seconds": float(args.ttl_seconds),
        "poll_seconds": float(args.poll_seconds),
        "ghost_penalty_bps": float(args.ghost_penalty_bps),
        "depth_notional_usd": float(args.depth_notional_usd),
        "quote_to_usd_rates": quote_to_usd_rates,
        "enforce_sell_floor_from_queue": bool(args.enforce_sell_floor_from_queue),
        "sell_floor_path": str(args.sell_floor_path) if args.sell_floor_path else "",
        "sell_floor_products": sorted(sell_floor_prices),
        "event_path": str(args.event_path),
        "product_source": str(args.product_source),
    }
    if selected_rows:
        summary["selected_products"] = selected_rows
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Public-only Kraken two-sided crossing-pressure tape.")
    parser.add_argument("--products", nargs="+", default=["GWEI-USD", "WEN-USD", "TRAC-USD"])
    parser.add_argument("--product-source", choices=["fixed", "top-spread", "radar-heartbeat", "radar-side-heartbeat", "dislocation-lab"], default="fixed")
    parser.add_argument("--quote-currencies", nargs="+", default=["USD"])
    parser.add_argument("--top-products", type=int, default=5)
    parser.add_argument("--min-spread-bps", type=float, default=0.0)
    parser.add_argument("--min-volume-24h", type=float, default=0.0)
    parser.add_argument("--radar-path", type=Path, default=DEFAULT_RADAR_PATH)
    parser.add_argument("--radar-cache-path", type=Path, default=DEFAULT_RADAR_CACHE_PATH)
    parser.add_argument("--radar-states", nargs="+", default=["live_hot", "building"])
    parser.add_argument("--max-radar-spread-bps", type=float, default=250.0)
    parser.add_argument("--min-best-short-bps", type=float, default=10.0)
    parser.add_argument("--min-radar-samples", type=int, default=2)
    parser.add_argument("--min-ask-down-bps", type=float, default=5.0)
    parser.add_argument("--min-bid-up-bps", type=float, default=5.0)
    parser.add_argument("--min-latest-ask-down-bps", type=float, default=0.0)
    parser.add_argument("--min-latest-bid-up-bps", type=float, default=0.0)
    parser.add_argument("--side-lookback-seconds", type=float, default=90.0)
    parser.add_argument("--side-mode", choices=["both", "entry", "exit", "either"], default="both")
    parser.add_argument("--dislocation-lab-path", type=Path, default=DEFAULT_DISLOCATION_LAB_PATH)
    parser.add_argument("--dislocation-horizon-seconds", type=int, default=60)
    parser.add_argument("--min-dislocation-net-pct", type=float, default=0.0)
    parser.add_argument("--min-dislocation-mfe-net-pct", type=float, default=0.0)
    parser.add_argument("--dislocation-score-mode", choices=["either", "realized", "mfe", "both"], default="either")
    parser.add_argument("--dislocation-setups", nargs="*", default=[])
    parser.add_argument("--offsets", nargs="+", default=["0.5"])
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--ttl-seconds", type=float, default=10.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--ghost-penalty-bps", type=float, default=0.0)
    parser.add_argument("--depth-notional-usd", type=float, default=15.0)
    parser.add_argument("--enforce-sell-floor-from-queue", action="store_true")
    parser.add_argument("--sell-floor-path", type=Path, default=DEFAULT_FIRE_QUEUE_PATH)
    parser.add_argument("--event-path", type=Path, default=DEFAULT_EVENT_PATH)
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = run(args)
    print(json.dumps({"summary_path": str(args.summary_path), "leaders": summary["leaders"][:5], "two_sided_records": summary["two_sided_records"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
