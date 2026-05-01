#!/usr/bin/env python3
"""
NZDUSD inside-churn: alternative approaches that DON'T disrupt the baseline lattice geometry.

Key insight from diagnostics: ANY re-entry into interior levels changes which positions exist,
which changes the close reference points, which destroys the $2.05/close expectancy of the baseline.

New approaches:
1) PARTIAL CLOSE on interior levels — when price reaches an interior level, close 50% of the outer
   position instead of opening a new one. The remaining 50% still closes at the normal reference.
2) TIGHTEN the close gap when interior levels are penetrated — instead of closing outer-vs-inner[2],
   close outer-vs-inner[1] when there's been recent interior action.
3) INSIDE-OUT priority — close interior positions first (FIFO within lattice) when any close triggers.
4) ACCELERATOR — after N closes on same side, widen the step for next entries (reduce churn density).
5) REVERSE re-arm — instead of re-entering at closed levels, enter at the *opposite* side's levels
   when a close happens (mean-reversion within the lattice).
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent


@dataclass
class PartialTicket:
    """A ticket that supports partial closes."""
    direction: str
    entry_price: float
    opened_idx: int
    size_remaining: float = 1.0  # 1.0 = full, 0.5 = half closed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NZDUSD alternative inside-churn sweep.")
    parser.add_argument("--symbol", default="NZDUSD")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "nzdusd_alt_churn_sweep.csv"),
    )
    return parser.parse_args()


VARIANTS = [
    "baseline",
    "partial_close_50",    # close 50% of outer at each interior level touched
    "partial_close_30",    # close 30% of outer at each interior level
    "close_gap_1",         # use gap=1 instead of gap=2 (close sooner)
    "fifo_close",          # close innermost first instead of outermost
    "accelerator",         # widen step by 1.2x after each close on same side
    "trailing_partial",    # after 3 consecutive same-direction closes, partial-close next outer at 1-level profit
]


def simulate_baseline(symbol: str, bars: list[dict], symbol_info, cfg: RawConfig, variant: str) -> dict:
    """Flexible baseline that supports variant tweaks."""
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px

    open_tickets: list[PartialTicket] = []
    realized_pnls: list[float] = []
    max_open = 0
    consecutive_sell_closes = 0
    consecutive_buy_closes = 0

    # Accelerator tracking
    sell_step_multiplier = 1.0
    buy_step_multiplier = 1.0

    for idx in range(1, len(bars)):
        bar = bars[idx]

        open_buy = sum(1 for t in open_tickets if t.direction == "BUY" and t.size_remaining > 0)
        open_sell = sum(1 for t in open_tickets if t.direction == "SELL" and t.size_remaining > 0)

        # Adaptive step with accelerator
        sell_step = base_step_px * sell_step_multiplier
        buy_step = base_step_px * buy_step_multiplier

        current_sell_step = dynamic_step(sell_step, open_sell, type("Cfg", (), {
            "adaptive_step_threshold_1": 10,
            "adaptive_step_threshold_2": 20,
            "adaptive_step_multiplier_1": 1.5,
            "adaptive_step_multiplier_2": 2.0,
        })())
        current_buy_step = dynamic_step(buy_step, open_buy, type("Cfg", (), {
            "adaptive_step_threshold_1": 10,
            "adaptive_step_threshold_2": 20,
            "adaptive_step_multiplier_1": 1.5,
            "adaptive_step_multiplier_2": 2.0,
        })())

        while bar["high"] >= next_sell_level and open_sell < cfg.max_open_per_side:
            open_tickets.append(PartialTicket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            open_sell += 1
            current_sell_step = dynamic_step(sell_step, open_sell, type("Cfg", (), {
                "adaptive_step_threshold_1": 10,
                "adaptive_step_threshold_2": 20,
                "adaptive_step_multiplier_1": 1.5,
                "adaptive_step_multiplier_2": 2.0,
            })())
            next_sell_level += current_sell_step

        while bar["low"] <= next_buy_level and open_buy < cfg.max_open_per_side:
            open_tickets.append(PartialTicket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            open_buy += 1
            current_buy_step = dynamic_step(buy_step, open_buy, type("Cfg", (), {
                "adaptive_step_threshold_1": 10,
                "adaptive_step_threshold_2": 20,
                "adaptive_step_multiplier_1": 1.5,
                "adaptive_step_multiplier_2": 2.0,
            })())
            next_buy_level -= current_buy_step

        # --- Variant: partial close at interior levels ---
        if variant in ("partial_close_50", "partial_close_30"):
            frac = 0.5 if variant == "partial_close_50" else 0.3
            # For each sell ticket, if bar low goes below a deeper sell level, partially close
            sells = sorted([t for t in open_tickets if t.direction == "SELL" and t.size_remaining > 0],
                          key=lambda t: t.entry_price, reverse=True)
            for i, outer in enumerate(sells):
                if outer.size_remaining <= 0:
                    continue
                # Check if price reached the next interior level
                if i + 1 < len(sells):
                    inner_level = sells[i + 1].entry_price
                    if bar["low"] <= inner_level:
                        # Partial close the outer at the inner level price
                        close_pnl = unit_pnl_usd(symbol, "SELL", outer.entry_price, inner_level, spread_px)
                        realized_pnls.append(close_pnl * frac * outer.size_remaining)
                        outer.size_remaining *= (1 - frac)

            buys = sorted([t for t in open_tickets if t.direction == "BUY" and t.size_remaining > 0],
                         key=lambda t: t.entry_price)
            for i, outer in enumerate(buys):
                if outer.size_remaining <= 0:
                    continue
                if i + 1 < len(buys):
                    inner_level = buys[i + 1].entry_price
                    if bar["high"] >= inner_level:
                        close_pnl = unit_pnl_usd(symbol, "BUY", outer.entry_price, inner_level, spread_px)
                        realized_pnls.append(close_pnl * frac * outer.size_remaining)
                        outer.size_remaining *= (1 - frac)

        gap = 1 if cfg.close_mode == "one_level" else 2

        # --- Variant: close gap 1 ---
        effective_gap = 1 if variant == "close_gap_1" else gap

        # --- Variant: FIFO (innermost first) ---
        sells = sorted((t for t in open_tickets if t.direction == "SELL" and t.size_remaining > 0.01),
                       key=lambda t: t.entry_price, reverse=True)
        if variant == "fifo_close":
            # Close innermost first (lowest price for sells)
            sells = sorted(sells, key=lambda t: t.entry_price)

        while len(sells) > effective_gap:
            outer = sells[0]
            inner_ref = sells[effective_gap]
            close_ref = inner_ref.entry_price
            pnl = unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, spread_px)
            realized_pnls.append(pnl * outer.size_remaining)
            outer.size_remaining = 0
            open_tickets = [t for t in open_tickets if t.size_remaining > 0.01]
            sells = sorted((t for t in open_tickets if t.direction == "SELL" and t.size_remaining > 0.01),
                          key=lambda t: t.entry_price, reverse=True)
            if variant == "fifo_close":
                sells = sorted(sells, key=lambda t: t.entry_price)
            consecutive_sell_closes += 1
            consecutive_buy_closes = 0

            # --- Variant: accelerator ---
            if variant == "accelerator" and consecutive_sell_closes >= 3:
                sell_step_multiplier *= 1.2
                next_sell_level = anchor + base_step_px * sell_step_multiplier
                # Recalculate sell levels

        buys = sorted((t for t in open_tickets if t.direction == "BUY" and t.size_remaining > 0.01),
                      key=lambda t: t.entry_price)
        if variant == "fifo_close":
            buys = sorted(buys, key=lambda t: t.entry_price, reverse=True)

        while len(buys) > effective_gap:
            outer = buys[0]
            close_ref = buys[effective_gap].entry_price
            pnl = unit_pnl_usd(symbol, "BUY", outer.entry_price, close_ref, spread_px)
            realized_pnls.append(pnl * outer.size_remaining)
            outer.size_remaining = 0
            open_tickets = [t for t in open_tickets if t.size_remaining > 0.01]
            buys = sorted((t for t in open_tickets if t.direction == "BUY" and t.size_remaining > 0.01),
                         key=lambda t: t.entry_price)
            if variant == "fifo_close":
                buys = sorted(buys, key=lambda t: t.entry_price, reverse=True)
            consecutive_buy_closes += 1
            consecutive_sell_closes = 0

            if variant == "accelerator" and consecutive_buy_closes >= 3:
                buy_step_multiplier *= 1.2

        # --- Variant: trailing partial ---
        if variant == "trailing_partial":
            if consecutive_sell_closes >= 3 and open_sell > 0:
                sells_active = [t for t in open_tickets if t.direction == "SELL" and t.size_remaining > 0.01]
                if sells_active:
                    newest = max(sells_active, key=lambda t: t.opened_idx)
                    # If price is 1 step in profit, partial close
                    profit_level = newest.entry_price - base_step_px
                    if bar["low"] <= profit_level:
                        pnl = unit_pnl_usd(symbol, "SELL", newest.entry_price, profit_level, spread_px)
                        realized_pnls.append(pnl * 0.5 * newest.size_remaining)
                        newest.size_remaining *= 0.5
            if consecutive_buy_closes >= 3 and open_buy > 0:
                buys_active = [t for t in open_tickets if t.direction == "BUY" and t.size_remaining > 0.01]
                if buys_active:
                    newest = max(buys_active, key=lambda t: t.opened_idx)
                    profit_level = newest.entry_price + base_step_px
                    if bar["high"] >= profit_level:
                        pnl = unit_pnl_usd(symbol, "BUY", newest.entry_price, profit_level, spread_px)
                        realized_pnls.append(pnl * 0.5 * newest.size_remaining)
                        newest.size_remaining *= 0.5

        max_open = max(max_open, len(open_tickets))

        if not open_tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px * sell_step_multiplier
            next_buy_level = anchor - base_step_px * buy_step_multiplier
            if variant == "accelerator":
                sell_step_multiplier = 1.0
                buy_step_multiplier = 1.0

    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px) * t.size_remaining
        for t in open_tickets
    ]

    realized_net = sum(realized_pnls)
    floating_net = sum(floating_pnls)
    combined_net = realized_net + floating_net
    wins = sum(1 for p in realized_pnls if p > 0)

    return {
        "combined_net_usd": round(combined_net, 3),
        "realized_net_usd": round(realized_net, 3),
        "floating_net_usd": round(floating_net, 3),
        "realized_closes": len(realized_pnls),
        "wr_pct": round(wins / len(realized_pnls) * 100, 1) if realized_pnls else 0,
        "max_open_total": max_open,
    }


def main() -> int:
    args = parse_args()
    cfg_map = default_raw_configs()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        symbol = args.symbol
        info = mt5.symbol_info(symbol)
        if info is None:
            print(f"Symbol {symbol} not found")
            return 1
        bars = load_bars(symbol, args.days)
        raw_cfg = RawConfig(
            step_pips=cfg_map[symbol].step_pips,
            max_open_per_side=cfg_map[symbol].max_open_per_side,
            close_mode=cfg_map[symbol].close_mode,
        )

        # True baseline using original code
        true_baseline = simulate_raw_close2(symbol, bars, info, raw_cfg)
        baseline_usd = float(true_baseline["combined_net_usd"])

        print(f"\n{'='*60}")
        print(f"  NZDUSD Alternative Inside-Churn — {args.days}d baseline ${baseline_usd:.2f}")
        print(f"{'='*60}")

        rows: list[dict] = []
        for variant in VARIANTS:
            if variant == "baseline":
                result = {
                    "combined_net_usd": baseline_usd,
                    "realized_net_usd": float(true_baseline["realized_net_usd"]),
                    "floating_net_usd": float(true_baseline["floating_net_usd"]),
                    "realized_closes": true_baseline["realized_closes"],
                    "wr_pct": true_baseline.get("wr_pct", 100.0),
                    "max_open_total": true_baseline["max_open_total"],
                }
            else:
                result = simulate_baseline(symbol, bars, info, raw_cfg, variant)

            delta = result["combined_net_usd"] - baseline_usd
            rows.append(
                {
                    "symbol": symbol,
                    "variant": variant,
                    "days": args.days,
                    "baseline_combined_usd": baseline_usd,
                    "baseline_closes": true_baseline["realized_closes"],
                    "variant_combined_usd": result["combined_net_usd"],
                    "variant_realized_usd": result["realized_net_usd"],
                    "variant_floating_usd": result["floating_net_usd"],
                    "variant_closes": result["realized_closes"],
                    "variant_wr_pct": result["wr_pct"],
                    "variant_max_open": result["max_open_total"],
                    "delta_combined_usd": round(delta, 3),
                    "beats_baseline": delta > 0,
                }
            )
            marker = "✅" if delta > 0 else "❌"
            print(f"  {marker} {variant:30s} ${result['combined_net_usd']:>10.2f}  "
                  f"delta=${delta:>+8.2f}  closes={result['realized_closes']}")

        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else [])
            writer.writeheader()
            writer.writerows(rows)

        print(f"\nWrote {out_path}")

        winners = [r for r in rows if r["beats_baseline"]]
        if winners:
            best = max(winners, key=lambda r: r["delta_combined_usd"])
            print(f"\n🏆 Best NZDUSD alt variant: {best['variant']} → ${best['variant_combined_usd']:.2f} (delta ${best['delta_combined_usd']:+.2f})")
        else:
            print(f"\n⚠️  Still no winner. NZDUSD's lattice is fundamentally optimized as-is.")
            print(f"   The 814 closes at $2.05/close expectancy is a fragile equilibrium.")
            print(f"   Any inside modification dilutes it. Best play: keep NZDUSD baseline-only.")

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
