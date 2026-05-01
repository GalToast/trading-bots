#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_CACHE_PATH = REPORTS / "cache" / "coinbase_spot_pulse_candles.json"
DEFAULT_PULSE_PATH = REPORTS / "coinbase_spot_pulse_board.json"
DEFAULT_JSON_PATH = REPORTS / "coinbase_spot_momentum_bruteforce.json"
DEFAULT_CSV_PATH = REPORTS / "coinbase_spot_momentum_bruteforce.csv"
DEFAULT_MD_PATH = REPORTS / "coinbase_spot_momentum_bruteforce.md"


@dataclass(frozen=True)
class Candle:
    start: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class EntryConfig:
    mode: str
    lookback: int
    min_ret_bps: float
    min_close_location: float
    min_volume_mult: float


@dataclass(frozen=True)
class ExitConfig:
    target_pct: float
    stop_pct: float
    max_hold_bars: int
    trail_giveback_pct: float


@dataclass(frozen=True)
class Trade:
    product_id: str
    entry_ts: int
    exit_ts: int
    net_pct: float
    gross_pct: float
    bars_held: int
    outcome: str


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_candles(rows: list[Any]) -> list[Candle]:
    candles: list[Candle] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        candle = Candle(
            start=int(to_float(row.get("start"))),
            open=to_float(row.get("open")),
            high=to_float(row.get("high")),
            low=to_float(row.get("low")),
            close=to_float(row.get("close")),
            volume=to_float(row.get("volume")),
        )
        if candle.open > 0.0 and candle.high >= candle.low and candle.close > 0.0:
            candles.append(candle)
    candles.sort(key=lambda candle: candle.start)
    dedup: dict[int, Candle] = {candle.start: candle for candle in candles}
    return [dedup[key] for key in sorted(dedup)]


def pulse_meta(path: Path) -> dict[str, dict[str, Any]]:
    payload = load_json(path)
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    return {str(row.get("product_id") or ""): row for row in rows if isinstance(row, dict)}


def load_candle_map(
    *,
    cache_path: Path,
    pulse_path: Path,
    hours: int,
    granularity: str,
    quote_currencies: set[str],
    include_non_usd_quotes: bool,
    min_candles: int,
    max_products: int,
) -> tuple[dict[str, list[Candle]], dict[str, dict[str, Any]]]:
    cache = load_json(cache_path)
    entries = cache.get("entries") if isinstance(cache.get("entries"), dict) else {}
    meta = pulse_meta(pulse_path)
    candle_map: dict[str, list[Candle]] = {}
    selected_meta: dict[str, dict[str, Any]] = {}
    suffix = f"|{granularity.upper()}|{int(hours)}H"
    for key, entry in entries.items():
        if not str(key).upper().endswith(suffix):
            continue
        if not isinstance(entry, dict):
            continue
        product_id = str(entry.get("product_id") or str(key).split("|", 1)[0]).upper()
        row = meta.get(product_id, {})
        quote = str(row.get("quote_currency") or product_id.rsplit("-", 1)[-1]).upper()
        if not include_non_usd_quotes and quote not in quote_currencies:
            continue
        if row and not bool(row.get("live_tradable", False)):
            continue
        candles = parse_candles(entry.get("candles") if isinstance(entry.get("candles"), list) else [])
        if len(candles) < min_candles:
            continue
        candle_map[product_id] = candles
        selected_meta[product_id] = row
    ranked = sorted(
        candle_map,
        key=lambda product_id: (
            to_float(selected_meta.get(product_id, {}).get("pulse_score")),
            to_float(selected_meta.get(product_id, {}).get("quote_volume_native")),
        ),
        reverse=True,
    )
    if max_products > 0:
        ranked = ranked[:max_products]
    return {product_id: candle_map[product_id] for product_id in ranked}, {
        product_id: selected_meta.get(product_id, {}) for product_id in ranked
    }


def close_location(candle: Candle) -> float:
    if candle.high <= candle.low:
        return 0.5
    return max(0.0, min(1.0, (candle.close - candle.low) / (candle.high - candle.low)))


def median_volume(candles: list[Candle], start: int, end: int) -> float:
    vols = [candle.volume for candle in candles[start:end] if candle.volume > 0.0]
    return statistics.median(vols) if vols else 0.0


def volume_ok(candles: list[Candle], idx: int, config: EntryConfig) -> bool:
    if config.min_volume_mult <= 0.0:
        return True
    base_vol = median_volume(candles, max(0, idx - 20), idx)
    return base_vol > 0.0 and candles[idx].volume >= base_vol * config.min_volume_mult


def entry_signal(candles: list[Candle], idx: int, config: EntryConfig) -> bool:
    prior_idx = idx - config.lookback
    if prior_idx < 0:
        return False
    prior = candles[prior_idx]
    cur = candles[idx]
    if prior.close <= 0.0 or cur.low <= 0.0:
        return False
    if close_location(cur) < config.min_close_location or not volume_ok(candles, idx, config):
        return False

    if config.mode == "impulse":
        ret_bps = ((cur.close / prior.close) - 1.0) * 10_000.0
        return ret_bps >= config.min_ret_bps

    if config.mode == "dump_reclaim":
        flush_bps = ((cur.low / prior.close) - 1.0) * 10_000.0
        reclaim_bps = ((cur.close / cur.low) - 1.0) * 10_000.0
        return flush_bps <= -config.min_ret_bps and reclaim_bps >= config.min_ret_bps

    if config.mode == "post_burst_pullback":
        if idx < config.lookback + 1:
            return False
        burst_anchor = candles[idx - config.lookback - 1]
        burst_high = candles[idx - 1]
        if burst_anchor.close <= 0.0 or burst_high.close <= 0.0:
            return False
        burst_bps = ((burst_high.close / burst_anchor.close) - 1.0) * 10_000.0
        pullback_bps = ((cur.low / burst_high.close) - 1.0) * 10_000.0
        held_half_burst = cur.close >= burst_anchor.close * (1.0 + (burst_bps / 20_000.0))
        return burst_bps >= config.min_ret_bps and -150.0 <= pullback_bps <= -25.0 and cur.close > cur.open and held_half_burst

    if config.mode == "compression_expansion":
        if idx < config.lookback:
            return False
        recent = candles[idx - config.lookback : idx]
        ranges = [((bar.high / bar.low) - 1.0) * 10_000.0 for bar in recent if bar.low > 0.0]
        if not ranges:
            return False
        median_range_bps = statistics.median(ranges)
        cur_range_bps = ((cur.high / cur.low) - 1.0) * 10_000.0
        body_bps = ((cur.close / cur.open) - 1.0) * 10_000.0 if cur.open > 0.0 else 0.0
        return median_range_bps <= 35.0 and cur_range_bps >= median_range_bps * 2.0 and body_bps >= config.min_ret_bps

    return False


def simulate_trade(
    product_id: str,
    candles: list[Candle],
    entry_idx: int,
    exit_config: ExitConfig,
    *,
    fee_bps_per_side: float,
    spread_bps: float,
) -> Trade | None:
    if entry_idx + 1 >= len(candles):
        return None
    entry_candle = candles[entry_idx]
    entry = entry_candle.close
    target = entry * (1.0 + exit_config.target_pct)
    stop = entry * (1.0 - exit_config.stop_pct)
    highest = entry
    fee_and_spread_pct = ((fee_bps_per_side * 2.0) + max(0.0, spread_bps)) / 10_000.0
    last_idx = min(len(candles) - 1, entry_idx + exit_config.max_hold_bars)
    exit_idx = last_idx
    exit_price = candles[last_idx].close
    outcome = "time_exit"
    for idx in range(entry_idx + 1, last_idx + 1):
        candle = candles[idx]
        highest = max(highest, candle.high)
        target_hit = candle.high >= target
        stop_hit = candle.low <= stop
        trail_hit = False
        trail_price = 0.0
        if exit_config.trail_giveback_pct > 0.0 and highest >= target:
            trail_price = highest * (1.0 - exit_config.trail_giveback_pct)
            trail_hit = candle.low <= trail_price
        if stop_hit and (target_hit or trail_hit):
            exit_idx = idx
            exit_price = stop
            outcome = "stop_first_ambiguous"
            break
        if stop_hit:
            exit_idx = idx
            exit_price = stop
            outcome = "stop"
            break
        if trail_hit:
            exit_idx = idx
            exit_price = trail_price
            outcome = "trail"
            break
        if target_hit and exit_config.trail_giveback_pct <= 0.0:
            exit_idx = idx
            exit_price = target
            outcome = "target"
            break
    gross_pct = (exit_price / entry) - 1.0
    net_pct = gross_pct - fee_and_spread_pct
    return Trade(
        product_id=product_id,
        entry_ts=entry_candle.start,
        exit_ts=candles[exit_idx].start,
        net_pct=net_pct,
        gross_pct=gross_pct,
        bars_held=exit_idx - entry_idx,
        outcome=outcome,
    )


def entry_configs() -> list[EntryConfig]:
    configs = [
        EntryConfig(mode="impulse", lookback=lookback, min_ret_bps=ret, min_close_location=loc, min_volume_mult=vol)
        for lookback in (1, 2, 3, 5)
        for ret in (25.0, 50.0, 100.0, 200.0)
        for loc in (0.6, 0.8)
        for vol in (0.0, 2.0)
    ]
    configs.extend(
        EntryConfig(mode="dump_reclaim", lookback=lookback, min_ret_bps=ret, min_close_location=loc, min_volume_mult=vol)
        for lookback in (1, 2, 3)
        for ret in (50.0, 100.0, 200.0, 300.0)
        for loc in (0.55, 0.7)
        for vol in (0.0, 2.0)
    )
    configs.extend(
        EntryConfig(mode="post_burst_pullback", lookback=lookback, min_ret_bps=ret, min_close_location=loc, min_volume_mult=vol)
        for lookback in (1, 2, 3, 5)
        for ret in (100.0, 200.0, 300.0, 500.0)
        for loc in (0.55, 0.7)
        for vol in (0.0, 1.5)
    )
    configs.extend(
        EntryConfig(mode="compression_expansion", lookback=lookback, min_ret_bps=ret, min_close_location=loc, min_volume_mult=vol)
        for lookback in (10, 20)
        for ret in (25.0, 50.0, 100.0)
        for loc in (0.7, 0.85)
        for vol in (0.0, 2.0)
    )
    return configs


def exit_configs() -> list[ExitConfig]:
    return [
        ExitConfig(target_pct=target, stop_pct=stop, max_hold_bars=hold, trail_giveback_pct=trail)
        for target in (0.03, 0.05, 0.08, 0.12)
        for stop in (0.01, 0.02, 0.03)
        for hold in (2, 5, 10, 15)
        for trail in (0.0, 0.01)
        if stop < target
    ]


def summarize(config_key: dict[str, Any], trades: list[Trade], product_count: int, hours: int) -> dict[str, Any]:
    wins = [trade for trade in trades if trade.net_pct > 0.0]
    product_net: dict[str, float] = {}
    product_trades: dict[str, int] = {}
    for trade in trades:
        product_net[trade.product_id] = product_net.get(trade.product_id, 0.0) + trade.net_pct * 100.0
        product_trades[trade.product_id] = product_trades.get(trade.product_id, 0) + 1
    ranked_products = sorted(product_net.items(), key=lambda item: item[1], reverse=True)
    cumulative_net_pct = sum(trade.net_pct for trade in trades) * 100.0
    avg_net_pct = statistics.fmean(trade.net_pct for trade in trades) * 100.0 if trades else 0.0
    worst_net_pct = min((trade.net_pct for trade in trades), default=0.0) * 100.0
    target_hits = sum(1 for trade in trades if trade.outcome in {"target", "trail"})
    positive_products = sum(1 for net in product_net.values() if net > 0.0)
    trades_per_hour = len(trades) / max(0.001, float(hours))
    return {
        **config_key,
        "signals": len(trades),
        "products_tested": product_count,
        "positive_products": positive_products,
        "win_rate_pct": round((len(wins) / len(trades)) * 100.0, 4) if trades else 0.0,
        "target_or_trail_rate_pct": round((target_hits / len(trades)) * 100.0, 4) if trades else 0.0,
        "avg_net_pct": round(avg_net_pct, 6),
        "median_net_pct": round(statistics.median(trade.net_pct for trade in trades) * 100.0, 6) if trades else 0.0,
        "worst_net_pct": round(worst_net_pct, 6),
        "cumulative_net_pct": round(cumulative_net_pct, 6),
        "net_pct_per_hour": round(cumulative_net_pct / max(0.001, float(hours)), 6),
        "trades_per_hour": round(trades_per_hour, 6),
        "avg_bars_held": round(statistics.fmean(trade.bars_held for trade in trades), 4) if trades else 0.0,
        "top_products": ", ".join(f"{product}:{net:.2f}" for product, net in ranked_products[:5]),
    }


def run_sweep(args: argparse.Namespace) -> dict[str, Any]:
    quote_currencies = {part.strip().upper() for part in str(args.quote_currencies).split(",") if part.strip()}
    candle_map, meta = load_candle_map(
        cache_path=Path(args.cache_path),
        pulse_path=Path(args.pulse_path),
        hours=int(args.hours),
        granularity=str(args.granularity),
        quote_currencies=quote_currencies,
        include_non_usd_quotes=bool(args.include_non_usd_quotes),
        min_candles=int(args.min_candles),
        max_products=int(args.max_products),
    )
    product_signals: dict[EntryConfig, dict[str, list[int]]] = {}
    for entry_config in entry_configs():
        per_product: dict[str, list[int]] = {}
        for product_id, candles in candle_map.items():
            indexes = [idx for idx in range(len(candles) - 1) if entry_signal(candles, idx, entry_config)]
            if indexes:
                per_product[product_id] = indexes
        product_signals[entry_config] = per_product

    rows: list[dict[str, Any]] = []
    sample_trades: dict[str, list[dict[str, Any]]] = {}
    for entry_config, signals_by_product in product_signals.items():
        if not signals_by_product:
            continue
        for exit_config in exit_configs():
            trades: list[Trade] = []
            for product_id, indexes in signals_by_product.items():
                candles = candle_map[product_id]
                spread_bps = min(max(0.0, to_float(meta.get(product_id, {}).get("spread_bps"))), float(args.max_spread_bps))
                for idx in indexes:
                    trade = simulate_trade(
                        product_id,
                        candles,
                        idx,
                        exit_config,
                        fee_bps_per_side=float(args.fee_bps_per_side),
                        spread_bps=spread_bps,
                    )
                    if trade:
                        trades.append(trade)
            if len(trades) < int(args.min_signals):
                continue
            key = {
                "mode": entry_config.mode,
                "lookback": entry_config.lookback,
                "min_ret_bps": entry_config.min_ret_bps,
                "min_close_location": entry_config.min_close_location,
                "min_volume_mult": entry_config.min_volume_mult,
                "target_pct": exit_config.target_pct * 100.0,
                "stop_pct": exit_config.stop_pct * 100.0,
                "max_hold_bars": exit_config.max_hold_bars,
                "trail_giveback_pct": exit_config.trail_giveback_pct * 100.0,
            }
            row = summarize(key, trades, len(candle_map), int(args.hours))
            rows.append(row)
            config_id = config_signature(row)
            sample_trades[config_id] = [
                {
                    "product_id": trade.product_id,
                    "entry_ts": trade.entry_ts,
                    "exit_ts": trade.exit_ts,
                    "net_pct": round(trade.net_pct * 100.0, 6),
                    "gross_pct": round(trade.gross_pct * 100.0, 6),
                    "bars_held": trade.bars_held,
                    "outcome": trade.outcome,
                }
                for trade in sorted(trades, key=lambda trade: trade.net_pct, reverse=True)[:10]
            ]
    rows.sort(
        key=lambda row: (
            row["net_pct_per_hour"],
            row["avg_net_pct"],
            row["positive_products"],
            row["signals"],
        ),
        reverse=True,
    )
    return {
        "mode": "coinbase_spot_momentum_bruteforce",
        "parameters": {
            "hours": int(args.hours),
            "granularity": str(args.granularity),
            "fee_bps_per_side": float(args.fee_bps_per_side),
            "quote_currencies": sorted(quote_currencies),
            "include_non_usd_quotes": bool(args.include_non_usd_quotes),
            "products_tested": len(candle_map),
            "min_signals": int(args.min_signals),
        },
        "ranking_note": "Historical candle replay only. Fees and current spread are charged, but historical queue depth and spread variation are not known.",
        "rows": rows,
        "top_sample_trades": {config_signature(row): sample_trades.get(config_signature(row), []) for row in rows[:10]},
    }


def config_signature(row: dict[str, Any]) -> str:
    return (
        f"{row['mode']}_lb{row['lookback']}_ret{row['min_ret_bps']}_loc{row['min_close_location']}"
        f"_vol{row['min_volume_mult']}_tp{row['target_pct']}_sl{row['stop_pct']}"
        f"_hold{row['max_hold_bars']}_trail{row['trail_giveback_pct']}"
    )


def write_outputs(payload: dict[str, Any], *, json_path: Path, csv_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    columns = [
        "lookback",
        "mode",
        "min_ret_bps",
        "min_close_location",
        "min_volume_mult",
        "target_pct",
        "stop_pct",
        "max_hold_bars",
        "trail_giveback_pct",
        "signals",
        "positive_products",
        "win_rate_pct",
        "target_or_trail_rate_pct",
        "avg_net_pct",
        "median_net_pct",
        "worst_net_pct",
        "cumulative_net_pct",
        "net_pct_per_hour",
        "trades_per_hour",
        "avg_bars_held",
        "top_products",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload["rows"]:
            writer.writerow({column: row.get(column, "") for column in columns})
    lines = [
        "# Coinbase Spot Momentum Brute Force",
        "",
        f"- Products tested: `{payload['parameters']['products_tested']}`",
        f"- Window: `{payload['parameters']['hours']}h` `{payload['parameters']['granularity']}` candles",
        f"- Fees: `{payload['parameters']['fee_bps_per_side']}` bps per side plus current spread proxy",
        f"- Quote currencies: `{payload['parameters']['quote_currencies']}`, include non-USD quotes: `{payload['parameters']['include_non_usd_quotes']}`",
        f"- Caveat: {payload['ranking_note']}",
        "",
        "| Rank | Lookback | Entry bps | Loc | Vol x | Target % | Stop % | Hold | Trail % | Signals | Win % | Avg Net % | Net %/h | Positive Products | Top Products |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for rank, row in enumerate(payload["rows"][:25], start=1):
        lines.append(
            "| {rank} | {mode}:{lookback} | {min_ret_bps:.0f} | {min_close_location:.2f} | {min_volume_mult:.1f} | {target_pct:.2f} | {stop_pct:.2f} | {max_hold_bars} | {trail_giveback_pct:.2f} | {signals} | {win_rate_pct:.1f} | {avg_net_pct:.3f} | {net_pct_per_hour:.3f} | {positive_products} | {top_products} |".format(
                rank=rank,
                **row,
            )
        )
    if not payload["rows"]:
        lines.append("|  |  |  |  |  |  |  |  |  | 0 | 0.0 | 0.000 | 0.000 | 0 |  |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Brute-force fee-correct Coinbase spot momentum capture geometry from cached candles.")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--pulse-path", default=str(DEFAULT_PULSE_PATH))
    parser.add_argument("--hours", type=int, default=3)
    parser.add_argument("--granularity", default="ONE_MINUTE")
    parser.add_argument("--quote-currencies", default="USD,USDC")
    parser.add_argument("--include-non-usd-quotes", action="store_true")
    parser.add_argument("--max-products", type=int, default=250)
    parser.add_argument("--min-candles", type=int, default=20)
    parser.add_argument("--min-signals", type=int, default=3)
    parser.add_argument("--fee-bps-per-side", type=float, default=120.0)
    parser.add_argument("--max-spread-bps", type=float, default=100.0)
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = run_sweep(args)
    write_outputs(payload, json_path=Path(args.json_path), csv_path=Path(args.csv_path), md_path=Path(args.md_path))
    print(json.dumps({"json_path": args.json_path, "csv_path": args.csv_path, "md_path": args.md_path, "top_rows": payload["rows"][:5]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
