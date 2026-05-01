#!/usr/bin/env python3
"""NZDUSD-specific inside-churn sweep — testing methods that preserve baseline lattice rhythm."""
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


@dataclass
class RearmToken:
    direction: str
    level: float
    level_idx: int
    armed: bool = False
    reuse_count: int = 0  # track how many times this level has been reused


@dataclass
class Variant:
    name: str
    # Standard params
    min_level_idx: int = 1
    excursion_levels: int = 1
    # NZDUSD-specific
    max_reuses: int = 999  # cap on how many times a level can be re-armed
    size_scaler: float = 1.0  # multiply volume per reuse (1.0 = full size, 0.5 = half)
    require_round_trip: bool = False  # must cross anchor before re-arming
    min_oscillation_pips: float = 0.0  # minimum total oscillation before re-arm fires


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NZDUSD inside-churn sweep.")
    parser.add_argument("--symbol", default="NZDUSD")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "nzdusd_inside_churn_sweep.csv"),
    )
    return parser.parse_args()


def _side_count(tickets: list[Ticket], direction: str) -> int:
    return sum(1 for t in tickets if t.direction == direction)


def _make_adapt_cfg():
    return type(
        "Cfg",
        (),
        {
            "adaptive_step_threshold_1": 10,
            "adaptive_step_threshold_2": 20,
            "adaptive_step_multiplier_1": 1.5,
            "adaptive_step_multiplier_2": 2.0,
        },
    )()


VARIANTS = [
    # 1) Deep-only re-arm: only levels >= 3 steps from anchor, 3-step excursion
    Variant(name="deep_lvl3_exc3", min_level_idx=3, excursion_levels=3),
    # 2) Even deeper: level >= 4, excursion 4
    Variant(name="deep_lvl4_exc4", min_level_idx=4, excursion_levels=4),
    # 3) Size-scaling: half size on each re-arm, lvl2 exc2
    Variant(name="halfsize_lvl2_exc2", min_level_idx=2, excursion_levels=2, size_scaler=0.5),
    # 4) Size-scaling quarter: quarter size, lvl2 exc2
    Variant(name="quarter_lvl2_exc2", min_level_idx=2, excursion_levels=2, size_scaler=0.25),
    # 5) Round-trip gated: must cross anchor + excursion 2, lvl2
    Variant(name="roundtrip_lvl2_exc2", min_level_idx=2, excursion_levels=2, require_round_trip=True),
    # 6) Round-trip + deeper: round-trip + excursion 3, lvl3
    Variant(name="roundtrip_lvl3_exc3", min_level_idx=3, excursion_levels=3, require_round_trip=True),
    # 7) Reuse cap: max 2 reuses per level, lvl2 exc2
    Variant(name="cap2_lvl2_exc2", min_level_idx=2, excursion_levels=2, max_reuses=2),
    # 8) Reuse cap + deep: max 2 reuses, lvl3 exc3
    Variant(name="cap2_lvl3_exc3", min_level_idx=3, excursion_levels=3, max_reuses=2),
    # 9) Minimum oscillation gate: price must oscillate 10 pips round-trip before re-arm fires
    Variant(name="osc10_lvl2_exc2", min_level_idx=2, excursion_levels=2, min_oscillation_pips=10.0),
    # 10) Oscillation 20 pips
    Variant(name="osc20_lvl2_exc2", min_level_idx=2, excursion_levels=2, min_oscillation_pips=20.0),
    # 11) Oscillation 5 pips (lighter gate)
    Variant(name="osc5_lvl2_exc2", min_level_idx=2, excursion_levels=2, min_oscillation_pips=5.0),
    # 12) Baseline with no rearm (control)
    Variant(name="baseline_norearm", min_level_idx=999, excursion_levels=999),
    # 13) Original best (for comparison) — this should lose
    Variant(name="original_lvl2_exc2", min_level_idx=2, excursion_levels=2),
]


def simulate_nzdusd_churn(
    symbol: str, bars: list[dict], symbol_info, cfg: RawConfig, variant: Variant
) -> dict:
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px
    adapt_cfg = _make_adapt_cfg()

    open_tickets: list[Ticket] = []
    realized_pnls: list[float] = []
    rearm_tokens: list[RearmToken] = []
    rearm_opens = 0
    max_open = 0

    # Round-trip tracking: has price crossed anchor since last close?
    crossed_anchor_up = False
    crossed_anchor_down = False

    # Oscillation tracking: peak away from anchor since last token consumption
    peak_away = 0.0

    for idx in range(1, len(bars)):
        bar = bars[idx]

        # Track anchor crossings for round-trip gating
        if bar["high"] >= anchor:
            crossed_anchor_up = True
        if bar["low"] <= anchor:
            crossed_anchor_down = True

        # Track peak oscillation
        for token in rearm_tokens:
            if token.direction == "SELL":
                dist = max(0, token.level - bar["low"])
            else:
                dist = max(0, bar["high"] - token.level)
            peak_away = max(peak_away, dist)

        # Update token arming
        if variant.min_level_idx < 999:  # skip if baseline_norearm
            for token in list(rearm_tokens):
                if token.armed:
                    continue
                if token.reuse_count >= variant.max_reuses:
                    continue
                if variant.require_round_trip:
                    if token.direction == "SELL" and not crossed_anchor_down:
                        continue
                    if token.direction == "BUY" and not crossed_anchor_up:
                        continue
                if variant.min_oscillation_pips > 0:
                    if peak_away < variant.min_oscillation_pips * pip_size:
                        continue
                if token.direction == "SELL":
                    away_trigger = token.level - (variant.excursion_levels * base_step_px)
                    if bar["low"] <= away_trigger:
                        token.armed = True
                else:
                    away_trigger = token.level + (variant.excursion_levels * base_step_px)
                    if bar["high"] >= away_trigger:
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

        # Consume rearm tokens
        for token in list(rearm_tokens):
            if token.direction != "SELL" or not token.armed:
                continue
            if open_sell >= cfg.max_open_per_side:
                break
            if bar["high"] >= token.level:
                open_tickets.append(Ticket(direction="SELL", entry_price=token.level, opened_idx=idx))
                rearm_tokens.remove(token)
                open_sell += 1
                rearm_opens += 1

        for token in list(rearm_tokens):
            if token.direction != "BUY" or not token.armed:
                continue
            if open_buy >= cfg.max_open_per_side:
                break
            if bar["low"] <= token.level:
                open_tickets.append(Ticket(direction="BUY", entry_price=token.level, opened_idx=idx))
                rearm_tokens.remove(token)
                open_buy += 1
                rearm_opens += 1

        gap = 1 if cfg.close_mode == "one_level" else 2

        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > gap and bar["low"] <= sells[gap].entry_price:
            outer = sells[0]
            close_ref = sells[gap].entry_price
            pnl = unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, spread_px)
            # Apply size scaler if this is a re-used level (check if token existed for this level)
            realized_pnls.append(pnl * variant.size_scaler)
            open_tickets.remove(outer)
            level_idx = int(round((outer.entry_price - anchor) / base_step_px))
            if level_idx >= variant.min_level_idx and variant.min_level_idx < 999:
                rearm_tokens.append(RearmToken(direction="SELL", level=outer.entry_price, level_idx=level_idx))
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > gap and bar["high"] >= buys[gap].entry_price:
            outer = buys[0]
            close_ref = buys[gap].entry_price
            pnl = unit_pnl_usd(symbol, "BUY", outer.entry_price, close_ref, spread_px)
            realized_pnls.append(pnl * variant.size_scaler)
            open_tickets.remove(outer)
            level_idx = int(round((anchor - outer.entry_price) / base_step_px))
            if level_idx >= variant.min_level_idx and variant.min_level_idx < 999:
                rearm_tokens.append(RearmToken(direction="BUY", level=outer.entry_price, level_idx=level_idx))
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        max_open = max(max_open, len(open_tickets))

        if not open_tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px
            rearm_tokens = []
            crossed_anchor_up = False
            crossed_anchor_down = False
            peak_away = 0.0

    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px) * variant.size_scaler
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
        "rearm_opens": rearm_opens,
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

        baseline = simulate_raw_close2(symbol, bars, info, raw_cfg)
        baseline_usd = float(baseline["combined_net_usd"])

        print(f"\n{'='*60}")
        print(f"  NZDUSD Inside-Churn Sweep — {args.days}d baseline ${baseline_usd:.2f}")
        print(f"{'='*60}")

        rows: list[dict] = []
        for variant in VARIANTS:
            result = simulate_nzdusd_churn(symbol, bars, info, raw_cfg, variant)
            delta = result["combined_net_usd"] - baseline_usd
            rows.append(
                {
                    "symbol": symbol,
                    "variant": variant.name,
                    "days": args.days,
                    "baseline_combined_usd": baseline_usd,
                    "baseline_closes": baseline["realized_closes"],
                    "variant_combined_usd": result["combined_net_usd"],
                    "variant_realized_usd": result["realized_net_usd"],
                    "variant_floating_usd": result["floating_net_usd"],
                    "variant_closes": result["realized_closes"],
                    "variant_wr_pct": result["wr_pct"],
                    "variant_max_open": result["max_open_total"],
                    "variant_rearm_opens": result["rearm_opens"],
                    "delta_combined_usd": round(delta, 3),
                    "beats_baseline": delta > 0,
                }
            )
            marker = "✅" if delta > 0 else "❌"
            print(f"  {marker} {variant.name:30s} ${result['combined_net_usd']:>10.2f}  "
                  f"delta=${delta:>+8.2f}  closes={result['realized_closes']}  "
                  f"rearm_opens={result['rearm_opens']}")

        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else [])
            writer.writeheader()
            writer.writerows(rows)

        print(f"\nWrote {out_path}")

        # Highlight winners
        winners = [r for r in rows if r["beats_baseline"]]
        if winners:
            best = max(winners, key=lambda r: r["delta_combined_usd"])
            print(f"\n🏆 Best NZDUSD variant: {best['variant']} → ${best['variant_combined_usd']:.2f} (delta ${best['delta_combined_usd']:+.2f})")
        else:
            print(f"\n⚠️  No variant beat baseline. Best approach: disable rearm on NZDUSD.")

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
