#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_usdjpy_same_bar_guard import (
    LIVE_VARIANT,
    RearmToken,
    _consume_rearm_tokens,
    _update_token_arming,
    make_cfg,
)
from penetration_lattice_lab_v2 import dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd, vwap_anchor
from penetration_lattice_lab_v3_bounded import recent_range


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Variant:
    name: str
    close_gap: int
    same_bar_min_pnl: float
    shallow_level_cap: int | None


@dataclass
class SimTicket:
    direction: str
    entry_price: float
    opened_idx: int
    level_idx: int


VARIANTS = [
    Variant(name="baseline_gap1", close_gap=1, same_bar_min_pnl=0.0, shallow_level_cap=None),
    Variant(name="gap1_lvl1_min0.03", close_gap=1, same_bar_min_pnl=0.03, shallow_level_cap=1),
    Variant(name="gap1_lvl1_min0.05", close_gap=1, same_bar_min_pnl=0.05, shallow_level_cap=1),
    Variant(name="gap1_lvl2_min0.03", close_gap=1, same_bar_min_pnl=0.03, shallow_level_cap=2),
    Variant(name="gap1_lvl2_min0.05", close_gap=1, same_bar_min_pnl=0.05, shallow_level_cap=2),
    Variant(name="gap1_lvl3_min0.03", close_gap=1, same_bar_min_pnl=0.03, shallow_level_cap=3),
    Variant(name="gap1_lvl3_min0.05", close_gap=1, same_bar_min_pnl=0.05, shallow_level_cap=3),
    Variant(name="baseline_gap2", close_gap=2, same_bar_min_pnl=0.0, shallow_level_cap=None),
    Variant(name="gap2_lvl1_min0.03", close_gap=2, same_bar_min_pnl=0.03, shallow_level_cap=1),
    Variant(name="gap2_lvl1_min0.05", close_gap=2, same_bar_min_pnl=0.05, shallow_level_cap=1),
    Variant(name="gap2_lvl2_min0.03", close_gap=2, same_bar_min_pnl=0.03, shallow_level_cap=2),
    Variant(name="gap2_lvl2_min0.05", close_gap=2, same_bar_min_pnl=0.05, shallow_level_cap=2),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark USDJPY bounded rearm with shallow-level same-bar hurdles.")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "usdjpy_same_bar_shallow_hurdle.csv"),
    )
    return parser.parse_args()


def _level_idx_for(direction: str, entry_price: float, anchor: float, base_step_px: float) -> int:
    if direction == "SELL":
        return max(1, int(round((entry_price - anchor) / base_step_px)))
    return max(1, int(round((anchor - entry_price) / base_step_px)))


def _same_bar_hurdle_applies(ticket: SimTicket, idx: int, variant: Variant, pnl: float) -> bool:
    if variant.same_bar_min_pnl <= 0.0 or variant.shallow_level_cap is None:
        return False
    if idx != int(ticket.opened_idx):
        return False
    if ticket.level_idx > int(variant.shallow_level_cap):
        return False
    return pnl < variant.same_bar_min_pnl


def simulate_variant(symbol: str, bars: list[dict], symbol_info, variant: Variant) -> dict:
    cfg = make_cfg()
    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size
    breakout_buffer_px = cfg.breakout_buffer_pips * pip_size

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px

    open_tickets: list[SimTicket] = []
    rearm_tokens: list[RearmToken] = []
    realized_pnls: list[float] = []
    forced_unwinds: list[float] = []
    breakout_flushes: list[float] = []
    rearm_opens = 0
    max_open = 0
    cooldown_until_idx = 0
    lattice_started_idx: int | None = None
    regime_high = bars[0]["close"]
    regime_low = bars[0]["close"]
    same_bar_closes = 0
    same_bar_blocked = 0
    shallow_same_bar_blocked = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]
        if idx < cfg.regime_lookback_bars:
            continue

        _update_token_arming(rearm_tokens, bar, base_step_px, LIVE_VARIANT)

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
            level_idx = _level_idx_for("SELL", next_sell_level, anchor, base_step_px)
            open_tickets.append(SimTicket(direction="SELL", entry_price=next_sell_level, opened_idx=idx, level_idx=level_idx))
            if lattice_started_idx is None:
                lattice_started_idx = idx
            open_sell += 1
            current_sell_step = dynamic_step(base_step_px, open_sell, cfg)
            next_sell_level += current_sell_step

        while bar["low"] <= next_buy_level and open_buy < cfg.max_open_per_side:
            level_idx = _level_idx_for("BUY", next_buy_level, anchor, base_step_px)
            open_tickets.append(SimTicket(direction="BUY", entry_price=next_buy_level, opened_idx=idx, level_idx=level_idx))
            if lattice_started_idx is None:
                lattice_started_idx = idx
            open_buy += 1
            current_buy_step = dynamic_step(base_step_px, open_buy, cfg)
            next_buy_level -= current_buy_step

        before_len = len(open_tickets)
        rearm_opens += _consume_rearm_tokens(
            tokens=rearm_tokens,
            bar=bar,
            idx=idx,
            tickets=open_tickets,  # type: ignore[arg-type]
            direction="SELL",
            max_open_per_side=cfg.max_open_per_side,
        )
        if len(open_tickets) > before_len:
            for ticket in open_tickets[before_len:]:
                ticket.level_idx = _level_idx_for(ticket.direction, ticket.entry_price, anchor, base_step_px)
        before_len = len(open_tickets)
        rearm_opens += _consume_rearm_tokens(
            tokens=rearm_tokens,
            bar=bar,
            idx=idx,
            tickets=open_tickets,  # type: ignore[arg-type]
            direction="BUY",
            max_open_per_side=cfg.max_open_per_side,
        )
        if len(open_tickets) > before_len:
            for ticket in open_tickets[before_len:]:
                ticket.level_idx = _level_idx_for(ticket.direction, ticket.entry_price, anchor, base_step_px)

        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > variant.close_gap and bar["low"] <= sells[variant.close_gap].entry_price:
            close_ref = bar["low"]
            profitable: list[SimTicket] = []
            for ticket in sells:
                pnl = unit_pnl_usd(symbol, "SELL", ticket.entry_price, close_ref, spread_px)
                if pnl <= 0:
                    continue
                if _same_bar_hurdle_applies(ticket, idx, variant, pnl):
                    same_bar_blocked += 1
                    shallow_same_bar_blocked += 1
                    continue
                profitable.append(ticket)
            if not profitable:
                break
            for ticket in profitable:
                pnl = unit_pnl_usd(symbol, "SELL", ticket.entry_price, close_ref, spread_px)
                realized_pnls.append(pnl)
                if idx == int(ticket.opened_idx):
                    same_bar_closes += 1
                open_tickets.remove(ticket)
                if ticket.level_idx >= LIVE_VARIANT.min_level_idx:
                    rearm_tokens.append(RearmToken(direction="SELL", level=ticket.entry_price, level_idx=ticket.level_idx))
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > variant.close_gap and bar["high"] >= buys[variant.close_gap].entry_price:
            close_ref = bar["high"]
            profitable: list[SimTicket] = []
            for ticket in buys:
                pnl = unit_pnl_usd(symbol, "BUY", ticket.entry_price, close_ref, spread_px)
                if pnl <= 0:
                    continue
                if _same_bar_hurdle_applies(ticket, idx, variant, pnl):
                    same_bar_blocked += 1
                    shallow_same_bar_blocked += 1
                    continue
                profitable.append(ticket)
            if not profitable:
                break
            for ticket in profitable:
                pnl = unit_pnl_usd(symbol, "BUY", ticket.entry_price, close_ref, spread_px)
                realized_pnls.append(pnl)
                if idx == int(ticket.opened_idx):
                    same_bar_closes += 1
                open_tickets.remove(ticket)
                if ticket.level_idx >= LIVE_VARIANT.min_level_idx:
                    rearm_tokens.append(RearmToken(direction="BUY", level=ticket.entry_price, level_idx=ticket.level_idx))
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
                cooldown_until_idx = idx + cfg.cooldown_bars
                lattice_started_idx = None
                rearm_tokens = []
                continue

            if breakout_up or breakout_down or timed_out:
                for ticket, pnl in list(floating):
                    breakout_flushes.append(pnl)
                    open_tickets.remove(ticket)
                cooldown_until_idx = idx + cfg.cooldown_bars
                lattice_started_idx = None
                rearm_tokens = []
                continue

        max_open = max(max_open, len(open_tickets))

    last_close = bars[-1]["close"]
    floating_pnls = [unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px) for t in open_tickets]
    combined_net = sum(realized_pnls) + sum(forced_unwinds) + sum(breakout_flushes) + sum(floating_pnls)

    return {
        "variant": variant.name,
        "combined_net_usd": round(combined_net, 3),
        "realized_net_usd": round(sum(realized_pnls), 3),
        "floating_net_usd": round(sum(floating_pnls), 3),
        "realized_closes": len(realized_pnls),
        "same_bar_closes": same_bar_closes,
        "same_bar_blocked": same_bar_blocked,
        "shallow_same_bar_blocked": shallow_same_bar_blocked,
        "rearm_opens": rearm_opens,
        "max_open_total": max_open,
    }


def main() -> int:
    args = parse_args()
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1
    try:
        info = mt5.symbol_info("USDJPY")
        if info is None:
            print("Missing USDJPY symbol info")
            return 1
        bars = load_bars("USDJPY", args.days)
        if not bars:
            print("Missing USDJPY bars")
            return 1

        rows = [simulate_variant("USDJPY", bars, info, variant) for variant in VARIANTS]
        baseline_total = next(row["combined_net_usd"] for row in rows if row["variant"] == "baseline_gap1")
        for row in rows:
            row["delta_vs_baseline_usd"] = round(float(row["combined_net_usd"]) - float(baseline_total), 3)

        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

        print(f"Wrote {out_path}")
        for row in rows:
            print(
                f"{row['variant']}: total={row['combined_net_usd']} "
                f"delta={row['delta_vs_baseline_usd']} same_bar={row['same_bar_closes']} "
                f"blocked={row['same_bar_blocked']}"
            )
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
