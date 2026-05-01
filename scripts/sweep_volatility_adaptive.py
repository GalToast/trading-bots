#!/usr/bin/env python3
"""
Volatility-adaptive step sizing — dynamic step_pips based on rolling ATR.

Current: Fixed step_pips regardless of market regime.
New: step_pips = atr_pips * step_atr_ratio

Hypothesis: Scaling steps to ATR maintains consistent entry density
across volatility regimes, capturing more profit in both quiet and wild markets.

Also tests: asymmetric direction configs (different step_pips for SELL vs BUY)
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SYMBOLS = ["GBPUSD", "EURUSD", "USDJPY", "NZDUSD"]


@dataclass(frozen=True)
class Variant:
    name: str
    # Base step_pips (used when ATR matches the reference ATR)
    base_step_pips: float | None = None  # None = use default from cfg_map
    # Step as fraction of ATR (overrides base_step_pips if set)
    step_atr_ratio: float = 0.0
    # ATR lookback window
    atr_window: int = 14
    # Asymmetric: separate BUY step multiplier (SELL uses base or atr_ratio)
    buy_step_mult: float = 1.0
    # Asymmetric: separate SELL step multiplier
    sell_step_mult: float = 1.0
    # Gap override per direction
    sell_gap: int = 2
    buy_gap: int = 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep volatility-adaptive step sizing.")
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--output-csv", default=str(ROOT / "reports" / "volatility_adaptive_sweep.csv"))
    return parser.parse_args()


VARIANTS = [
    # Baseline: fixed steps (current default)
    Variant(name="baseline_fixed"),

    # ATR-based: step = ATR * ratio
    Variant(name="atr_040", step_atr_ratio=0.40),
    Variant(name="atr_060", step_atr_ratio=0.60),
    Variant(name="atr_080", step_atr_ratio=0.80),
    Variant(name="atr_100", step_atr_ratio=1.00),
    Variant(name="atr_120", step_atr_ratio=1.20),

    # Asymmetric direction: wider SELL steps (downtrends faster), tighter BUY
    Variant(name="asym_sell120_buy080", step_atr_ratio=0.60, sell_step_mult=1.2, buy_step_mult=0.8),
    Variant(name="asym_sell150_buy067", step_atr_ratio=0.60, sell_step_mult=1.5, buy_step_mult=0.67),

    # Gap=1 for more closes (ranging market specialist)
    Variant(name="gap1_fixed", base_step_pips=None, sell_gap=1, buy_gap=1),

    # Combined: atr + asymmetric + gap
    Variant(name="atr060_asym_gap1", step_atr_ratio=0.60, sell_step_mult=1.2, buy_step_mult=0.8, sell_gap=1, buy_gap=1),
]


def compute_atr(bars: list[dict], idx: int, window: int) -> float:
    if idx < window + 1:
        return 0.0
    trs = []
    for i in range(idx - window, idx):
        h = bars[i]["high"]
        l = bars[i]["low"]
        pc = bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def simulate_vol_adaptive(symbol: str, bars: list[dict], symbol_info, cfg: RawConfig, variant: Variant) -> dict:
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)

    # Determine base step in price
    if variant.base_step_pips is not None:
        base_step_px = variant.base_step_pips * pip_size
    elif cfg.step_pips > 0:
        base_step_px = cfg.step_pips * pip_size
    else:
        base_step_px = 2.0 * pip_size  # fallback

    atr_window = variant.atr_window
    reference_atr = compute_atr(bars, min(100, len(bars) - 1), atr_window)
    if reference_atr <= 0:
        reference_atr = base_step_px  # fallback to avoid division by zero

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px

    adapt_cfg = type("Cfg", (), {
        "adaptive_step_threshold_1": 10,
        "adaptive_step_threshold_2": 20,
        "adaptive_step_multiplier_1": 1.5,
        "adaptive_step_multiplier_2": 2.0,
    })()

    open_tickets: list[Ticket] = []
    realized_pnls: list[float] = []
    max_open = 0
    max_open_buy = 0
    max_open_sell = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]

        # Compute current ATR-based step
        current_atr = compute_atr(bars, idx, atr_window)
        if variant.step_atr_ratio > 0 and current_atr > 0:
            atr_step_px = current_atr * variant.step_atr_ratio
        else:
            atr_step_px = base_step_px

        # Apply asymmetric multipliers
        sell_step_px = atr_step_px * variant.sell_step_mult
        buy_step_px = atr_step_px * variant.buy_step_mult

        # Adaptive step widening for large open counts
        open_sell = sum(1 for t in open_tickets if t.direction == "SELL")
        open_buy = sum(1 for t in open_tickets if t.direction == "BUY")

        current_sell_step = dynamic_step(sell_step_px, open_sell, adapt_cfg)
        current_buy_step = dynamic_step(buy_step_px, open_buy, adapt_cfg)

        while bar["high"] >= next_sell_level and open_sell < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            open_sell += 1
            current_sell_step = dynamic_step(sell_step_px, open_sell, adapt_cfg)
            next_sell_level += current_sell_step

        while bar["low"] <= next_buy_level and open_buy < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            open_buy += 1
            current_buy_step = dynamic_step(buy_step_px, open_buy, adapt_cfg)
            next_buy_level -= current_buy_step

        # Close logic with per-direction gap
        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        sell_gap = variant.sell_gap
        while len(sells) > sell_gap and bar["low"] <= sells[sell_gap].entry_price:
            outer = sells[0]
            close_ref = sells[sell_gap].entry_price
            realized_pnls.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, spread_px))
            open_tickets.remove(outer)
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        buy_gap = variant.buy_gap
        while len(buys) > buy_gap and bar["high"] >= buys[buy_gap].entry_price:
            outer = buys[0]
            close_ref = buys[buy_gap].entry_price
            realized_pnls.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, close_ref, spread_px))
            open_tickets.remove(outer)
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        max_open = max(max_open, len(open_tickets))
        max_open_buy = max(max_open_buy, sum(1 for t in open_tickets if t.direction == "BUY"))
        max_open_sell = max(max_open_sell, sum(1 for t in open_tickets if t.direction == "SELL"))

        if not open_tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px

    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px)
        for t in open_tickets
    ]

    realized_net = sum(realized_pnls)
    floating_net = sum(floating_pnls)
    return {
        "combined_net_usd": round(realized_net + floating_net, 3),
        "realized_net_usd": round(realized_net, 3),
        "floating_net_usd": round(floating_net, 3),
        "realized_closes": len(realized_pnls),
        "max_open_total": max_open,
        "max_open_sell": max_open_sell,
        "max_open_buy": max_open_buy,
    }


def main() -> int:
    args = parse_args()
    cfg_map = default_raw_configs()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        rows = []
        baseline_total = 0.0
        variant_totals: dict[str, float] = {v.name: 0.0 for v in VARIANTS}
        variant_by_symbol: dict[str, dict[str, float]] = {}

        for symbol in args.symbols:
            if symbol not in cfg_map:
                continue
            info = mt5.symbol_info(symbol)
            if info is None:
                continue
            bars = load_bars(symbol, args.days)
            raw_cfg = RawConfig(
                step_pips=cfg_map[symbol].step_pips,
                max_open_per_side=cfg_map[symbol].max_open_per_side,
                close_mode=cfg_map[symbol].close_mode,
            )

            # Run baseline
            baseline = simulate_vol_adaptive(symbol, bars, info, raw_cfg, Variant(name="baseline"))
            if not baseline:
                continue
            baseline_total += float(baseline["combined_net_usd"])

            for variant in VARIANTS:
                result = simulate_vol_adaptive(symbol, bars, info, raw_cfg, variant)
                variant_totals[variant.name] += float(result["combined_net_usd"])
                if variant.name not in variant_by_symbol:
                    variant_by_symbol[variant.name] = {}
                variant_by_symbol[variant.name][symbol] = float(result["combined_net_usd"])

                rows.append({
                    "symbol": symbol,
                    "variant": variant.name,
                    "days": args.days,
                    "baseline_combined_usd": baseline["combined_net_usd"],
                    "baseline_closes": baseline["realized_closes"],
                    "variant_combined_usd": result["combined_net_usd"],
                    "variant_realized_usd": result["realized_net_usd"],
                    "variant_floating_usd": result["floating_net_usd"],
                    "variant_closes": result["realized_closes"],
                    "variant_max_open": result["max_open_total"],
                    "delta_combined_usd": round(result["combined_net_usd"] - baseline["combined_net_usd"], 3),
                })

        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "symbol", "variant", "days", "baseline_combined_usd", "baseline_closes",
            "variant_combined_usd", "variant_realized_usd", "variant_floating_usd",
            "variant_closes", "variant_max_open", "delta_combined_usd",
        ]
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        # Summary
        summary_path = out_path.with_name("volatility_adaptive_summary.csv")
        with summary_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "variant", "baseline_total_usd", "variant_total_usd", "delta_total_usd",
                "GBPUSD", "EURUSD", "USDJPY", "NZDUSD",
            ])
            writer.writeheader()
            for v in VARIANTS:
                gbp = variant_by_symbol.get(v.name, {}).get("GBPUSD", 0)
                eur = variant_by_symbol.get(v.name, {}).get("EURUSD", 0)
                usdjpy = variant_by_symbol.get(v.name, {}).get("USDJPY", 0)
                nzd = variant_by_symbol.get(v.name, {}).get("NZDUSD", 0)
                writer.writerow({
                    "variant": v.name,
                    "baseline_total_usd": round(baseline_total, 3),
                    "variant_total_usd": round(variant_totals[v.name], 3),
                    "delta_total_usd": round(variant_totals[v.name] - baseline_total, 3),
                    "GBPUSD": round(gbp, 3),
                    "EURUSD": round(eur, 3),
                    "USDJPY": round(usdjpy, 3),
                    "NZDUSD": round(nzd, 3),
                })

        print(f"Wrote {out_path}")
        print(f"\n{'Variant':<30} {'Total':>12} {'Delta':>12} {'%Over':>8} {'GBPUSD':>10} {'EURUSD':>10} {'USDJPY':>10} {'NZDUSD':>10}")
        print("-" * 110)
        for v in VARIANTS:
            gbp = variant_by_symbol.get(v.name, {}).get("GBPUSD", 0)
            eur = variant_by_symbol.get(v.name, {}).get("EURUSD", 0)
            usdjpy = variant_by_symbol.get(v.name, {}).get("USDJPY", 0)
            nzd = variant_by_symbol.get(v.name, {}).get("NZDUSD", 0)
            delta = variant_totals[v.name] - baseline_total
            pct = (variant_totals[v.name] / baseline_total - 1) * 100 if baseline_total else 0
            print(f"{v.name:<30} {variant_totals[v.name]:>12.2f} {delta:>12.2f} {pct:>7.1f}% {gbp:>10.2f} {eur:>10.2f} {usdjpy:>10.2f} {nzd:>10.2f}")

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
