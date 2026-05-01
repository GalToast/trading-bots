#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
DEFAULT_OUTPUT_CSV = ROOT / "reports" / "creative_lattice_family_study.csv"
DEFAULT_OUTPUT_MD = ROOT / "reports" / "creative_lattice_family_study.md"
DEFAULT_SYMBOLS = ["SOLUSD", "XRPUSD", "ADAUSD", "LTCUSD"]
DEFAULT_STEP_MULTIPLIERS = [1.0, 1.5, 2.0]


TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
}


@dataclass(frozen=True)
class LiveLaneConfig:
    lane_name: str
    symbol: str
    timeframe: str
    step_px: float
    max_open_per_side: int


@dataclass(frozen=True)
class Variant:
    name: str
    entry_mode: str
    exit_mode: str
    primary_period: int = 20
    secondary_period: int = 50
    ribbon_extra_steps: float = 0.0


@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int
    entry_center: float


VARIANTS = [
    Variant(name="vwap20_touch", entry_mode="vwap", exit_mode="touch_center", primary_period=20),
    Variant(name="vwap50_touch", entry_mode="vwap", exit_mode="touch_center", primary_period=50),
    Variant(name="vwap20_stepback1", entry_mode="vwap", exit_mode="stepback1", primary_period=20),
    Variant(name="ema20_touch", entry_mode="ema", exit_mode="touch_center", primary_period=20),
    Variant(name="ema20_stepback1", entry_mode="ema", exit_mode="stepback1", primary_period=20),
    Variant(name="ema20_to_ema50", entry_mode="ema_to_ema", exit_mode="touch_secondary", primary_period=20, secondary_period=50),
    Variant(name="ribbon_mid_touch", entry_mode="ribbon", exit_mode="touch_center", primary_period=20, secondary_period=50, ribbon_extra_steps=0.0),
    Variant(name="ribbon_mid_stepback1", entry_mode="ribbon", exit_mode="stepback1", primary_period=20, secondary_period=50, ribbon_extra_steps=0.0),
    Variant(name="ribbon_outerplus1_touch", entry_mode="ribbon", exit_mode="touch_center", primary_period=20, secondary_period=50, ribbon_extra_steps=1.0),
    Variant(name="vwap20_to_ribbon_mid", entry_mode="vwap_to_ribbon", exit_mode="touch_secondary_mid", primary_period=20, secondary_period=50),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Creative family search for non-BTC crypto lattice anchors, entries, and exits."
    )
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--step-multipliers", nargs="*", type=float, default=DEFAULT_STEP_MULTIPLIERS)
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT_CSV))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    return parser.parse_args()


def _arg_value(args: list[str], key: str, default: str = "") -> str:
    try:
        idx = args.index(key)
    except ValueError:
        return default
    if idx + 1 >= len(args):
        return default
    return str(args[idx + 1])


def load_live_crypto_configs(symbol_filter: set[str] | None = None) -> list[LiveLaneConfig]:
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    lanes = payload.get("lanes") if isinstance(payload, dict) else payload
    if not isinstance(lanes, list):
        return []
    rows: list[LiveLaneConfig] = []
    for row in lanes:
        if not isinstance(row, dict):
            continue
        if row.get("kind") != "live_crypto":
            continue
        if not row.get("enabled"):
            continue
        args = [str(v) for v in (row.get("restart_args") or [])]
        symbol = _arg_value(args, "--symbol")
        if not symbol:
            continue
        if symbol_filter and symbol not in symbol_filter:
            continue
        timeframe = _arg_value(args, "--timeframe", "M15")
        step_px = float(_arg_value(args, "--step", "0"))
        max_open = int(float(_arg_value(args, "--max-open-per-side", "0") or 0))
        if step_px <= 0 or max_open <= 0 or timeframe not in TIMEFRAME_MAP:
            continue
        rows.append(
            LiveLaneConfig(
                lane_name=str(row.get("name") or symbol),
                symbol=symbol,
                timeframe=timeframe,
                step_px=step_px,
                max_open_per_side=max_open,
            )
        )
    return rows


def load_bars(symbol: str, timeframe_name: str, days: int) -> list[dict[str, Any]]:
    timeframe = TIMEFRAME_MAP[timeframe_name]
    bars_per_day = {"M1": 1440, "M5": 288, "M15": 96, "H1": 24}[timeframe_name]
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars_per_day * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "tick_volume": int(r[5]),
        }
        for r in rates
    ]


def compute_ema_series(bars: list[dict[str, Any]], period: int) -> list[float]:
    values: list[float] = []
    alpha = 2.0 / (period + 1.0)
    ema = 0.0
    for idx, bar in enumerate(bars):
        close = float(bar["close"])
        if idx == 0:
            ema = close
        else:
            ema = (close * alpha) + (ema * (1.0 - alpha))
        values.append(ema)
    return values


def compute_vwap_series(bars: list[dict[str, Any]], window: int) -> list[float]:
    rows: list[float] = []
    for idx in range(len(bars)):
        start = max(0, idx - window + 1)
        total_vp = 0.0
        total_v = 0.0
        for bar in bars[start : idx + 1]:
            typical = (float(bar["high"]) + float(bar["low"]) + float(bar["close"])) / 3.0
            vol = float(bar.get("tick_volume", 1) or 1)
            total_vp += typical * vol
            total_v += vol
        rows.append(total_vp / total_v if total_v > 0 else float(bars[idx]["close"]))
    return rows


def _entry_levels(
    *,
    variant: Variant,
    idx: int,
    step_px: float,
    series: dict[str, list[float]],
) -> tuple[float, float, float, float]:
    primary = float(series[f"ema_{variant.primary_period}"][idx] if variant.entry_mode in {"ema", "ema_to_ema", "ribbon"} else series[f"vwap_{variant.primary_period}"][idx])
    secondary = float(series.get(f"ema_{variant.secondary_period}", [primary])[idx] if variant.secondary_period else primary)

    if variant.entry_mode == "vwap":
        center = float(series[f"vwap_{variant.primary_period}"][idx])
        return center, center + step_px, center - step_px, center
    if variant.entry_mode == "ema":
        center = float(series[f"ema_{variant.primary_period}"][idx])
        return center, center + step_px, center - step_px, center
    if variant.entry_mode == "ema_to_ema":
        center = float(series[f"ema_{variant.primary_period}"][idx])
        return center, center + step_px, center - step_px, center
    if variant.entry_mode == "vwap_to_ribbon":
        center = float(series[f"vwap_{variant.primary_period}"][idx])
        return center, center + step_px, center - step_px, center
    if variant.entry_mode == "ribbon":
        ema_fast = float(series[f"ema_{variant.primary_period}"][idx])
        ema_slow = float(series[f"ema_{variant.secondary_period}"][idx])
        upper = max(ema_fast, ema_slow) + (variant.ribbon_extra_steps * step_px)
        lower = min(ema_fast, ema_slow) - (variant.ribbon_extra_steps * step_px)
        center = (ema_fast + ema_slow) / 2.0
        return center, upper + step_px, lower - step_px, center
    return primary, primary + step_px, primary - step_px, primary


def _exit_price(ticket: Ticket, variant: Variant, idx: int, step_px: float, series: dict[str, list[float]]) -> float:
    if variant.exit_mode == "stepback1":
        return ticket.entry_price - step_px if ticket.direction == "SELL" else ticket.entry_price + step_px
    if variant.exit_mode == "touch_secondary":
        return float(series[f"ema_{variant.secondary_period}"][idx])
    if variant.exit_mode == "touch_secondary_mid":
        ema_fast = float(series[f"ema_{variant.primary_period}"][idx])
        ema_slow = float(series[f"ema_{variant.secondary_period}"][idx])
        return (ema_fast + ema_slow) / 2.0
    return ticket.entry_center


def _exit_touched(ticket: Ticket, exit_price: float, bar: dict[str, Any]) -> bool:
    if ticket.direction == "SELL":
        return float(bar["low"]) <= exit_price
    return float(bar["high"]) >= exit_price


def simulate_variant(
    *,
    symbol: str,
    bars: list[dict[str, Any]],
    symbol_info: Any,
    variant: Variant,
    step_px: float,
    max_open_per_side: int,
) -> dict[str, Any]:
    if not bars:
        return {}
    spread_px = spread_price(symbol_info)
    ema_periods = {variant.primary_period, variant.secondary_period}
    vwap_periods = {variant.primary_period}
    series: dict[str, list[float]] = {}
    for period in ema_periods:
        if period > 0:
            series[f"ema_{period}"] = compute_ema_series(bars, period)
    for period in vwap_periods:
        if period > 0:
            series[f"vwap_{period}"] = compute_vwap_series(bars, period)

    tickets: list[Ticket] = []
    realized_net = 0.0
    realized_closes = 0
    max_open_total = 0
    max_open_buy = 0
    max_open_sell = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]
        center, first_sell, first_buy, entry_center = _entry_levels(variant=variant, idx=idx, step_px=step_px, series=series)
        if center <= 0 or step_px <= 0:
            continue

        sell_count = sum(1 for t in tickets if t.direction == "SELL")
        buy_count = sum(1 for t in tickets if t.direction == "BUY")

        sell_steps_reached = int(max(0, math.floor((float(bar["high"]) - first_sell) / step_px + 1.0 + 1e-9))) if float(bar["high"]) >= first_sell else 0
        buy_steps_reached = int(max(0, math.floor((first_buy - float(bar["low"])) / step_px + 1.0 + 1e-9))) if float(bar["low"]) <= first_buy else 0

        target_sell_count = min(sell_steps_reached, max_open_per_side)
        target_buy_count = min(buy_steps_reached, max_open_per_side)

        while sell_count < target_sell_count:
            level_idx = sell_count
            entry_price = first_sell + (level_idx * step_px)
            tickets.append(Ticket(direction="SELL", entry_price=entry_price, opened_idx=idx, entry_center=entry_center))
            sell_count += 1

        while buy_count < target_buy_count:
            level_idx = buy_count
            entry_price = first_buy - (level_idx * step_px)
            tickets.append(Ticket(direction="BUY", entry_price=entry_price, opened_idx=idx, entry_center=entry_center))
            buy_count += 1

        for ticket in list(tickets):
            if ticket.opened_idx >= idx:
                continue
            exit_price = _exit_price(ticket, variant, idx, step_px, series)
            if not _exit_touched(ticket, exit_price, bar):
                continue
            pnl = unit_pnl_usd(symbol, ticket.direction, ticket.entry_price, exit_price, spread_px)
            if pnl > 0:
                realized_net += pnl
                realized_closes += 1
                tickets.remove(ticket)

        open_buy = sum(1 for t in tickets if t.direction == "BUY")
        open_sell = sum(1 for t in tickets if t.direction == "SELL")
        max_open_buy = max(max_open_buy, open_buy)
        max_open_sell = max(max_open_sell, open_sell)
        max_open_total = max(max_open_total, len(tickets))

    last_close = float(bars[-1]["close"])
    floating_net = sum(unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px) for t in tickets)
    total_hours = (bars[-1]["time"] - bars[0]["time"]) / 3600.0 if len(bars) > 1 else 0.0
    usd_per_hour = realized_net / total_hours if total_hours > 0 else 0.0
    closes_per_hour = realized_closes / total_hours if total_hours > 0 else 0.0
    return {
        "realized_closes": realized_closes,
        "realized_net_usd": round(realized_net, 3),
        "floating_net_usd": round(floating_net, 3),
        "combined_net_usd": round(realized_net + floating_net, 3),
        "usd_per_hour": round(usd_per_hour, 4),
        "closes_per_hour": round(closes_per_hour, 4),
        "open_tickets_left": len(tickets),
        "max_open_total": max_open_total,
        "max_open_buy": max_open_buy,
        "max_open_sell": max_open_sell,
    }


def build_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Creative Lattice Family Study",
        "",
        "Study question: which creative non-BTC crypto family makes the best money when we vary anchor logic, stretch-entry logic, and profitable-only harvest logic?",
        "",
        "## Best Per Symbol",
        "",
        "| Symbol | Variant | Step Mult | Step Px | $/h | Closes/h | Realized | Floating | Open Left | Max Open |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    best_by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        current = best_by_symbol.get(str(row["symbol"]))
        if current is None or float(row["usd_per_hour"]) > float(current["usd_per_hour"]):
            best_by_symbol[str(row["symbol"])] = row
    for symbol in sorted(best_by_symbol):
        row = best_by_symbol[symbol]
        lines.append(
            f"| {row['symbol']} | {row['variant']} | {row['step_multiplier']} | {row['step_px']} | {row['usd_per_hour']} | "
            f"{row['closes_per_hour']} | {row['realized_net_usd']} | {row['floating_net_usd']} | {row['open_tickets_left']} | {row['max_open_total']} |"
        )
    lines.extend(
        [
            "",
            "## Full Ranking",
            "",
            "| Symbol | Variant | Step Mult | Step Px | $/h | Closes/h | Realized | Floating | Open Left | Max Open |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in sorted(rows, key=lambda r: (r["symbol"], -float(r["usd_per_hour"]))):
        lines.append(
            f"| {row['symbol']} | {row['variant']} | {row['step_multiplier']} | {row['step_px']} | {row['usd_per_hour']} | "
            f"{row['closes_per_hour']} | {row['realized_net_usd']} | {row['floating_net_usd']} | {row['open_tickets_left']} | {row['max_open_total']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    symbol_filter = set(args.symbols) if args.symbols else None
    lane_configs = load_live_crypto_configs(symbol_filter)
    if not lane_configs:
        print("No enabled live crypto lanes matched the requested symbols.")
        return 1

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        rows: list[dict[str, Any]] = []
        for cfg in lane_configs:
            info = mt5.symbol_info(cfg.symbol)
            if info is None:
                continue
            bars = load_bars(cfg.symbol, cfg.timeframe, args.days)
            if not bars:
                continue
            for variant in VARIANTS:
                for step_multiplier in args.step_multipliers:
                    if step_multiplier <= 0:
                        continue
                    step_px = cfg.step_px * float(step_multiplier)
                    result = simulate_variant(
                        symbol=cfg.symbol,
                        bars=bars,
                        symbol_info=info,
                        variant=variant,
                        step_px=step_px,
                        max_open_per_side=cfg.max_open_per_side,
                    )
                    if not result:
                        continue
                    result.update(
                        {
                            "lane_name": cfg.lane_name,
                            "symbol": cfg.symbol,
                            "timeframe": cfg.timeframe,
                            "variant": variant.name,
                            "step_multiplier": round(float(step_multiplier), 4),
                            "step_px": round(step_px, 8),
                        }
                    )
                    rows.append(result)
                    print(
                        f"{cfg.symbol:<7} {cfg.timeframe:<3} {variant.name:<24} x{step_multiplier:<4} "
                        f"step={step_px:<10.6f} $/h={result['usd_per_hour']:+.4f} "
                        f"closes/h={result['closes_per_hour']:.4f} realized={result['realized_net_usd']:+.2f} "
                        f"float={result['floating_net_usd']:+.2f}"
                    )

        if not rows:
            print("No study rows produced.")
            return 1

        csv_path = Path(args.output_csv)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

        md_path = Path(args.output_md)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(build_markdown(rows), encoding="utf-8")
        print(f"Saved {csv_path}")
        print(f"Saved {md_path}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
