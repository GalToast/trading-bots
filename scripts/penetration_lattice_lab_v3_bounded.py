#!/usr/bin/env python3
"""
Penetration Lattice Lab v3 — bounded, breakout-aware micro-retrace harvesting.

This branch treats V1/V2 as baselines and adds one core idea:
the lattice only gets to live while price still behaves like a harvestable
oscillation around a recent center. When price proves it is breaking regime,
the whole structure dies and waits.

Key additions over v2:
  1. Local range / center gate before opening a lattice
  2. Breakout kill switch when price exits the local range with buffer
  3. Session/window kill switch after a bounded lattice lifetime
  4. Cooldown after regime death before new orders can start
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import (
    DEFAULT_SYMBOLS,
    ROOT,
    Ticket,
    dynamic_step,
    load_bars,
    pip_size_for,
    spread_price,
    unit_pnl_usd,
    vwap_anchor,
)


@dataclass(frozen=True)
class Config:
    step_pips: float = 1.0
    max_open_per_side: int = 20
    max_floating_loss_usd: float = -10.0
    vwap_lookback: int = 20
    adaptive_step_threshold_1: int = 10
    adaptive_step_threshold_2: int = 20
    adaptive_step_multiplier_1: float = 1.5
    adaptive_step_multiplier_2: float = 2.0
    regime_lookback_bars: int = 60
    max_range_pips: float = 18.0
    breakout_buffer_pips: float = 3.0
    max_lattice_window_bars: int = 240
    cooldown_bars: int = 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bounded penetration lattice with breakout-aware regime death."
    )
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--step-pips", type=float, default=1.0)
    parser.add_argument("--max-open-per-side", type=int, default=20)
    parser.add_argument("--max-floating-loss-usd", type=float, default=-10.0)
    parser.add_argument("--vwap-lookback", type=int, default=20)
    parser.add_argument("--regime-lookback-bars", type=int, default=60)
    parser.add_argument("--max-range-pips", type=float, default=18.0)
    parser.add_argument("--breakout-buffer-pips", type=float, default=3.0)
    parser.add_argument("--max-lattice-window-bars", type=int, default=240)
    parser.add_argument("--cooldown-bars", type=int, default=60)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "penetration_lattice_lab_v3_bounded.csv"),
    )
    return parser.parse_args()


def recent_range(bars: list[dict], idx: int, lookback: int) -> tuple[float, float]:
    window = bars[max(0, idx - lookback):idx]
    if not window:
        close = bars[idx - 1]["close"]
        return close, close
    return max(b["high"] for b in window), min(b["low"] for b in window)


def simulate_symbol(symbol: str, bars: list[dict], symbol_info, cfg: Config) -> dict:
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size
    breakout_buffer_px = cfg.breakout_buffer_pips * pip_size

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px

    open_tickets: list[Ticket] = []
    realized_pnls: list[float] = []
    forced_unwinds: list[float] = []
    breakout_flushes: list[float] = []
    max_open = 0
    max_open_buy = 0
    max_open_sell = 0
    anchor_resets = 0
    hard_stop_fires = 0
    breakout_kills = 0
    timed_kills = 0
    cooldown_until_idx = 0
    lattice_started_idx: int | None = None
    regime_high = bars[0]["close"]
    regime_low = bars[0]["close"]

    for idx in range(1, len(bars)):
        bar = bars[idx]

        if idx < cfg.regime_lookback_bars:
            continue

        if not open_tickets:
            regime_high, regime_low = recent_range(bars, idx, cfg.regime_lookback_bars)
            regime_width_pips = (regime_high - regime_low) / pip_size
            if regime_width_pips > cfg.max_range_pips or idx < cooldown_until_idx:
                continue
            anchor = vwap_anchor(bars, idx, cfg.vwap_lookback)
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px

        open_buy = sum(1 for t in open_tickets if t.direction == "BUY")
        open_sell = sum(1 for t in open_tickets if t.direction == "SELL")

        current_sell_step = dynamic_step(base_step_px, open_sell, cfg)
        current_buy_step = dynamic_step(base_step_px, open_buy, cfg)

        while bar["high"] >= next_sell_level and open_sell < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            if lattice_started_idx is None:
                lattice_started_idx = idx
            open_sell += 1
            current_sell_step = dynamic_step(base_step_px, open_sell, cfg)
            next_sell_level += current_sell_step

        while bar["low"] <= next_buy_level and open_buy < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            if lattice_started_idx is None:
                lattice_started_idx = idx
            open_buy += 1
            current_buy_step = dynamic_step(base_step_px, open_buy, cfg)
            next_buy_level -= current_buy_step

        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) >= 2 and bar["low"] <= sells[1].entry_price:
            close_ref = bar["low"]
            profitable = [t for t in sells if unit_pnl_usd(symbol, "SELL", t.entry_price, close_ref, spread_px) > 0]
            if not profitable:
                break
            for ticket in profitable:
                realized_pnls.append(unit_pnl_usd(symbol, "SELL", ticket.entry_price, close_ref, spread_px))
                open_tickets.remove(ticket)
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) >= 2 and bar["high"] >= buys[1].entry_price:
            close_ref = bar["high"]
            profitable = [t for t in buys if unit_pnl_usd(symbol, "BUY", t.entry_price, close_ref, spread_px) > 0]
            if not profitable:
                break
            for ticket in profitable:
                realized_pnls.append(unit_pnl_usd(symbol, "BUY", ticket.entry_price, close_ref, spread_px))
                open_tickets.remove(ticket)
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        if open_tickets:
            floating = [(t, unit_pnl_usd(symbol, t.direction, t.entry_price, bar["close"], spread_px)) for t in open_tickets]
            worst_pnl = min(pnl for _, pnl in floating)
            breakout_up = bar["close"] >= regime_high + breakout_buffer_px
            breakout_down = bar["close"] <= regime_low - breakout_buffer_px
            timed_out = lattice_started_idx is not None and (idx - lattice_started_idx) >= cfg.max_lattice_window_bars

            if worst_pnl <= cfg.max_floating_loss_usd:
                for ticket, pnl in list(floating):
                    forced_unwinds.append(pnl)
                    open_tickets.remove(ticket)
                hard_stop_fires += 1
                cooldown_until_idx = idx + cfg.cooldown_bars
                lattice_started_idx = None
                continue

            if breakout_up or breakout_down or timed_out:
                for ticket, pnl in list(floating):
                    breakout_flushes.append(pnl)
                    open_tickets.remove(ticket)
                breakout_kills += 1 if (breakout_up or breakout_down) else 0
                timed_kills += 1 if timed_out and not (breakout_up or breakout_down) else 0
                cooldown_until_idx = idx + cfg.cooldown_bars
                lattice_started_idx = None
                continue

        if not open_tickets:
            lattice_started_idx = None
            candidate_anchor = vwap_anchor(bars, idx, cfg.vwap_lookback)
            if abs(candidate_anchor - anchor) >= base_step_px:
                anchor = candidate_anchor
                next_sell_level = anchor + base_step_px
                next_buy_level = anchor - base_step_px
                anchor_resets += 1

        max_open = max(max_open, len(open_tickets))
        max_open_buy = max(max_open_buy, sum(1 for t in open_tickets if t.direction == "BUY"))
        max_open_sell = max(max_open_sell, sum(1 for t in open_tickets if t.direction == "SELL"))

    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px)
        for t in open_tickets
    ]

    realized_net = sum(realized_pnls)
    forced_net = sum(forced_unwinds)
    breakout_net = sum(breakout_flushes)
    floating_net = sum(floating_pnls)
    combined_net = realized_net + forced_net + breakout_net + floating_net
    total_closes = len(realized_pnls) + len(forced_unwinds) + len(breakout_flushes)
    wins = sum(1 for p in realized_pnls + forced_unwinds + breakout_flushes if p > 0)

    return {
        "symbol": symbol,
        "realized_closes": len(realized_pnls),
        "forced_unwinds": len(forced_unwinds),
        "breakout_flushes": len(breakout_flushes),
        "total_closes": total_closes,
        "wr_pct": round(wins / total_closes * 100.0, 1) if total_closes else 0.0,
        "realized_net_usd": round(realized_net, 3),
        "forced_net_usd": round(forced_net, 3),
        "breakout_net_usd": round(breakout_net, 3),
        "open_tickets_left": len(open_tickets),
        "floating_net_usd": round(floating_net, 3),
        "worst_floating_usd": round(min(floating_pnls), 3) if floating_pnls else 0.0,
        "combined_net_usd": round(combined_net, 3),
        "max_open_total": max_open,
        "max_open_buy": max_open_buy,
        "max_open_sell": max_open_sell,
        "anchor_resets": anchor_resets,
        "hard_stop_fires": hard_stop_fires,
        "breakout_kills": breakout_kills,
        "timed_kills": timed_kills,
    }


def main() -> int:
    args = parse_args()
    cfg = Config(
        step_pips=args.step_pips,
        max_open_per_side=args.max_open_per_side,
        max_floating_loss_usd=args.max_floating_loss_usd,
        vwap_lookback=args.vwap_lookback,
        regime_lookback_bars=args.regime_lookback_bars,
        max_range_pips=args.max_range_pips,
        breakout_buffer_pips=args.breakout_buffer_pips,
        max_lattice_window_bars=args.max_lattice_window_bars,
        cooldown_bars=args.cooldown_bars,
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
                f"{symbol:<7} realized={row['realized_closes']:>5} forced={row['forced_unwinds']:>4} "
                f"breakout={row['breakout_flushes']:>4} banked={row['realized_net_usd']:+.2f} "
                f"forced_net={row['forced_net_usd']:+.2f} breakout_net={row['breakout_net_usd']:+.2f} "
                f"float={row['floating_net_usd']:+.2f} combined={row['combined_net_usd']:+.2f} "
                f"max_open={row['max_open_total']:>3}"
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
