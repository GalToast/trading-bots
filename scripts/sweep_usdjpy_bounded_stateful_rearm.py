#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd, vwap_anchor
from penetration_lattice_lab_v3_bounded import Config, recent_range, simulate_symbol


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Variant:
    name: str
    min_level_idx: int
    excursion_levels: int


@dataclass
class RearmToken:
    direction: str
    level: float
    level_idx: int
    armed: bool = False


VARIANTS = [
    Variant(name="rearm_lvl1_exc1", min_level_idx=1, excursion_levels=1),
    Variant(name="rearm_lvl2_exc1", min_level_idx=2, excursion_levels=1),
    Variant(name="rearm_lvl2_exc2", min_level_idx=2, excursion_levels=2),
    Variant(name="rearm_lvl3_exc1", min_level_idx=3, excursion_levels=1),
    Variant(name="rearm_lvl3_exc2", min_level_idx=3, excursion_levels=2),
    Variant(name="rearm_lvl4_exc2", min_level_idx=4, excursion_levels=2),
    Variant(name="rearm_lvl4_exc3", min_level_idx=4, excursion_levels=3),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep USDJPY bounded stateful re-arm variants against the current bounded baseline.")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "usdjpy_bounded_stateful_rearm.csv"),
    )
    return parser.parse_args()


def make_cfg() -> Config:
    return Config(
        step_pips=0.5,
        max_open_per_side=20,
        max_floating_loss_usd=-10.0,
        vwap_lookback=20,
        regime_lookback_bars=60,
        max_range_pips=24.0,
        breakout_buffer_pips=5.0,
        max_lattice_window_bars=240,
        cooldown_bars=60,
    )


def _update_token_arming(tokens: list[RearmToken], bar: dict, base_step_px: float, variant: Variant) -> None:
    for token in tokens:
        if token.armed:
            continue
        if token.direction == "SELL":
            away_trigger = token.level - (variant.excursion_levels * base_step_px)
            if bar["low"] <= away_trigger:
                token.armed = True
        else:
            away_trigger = token.level + (variant.excursion_levels * base_step_px)
            if bar["high"] >= away_trigger:
                token.armed = True


def _consume_rearm_tokens(
    *,
    tokens: list[RearmToken],
    bar: dict,
    idx: int,
    tickets: list[Ticket],
    direction: str,
    max_open_per_side: int,
) -> int:
    open_count = sum(1 for t in tickets if t.direction == direction)
    opened = 0
    for token in list(tokens):
        if token.direction != direction or not token.armed:
            continue
        if open_count >= max_open_per_side:
            break
        if direction == "SELL" and bar["high"] >= token.level:
            tickets.append(Ticket(direction="SELL", entry_price=token.level, opened_idx=idx))
            tokens.remove(token)
            open_count += 1
            opened += 1
        elif direction == "BUY" and bar["low"] <= token.level:
            tickets.append(Ticket(direction="BUY", entry_price=token.level, opened_idx=idx))
            tokens.remove(token)
            open_count += 1
            opened += 1
    return opened


def simulate_bounded_stateful_rearm(symbol: str, bars: list[dict], symbol_info, cfg: Config, variant: Variant) -> dict:
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
    rearm_tokens: list[RearmToken] = []
    realized_pnls: list[float] = []
    forced_unwinds: list[float] = []
    breakout_flushes: list[float] = []
    rearm_opens = 0
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

        _update_token_arming(rearm_tokens, bar, base_step_px, variant)

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

        rearm_opens += _consume_rearm_tokens(
            tokens=rearm_tokens,
            bar=bar,
            idx=idx,
            tickets=open_tickets,
            direction="SELL",
            max_open_per_side=cfg.max_open_per_side,
        )
        rearm_opens += _consume_rearm_tokens(
            tokens=rearm_tokens,
            bar=bar,
            idx=idx,
            tickets=open_tickets,
            direction="BUY",
            max_open_per_side=cfg.max_open_per_side,
        )

        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) >= 2 and bar["low"] <= sells[1].entry_price:
            close_ref = bar["low"]
            profitable = [t for t in sells if unit_pnl_usd(symbol, "SELL", t.entry_price, close_ref, spread_px) > 0]
            if not profitable:
                break
            for ticket in profitable:
                realized_pnls.append(unit_pnl_usd(symbol, "SELL", ticket.entry_price, close_ref, spread_px))
                open_tickets.remove(ticket)
                level_idx = int(round((ticket.entry_price - anchor) / base_step_px))
                if level_idx >= variant.min_level_idx:
                    rearm_tokens.append(RearmToken(direction="SELL", level=ticket.entry_price, level_idx=level_idx))
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
                level_idx = int(round((anchor - ticket.entry_price) / base_step_px))
                if level_idx >= variant.min_level_idx:
                    rearm_tokens.append(RearmToken(direction="BUY", level=ticket.entry_price, level_idx=level_idx))
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
                rearm_tokens = []
                continue

            if breakout_up or breakout_down or timed_out:
                for ticket, pnl in list(floating):
                    breakout_flushes.append(pnl)
                    open_tickets.remove(ticket)
                breakout_kills += 1 if (breakout_up or breakout_down) else 0
                timed_kills += 1 if timed_out and not (breakout_up or breakout_down) else 0
                cooldown_until_idx = idx + cfg.cooldown_bars
                lattice_started_idx = None
                rearm_tokens = []
                continue

        if not open_tickets:
            lattice_started_idx = None
            candidate_anchor = vwap_anchor(bars, idx, cfg.vwap_lookback)
            if abs(candidate_anchor - anchor) >= base_step_px:
                anchor = candidate_anchor
                next_sell_level = anchor + base_step_px
                next_buy_level = anchor - base_step_px
                anchor_resets += 1
                rearm_tokens = []

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
        "rearm_opens": rearm_opens,
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

        cfg = make_cfg()
        baseline = simulate_symbol("USDJPY", bars, info, cfg)
        rows: list[dict] = []
        for variant in VARIANTS:
            result = simulate_bounded_stateful_rearm("USDJPY", bars, info, cfg, variant)
            rows.append(
                {
                    "variant": variant.name,
                    "baseline_total_usd": baseline["combined_net_usd"],
                    "variant_total_usd": result["combined_net_usd"],
                    "delta_total_usd": round(float(result["combined_net_usd"]) - float(baseline["combined_net_usd"]), 3),
                    "baseline_realized_usd": baseline["realized_net_usd"],
                    "variant_realized_usd": result["realized_net_usd"],
                    "baseline_breakout_usd": baseline["breakout_net_usd"],
                    "variant_breakout_usd": result["breakout_net_usd"],
                    "baseline_floating_usd": baseline["floating_net_usd"],
                    "variant_floating_usd": result["floating_net_usd"],
                    "baseline_max_open": baseline["max_open_total"],
                    "variant_max_open": result["max_open_total"],
                    "variant_rearm_opens": result["rearm_opens"],
                    "baseline_breakout_kills": baseline["breakout_kills"],
                    "variant_breakout_kills": result["breakout_kills"],
                    "baseline_timed_kills": baseline["timed_kills"],
                    "variant_timed_kills": result["timed_kills"],
                }
            )

        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else [
                "variant",
                "baseline_total_usd",
                "variant_total_usd",
                "delta_total_usd",
                "baseline_realized_usd",
                "variant_realized_usd",
                "baseline_breakout_usd",
                "variant_breakout_usd",
                "baseline_floating_usd",
                "variant_floating_usd",
                "baseline_max_open",
                "variant_max_open",
                "variant_rearm_opens",
                "baseline_breakout_kills",
                "variant_breakout_kills",
                "baseline_timed_kills",
                "variant_timed_kills",
            ])
            writer.writeheader()
            writer.writerows(rows)

        print(f"Wrote {out_path}")
        print(f"baseline={baseline['combined_net_usd']}")
        for row in rows:
            print(
                f"{row['variant']}: total={row['variant_total_usd']} delta={row['delta_total_usd']} "
                f"rearm_opens={row['variant_rearm_opens']} max_open={row['variant_max_open']}"
            )
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
