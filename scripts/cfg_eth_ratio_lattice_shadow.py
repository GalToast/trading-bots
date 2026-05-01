#!/usr/bin/env python3
"""
CFG/ETH Synthetic Ratio Lattice Shadow Runner
=============================================

Shadow-only proof harness for the first deployed long-only ratio sleeve.

This models the route we can actually trade on Coinbase spot:
- park capital in `ETH`
- when `CFG/ETH` gets cheap, rotate one sleeve `ETH -> USD -> CFG`
- when the ratio recovers to the frozen target, rotate `CFG -> USD -> ETH`

Important:
- no opposite-side hedge book
- no synthetic "long ETH because ratio is rich" proxy
- no live orders; this is paper routing only
- attractor levels are frozen from a startup training window and then held fixed

Current durable defaults:
- pair: `CFG/ETH`
- frozen training window: `20d`
- profit threshold: `1.012`
- max levels: `5`
- max hold: `96` bars
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from coinbase_advanced_client import CoinbaseAdvancedClient
from multi_coin_isolated_runner import append_jsonl, fetch_candles
from ratio_lattice_60d_validation import (
    SYMBOL_TO_PRODUCT,
    build_price_map,
    build_ratio_series,
    find_attractors_kde,
)


DEFAULT_NUMERATOR = "CFG"
DEFAULT_DENOMINATOR = "ETH"
DEFAULT_PAIR = f"{DEFAULT_NUMERATOR}/{DEFAULT_DENOMINATOR}"
DEFAULT_PROFIT_THRESHOLD = 1.012
DEFAULT_MAX_LEVELS = 5
DEFAULT_MAX_HOLD_BARS = 96
DEFAULT_TRAIN_DAYS = 20
DEFAULT_FEE_BPS_PER_LEG = 40.0
DEFAULT_LOOKBACK_MINUTES = 24 * 60
DEFAULT_POLL_SECONDS = 30


@dataclass
class FrozenLevel:
    level_idx: int
    ratio: float
    source: str
    density: float | None = None


@dataclass
class OpenSleeve:
    level_idx: int
    level_ratio: float
    target_ratio: float
    entry_ratio_signal: float
    opened_bar_time: int
    opened_at: str
    den_units_in: float
    num_units: float
    num_ask: float
    den_bid: float
    hold_bars: int = 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def fee_rate_from_bps(fee_bps_per_leg: float) -> float:
    return float(fee_bps_per_leg) / 10000.0


def parse_pair_spec(pair_spec: str) -> dict[str, str]:
    parts = [part.strip().upper() for part in str(pair_spec).split("/") if part.strip()]
    if len(parts) != 2:
        raise ValueError(f"Pair must look like NUM/DEN, got: {pair_spec!r}")
    numerator, denominator = parts
    if numerator not in SYMBOL_TO_PRODUCT:
        raise ValueError(f"Unsupported numerator symbol: {numerator}")
    if denominator not in SYMBOL_TO_PRODUCT:
        raise ValueError(f"Unsupported denominator symbol: {denominator}")
    return {
        "numerator": numerator,
        "denominator": denominator,
        "pair_label": f"{numerator}/{denominator}",
        "num_product": SYMBOL_TO_PRODUCT[numerator],
        "den_product": SYMBOL_TO_PRODUCT[denominator],
    }


def default_artifact_paths(pair_ctx: dict[str, str]) -> tuple[Path, Path, Path]:
    stem = f"{pair_ctx['numerator'].lower()}_{pair_ctx['denominator'].lower()}_synthetic_sleeve_shadow"
    return (
        ROOT / "reports" / f"{stem}_state.json",
        ROOT / "reports" / f"{stem}_events.jsonl",
        ROOT / "reports" / f"{stem}_heartbeat.json",
    )


def freeze_levels(
    client: CoinbaseAdvancedClient,
    *,
    pair_ctx: dict[str, str],
    train_days: int,
    max_levels: int,
) -> tuple[list[FrozenLevel], dict[str, Any]]:
    now_ts = int(time.time())
    start_ts = now_ts - int(train_days) * 86400
    num_product = pair_ctx["num_product"]
    den_product = pair_ctx["den_product"]
    num_candles = fetch_candles(client, num_product, start_ts, now_ts, "FIVE_MINUTE")
    den_candles = fetch_candles(client, den_product, start_ts, now_ts, "FIVE_MINUTE")
    ratio_series = build_ratio_series(build_price_map(num_candles), build_price_map(den_candles))
    if len(ratio_series) < 50:
        raise RuntimeError(f"Not enough ratio history to freeze levels for {pair_ctx['pair_label']}: {len(ratio_series)} bars")

    attractors = find_attractors_kde(ratio_series)
    levels: list[FrozenLevel] = []
    seen: set[float] = set()
    for row in attractors:
        ratio = float(row.get("ratio", 0.0) or 0.0)
        if ratio <= 0:
            continue
        rounded = round(ratio, 12)
        if rounded in seen:
            continue
        seen.add(rounded)
        levels.append(
            FrozenLevel(
                level_idx=len(levels),
                ratio=ratio,
                source="kde",
                density=float(row.get("density", 0.0) or 0.0),
            )
        )
        if len(levels) >= int(max_levels):
            break

    if len(levels) < int(max_levels):
        sorted_ratios = sorted(float(row["ratio"]) for row in ratio_series if float(row["ratio"]) > 0)
        for rank in range(len(levels), int(max_levels)):
            pct = (rank + 1) / (int(max_levels) + 1)
            idx = min(len(sorted_ratios) - 1, max(0, int(round((len(sorted_ratios) - 1) * pct))))
            ratio = float(sorted_ratios[idx])
            rounded = round(ratio, 12)
            if rounded in seen:
                continue
            seen.add(rounded)
            levels.append(
                FrozenLevel(
                    level_idx=len(levels),
                    ratio=ratio,
                    source="percentile_fallback",
                    density=None,
                )
            )

    if not levels:
        raise RuntimeError(f"Could not freeze any levels for {pair_ctx['pair_label']}")

    freeze_meta = {
        "pair": pair_ctx["pair_label"],
        "train_days": int(train_days),
        "train_start_ts": start_ts,
        "train_end_ts": now_ts,
        "train_ratio_bars": len(ratio_series),
        "numerator_product": num_product,
        "denominator_product": den_product,
    }
    return levels[: int(max_levels)], freeze_meta


def fetch_recent_ratio_bar(
    client: CoinbaseAdvancedClient,
    *,
    pair_ctx: dict[str, str],
    lookback_minutes: int,
) -> dict[str, Any] | None:
    now_ts = int(time.time())
    start_ts = now_ts - int(lookback_minutes) * 60
    num_candles = fetch_candles(client, pair_ctx["num_product"], start_ts, now_ts, "FIVE_MINUTE")
    den_candles = fetch_candles(client, pair_ctx["den_product"], start_ts, now_ts, "FIVE_MINUTE")
    ratio_series = build_ratio_series(build_price_map(num_candles), build_price_map(den_candles))
    if not ratio_series:
        return None
    return ratio_series[-1]


def fetch_quotes(client: CoinbaseAdvancedClient, *, pair_ctx: dict[str, str]) -> dict[str, dict[str, float]]:
    product_ids = [pair_ctx["num_product"], pair_ctx["den_product"]]
    payload = client.best_bid_ask(product_ids)
    books = payload.get("pricebooks") or []
    snapshot: dict[str, dict[str, float]] = {}
    for product_id in product_ids:
        book = next((row for row in books if str(row.get("product_id", "")).upper() == product_id), None)
        if not book:
            raise RuntimeError(f"No pricebook returned for {product_id}")
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not bids or not asks:
            raise RuntimeError(f"Incomplete pricebook for {product_id}")
        bid = float(bids[0]["price"])
        ask = float(asks[0]["price"])
        snapshot[product_id] = {
            "bid": bid,
            "ask": ask,
            "mid": (bid + ask) / 2.0,
        }
    return snapshot


def denominator_units_from_usd(starting_cash_usd: float, ask_den: float, fee_rate: float) -> float:
    if ask_den <= 0:
        raise ValueError("Denominator ask must be positive")
    return (float(starting_cash_usd) / ask_den) * (1.0 - fee_rate)


def open_num_from_den(
    den_units_in: float,
    *,
    num_ask: float,
    den_bid: float,
    fee_rate: float,
) -> dict[str, float]:
    usd_after_den_sale = float(den_units_in) * float(den_bid) * (1.0 - fee_rate)
    num_units = (usd_after_den_sale / float(num_ask)) * (1.0 - fee_rate)
    return {
        "usd_after_den_sale": usd_after_den_sale,
        "num_units": num_units,
    }


def close_num_to_den(
    num_units: float,
    *,
    num_bid: float,
    den_ask: float,
    fee_rate: float,
) -> dict[str, float]:
    usd_after_num_sale = float(num_units) * float(num_bid) * (1.0 - fee_rate)
    den_units_out = (usd_after_num_sale / float(den_ask)) * (1.0 - fee_rate)
    return {
        "usd_after_num_sale": usd_after_num_sale,
        "den_units_out": den_units_out,
    }


def mark_open_positions_den(
    positions: list[OpenSleeve],
    *,
    num_bid: float,
    den_ask: float,
    fee_rate: float,
) -> tuple[float, float]:
    current_den_units = 0.0
    floating_pnl_den = 0.0
    for pos in positions:
        route = close_num_to_den(pos.num_units, num_bid=num_bid, den_ask=den_ask, fee_rate=fee_rate)
        den_out = float(route["den_units_out"])
        current_den_units += den_out
        floating_pnl_den += den_out - float(pos.den_units_in)
    return current_den_units, floating_pnl_den


def hydrate_levels(payload: dict[str, Any] | None) -> list[FrozenLevel]:
    rows = ((payload or {}).get("freeze") or {}).get("levels") or []
    levels: list[FrozenLevel] = []
    for row in rows:
        try:
            levels.append(
                FrozenLevel(
                    level_idx=int(row.get("level_idx", len(levels)) or len(levels)),
                    ratio=float(row.get("ratio", 0.0) or 0.0),
                    source=str(row.get("source", "") or ""),
                    density=float(row["density"]) if row.get("density") is not None else None,
                )
            )
        except Exception:
            continue
    return levels


def hydrate_positions(payload: dict[str, Any] | None) -> list[OpenSleeve]:
    rows = ((payload or {}).get("state") or {}).get("positions") or []
    positions: list[OpenSleeve] = []
    for row in rows:
        try:
            positions.append(
                OpenSleeve(
                    level_idx=int(row.get("level_idx", 0) or 0),
                    level_ratio=float(row.get("level_ratio", 0.0) or 0.0),
                    target_ratio=float(row.get("target_ratio", 0.0) or 0.0),
                    entry_ratio_signal=float(row.get("entry_ratio_signal", 0.0) or 0.0),
                    opened_bar_time=int(row.get("opened_bar_time", 0) or 0),
                    opened_at=str(row.get("opened_at", "") or ""),
                    den_units_in=float(row.get("den_units_in", 0.0) or 0.0),
                    num_units=float(row.get("num_units", row.get("cfg_units", 0.0)) or 0.0),
                    num_ask=float(row.get("num_ask", row.get("cfg_ask", 0.0)) or 0.0),
                    den_bid=float(row.get("den_bid", row.get("eth_bid", 0.0)) or 0.0),
                    hold_bars=int(row.get("hold_bars", 0) or 0),
                )
            )
        except Exception:
            continue
    return positions


def build_state_payload(
    *,
    pair_ctx: dict[str, str],
    args: argparse.Namespace,
    freeze_meta: dict[str, Any],
    levels: list[FrozenLevel],
    cycle: int,
    parked_den_units: float,
    initial_den_units: float,
    per_level_den_units: float,
    positions: list[OpenSleeve],
    total_opens: int,
    total_closes: int,
    max_open_total: int,
    wins: int,
    losses: int,
    realized_pnl_den: float,
    realized_pnl_usd_mark: float,
    last_ratio: float | None,
    last_bar_time: int,
    quotes: dict[str, dict[str, float]],
    fee_rate: float,
    runner_status: dict[str, Any],
) -> dict[str, Any]:
    num_product = pair_ctx["num_product"]
    den_product = pair_ctx["den_product"]
    current_den_units, floating_pnl_den = mark_open_positions_den(
        positions,
        num_bid=quotes[num_product]["bid"],
        den_ask=quotes[den_product]["ask"],
        fee_rate=fee_rate,
    )
    total_equity_den = parked_den_units + current_den_units
    total_equity_usd = total_equity_den * quotes[den_product]["mid"]
    metadata = {
        "venue": "coinbase_advanced",
        "pair": pair_ctx["pair_label"],
        "numerator_product": num_product,
        "denominator_product": den_product,
        "train_days": int(args.train_days),
        "profit_threshold": float(args.profit_threshold),
        "max_levels": int(args.max_levels),
        "max_hold_bars": int(args.max_hold_bars),
        "fee_bps_per_leg": float(args.fee_bps_per_leg),
        "starting_cash_usd": float(args.starting_cash_usd),
        "lookback_minutes": int(args.lookback_minutes),
        "shadow_only": True,
        "strategy_kind": "synthetic_ratio_lattice",
    }
    return {
        "updated_at": utc_now_iso(),
        "pair": pair_ctx["pair_label"],
        "mode": "synthetic_ratio_lattice_shadow",
        "metadata": metadata,
        "config": {
            "train_days": int(args.train_days),
            "profit_threshold": float(args.profit_threshold),
            "max_levels": int(args.max_levels),
            "max_hold_bars": int(args.max_hold_bars),
            "fee_bps_per_leg": float(args.fee_bps_per_leg),
            "starting_cash_usd": float(args.starting_cash_usd),
            "starting_den_units": float(args.starting_den_units) if args.starting_den_units is not None else None,
            "lookback_minutes": int(args.lookback_minutes),
            "poll_seconds": int(args.poll_seconds),
        },
        "freeze": {
            **freeze_meta,
            "levels": [asdict(level) for level in levels],
        },
        "runner": dict(runner_status),
        "account": {
            "parked_den_units": parked_den_units,
            "initial_den_units": initial_den_units,
            "per_level_den_units": per_level_den_units,
            "floating_den_units_mark": current_den_units,
            "floating_pnl_den_mark": floating_pnl_den,
            "total_equity_den_mark": total_equity_den,
            "total_equity_usd_mark": total_equity_usd,
        },
        "market": {
            "last_ratio": last_ratio,
            "last_bar_time": int(last_bar_time),
            "numerator_bid": quotes[num_product]["bid"],
            "numerator_ask": quotes[num_product]["ask"],
            "denominator_bid": quotes[den_product]["bid"],
            "denominator_ask": quotes[den_product]["ask"],
            "denominator_mid": quotes[den_product]["mid"],
        },
        "stats": {
            "total_opens": int(total_opens),
            "total_closes": int(total_closes),
            "wins": int(wins),
            "losses": int(losses),
            "max_open_total": int(max_open_total),
            "realized_pnl_den": realized_pnl_den,
            "realized_pnl_usd_mark": realized_pnl_usd_mark,
        },
        "positions": [asdict(pos) for pos in positions],
        "state": {
            "positions": [asdict(pos) for pos in positions],
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synthetic ratio sleeve shadow runner.")
    parser.add_argument("--pair", default=DEFAULT_PAIR)
    parser.add_argument("--starting-cash-usd", type=float, default=25.0)
    parser.add_argument("--starting-den-units", type=float, default=None)
    parser.add_argument("--train-days", type=int, default=DEFAULT_TRAIN_DAYS)
    parser.add_argument("--profit-threshold", type=float, default=DEFAULT_PROFIT_THRESHOLD)
    parser.add_argument("--max-levels", type=int, default=DEFAULT_MAX_LEVELS)
    parser.add_argument("--max-hold-bars", type=int, default=DEFAULT_MAX_HOLD_BARS)
    parser.add_argument("--fee-bps-per-leg", type=float, default=DEFAULT_FEE_BPS_PER_LEG)
    parser.add_argument("--lookback-minutes", type=int, default=DEFAULT_LOOKBACK_MINUTES)
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--state-path", default="")
    parser.add_argument("--event-path", default="")
    parser.add_argument("--heartbeat-path", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-cycles", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pair_ctx = parse_pair_spec(args.pair)
    default_state_path, default_event_path, default_heartbeat_path = default_artifact_paths(pair_ctx)
    state_path = Path(args.state_path) if args.state_path else default_state_path
    event_path = Path(args.event_path) if args.event_path else default_event_path
    heartbeat_path = Path(args.heartbeat_path) if args.heartbeat_path else default_heartbeat_path
    fee_rate = fee_rate_from_bps(args.fee_bps_per_leg)

    print("=" * 72)
    print("COINBASE SYNTHETIC RATIO LATTICE SHADOW")
    print(f"Pair: {pair_ctx['pair_label']}")
    print(f"Frozen shape: train={args.train_days}d, thr={args.profit_threshold:.3f}, levels={args.max_levels}")
    print(
        f"Route: {pair_ctx['denominator']} -> USD -> {pair_ctx['numerator']} on open, "
        f"{pair_ctx['numerator']} -> USD -> {pair_ctx['denominator']} on close"
    )
    print("=" * 72)

    client = CoinbaseAdvancedClient()
    payload = load_json(state_path)
    levels = hydrate_levels(payload)
    freeze_meta = ((payload or {}).get("freeze") or {}).copy()

    if not levels:
        levels, freeze_meta = freeze_levels(
            client,
            pair_ctx=pair_ctx,
            train_days=args.train_days,
            max_levels=args.max_levels,
        )

    quotes = fetch_quotes(client, pair_ctx=pair_ctx)
    den_product = pair_ctx["den_product"]
    num_product = pair_ctx["num_product"]
    if args.starting_den_units is not None:
        initial_den_units = float(args.starting_den_units)
    else:
        initial_den_units = denominator_units_from_usd(args.starting_cash_usd, quotes[den_product]["ask"], fee_rate)
    per_level_den_units = initial_den_units / max(1, int(args.max_levels))

    positions = hydrate_positions(payload)
    parked_den_units = float((((payload or {}).get("account") or {}).get("parked_den_units", initial_den_units)) or initial_den_units)
    stats = (payload or {}).get("stats") or {}
    market = (payload or {}).get("market") or {}
    runner = (payload or {}).get("runner") or {}
    cycle = int(runner.get("cycle", 0) or 0)
    total_opens = int(stats.get("total_opens", 0) or 0)
    total_closes = int(stats.get("total_closes", 0) or 0)
    max_open_total = int(stats.get("max_open_total", len(positions)) or len(positions))
    wins = int(stats.get("wins", 0) or 0)
    losses = int(stats.get("losses", 0) or 0)
    realized_pnl_den = float(stats.get("realized_pnl_den", 0.0) or 0.0)
    last_bar_time = int(market.get("last_bar_time", 0) or 0)
    runner_status = {
        "pid": os.getpid(),
        "script": Path(__file__).name,
        "started_at": str(runner.get("started_at") or utc_now_iso()),
        "poll_seconds": int(args.poll_seconds),
        "heartbeat_at": str(runner.get("heartbeat_at") or ""),
        "last_successful_run_at": str(runner.get("last_successful_run_at") or ""),
        "consecutive_exceptions": int(runner.get("consecutive_exceptions", 0) or 0),
        "last_exception_at": str(runner.get("last_exception_at") or ""),
        "last_exception_type": str(runner.get("last_exception_type") or ""),
        "last_exception_message": str(runner.get("last_exception_message") or ""),
        "cycle": cycle,
    }

    print(f"Frozen levels: {[round(level.ratio, 10) for level in levels]}")
    print(f"Initial parked {pair_ctx['denominator']}: {initial_den_units:.8f}")
    print(f"Per-level sleeve {pair_ctx['denominator']}: {per_level_den_units:.8f}")

    if args.dry_run:
        ratio_bar = fetch_recent_ratio_bar(client, pair_ctx=pair_ctx, lookback_minutes=args.lookback_minutes)
        if ratio_bar is not None:
            current_ratio = float(ratio_bar["ratio"])
            print(f"Current ratio: {current_ratio:.10f}")
            print(f"{pair_ctx['numerator']} bid/ask: {quotes[num_product]['bid']:.6f} / {quotes[num_product]['ask']:.6f}")
            print(f"{pair_ctx['denominator']} bid/ask: {quotes[den_product]['bid']:.6f} / {quotes[den_product]['ask']:.6f}")
        print("Dry run only. No state or event files written.")
        return 0

    append_jsonl(
        event_path,
        {
            "ts_utc": utc_now_iso(),
            "action": "runner_start",
            "pair": pair_ctx["pair_label"],
            "mode": "synthetic_ratio_lattice_shadow",
            "state_path": str(state_path),
            "starting_cash_usd": float(args.starting_cash_usd),
            "initial_den_units": initial_den_units,
            "per_level_den_units": per_level_den_units,
            "profit_threshold": float(args.profit_threshold),
            "max_levels": int(args.max_levels),
        },
    )
    append_jsonl(
        event_path,
        {
            "ts_utc": utc_now_iso(),
            "action": "freeze_levels",
            "pair": pair_ctx["pair_label"],
            "train_days": int(args.train_days),
            "levels": [asdict(level) for level in levels],
        },
    )

    try:
        while True:
            if args.max_cycles > 0 and cycle >= int(args.max_cycles):
                break
            cycle += 1

            try:
                ratio_bar = fetch_recent_ratio_bar(client, pair_ctx=pair_ctx, lookback_minutes=args.lookback_minutes)
                quotes = fetch_quotes(client, pair_ctx=pair_ctx)
                if ratio_bar is None:
                    time.sleep(args.poll_seconds)
                    continue

                current_ratio = float(ratio_bar["ratio"])
                current_bar_time = int(ratio_bar["t"])

                if current_bar_time > last_bar_time:
                    next_positions: list[OpenSleeve] = []
                    for pos in positions:
                        hold_bars = int(pos.hold_bars) + 1
                        exit_reason = ""
                        if current_ratio >= float(pos.target_ratio):
                            exit_reason = "target_reversion"
                        elif hold_bars >= int(args.max_hold_bars):
                            exit_reason = "timeout"

                        if exit_reason:
                            route = close_num_to_den(
                                pos.num_units,
                                num_bid=quotes[num_product]["bid"],
                                den_ask=quotes[den_product]["ask"],
                                fee_rate=fee_rate,
                            )
                            den_units_out = float(route["den_units_out"])
                            pnl_den = den_units_out - float(pos.den_units_in)
                            parked_den_units += den_units_out
                            total_closes += 1
                            realized_pnl_den += pnl_den
                            if pnl_den >= 0:
                                wins += 1
                            else:
                                losses += 1
                            append_jsonl(
                                event_path,
                                {
                                    "ts_utc": utc_now_iso(),
                                    "action": "close_sleeve",
                                    "pair": pair_ctx["pair_label"],
                                    "level_idx": int(pos.level_idx),
                                    "level_ratio": float(pos.level_ratio),
                                    "target_ratio": float(pos.target_ratio),
                                    "entry_ratio_signal": float(pos.entry_ratio_signal),
                                    "exit_ratio_signal": current_ratio,
                                    "opened_bar_time": int(pos.opened_bar_time),
                                    "closed_bar_time": current_bar_time,
                                    "hold_bars": hold_bars,
                                    "exit_reason": exit_reason,
                                    "num_units": float(pos.num_units),
                                    "den_units_in": float(pos.den_units_in),
                                    "den_units_out": den_units_out,
                                    "pnl_den": pnl_den,
                                    "pnl_usd_mark": pnl_den * quotes[den_product]["mid"],
                                    "numerator_bid": quotes[num_product]["bid"],
                                    "denominator_ask": quotes[den_product]["ask"],
                                },
                            )
                        else:
                            pos.hold_bars = hold_bars
                            next_positions.append(pos)

                    positions = next_positions
                    occupied = {int(pos.level_idx) for pos in positions}
                    for level in levels:
                        if int(level.level_idx) in occupied:
                            continue
                        if current_ratio > float(level.ratio):
                            continue
                        if parked_den_units + 1e-12 < per_level_den_units:
                            break
                        route = open_num_from_den(
                            per_level_den_units,
                            num_ask=quotes[num_product]["ask"],
                            den_bid=quotes[den_product]["bid"],
                            fee_rate=fee_rate,
                        )
                        num_units = float(route["num_units"])
                        if num_units <= 0:
                            continue
                        parked_den_units -= per_level_den_units
                        opened = OpenSleeve(
                            level_idx=int(level.level_idx),
                            level_ratio=float(level.ratio),
                            target_ratio=float(level.ratio) * float(args.profit_threshold),
                            entry_ratio_signal=current_ratio,
                            opened_bar_time=current_bar_time,
                            opened_at=utc_now_iso(),
                            den_units_in=per_level_den_units,
                            num_units=num_units,
                            num_ask=quotes[num_product]["ask"],
                            den_bid=quotes[den_product]["bid"],
                            hold_bars=0,
                        )
                        positions.append(opened)
                        occupied.add(int(level.level_idx))
                        total_opens += 1
                        max_open_total = max(max_open_total, len(positions))
                        append_jsonl(
                            event_path,
                            {
                                "ts_utc": opened.opened_at,
                                "action": "open_sleeve",
                                "pair": pair_ctx["pair_label"],
                                "level_idx": int(opened.level_idx),
                                "level_ratio": float(opened.level_ratio),
                                "target_ratio": float(opened.target_ratio),
                                "entry_ratio_signal": current_ratio,
                                "opened_bar_time": current_bar_time,
                                "den_units_in": float(opened.den_units_in),
                                "num_units": float(opened.num_units),
                                "numerator_ask": float(opened.num_ask),
                                "denominator_bid": float(opened.den_bid),
                                "exec_ratio_open": float(opened.num_ask) / float(opened.den_bid),
                            },
                        )

                    last_bar_time = current_bar_time

                realized_pnl_usd_mark = realized_pnl_den * quotes[den_product]["mid"]
                runner_status["heartbeat_at"] = utc_now_iso()
                runner_status["last_successful_run_at"] = str(runner_status["heartbeat_at"])
                runner_status["consecutive_exceptions"] = 0
                runner_status["last_exception_at"] = ""
                runner_status["last_exception_type"] = ""
                runner_status["last_exception_message"] = ""
                runner_status["cycle"] = cycle
                payload = build_state_payload(
                    pair_ctx=pair_ctx,
                    args=args,
                    freeze_meta=freeze_meta,
                    levels=levels,
                    cycle=cycle,
                    parked_den_units=parked_den_units,
                    initial_den_units=initial_den_units,
                    per_level_den_units=per_level_den_units,
                    positions=positions,
                    total_opens=total_opens,
                    total_closes=total_closes,
                    max_open_total=max_open_total,
                    wins=wins,
                    losses=losses,
                    realized_pnl_den=realized_pnl_den,
                    realized_pnl_usd_mark=realized_pnl_usd_mark,
                    last_ratio=current_ratio,
                    last_bar_time=last_bar_time,
                    quotes=quotes,
                    fee_rate=fee_rate,
                    runner_status=runner_status,
                )
                atomic_write_json(state_path, payload)
                atomic_write_json(
                    heartbeat_path,
                    {
                        "updated_at": utc_now_iso(),
                        "pair": pair_ctx["pair_label"],
                        "cycle": cycle,
                        "pid": os.getpid(),
                        "positions_open": len(positions),
                        "realized_pnl_den": realized_pnl_den,
                        "realized_pnl_usd_mark": realized_pnl_usd_mark,
                    },
                )
                print(
                    f"[{cycle}] ratio={current_ratio:.10f} "
                    f"open={len(positions)} parked_{pair_ctx['denominator'].lower()}={parked_den_units:.8f} "
                    f"realized_{pair_ctx['denominator'].lower()}={realized_pnl_den:+.8f}"
                )
            except Exception as exc:
                runner_status["heartbeat_at"] = utc_now_iso()
                runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
                runner_status["last_exception_at"] = str(runner_status["heartbeat_at"])
                runner_status["last_exception_type"] = type(exc).__name__
                runner_status["last_exception_message"] = str(exc)
                runner_status["cycle"] = cycle
                append_jsonl(
                    event_path,
                    {
                        "ts_utc": utc_now_iso(),
                        "action": "runner_error",
                        "pair": pair_ctx["pair_label"],
                        "cycle": cycle,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                )
                safe_quotes = quotes if isinstance(quotes, dict) and den_product in quotes and num_product in quotes else {
                    num_product: {"bid": 0.0, "ask": 0.0, "mid": 0.0},
                    den_product: {"bid": 0.0, "ask": 0.0, "mid": 0.0},
                }
                payload = build_state_payload(
                    pair_ctx=pair_ctx,
                    args=args,
                    freeze_meta=freeze_meta,
                    levels=levels,
                    cycle=cycle,
                    parked_den_units=parked_den_units,
                    initial_den_units=initial_den_units,
                    per_level_den_units=per_level_den_units,
                    positions=positions,
                    total_opens=total_opens,
                    total_closes=total_closes,
                    max_open_total=max_open_total,
                    wins=wins,
                    losses=losses,
                    realized_pnl_den=realized_pnl_den,
                    realized_pnl_usd_mark=realized_pnl_den * float(safe_quotes[den_product]["mid"]),
                    last_ratio=market.get("last_ratio"),
                    last_bar_time=last_bar_time,
                    quotes=safe_quotes,
                    fee_rate=fee_rate,
                    runner_status=runner_status,
                )
                atomic_write_json(state_path, payload)
                print(f"[{cycle}] ERROR: {exc}")

            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        print("Stopped by user.")
    finally:
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": "runner_stop",
                "pair": pair_ctx["pair_label"],
                "cycle": cycle,
                "realized_pnl_den": realized_pnl_den,
                "realized_pnl_usd_mark": realized_pnl_den * quotes[den_product]["mid"],
                "positions_open": len(positions),
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
