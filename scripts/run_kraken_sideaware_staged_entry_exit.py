#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import kraken_config as cfg  # noqa: E402
from build_kraken_spot_live_radar import build_once as build_radar_once  # noqa: E402
from kraken_spot_client import KrakenSpotClient, to_float  # noqa: E402
from run_kraken_crossing_pressure_tape import FILL_LIKE_RESULTS  # noqa: E402
from run_kraken_crossing_pressure_tape import load_radar_side_heartbeat_products, parse_float_csv, side_motion_from_samples  # noqa: E402
from run_kraken_maker_microfill_calibrator import fetch_top, load_pair_info, maker_price_at_offset, run_trial  # noqa: E402


DEFAULT_EVENT_PATH = REPORTS / "kraken_sideaware_staged_entry_exit_events.jsonl"
DEFAULT_SUMMARY_PATH = REPORTS / "kraken_sideaware_staged_entry_exit_summary.json"
DEFAULT_RADAR_PATH = REPORTS / "kraken_sideaware_staged_radar.json"
DEFAULT_RADAR_CACHE_PATH = REPORTS / "cache" / "kraken_sideaware_staged_radar_ticks.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def is_fill_like(result: Any) -> bool:
    return str(result or "") in FILL_LIKE_RESULTS


def net_roundtrip_bps(entry_price: float, exit_price: float, fee_bps_per_side: float) -> float:
    if entry_price <= 0.0 or exit_price <= 0.0:
        return 0.0
    gross_bps = ((exit_price - entry_price) / entry_price) * 10000.0
    return gross_bps - (2.0 * float(fee_bps_per_side))


def clears_exit_net_floor(entry_price: float, exit_price: float, fee_bps_per_side: float, min_exit_net_bps: float) -> tuple[bool, float]:
    net_bps = net_roundtrip_bps(entry_price, exit_price, fee_bps_per_side)
    return net_bps >= float(min_exit_net_bps), net_bps


def minimum_exit_price(entry_price: float, fee_bps_per_side: float, min_exit_net_bps: float) -> float:
    if entry_price <= 0.0:
        return 0.0
    hurdle_bps = (2.0 * float(fee_bps_per_side)) + float(min_exit_net_bps)
    return entry_price * (1.0 + (hurdle_bps / 10000.0))


def price_above_ask_bps(price: float, ask: float) -> float:
    if price <= 0.0 or ask <= 0.0:
        return 0.0
    return max(0.0, ((price - ask) / ask) * 10000.0)


def radar_args(args: argparse.Namespace) -> SimpleNamespace:
    return SimpleNamespace(
        state_path=str(args.radar_cache_path),
        json_path=str(args.radar_path),
        csv_path=str(args.radar_csv_path),
        md_path=str(args.radar_md_path),
        quotes=args.quotes,
        all_quotes=False,
        max_products=args.max_products,
        chunk_size=args.chunk_size,
        keep_seconds=args.keep_seconds,
        poll_seconds=args.poll_seconds,
        loop=False,
        max_spread_bps=args.max_radar_spread_bps,
        hot_bps=args.hot_bps,
        building_bps=args.building_bps,
        starting_cash=args.starting_cash,
        deploy_pct=args.deploy_pct,
        maker_fee_bps=args.maker_fee_bps,
        taker_fee_bps=args.taker_fee_bps,
        use_websocket=args.use_websocket,
        websocket_timeout_seconds=args.websocket_timeout_seconds,
    )


def side_source_args(args: argparse.Namespace, *, side_mode: str) -> SimpleNamespace:
    return SimpleNamespace(
        radar_path=args.radar_path,
        radar_cache_path=args.radar_cache_path,
        quote_currencies=args.quotes.split(","),
        top_products=args.top_products,
        max_radar_spread_bps=args.max_radar_spread_bps,
        min_ask_down_bps=args.min_entry_ask_down_bps,
        min_bid_up_bps=args.min_exit_bid_up_bps,
        min_latest_ask_down_bps=args.min_latest_entry_ask_down_bps,
        min_latest_bid_up_bps=args.min_latest_exit_bid_up_bps,
        min_radar_samples=args.min_radar_samples,
        side_lookback_seconds=args.side_lookback_seconds,
        side_mode=side_mode,
    )


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def side_motion_for_product(args: argparse.Namespace, rest_pair: str) -> dict[str, float]:
    cache = load_json(args.radar_cache_path)
    samples = (cache.get("samples") or {}).get(rest_pair) or []
    return side_motion_from_samples(samples, lookback_seconds=args.side_lookback_seconds)


def parse_str_csv(value: str) -> list[str]:
    return [part.strip().upper() for part in str(value or "").replace(";", ",").split(",") if part.strip()]


def load_entry_products(args: argparse.Namespace) -> tuple[list[str], list[dict[str, Any]]]:
    source = str(args.entry_product_source or "side-aware").lower()
    if source == "side-aware":
        return load_radar_side_heartbeat_products(side_source_args(args, side_mode="entry"))
    if source == "fixed":
        products = parse_str_csv(args.products)
        rows = [{"product_id": product, "source": "fixed"} for product in products]
        return products, rows
    if source != "radar":
        raise ValueError(f"unknown entry product source {source!r}")

    radar_payload = load_json(args.radar_path)
    quotes = set(parse_str_csv(args.quotes))
    rows: list[dict[str, Any]] = []
    for row in radar_payload.get("rows") or []:
        product = str(row.get("product_id") or "").upper()
        quote = str(row.get("quote_currency") or "").upper()
        spread_bps = to_float(row.get("spread_bps"))
        if not product:
            continue
        if quotes and quote not in quotes:
            continue
        if spread_bps < float(args.min_entry_spread_bps):
            continue
        if float(args.max_radar_spread_bps) > 0.0 and spread_bps > float(args.max_radar_spread_bps):
            continue
        rows.append(
            {
                "product_id": product,
                "rest_pair": str(row.get("rest_pair") or ""),
                "quote_currency": quote,
                "signal_state": str(row.get("signal_state") or ""),
                "velocity_score": to_float(row.get("velocity_score")),
                "best_short_bps": to_float(row.get("best_short_bps")),
                "spread_bps": spread_bps,
            }
        )
    rows.sort(key=lambda row: (-to_float(row["velocity_score"]), -to_float(row["best_short_bps"]), -to_float(row["spread_bps"]), str(row["product_id"])))
    rows = rows[: max(0, int(args.top_products))]
    return [str(row["product_id"]) for row in rows], rows


def run(args: argparse.Namespace) -> dict[str, Any]:
    client = KrakenSpotClient()
    offsets = parse_float_csv(args.offsets)
    staged_events: list[dict[str, Any]] = []
    for scan_index in range(1, int(args.scans) + 1):
        build_radar_once(radar_args(args))
        entry_products, entry_selected = load_entry_products(args)
        if not entry_products:
            event = {
                "action": "staged_scan_no_entry_candidate",
                "scan_index": scan_index,
                "ts_utc": utc_now_iso(),
                "read": "Public-only staged entry/exit proof. No private endpoints, validate calls, or live orders.",
            }
            append_jsonl(args.event_path, event)
            staged_events.append(event)
            time.sleep(max(0.0, float(args.scan_sleep_seconds)))
            continue

        pair_info = load_pair_info(client, entry_products)
        for product in entry_products[: int(args.top_products)]:
            info = pair_info[product]
            for offset in offsets:
                entry_book = fetch_top(client, info.rest_pair)
                if entry_book is None:
                    veto_event = {
                        "action": "staged_entry_veto",
                        "scan_index": scan_index,
                        "ts_utc": utc_now_iso(),
                        "product_id": product,
                        "offset_frac": float(offset),
                        "reason": "entry_book_unavailable",
                    }
                    append_jsonl(args.event_path, veto_event)
                    staged_events.append(veto_event)
                    continue
                entry_bid_depth_usd = entry_book.bid * entry_book.bid_size
                entry_ask_depth_usd = entry_book.ask * entry_book.ask_size
                if float(args.depth_notional_usd) > 0.0 and (
                    entry_bid_depth_usd < float(args.depth_notional_usd) or entry_ask_depth_usd < float(args.depth_notional_usd)
                ):
                    veto_event = {
                        "action": "staged_entry_veto",
                        "scan_index": scan_index,
                        "ts_utc": utc_now_iso(),
                        "product_id": product,
                        "offset_frac": float(offset),
                        "entry_bid": entry_book.bid,
                        "entry_ask": entry_book.ask,
                        "entry_bid_depth_usd": round(entry_bid_depth_usd, 6),
                        "entry_ask_depth_usd": round(entry_ask_depth_usd, 6),
                        "depth_notional_usd": float(args.depth_notional_usd),
                        "reason": "entry_depth_below_notional",
                    }
                    append_jsonl(args.event_path, veto_event)
                    staged_events.append(veto_event)
                    continue
                if bool(args.fixed_requires_entry_trigger) and str(args.entry_product_source or "").lower() == "fixed":
                    motion = side_motion_for_product(args, info.rest_pair)
                    ask_down = max(to_float(motion.get("ask_down_bps")), to_float(motion.get("latest_ask_down_bps")))
                    if ask_down < float(args.min_entry_ask_down_bps):
                        veto_event = {
                            "action": "staged_entry_veto",
                            "scan_index": scan_index,
                            "ts_utc": utc_now_iso(),
                            "product_id": product,
                            "offset_frac": float(offset),
                            "ask_down_bps": motion.get("ask_down_bps"),
                            "latest_ask_down_bps": motion.get("latest_ask_down_bps"),
                            "min_entry_ask_down_bps": float(args.min_entry_ask_down_bps),
                            "reason": "entry_trigger_below_threshold",
                        }
                        append_jsonl(args.event_path, veto_event)
                        staged_events.append(veto_event)
                        continue
                candidate_entry_price = maker_price_at_offset("buy", entry_book, offset)
                candidate_exit_floor = minimum_exit_price(candidate_entry_price, args.maker_fee_bps, args.min_exit_net_bps)
                candidate_floor_above_ask_bps = price_above_ask_bps(candidate_exit_floor, entry_book.ask)
                if float(args.max_entry_spread_bps) > 0.0 and entry_book.spread_bps > float(args.max_entry_spread_bps):
                    veto_event = {
                        "action": "staged_entry_veto",
                        "scan_index": scan_index,
                        "ts_utc": utc_now_iso(),
                        "product_id": product,
                        "offset_frac": float(offset),
                        "entry_bid": entry_book.bid,
                        "entry_ask": entry_book.ask,
                        "entry_spread_bps": round(entry_book.spread_bps, 6),
                        "candidate_entry_price": candidate_entry_price,
                        "candidate_exit_floor": candidate_exit_floor,
                        "candidate_exit_floor_above_ask_bps": round(candidate_floor_above_ask_bps, 6),
                        "max_entry_spread_bps": float(args.max_entry_spread_bps),
                        "reason": "entry_spread_above_limit",
                    }
                    append_jsonl(args.event_path, veto_event)
                    staged_events.append(veto_event)
                    continue
                if float(args.max_exit_floor_above_ask_bps) >= 0.0 and candidate_floor_above_ask_bps > float(args.max_exit_floor_above_ask_bps):
                    veto_event = {
                        "action": "staged_entry_veto",
                        "scan_index": scan_index,
                        "ts_utc": utc_now_iso(),
                        "product_id": product,
                        "offset_frac": float(offset),
                        "entry_bid": entry_book.bid,
                        "entry_ask": entry_book.ask,
                        "entry_spread_bps": round(entry_book.spread_bps, 6),
                        "candidate_entry_price": candidate_entry_price,
                        "candidate_exit_floor": candidate_exit_floor,
                        "candidate_exit_floor_above_ask_bps": round(candidate_floor_above_ask_bps, 6),
                        "max_exit_floor_above_ask_bps": float(args.max_exit_floor_above_ask_bps),
                        "reason": "exit_floor_too_far_above_entry_ask",
                    }
                    append_jsonl(args.event_path, veto_event)
                    staged_events.append(veto_event)
                    continue
                entry_trial = run_trial(
                    client=client,
                    product=product,
                    rest_pair=info.rest_pair,
                    side="buy",
                    price_offset_frac=offset,
                    tick_back=None,
                    tick_size=info.tick_size,
                    ttl_seconds=args.entry_ttl_seconds,
                    poll_seconds=args.poll_seconds,
                    ghost_penalty_bps=args.ghost_penalty_bps,
                )
                append_jsonl(args.event_path, entry_trial)
                entry_event = {
                    "action": "staged_entry_trial",
                    "scan_index": scan_index,
                    "ts_utc": utc_now_iso(),
                    "product_id": product,
                    "rest_pair": info.rest_pair,
                    "offset_frac": float(offset),
                    "selected_products": entry_selected,
                    "fill_like": is_fill_like(entry_trial.get("result")),
                    "entry_result": entry_trial.get("result"),
                    "entry_reason": entry_trial.get("reason"),
                    "entry_price": entry_trial.get("order_price"),
                    "candidate_exit_floor": candidate_exit_floor,
                    "candidate_exit_floor_above_ask_bps": round(candidate_floor_above_ask_bps, 6),
                    "entry_elapsed_seconds": entry_trial.get("elapsed_seconds"),
                    "read": "Public-only staged entry proxy. No private endpoints, validate calls, or live orders.",
                }
                append_jsonl(args.event_path, entry_event)
                staged_events.append(entry_event)
                if not entry_event["fill_like"]:
                    continue

                exit_found = False
                for exit_index in range(1, int(args.exit_scans) + 1):
                    time.sleep(max(0.0, float(args.exit_sleep_seconds)))
                    build_radar_once(radar_args(args))
                    motion = side_motion_for_product(args, info.rest_pair)
                    if to_float(motion.get("bid_up_bps")) < float(args.min_exit_bid_up_bps):
                        wait_event = {
                            "action": "staged_exit_wait",
                            "scan_index": scan_index,
                            "exit_index": exit_index,
                            "ts_utc": utc_now_iso(),
                            "product_id": product,
                            "bid_up_bps": motion.get("bid_up_bps"),
                            "ask_down_bps": motion.get("ask_down_bps"),
                            "reason": "bid_up_below_exit_threshold",
                        }
                        append_jsonl(args.event_path, wait_event)
                        staged_events.append(wait_event)
                        continue

                    entry_price = to_float(entry_event.get("entry_price"))
                    exit_book = fetch_top(client, info.rest_pair)
                    if exit_book is None:
                        wait_event = {
                            "action": "staged_exit_wait",
                            "scan_index": scan_index,
                            "exit_index": exit_index,
                            "ts_utc": utc_now_iso(),
                            "product_id": product,
                            "bid_up_bps": motion.get("bid_up_bps"),
                            "ask_down_bps": motion.get("ask_down_bps"),
                            "reason": "exit_book_unavailable",
                        }
                        append_jsonl(args.event_path, wait_event)
                        staged_events.append(wait_event)
                        continue
                    exit_ask_depth_usd = exit_book.ask * exit_book.ask_size
                    if float(args.depth_notional_usd) > 0.0 and exit_ask_depth_usd < float(args.depth_notional_usd):
                        wait_event = {
                            "action": "staged_exit_wait",
                            "scan_index": scan_index,
                            "exit_index": exit_index,
                            "ts_utc": utc_now_iso(),
                            "product_id": product,
                            "bid_up_bps": motion.get("bid_up_bps"),
                            "ask_down_bps": motion.get("ask_down_bps"),
                            "exit_ask_depth_usd": round(exit_ask_depth_usd, 6),
                            "depth_notional_usd": float(args.depth_notional_usd),
                            "reason": "exit_ask_depth_below_notional",
                        }
                        append_jsonl(args.event_path, wait_event)
                        staged_events.append(wait_event)
                        continue

                    candidate_exit_price = maker_price_at_offset("sell", exit_book, offset)
                    floor_ok, candidate_net_bps = clears_exit_net_floor(
                        entry_price,
                        candidate_exit_price,
                        args.maker_fee_bps,
                        args.min_exit_net_bps,
                    )
                    if not floor_ok and not args.enforce_exit_price_floor:
                        wait_event = {
                            "action": "staged_exit_wait",
                            "scan_index": scan_index,
                            "exit_index": exit_index,
                            "ts_utc": utc_now_iso(),
                            "product_id": product,
                            "bid_up_bps": motion.get("bid_up_bps"),
                            "ask_down_bps": motion.get("ask_down_bps"),
                            "entry_price": entry_event.get("entry_price"),
                            "candidate_exit_price": candidate_exit_price,
                            "candidate_net_roundtrip_bps_after_maker_fees": round(candidate_net_bps, 6),
                            "min_exit_net_bps": float(args.min_exit_net_bps),
                            "exit_bid": exit_book.bid,
                            "exit_ask": exit_book.ask,
                            "reason": "exit_net_below_profit_floor",
                        }
                        append_jsonl(args.event_path, wait_event)
                        staged_events.append(wait_event)
                        continue
                    exit_min_order_price = minimum_exit_price(entry_price, args.maker_fee_bps, args.min_exit_net_bps) if args.enforce_exit_price_floor else None

                    exit_trial = run_trial(
                        client=client,
                        product=product,
                        rest_pair=info.rest_pair,
                        side="sell",
                        price_offset_frac=offset,
                        tick_back=None,
                        tick_size=info.tick_size,
                        ttl_seconds=args.exit_ttl_seconds,
                        poll_seconds=args.poll_seconds,
                        ghost_penalty_bps=args.ghost_penalty_bps,
                        min_order_price=exit_min_order_price,
                    )
                    append_jsonl(args.event_path, exit_trial)
                    net_bps = net_roundtrip_bps(entry_price, to_float(exit_trial.get("order_price")), args.maker_fee_bps)
                    raw_fill_like = is_fill_like(exit_trial.get("result"))
                    profit_floor_cleared = net_bps >= float(args.min_exit_net_bps)
                    exit_event = {
                        "action": "staged_exit_trial",
                        "scan_index": scan_index,
                        "exit_index": exit_index,
                        "ts_utc": utc_now_iso(),
                        "product_id": product,
                        "offset_frac": float(offset),
                        "fill_like": raw_fill_like and profit_floor_cleared,
                        "raw_fill_like": raw_fill_like,
                        "profit_floor_cleared": profit_floor_cleared,
                        "exit_result": exit_trial.get("result"),
                        "exit_reason": exit_trial.get("reason"),
                        "entry_price": entry_event.get("entry_price"),
                        "exit_price": exit_trial.get("order_price"),
                        "minimum_exit_price": exit_min_order_price,
                        "net_roundtrip_bps_after_maker_fees": round(net_bps, 6),
                        "min_exit_net_bps": float(args.min_exit_net_bps),
                        "bid_up_bps": motion.get("bid_up_bps"),
                        "ask_down_bps": motion.get("ask_down_bps"),
                    }
                    append_jsonl(args.event_path, exit_event)
                    staged_events.append(exit_event)
                    if exit_event["fill_like"]:
                        exit_found = True
                        break
                if not exit_found:
                    unresolved = {
                        "action": "staged_roundtrip_unresolved",
                        "scan_index": scan_index,
                        "ts_utc": utc_now_iso(),
                        "product_id": product,
                        "entry_price": entry_event.get("entry_price"),
                        "exit_scans": int(args.exit_scans),
                    }
                    append_jsonl(args.event_path, unresolved)
                    staged_events.append(unresolved)
        time.sleep(max(0.0, float(args.scan_sleep_seconds)))

    entry_trials = [event for event in staged_events if event.get("action") == "staged_entry_trial"]
    entry_vetoes = [event for event in staged_events if event.get("action") == "staged_entry_veto"]
    exit_trials = [event for event in staged_events if event.get("action") == "staged_exit_trial"]
    exit_waits = [event for event in staged_events if event.get("action") == "staged_exit_wait"]
    roundtrips = [event for event in exit_trials if event.get("fill_like")]
    raw_exit_fill_like = sum(1 for event in exit_trials if event.get("raw_fill_like", event.get("fill_like")))
    profit_floor_vetoes = sum(1 for event in exit_waits if event.get("reason") == "exit_net_below_profit_floor") + sum(
        1 for event in exit_trials if event.get("raw_fill_like") and not event.get("profit_floor_cleared")
    )
    summary = {
        "generated_at": utc_now_iso(),
        "mode": "kraken_sideaware_staged_entry_exit",
        "read": "Public-only staged entry/exit proof. No private endpoints, validate calls, or live orders.",
        "parameters": vars(args),
        "entry_trials": len(entry_trials),
        "entry_vetoes": len(entry_vetoes),
        "entry_floor_vetoes": sum(1 for event in entry_vetoes if event.get("reason") == "exit_floor_too_far_above_entry_ask"),
        "entry_fill_like": sum(1 for event in entry_trials if event.get("fill_like")),
        "entry_fill_like_rate": round(sum(1 for event in entry_trials if event.get("fill_like")) / len(entry_trials), 6) if entry_trials else 0.0,
        "exit_trials": len(exit_trials),
        "exit_fill_like": sum(1 for event in exit_trials if event.get("fill_like")),
        "exit_fill_like_rate": round(sum(1 for event in exit_trials if event.get("fill_like")) / len(exit_trials), 6) if exit_trials else 0.0,
        "raw_exit_fill_like": raw_exit_fill_like,
        "profit_floor_vetoes": profit_floor_vetoes,
        "staged_roundtrip_fill_like": len(roundtrips),
        "roundtrips": roundtrips,
    }
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Public-only staged side-aware entry then exit proof.")
    parser.add_argument("--scans", type=int, default=2)
    parser.add_argument("--scan-sleep-seconds", type=float, default=5.0)
    parser.add_argument("--exit-scans", type=int, default=4)
    parser.add_argument("--exit-sleep-seconds", type=float, default=5.0)
    parser.add_argument("--quotes", default="USD")
    parser.add_argument("--max-products", type=int, default=10000)
    parser.add_argument("--chunk-size", type=int, default=50)
    parser.add_argument("--keep-seconds", type=float, default=3900.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--use-websocket", action="store_true")
    parser.add_argument("--websocket-timeout-seconds", type=float, default=8.0)
    parser.add_argument("--starting-cash", type=float, default=50.0)
    parser.add_argument("--deploy-pct", type=float, default=0.3)
    parser.add_argument("--maker-fee-bps", type=float, default=cfg.DEFAULT_MAKER_FEE_BPS)
    parser.add_argument("--taker-fee-bps", type=float, default=cfg.DEFAULT_TAKER_FEE_BPS)
    parser.add_argument("--hot-bps", type=float, default=25.0)
    parser.add_argument("--building-bps", type=float, default=10.0)
    parser.add_argument("--top-products", type=int, default=3)
    parser.add_argument("--entry-product-source", choices=["side-aware", "radar", "fixed"], default="side-aware")
    parser.add_argument("--products", default="")
    parser.add_argument("--min-entry-spread-bps", type=float, default=0.0)
    parser.add_argument("--max-radar-spread-bps", type=float, default=250.0)
    parser.add_argument("--min-radar-samples", type=int, default=3)
    parser.add_argument("--min-entry-ask-down-bps", type=float, default=50.0)
    parser.add_argument("--min-exit-bid-up-bps", type=float, default=50.0)
    parser.add_argument("--min-exit-net-bps", type=float, default=0.0)
    parser.add_argument("--enforce-exit-price-floor", action="store_true")
    parser.add_argument("--min-latest-entry-ask-down-bps", type=float, default=0.0)
    parser.add_argument("--min-latest-exit-bid-up-bps", type=float, default=0.0)
    parser.add_argument("--max-entry-spread-bps", type=float, default=0.0)
    parser.add_argument("--max-exit-floor-above-ask-bps", type=float, default=-1.0)
    parser.add_argument("--depth-notional-usd", type=float, default=15.0)
    parser.add_argument("--fixed-requires-entry-trigger", action="store_true")
    parser.add_argument("--side-lookback-seconds", type=float, default=120.0)
    parser.add_argument("--offsets", default="0.5")
    parser.add_argument("--entry-ttl-seconds", type=float, default=8.0)
    parser.add_argument("--exit-ttl-seconds", type=float, default=8.0)
    parser.add_argument("--ghost-penalty-bps", type=float, default=2.0)
    parser.add_argument("--radar-path", type=Path, default=DEFAULT_RADAR_PATH)
    parser.add_argument("--radar-cache-path", type=Path, default=DEFAULT_RADAR_CACHE_PATH)
    parser.add_argument("--radar-csv-path", type=Path, default=REPORTS / "kraken_sideaware_staged_radar.csv")
    parser.add_argument("--radar-md-path", type=Path, default=REPORTS / "kraken_sideaware_staged_radar.md")
    parser.add_argument("--event-path", type=Path, default=DEFAULT_EVENT_PATH)
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY_PATH)
    return parser.parse_args()


def main() -> int:
    summary = run(parse_args())
    print(
        json.dumps(
            {
                "summary_path": str(summary["parameters"]["summary_path"]),
                "entry_fill_like": summary["entry_fill_like"],
                "exit_fill_like": summary["exit_fill_like"],
                "staged_roundtrip_fill_like": summary["staged_roundtrip_fill_like"],
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
