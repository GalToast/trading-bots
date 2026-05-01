#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
DEFAULT_OUTPUT_CSV = ROOT / "reports" / "ema_anchor_lattice_study.csv"
DEFAULT_OUTPUT_MD = ROOT / "reports" / "ema_anchor_lattice_study.md"
DEFAULT_EMA_PERIODS = [10, 20, 50, 100]
DEFAULT_STEP_MULTIPLIERS = [0.5, 1.0, 1.5, 2.0]


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


@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Study EMA-anchored lattice entries for current live crypto lanes."
    )
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--ema-periods", nargs="*", type=int, default=DEFAULT_EMA_PERIODS)
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
    lane_rows = payload.get("lanes") if isinstance(payload, dict) else payload
    if not isinstance(lane_rows, list):
        return []
    rows: list[LiveLaneConfig] = []
    for row in lane_rows:
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
    bars_per_day = {
        "M1": 1440,
        "M5": 288,
        "M15": 96,
        "H1": 24,
    }[timeframe_name]
    count = bars_per_day * days
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
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


def compute_ema(bars: list[dict[str, Any]], period: int) -> list[float]:
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


def simulate_ema_anchor_variant(
    *,
    symbol: str,
    bars: list[dict[str, Any]],
    symbol_info: Any,
    ema_period: int,
    step_px: float,
    max_open_per_side: int,
) -> dict[str, Any]:
    if not bars:
        return {}
    spread_px = spread_price(symbol_info)
    ema_values = compute_ema(bars, ema_period)
    tickets: list[Ticket] = []
    realized_net = 0.0
    realized_closes = 0
    max_open_total = 0
    max_open_buy = 0
    max_open_sell = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]
        ema_anchor = float(ema_values[idx])
        if ema_anchor <= 0 or step_px <= 0:
            continue

        sell_count = sum(1 for t in tickets if t.direction == "SELL")
        buy_count = sum(1 for t in tickets if t.direction == "BUY")

        sell_steps_reached = int(max(0, math.floor((bar["high"] - ema_anchor) / step_px + 1e-9)))
        buy_steps_reached = int(max(0, math.floor((ema_anchor - bar["low"]) / step_px + 1e-9)))

        target_sell_count = min(sell_steps_reached, max_open_per_side)
        target_buy_count = min(buy_steps_reached, max_open_per_side)

        while sell_count < target_sell_count:
            level_idx = sell_count + 1
            entry = ema_anchor + (level_idx * step_px)
            tickets.append(Ticket(direction="SELL", entry_price=entry, opened_idx=idx))
            sell_count += 1

        while buy_count < target_buy_count:
            level_idx = buy_count + 1
            entry = ema_anchor - (level_idx * step_px)
            tickets.append(Ticket(direction="BUY", entry_price=entry, opened_idx=idx))
            buy_count += 1

        # Conservative ordering: do not allow same-bar open+close on a fresh ticket.
        # At EMA touch we only harvest tickets that are green at the executable
        # reference price; losers stay in inventory instead of being force-closed.
        for ticket in list(tickets):
            if ticket.opened_idx >= idx:
                continue
            if ticket.direction == "SELL" and bar["low"] <= ema_anchor:
                pnl = unit_pnl_usd(symbol, "SELL", ticket.entry_price, ema_anchor, spread_px)
                if pnl > 0:
                    realized_net += pnl
                    realized_closes += 1
                    tickets.remove(ticket)
            elif ticket.direction == "BUY" and bar["high"] >= ema_anchor:
                pnl = unit_pnl_usd(symbol, "BUY", ticket.entry_price, ema_anchor, spread_px)
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
    floating_net = sum(
        unit_pnl_usd(
            symbol,
            t.direction,
            t.entry_price,
            last_close,
            spread_px,
        )
        for t in tickets
    )
    timeframe_hours = (bars[-1]["time"] - bars[0]["time"]) / 3600.0 if len(bars) > 1 else 0.0
    per_hour = realized_net / timeframe_hours if timeframe_hours > 0 else 0.0
    closes_per_hour = realized_closes / timeframe_hours if timeframe_hours > 0 else 0.0

    return {
        "realized_closes": realized_closes,
        "realized_net_usd": round(realized_net, 3),
        "floating_net_usd": round(floating_net, 3),
        "combined_net_usd": round(realized_net + floating_net, 3),
        "usd_per_hour": round(per_hour, 4),
        "closes_per_hour": round(closes_per_hour, 4),
        "open_tickets_left": len(tickets),
        "max_open_total": max_open_total,
        "max_open_buy": max_open_buy,
        "max_open_sell": max_open_sell,
    }


def build_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# EMA Anchor Lattice Study",
        "",
        "Study question: open every `x` steps above/below a moving EMA anchor and harvest only profitable orders when price touches that same EMA line again.",
        "",
        "Conservative assumptions:",
        "- live crypto lanes only (enabled registry set)",
        "- timeframe/step seeded from the current live contract for each symbol",
        "- EMA period sweep changes the anchor only; no additional close alpha or rescue law",
        "- EMA touch is a profitable-only harvest checkpoint; losers are not force-closed there",
        "- no same-bar open-and-close on a fresh ticket",
        "",
        "## Best Per Symbol",
        "",
        "| Symbol | Timeframe | Lane | EMA | Step Mult | Step Px | $/h | Closes/h | Realized | Floating | Open Left | Max Open |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    best_by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row["symbol"])
        current = best_by_symbol.get(symbol)
        if current is None or float(row["usd_per_hour"]) > float(current["usd_per_hour"]):
            best_by_symbol[symbol] = row
    for symbol in sorted(best_by_symbol):
        row = best_by_symbol[symbol]
        lines.append(
            f"| {row['symbol']} | {row['timeframe']} | {row['lane_name']} | {row['ema_period']} | "
            f"{row['step_multiplier']} | {row['step_px']} | {row['usd_per_hour']} | {row['closes_per_hour']} | "
            f"{row['realized_net_usd']} | {row['floating_net_usd']} | {row['open_tickets_left']} | {row['max_open_total']} |"
        )

    lines.extend(
        [
            "",
            "## Full Ranking",
            "",
            "| Symbol | EMA | Step Mult | Step Px | $/h | Closes/h | Realized | Floating | Open Left | Max Open |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in sorted(rows, key=lambda r: (r["symbol"], -float(r["usd_per_hour"]))):
        lines.append(
            f"| {row['symbol']} | {row['ema_period']} | {row['step_multiplier']} | {row['step_px']} | "
            f"{row['usd_per_hour']} | {row['closes_per_hour']} | {row['realized_net_usd']} | "
            f"{row['floating_net_usd']} | {row['open_tickets_left']} | {row['max_open_total']} |"
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
            for ema_period in args.ema_periods:
                if ema_period <= 1:
                    continue
                for step_multiplier in args.step_multipliers:
                    if step_multiplier <= 0:
                        continue
                    step_px = cfg.step_px * float(step_multiplier)
                    row = simulate_ema_anchor_variant(
                        symbol=cfg.symbol,
                        bars=bars,
                        symbol_info=info,
                        ema_period=ema_period,
                        step_px=step_px,
                        max_open_per_side=cfg.max_open_per_side,
                    )
                    if not row:
                        continue
                    row.update(
                        {
                            "lane_name": cfg.lane_name,
                            "symbol": cfg.symbol,
                            "timeframe": cfg.timeframe,
                            "ema_period": ema_period,
                            "step_multiplier": round(float(step_multiplier), 4),
                            "step_px": round(step_px, 8),
                        }
                    )
                    rows.append(row)
                    print(
                        f"{cfg.symbol:<7} {cfg.timeframe:<3} EMA{ema_period:<3} x{step_multiplier:<4} "
                        f"step={step_px:<10.6f} $/h={row['usd_per_hour']:+.4f} "
                        f"closes/h={row['closes_per_hour']:.4f} realized={row['realized_net_usd']:+.2f} "
                        f"float={row['floating_net_usd']:+.2f}"
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
