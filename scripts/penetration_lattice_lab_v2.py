#!/usr/bin/env python3
"""
Penetration Lattice Lab v2 — Structural improvements over v1 baseline.

Changes from v1:
  1. Floating-book hard stop (per-ticket loss cap, whole-book flush)
  2. Pair-wise profitable unwind (close ALL profitable on a side during penetration)
  3. Adaptive step density (widen steps after 10/20 open per side)
  4. VWAP anchor reset (center on fair value, not last wick)
  5. Time-based floating flush (max_hold_bars safety valve)
"""
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
    step_pips: float = 1.0
    anchor_reset_pips: float = 3.0
    max_open_per_side: int = 50
    max_floating_loss_usd: float = -10.0  # per-ticket hard stop threshold
    vwap_lookback: int = 20               # bars for VWAP anchor reset
    max_hold_bars: int = 1440             # time-based flush (24h default)
    # Adaptive step density thresholds
    adaptive_step_threshold_1: int = 10   # widen step after N open
    adaptive_step_threshold_2: int = 20   # widen further after N open
    adaptive_step_multiplier_1: float = 1.5
    adaptive_step_multiplier_2: float = 2.0


@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Penetration lattice v2: structural improvements over v1."
    )
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument("--step-pips", type=float, default=1.0)
    parser.add_argument("--anchor-reset-pips", type=float, default=3.0)
    parser.add_argument("--max-open-per-side", type=int, default=50)
    parser.add_argument("--max-floating-loss-usd", type=float, default=-10.0)
    parser.add_argument("--vwap-lookback", type=int, default=20)
    parser.add_argument("--max-hold-bars", type=int, default=1440)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "penetration_lattice_lab_v2.csv"),
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


def dynamic_step(base_step: float, open_count: int, cfg: Config) -> float:
    """Adaptive step density: widen steps as exposure grows."""
    if open_count >= cfg.adaptive_step_threshold_2:
        return base_step * cfg.adaptive_step_multiplier_2
    elif open_count >= cfg.adaptive_step_threshold_1:
        return base_step * cfg.adaptive_step_multiplier_1
    return base_step


def vwap_anchor(bars: list[dict], idx: int, lookback: int) -> float:
    """Compute VWAP over the last `lookback` bars for fair-value centering."""
    start = max(0, idx - lookback)
    window = bars[start:idx]
    if not window:
        return bars[idx - 1]["close"]
    cum_cv = sum(b["close"] * b["tick_volume"] for b in window)
    cum_v = sum(b["tick_volume"] for b in window)
    return cum_cv / cum_v if cum_v > 0 else window[-1]["close"]


def simulate_symbol(symbol: str, bars: list[dict], symbol_info, cfg: Config) -> dict:
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
    forced_unwinds: list[float] = []  # track forced closes separately
    time_flushes: list[float] = []    # track time-based flushes
    max_open = 0
    max_open_buy = 0
    max_open_sell = 0
    anchor_resets = 0
    hard_stop_fires = 0
    time_flush_fires = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]

        open_buy = sum(1 for t in open_tickets if t.direction == "BUY")
        open_sell = sum(1 for t in open_tickets if t.direction == "SELL")

        # --- Compute current adaptive step ---
        current_sell_step = dynamic_step(base_step_px, open_sell, cfg)
        current_buy_step = dynamic_step(base_step_px, open_buy, cfg)

        # --- Open new lattice orders ---
        while bar["high"] >= next_sell_level and open_sell < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            next_sell_level += current_sell_step
            open_sell += 1
            # Re-compute step after adding a ticket
            current_sell_step = dynamic_step(base_step_px, open_sell, cfg)

        while bar["low"] <= next_buy_level and open_buy < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            next_buy_level -= current_buy_step
            open_buy += 1
            current_buy_step = dynamic_step(base_step_px, open_buy, cfg)

        # --- Penetration close: sell side ---
        # Close ALL profitable sells when price penetrates below the second-highest sell level.
        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) >= 2 and bar["low"] <= sells[1].entry_price:
            close_ref = bar["low"]  # Use bar low as realistic fill
            # Close all profitable sells at this penetration point
            profitable = [t for t in sells if unit_pnl_usd(symbol, "SELL", t.entry_price, close_ref, spread_px) > 0]
            if not profitable:
                break  # No profitable closes possible at this level
            for ticket in profitable:
                pnl = unit_pnl_usd(symbol, "SELL", ticket.entry_price, close_ref, spread_px)
                realized_pnls.append(pnl)
                open_tickets.remove(ticket)
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        # --- Penetration close: buy side ---
        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) >= 2 and bar["high"] >= buys[1].entry_price:
            close_ref = bar["high"]  # Use bar high as realistic fill
            profitable = [t for t in buys if unit_pnl_usd(symbol, "BUY", t.entry_price, close_ref, spread_px) > 0]
            if not profitable:
                break
            for ticket in profitable:
                pnl = unit_pnl_usd(symbol, "BUY", ticket.entry_price, close_ref, spread_px)
                realized_pnls.append(pnl)
                open_tickets.remove(ticket)
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        # --- Floating book hard stop ---
        if open_tickets:
            floating_pnls_list = [
                (t, unit_pnl_usd(symbol, t.direction, t.entry_price, bar["close"], spread_px))
                for t in open_tickets
            ]
            worst_pnl = min(pnl for _, pnl in floating_pnls_list)
            if worst_pnl <= cfg.max_floating_loss_usd:
                # Close entire book at market
                for ticket, _ in list(floating_pnls_list):
                    pnl = unit_pnl_usd(symbol, ticket.direction, ticket.entry_price, bar["close"], spread_px)
                    forced_unwinds.append(pnl)
                    open_tickets.remove(ticket)
                hard_stop_fires += 1
                # Reset anchor after forced flush
                anchor = bar["close"]
                next_sell_level = anchor + base_step_px
                next_buy_level = anchor - base_step_px

        # --- Time-based floating flush ---
        if open_tickets and cfg.max_hold_bars > 0:
            aged = [t for t in open_tickets if (idx - t.opened_idx) >= cfg.max_hold_bars]
            for ticket in aged:
                pnl = unit_pnl_usd(symbol, ticket.direction, ticket.entry_price, bar["close"], spread_px)
                time_flushes.append(pnl)
                open_tickets.remove(ticket)
            if aged:
                time_flush_fires += len(aged)

        # --- Anchor reset with VWAP ---
        if not open_tickets:
            candidate_anchor = vwap_anchor(bars, idx, cfg.vwap_lookback)
            if abs(bar["close"] - anchor) >= reset_px:
                anchor = candidate_anchor
                next_sell_level = anchor + base_step_px
                next_buy_level = anchor - base_step_px
                anchor_resets += 1

        max_open = max(max_open, len(open_tickets))
        max_open_buy = max(max_open_buy, sum(1 for t in open_tickets if t.direction == "BUY"))
        max_open_sell = max(max_open_sell, sum(1 for t in open_tickets if t.direction == "SELL"))

    # --- End-of-sample floating book ---
    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px)
        for t in open_tickets
    ]

    realized_net = sum(realized_pnls)
    forced_net = sum(forced_unwinds)
    time_flush_net = sum(time_flushes)
    floating_net = sum(floating_pnls)
    combined_net = realized_net + forced_net + time_flush_net + floating_net

    all_closes = realized_pnls + forced_unwinds + time_flushes
    total_closes = len(all_closes)
    wins = sum(1 for p in all_closes if p > 0)

    return {
        "symbol": symbol,
        "realized_closes": len(realized_pnls),
        "forced_unwinds": len(forced_unwinds),
        "time_flushes": len(time_flushes),
        "total_closes": total_closes,
        "wr_pct": round(wins / total_closes * 100.0, 1) if total_closes else 0.0,
        "realized_net_usd": round(realized_net, 3),
        "forced_net_usd": round(forced_net, 3),
        "time_flush_net_usd": round(time_flush_net, 3),
        "open_tickets_left": len(open_tickets),
        "floating_net_usd": round(floating_net, 3),
        "worst_floating_usd": round(min(floating_pnls), 3) if floating_pnls else 0.0,
        "combined_net_usd": round(combined_net, 3),
        "max_open_total": max_open,
        "max_open_buy": max_open_buy,
        "max_open_sell": max_open_sell,
        "anchor_resets": anchor_resets,
        "hard_stop_fires": hard_stop_fires,
        "time_flush_fires": time_flush_fires,
    }


def main() -> int:
    args = parse_args()
    cfg = Config(
        step_pips=args.step_pips,
        anchor_reset_pips=args.anchor_reset_pips,
        max_open_per_side=args.max_open_per_side,
        max_floating_loss_usd=args.max_floating_loss_usd,
        vwap_lookback=args.vwap_lookback,
        max_hold_bars=args.max_hold_bars,
    )

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        rows: list[dict] = []
        for symbol in args.symbols:
            info = mt5.symbol_info(symbol)
            if info is None:
                continue
            bars = load_bars(symbol, args.days)
            if not bars:
                continue
            row = simulate_symbol(symbol, bars, info, cfg)
            rows.append(row)
            print(
                f"{symbol:<7} realized={row['realized_closes']:>4} forced={row['forced_unwinds']:>3} "
                f"time_flush={row['time_flushes']:>3} banked={row['realized_net_usd']:+.2f} "
                f"forced_net={row['forced_net_usd']:+.2f} left={row['open_tickets_left']:>3} "
                f"float={row['floating_net_usd']:+.2f} combined={row['combined_net_usd']:+.2f} "
                f"max_open={row['max_open_total']:>3} wr={row['wr_pct']:.1f}%"
            )

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
