#!/usr/bin/env python3
"""
VWAP-anchored lattice — anchor to rolling VWAP instead of first bar close.

Current: Lattice anchor = first bar close. Levels spread symmetrically from there.
New: Anchor = N-bar VWAP (volume-weighted average price).

Hypothesis: VWAP is the "fair value" of the recent market. Lattices centered on VWAP
have better entry geometry because they're aligned with where the market thinks price
should be, not an arbitrary starting point.

Also tests: dynamic anchor reset when price moves X pips from VWAP anchor.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SYMBOLS = ["GBPUSD", "EURUSD", "USDJPY", "NZDUSD"]


@dataclass(frozen=True)
class Variant:
    name: str
    anchor_mode: str = "first_close"  # first_close, vwap_20, vwap_50, vwap_100
    anchor_reset_pips: float = 0.0    # reset anchor when price moves this far from it (0 = never)
    momentum_gate: bool = False
    close_alpha: float = 0.0
    cooldown_bars: int = 0


VARIANTS = [
    # Baseline re-confirmation
    Variant(name="baseline"),

    # VWAP-anchored variants
    Variant(name="vwap_20", anchor_mode="vwap_20"),
    Variant(name="vwap_50", anchor_mode="vwap_50"),
    Variant(name="vwap_100", anchor_mode="vwap_100"),

    # VWAP + anchor reset when price moves away
    Variant(name="vwap50_reset5px", anchor_mode="vwap_50", anchor_reset_pips=5.0),
    Variant(name="vwap50_reset10px", anchor_mode="vwap_50", anchor_reset_pips=10.0),

    # VWAP + momentum + alpha (compounding on $94K)
    Variant(name="vwap50_momentum_alpha50", anchor_mode="vwap_50", momentum_gate=True, close_alpha=0.50),
    Variant(name="vwap50_cool12_alpha50", anchor_mode="vwap_50", cooldown_bars=12, close_alpha=0.50),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep VWAP-anchored lattice variants.")
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--output-csv", default=str(ROOT / "reports" / "vwap_anchored_sweep.csv"))
    return parser.parse_args()


def compute_vwap(bars: list[dict], idx: int, window: int) -> float:
    """Compute N-bar VWAP ending at idx (exclusive)."""
    start = max(0, idx - window)
    if start >= idx:
        return bars[idx - 1]["close"]
    total_vp = 0.0
    total_v = 0.0
    for i in range(start, idx):
        typical = (bars[i]["high"] + bars[i]["low"] + bars[i]["close"]) / 3.0
        vol = bars[i].get("tick_volume", 1)
        total_vp += typical * vol
        total_v += vol
    return total_vp / total_v if total_v > 0 else bars[idx - 1]["close"]


@dataclass
class RearmToken:
    direction: str
    level: float
    level_idx: int
    armed: bool = False
    cooldown_until: int = 0


def _side_count(tickets: list[Ticket], direction: str) -> int:
    return sum(1 for t in tickets if t.direction == direction)


def _make_adapt_cfg():
    return type("Cfg", (), {
        "adaptive_step_threshold_1": 10,
        "adaptive_step_threshold_2": 20,
        "adaptive_step_multiplier_1": 1.5,
        "adaptive_step_multiplier_2": 2.0,
    })()


def _interpolate_close_ref(level_price, bar_extreme, direction, alpha):
    return level_price + alpha * (bar_extreme - level_price)


def _update_token_arming(tokens, bar, base_step_px, current_bar):
    for token in tokens:
        if token.armed:
            continue
        if token.direction == "SELL":
            if bar["low"] <= token.level - base_step_px:
                token.armed = True
        else:
            if bar["high"] >= token.level + base_step_px:
                token.armed = True


def simulate_vwap_variant(symbol, bars, symbol_info, cfg, variant):
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)

    if variant.anchor_mode == "first_close":
        # Use the canonical raw close2 simulation
        raw_cfg = RawConfig(
            step_pips=cfg.step_pips,
            max_open_per_side=cfg.max_open_per_side,
            close_mode=cfg.close_mode,
        )
        return simulate_raw_close2(symbol, bars, symbol_info, raw_cfg)

    base_step_px = cfg.step_pips * pip_size
    adapt_cfg = _make_adapt_cfg()

    # Initial anchor
    if variant.anchor_mode.startswith("vwap_"):
        window = int(variant.anchor_mode.split("_")[1])
    else:
        window = 20

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px

    open_tickets: list[Ticket] = []
    realized_pnls: list[float] = []
    rearm_tokens: list[RearmToken] = []
    rearm_opens = 0
    max_open = 0

    level_reuse: dict[float, int] = {}

    for idx in range(1, len(bars)):
        bar = bars[idx]

        # Update anchor if VWAP mode
        if variant.anchor_mode.startswith("vwap_"):
            vwap = compute_vwap(bars, idx, window)
            # Only reset when no open tickets, to avoid mid-lattice disruption
            if not open_tickets:
                anchor = vwap
                next_sell_level = anchor + base_step_px
                next_buy_level = anchor - base_step_px
            elif variant.anchor_reset_pips > 0:
                reset_px = variant.anchor_reset_pips * pip_size
                if abs(bar["close"] - anchor) > reset_px:
                    # Close all open positions at current price before resetting
                    for t in open_tickets:
                        pnl = unit_pnl_usd(symbol, t.direction, t.entry_price, bar["close"], spread_px)
                        realized_pnls.append(pnl)
                    anchor = vwap
                    next_sell_level = anchor + base_step_px
                    next_buy_level = anchor - base_step_px
                    open_tickets = []
                    rearm_tokens = []
                    level_reuse.clear()

        # Update token arming
        for token in rearm_tokens:
            if token.armed:
                continue
            if variant.cooldown_bars > 0 and idx < token.cooldown_until:
                continue
            if token.direction == "SELL":
                if bar["low"] <= token.level - base_step_px:
                    token.armed = True
            else:
                if bar["high"] >= token.level + base_step_px:
                    token.armed = True

        open_buy = _side_count(open_tickets, "BUY")
        open_sell = _side_count(open_tickets, "SELL")

        current_sell_step = dynamic_step(base_step_px, open_sell, adapt_cfg)
        current_buy_step = dynamic_step(base_step_px, open_buy, adapt_cfg)

        while bar["high"] >= next_sell_level and open_sell < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            open_sell += 1
            current_sell_step = dynamic_step(base_step_px, open_sell, adapt_cfg)
            next_sell_level += current_sell_step

        while bar["low"] <= next_buy_level and open_buy < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            open_buy += 1
            current_buy_step = dynamic_step(base_step_px, open_buy, adapt_cfg)
            next_buy_level -= current_buy_step

        # Consume rearm
        open_sell = _side_count(open_tickets, "SELL")
        open_buy = _side_count(open_tickets, "BUY")
        for token in list(rearm_tokens):
            if not token.armed:
                continue
            if token.direction == "SELL" and open_sell < cfg.max_open_per_side and bar["high"] >= token.level:
                open_tickets.append(Ticket(direction="SELL", entry_price=token.level, opened_idx=idx))
                rearm_tokens.remove(token)
                open_sell += 1
                rearm_opens += 1
            elif token.direction == "BUY" and open_buy < cfg.max_open_per_side and bar["low"] <= token.level:
                open_tickets.append(Ticket(direction="BUY", entry_price=token.level, opened_idx=idx))
                rearm_tokens.remove(token)
                open_buy += 1
                rearm_opens += 1

        gap = 2

        # Momentum gate check
        def momentum_ok(direction, entry_price):
            if not variant.momentum_gate:
                return True
            if direction == "SELL":
                return bar["close"] < entry_price
            return bar["close"] > entry_price

        # Close
        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > gap and bar["low"] <= sells[gap].entry_price:
            outer = sells[0]
            close_ref = _interpolate_close_ref(sells[gap].entry_price, bar["low"], "SELL", variant.close_alpha)
            pnl = unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, spread_px)
            realized_pnls.append(pnl)
            open_tickets.remove(outer)
            level_idx = int(round((outer.entry_price - anchor) / base_step_px))
            if level_idx >= 2 and momentum_ok("SELL", outer.entry_price):
                cooldown_end = idx + variant.cooldown_bars if variant.cooldown_bars > 0 else 0
                rearm_tokens.append(RearmToken(
                    direction="SELL", level=outer.entry_price, level_idx=level_idx,
                    cooldown_until=cooldown_end,
                ))
                level_reuse[outer.entry_price] = level_reuse.get(outer.entry_price, 0) + 1
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > gap and bar["high"] >= buys[gap].entry_price:
            outer = buys[0]
            close_ref = _interpolate_close_ref(buys[gap].entry_price, bar["high"], "BUY", variant.close_alpha)
            pnl = unit_pnl_usd(symbol, "BUY", outer.entry_price, close_ref, spread_px)
            realized_pnls.append(pnl)
            open_tickets.remove(outer)
            level_idx = int(round((anchor - outer.entry_price) / base_step_px))
            if level_idx >= 2 and momentum_ok("BUY", outer.entry_price):
                cooldown_end = idx + variant.cooldown_bars if variant.cooldown_bars > 0 else 0
                rearm_tokens.append(RearmToken(
                    direction="BUY", level=outer.entry_price, level_idx=level_idx,
                    cooldown_until=cooldown_end,
                ))
                level_reuse[outer.entry_price] = level_reuse.get(outer.entry_price, 0) + 1
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        max_open = max(max_open, len(open_tickets))

        if not open_tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px
            rearm_tokens = []
            level_reuse.clear()

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
        "rearm_opens": rearm_opens,
        "max_open_total": max_open,
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

            baseline = simulate_vwap_variant(symbol, bars, info, raw_cfg, Variant(name="baseline"))
            if not baseline:
                continue
            baseline_total += float(baseline["combined_net_usd"])

            for variant in VARIANTS:
                result = simulate_vwap_variant(symbol, bars, info, raw_cfg, variant)
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
                    "variant_max_open": result.get("max_open_total", ""),
                    "variant_rearm_opens": result.get("rearm_opens", ""),
                    "delta_combined_usd": round(result["combined_net_usd"] - baseline["combined_net_usd"], 3),
                })

        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "symbol", "variant", "days", "baseline_combined_usd", "baseline_closes",
            "variant_combined_usd", "variant_realized_usd", "variant_floating_usd",
            "variant_closes", "variant_max_open", "variant_rearm_opens", "delta_combined_usd",
        ]
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        summary_path = out_path.with_name("vwap_anchored_summary.csv")
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
        print(f"\n{'Variant':<35} {'Total':>12} {'Delta':>12} {'%Over':>8} {'GBPUSD':>10} {'EURUSD':>10} {'USDJPY':>10} {'NZDUSD':>10}")
        print("-" * 115)
        for v in VARIANTS:
            gbp = variant_by_symbol.get(v.name, {}).get("GBPUSD", 0)
            eur = variant_by_symbol.get(v.name, {}).get("EURUSD", 0)
            usdjpy = variant_by_symbol.get(v.name, {}).get("USDJPY", 0)
            nzd = variant_by_symbol.get(v.name, {}).get("NZDUSD", 0)
            delta = variant_totals[v.name] - baseline_total
            pct = (variant_totals[v.name] / baseline_total - 1) * 100 if baseline_total else 0
            print(f"{v.name:<35} {variant_totals[v.name]:>12.2f} {delta:>12.2f} {pct:>7.1f}% {gbp:>10.2f} {eur:>10.2f} {usdjpy:>10.2f} {nzd:>10.2f}")

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
