#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

import MetaTrader5 as mt5


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SYMBOLS = ["USDJPY", "GBPUSD", "EURUSD", "USDCHF", "NZDUSD"]
VOLUME = 0.01


@dataclass(frozen=True)
class Config:
    config_id: str
    step_pips: float
    anchor_reset_pips: float
    max_open_per_side: int
    close_mode: str
    extension_trigger_pips: float
    aggressive_after_levels: int
    aggressive_step_mult: float
    defensive_after_levels: int
    defensive_step_mult: float


@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sweep the penetration lattice design space: spacing, reset distance, "
            "depth cap, unwind policy, and density ramps."
        )
    )
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument(
        "--forced-reset-bars",
        type=int,
        default=0,
        help="If > 0, flatten all open tickets and reset the anchor after this many bars.",
    )
    parser.add_argument(
        "--floating-stop-usd",
        type=float,
        default=0.0,
        help="If > 0, flatten all open tickets when floating PnL falls below -this amount.",
    )
    parser.add_argument(
        "--config-ids",
        nargs="*",
        default=[],
        help="Optional exact config ids to run instead of the full sweep.",
    )
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "penetration_lattice_sweep.csv"),
    )
    return parser.parse_args()


def pip_size_for(symbol_info) -> float:
    point = float(symbol_info.point or 0.0)
    digits = int(symbol_info.digits or 0)
    return point * 10.0 if digits in (3, 5) else point


def load_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1440 * days)
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


def spread_price(symbol_info) -> float:
    return float(symbol_info.spread or 0.0) * float(symbol_info.point or 0.0)


def unit_pnl_usd(symbol: str, direction: str, entry_price: float, exit_price: float, spread_px: float) -> float:
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    gross = mt5.order_calc_profit(order_type, symbol, VOLUME, entry_price, exit_price)
    if gross is None:
        return 0.0
    if direction == "BUY":
        spread_cost = mt5.order_calc_profit(order_type, symbol, VOLUME, entry_price + spread_px, entry_price)
    else:
        spread_cost = mt5.order_calc_profit(order_type, symbol, VOLUME, entry_price, entry_price + spread_px)
    return float(gross) - abs(float(spread_cost or 0.0))


def build_configs() -> list[Config]:
    configs: list[Config] = []
    for step in (1.0, 2.0, 3.0, 5.0):
        for reset in (3.0, 5.0, 8.0):
            for cap in (20, 40):
                configs.append(
                    Config(
                        config_id=f"base_s{step:g}_r{reset:g}_c{cap}",
                        step_pips=step,
                        anchor_reset_pips=reset,
                        max_open_per_side=cap,
                        close_mode="one_level",
                        extension_trigger_pips=0.0,
                        aggressive_after_levels=999,
                        aggressive_step_mult=1.0,
                        defensive_after_levels=999,
                        defensive_step_mult=1.0,
                    )
                )
                configs.append(
                    Config(
                        config_id=f"def_s{step:g}_r{reset:g}_c{cap}",
                        step_pips=step,
                        anchor_reset_pips=reset,
                        max_open_per_side=cap,
                        close_mode="one_level",
                        extension_trigger_pips=0.0,
                        aggressive_after_levels=999,
                        aggressive_step_mult=1.0,
                        defensive_after_levels=6,
                        defensive_step_mult=1.5,
                    )
                )
                configs.append(
                    Config(
                        config_id=f"agg_s{step:g}_r{reset:g}_c{cap}",
                        step_pips=step,
                        anchor_reset_pips=reset,
                        max_open_per_side=cap,
                        close_mode="one_level",
                        extension_trigger_pips=0.0,
                        aggressive_after_levels=6,
                        aggressive_step_mult=0.5,
                        defensive_after_levels=999,
                        defensive_step_mult=1.0,
                    )
                )
                configs.append(
                    Config(
                        config_id=f"close2_s{step:g}_r{reset:g}_c{cap}",
                        step_pips=step,
                        anchor_reset_pips=reset,
                        max_open_per_side=cap,
                        close_mode="two_level",
                        extension_trigger_pips=0.0,
                        aggressive_after_levels=999,
                        aggressive_step_mult=1.0,
                        defensive_after_levels=999,
                        defensive_step_mult=1.0,
                    )
                )
    return configs


def current_spacing_px(direction: str, open_tickets: list[Ticket], cfg: Config, pip_size: float) -> float:
    base_step = cfg.step_pips * pip_size
    same_side = [ticket for ticket in open_tickets if ticket.direction == direction]
    level_count = len(same_side)
    if level_count >= cfg.defensive_after_levels:
        return base_step * cfg.defensive_step_mult
    if level_count >= cfg.aggressive_after_levels:
        return base_step * cfg.aggressive_step_mult
    return base_step


def unwind_side(symbol: str, direction: str, tickets: list[Ticket], bar: dict, spread_px: float, close_mode: str) -> tuple[list[Ticket], list[float]]:
    realized: list[float] = []
    survivors = tickets[:]

    if direction == "SELL":
        ordered = sorted((ticket for ticket in survivors if ticket.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        gap = 1 if close_mode == "one_level" else 2
        while len(ordered) > gap and bar["low"] <= ordered[gap].entry_price:
            outer = ordered[0]
            close_ref = ordered[gap].entry_price
            realized.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, spread_px))
            survivors.remove(outer)
            ordered = sorted((ticket for ticket in survivors if ticket.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
    else:
        ordered = sorted((ticket for ticket in survivors if ticket.direction == "BUY"), key=lambda t: t.entry_price)
        gap = 1 if close_mode == "one_level" else 2
        while len(ordered) > gap and bar["high"] >= ordered[gap].entry_price:
            outer = ordered[0]
            close_ref = ordered[gap].entry_price
            realized.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, close_ref, spread_px))
            survivors.remove(outer)
            ordered = sorted((ticket for ticket in survivors if ticket.direction == "BUY"), key=lambda t: t.entry_price)

    return survivors, realized


def simulate_symbol(
    symbol: str,
    bars: list[dict],
    symbol_info,
    cfg: Config,
    forced_reset_bars: int,
    floating_stop_usd: float,
) -> dict:
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size
    reset_px = cfg.anchor_reset_pips * pip_size

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px

    open_tickets: list[Ticket] = []
    realized_pnls: list[float] = []
    max_open = 0
    max_open_buy = 0
    max_open_sell = 0
    reset_count = 0
    forced_reset_count = 0
    floating_stopout_count = 0
    forced_reset_realized = 0.0
    floating_stopout_realized = 0.0
    last_reset_idx = 0

    def flatten_all(exit_price: float) -> list[float]:
        return [
            unit_pnl_usd(symbol, ticket.direction, ticket.entry_price, exit_price, spread_px)
            for ticket in open_tickets
        ]

    for idx in range(1, len(bars)):
        bar = bars[idx]

        open_sell = sum(1 for ticket in open_tickets if ticket.direction == "SELL")
        while bar["high"] >= next_sell_level and open_sell < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            open_sell += 1
            next_sell_level += current_spacing_px("SELL", open_tickets, cfg, pip_size)

        open_buy = sum(1 for ticket in open_tickets if ticket.direction == "BUY")
        while bar["low"] <= next_buy_level and open_buy < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            open_buy += 1
            next_buy_level -= current_spacing_px("BUY", open_tickets, cfg, pip_size)

        open_tickets, realized_sell = unwind_side(symbol, "SELL", open_tickets, bar, spread_px, cfg.close_mode)
        realized_pnls.extend(realized_sell)
        open_tickets, realized_buy = unwind_side(symbol, "BUY", open_tickets, bar, spread_px, cfg.close_mode)
        realized_pnls.extend(realized_buy)

        max_open = max(max_open, len(open_tickets))
        max_open_buy = max(max_open_buy, sum(1 for ticket in open_tickets if ticket.direction == "BUY"))
        max_open_sell = max(max_open_sell, sum(1 for ticket in open_tickets if ticket.direction == "SELL"))

        floating_pnls_now = flatten_all(bar["close"]) if open_tickets else []
        floating_net_now = sum(floating_pnls_now)

        if floating_stop_usd > 0.0 and open_tickets and floating_net_now <= -floating_stop_usd:
            realized_pnls.extend(floating_pnls_now)
            floating_stopout_realized += floating_net_now
            floating_stopout_count += 1
            open_tickets.clear()
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px
            last_reset_idx = idx
            continue

        if forced_reset_bars > 0 and (idx - last_reset_idx) >= forced_reset_bars:
            if open_tickets:
                realized_pnls.extend(floating_pnls_now)
                forced_reset_realized += floating_net_now
                open_tickets.clear()
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px
            forced_reset_count += 1
            last_reset_idx = idx
            continue

        if abs(bar["close"] - anchor) >= reset_px and not open_tickets:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px
            reset_count += 1
            last_reset_idx = idx

    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, ticket.direction, ticket.entry_price, last_close, spread_px)
        for ticket in open_tickets
    ]

    realized_net = sum(realized_pnls)
    floating_net = sum(floating_pnls)
    combined_net = realized_net + floating_net

    return {
        "symbol": symbol,
        "config_id": cfg.config_id,
        "step_pips": cfg.step_pips,
        "anchor_reset_pips": cfg.anchor_reset_pips,
        "max_open_per_side": cfg.max_open_per_side,
        "close_mode": cfg.close_mode,
        "aggressive_after_levels": cfg.aggressive_after_levels,
        "aggressive_step_mult": cfg.aggressive_step_mult,
        "defensive_after_levels": cfg.defensive_after_levels,
        "defensive_step_mult": cfg.defensive_step_mult,
        "realized_closes": len(realized_pnls),
        "realized_net_usd": round(realized_net, 3),
        "realized_exp_usd": round(mean(realized_pnls), 3) if realized_pnls else 0.0,
        "open_tickets_left": len(open_tickets),
        "floating_net_usd": round(floating_net, 3),
        "combined_net_usd": round(combined_net, 3),
        "worst_floating_ticket_usd": round(min(floating_pnls), 3) if floating_pnls else 0.0,
        "max_open_total": max_open,
        "max_open_buy": max_open_buy,
        "max_open_sell": max_open_sell,
        "anchor_resets": reset_count,
        "forced_resets": forced_reset_count,
        "forced_reset_realized_usd": round(forced_reset_realized, 3),
        "floating_stopouts": floating_stopout_count,
        "floating_stopout_realized_usd": round(floating_stopout_realized, 3),
        "score": round(combined_net - max(0.0, -min(floating_pnls) if floating_pnls else 0.0), 3),
    }


def main() -> int:
    args = parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        configs = build_configs()
        if args.config_ids:
            allowed = set(args.config_ids)
            configs = [cfg for cfg in configs if cfg.config_id in allowed]
            missing = sorted(allowed - {cfg.config_id for cfg in configs})
            if missing:
                print(f"Unknown config ids: {', '.join(missing)}")
                return 1
        rows: list[dict] = []
        for symbol in args.symbols:
            info = mt5.symbol_info(symbol)
            if info is None:
                continue
            bars = load_bars(symbol, args.days)
            if not bars:
                continue
            for cfg in configs:
                row = simulate_symbol(
                    symbol,
                    bars,
                    info,
                    cfg,
                    forced_reset_bars=args.forced_reset_bars,
                    floating_stop_usd=args.floating_stop_usd,
                )
                rows.append(row)
                print(
                    f"{symbol:<7} {cfg.config_id:<20} combined={row['combined_net_usd']:+.2f} "
                    f"realized={row['realized_net_usd']:+.2f} float={row['floating_net_usd']:+.2f} "
                    f"left={row['open_tickets_left']:>3} max_open={row['max_open_total']:>3}"
                )

        rows.sort(key=lambda row: row["score"], reverse=True)
        output_path = Path(args.output_csv)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if rows:
            with output_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            print(f"Saved {output_path}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
