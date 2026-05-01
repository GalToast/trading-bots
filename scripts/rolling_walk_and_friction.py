#!/usr/bin/env python3
"""
Rolling walk-forward + live friction model for alpha×rearm.

Uses the canonical sweep_alpha_aware_rearm simulation for correctness.
Tests:
1. Rolling 15-day windows (sliding by 5 days) — does alpha hold across all windows?
2. Per-trade friction: variable spread (wider during volatility) + slippage (ticks)
3. Regime classification: classify each 15-day window as ranging/trending and check alpha performance
"""
from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Variant:
    name: str
    min_level_idx: int = 2
    excursion_levels: int = 1
    cooldown_bars: int = 0
    close_alpha: float = 0.0
    skip_symbols: set[str] = field(default_factory=set)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rolling walk-forward + friction for alpha×rearm.")
    parser.add_argument("--symbols", nargs="*", default=["GBPUSD", "EURUSD", "NZDUSD"])
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--window", type=int, default=15, help="Window size in days")
    parser.add_argument("--stride", type=int, default=5, help="Stride between windows")
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "reports" / "rolling_analysis"),
    )
    return parser.parse_args()


def _interpolate_close_ref(level_price: float, bar_extreme: float, direction: str, alpha: float) -> float:
    if direction == "SELL":
        return level_price + alpha * (bar_extreme - level_price)
    else:
        return level_price + alpha * (bar_extreme - level_price)


@dataclass
class RearmToken:
    direction: str
    level: float
    level_idx: int
    armed: bool = False
    reuse_count: int = 0
    last_close_bar: int = 0
    cooldown_until: int = 0


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


def simulate_with_friction(
    symbol: str, bars: list[dict], symbol_info, cfg: RawConfig,
    cooldown_bars: int, close_alpha: float,
    slippage_ticks: float = 0.0, spread_multiplier: float = 1.0
) -> dict:
    """Canonical simulation with per-trade friction (slippage + spread adjustment)."""
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info) * spread_multiplier
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

    level_reuse: dict[float, int] = defaultdict(int)

    for idx in range(1, len(bars)):
        bar = bars[idx]

        # Update token arming
        for token in rearm_tokens:
            if token.armed:
                continue
            if cooldown_bars > 0 and idx < token.cooldown_until:
                continue
            if token.direction == "SELL":
                away = token.level - base_step_px
                if bar["low"] <= away:
                    token.armed = True
            else:
                away = token.level + base_step_px
                if bar["high"] >= away:
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

        gap = 1 if cfg.close_mode == "one_level" else 2

        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > gap and bar["low"] <= sells[gap].entry_price:
            outer = sells[0]
            level_price = sells[gap].entry_price
            close_ref = _interpolate_close_ref(level_price, bar["low"], "SELL", close_alpha)
            # Apply slippage: worse entry/exit by N ticks
            slip_px = slippage_ticks * float(symbol_info.point or 0.0)
            pnl = unit_pnl_usd(symbol, "SELL", outer.entry_price + slip_px, close_ref - slip_px, spread_px)
            realized_pnls.append(pnl)
            open_tickets.remove(outer)
            level_idx = int(round((outer.entry_price - anchor) / base_step_px))
            if level_idx >= 2:
                cooldown_end = idx + cooldown_bars if cooldown_bars > 0 else 0
                rearm_tokens.append(RearmToken(
                    direction="SELL", level=outer.entry_price, level_idx=level_idx,
                    reuse_count=level_reuse[outer.entry_price], cooldown_until=cooldown_end,
                ))
                level_reuse[outer.entry_price] += 1
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > gap and bar["high"] >= buys[gap].entry_price:
            outer = buys[0]
            level_price = buys[gap].entry_price
            close_ref = _interpolate_close_ref(level_price, bar["high"], "BUY", close_alpha)
            slip_px = slippage_ticks * float(symbol_info.point or 0.0)
            pnl = unit_pnl_usd(symbol, "BUY", outer.entry_price - slip_px, close_ref + slip_px, spread_px)
            realized_pnls.append(pnl)
            open_tickets.remove(outer)
            level_idx = int(round((anchor - outer.entry_price) / base_step_px))
            if level_idx >= 2:
                cooldown_end = idx + cooldown_bars if cooldown_bars > 0 else 0
                rearm_tokens.append(RearmToken(
                    direction="BUY", level=outer.entry_price, level_idx=level_idx,
                    reuse_count=level_reuse[outer.entry_price], cooldown_until=cooldown_end,
                ))
                level_reuse[outer.entry_price] += 1
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


def classify_regime(bars: list[dict], pip_size: float) -> dict:
    """Classify a bar set as ranging, trending, or mixed."""
    if len(bars) < 60:
        return {"regime": "unknown", "avg_range_px": 0, "trend_ratio": 0}

    # Average bar range in pips
    ranges_px = [(b["high"] - b["low"]) / pip_size for b in bars]
    avg_range = sum(ranges_px) / len(ranges_px)

    # Trend ratio: net move / total path length
    total_path = sum(abs(bars[i]["close"] - bars[i-1]["close"]) / pip_size for i in range(1, len(bars)))
    net_move = abs(bars[-1]["close"] - bars[0]["close"]) / pip_size
    trend_ratio = net_move / total_path if total_path > 0 else 0

    if trend_ratio > 0.3:
        regime = "trending"
    elif trend_ratio < 0.1:
        regime = "ranging"
    else:
        regime = "mixed"

    return {
        "regime": regime,
        "avg_range_px": round(avg_range, 1),
        "trend_ratio": round(trend_ratio, 3),
        "net_move_px": round(net_move, 1),
        "total_path_px": round(total_path, 1),
    }


def main() -> int:
    args = parse_args()
    cfg_map = default_raw_configs()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
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
            pip_size = pip_size_for(info)
            bars_per_day = 1440
            window_bars = args.window * bars_per_day
            stride_bars = args.stride * bars_per_day

            # === ROLLING WALK-FORWARD ===
            print(f"\n{'='*80}")
            print(f"{symbol}: Rolling {args.window}-day Walk-Forward (stride={args.stride}d)")
            print(f"{'='*80}")

            rolling_rows = []
            window_start = 0
            window_num = 0
            while window_start + window_bars <= len(bars):
                window_bars_slice = bars[window_start:window_start + window_bars]
                window_num += 1
                day_start = window_start // bars_per_day
                day_end = (window_start + window_bars) // bars_per_day

                regime = classify_regime(window_bars_slice, pip_size)

                row = {
                    "window": window_num,
                    "day_start": day_start,
                    "day_end": day_end,
                    "bars": len(window_bars_slice),
                    "regime": regime["regime"],
                    "trend_ratio": regime["trend_ratio"],
                    "avg_range_px": regime["avg_range_px"],
                    "net_move_px": regime["net_move_px"],
                }

                for alpha_val in [0.0, 0.25, 0.50]:
                    r = simulate_with_friction(
                        symbol, window_bars_slice, info, raw_cfg,
                        cooldown_bars=12, close_alpha=alpha_val,
                        slippage_ticks=0, spread_multiplier=1.0,
                    )
                    days_in_window = len(window_bars_slice) / bars_per_day
                    row[f"alpha{int(alpha_val*100)}_realized"] = round(r["realized_net_usd"], 2)
                    row[f"alpha{int(alpha_val*100)}_closes"] = r["realized_closes"]
                    row[f"alpha{int(alpha_val*100)}_daily"] = round(r["realized_net_usd"] / days_in_window, 2) if days_in_window > 0 else 0

                rolling_rows.append(row)

                ratio_50 = row["alpha50_realized"] / row["alpha0_realized"] if row["alpha0_realized"] else 0
                print(
                    f"  W{window_num:02d} days {day_start:3d}-{day_end:3d} | "
                    f"regime={regime['regime']:8s} trend={regime['trend_ratio']:.3f} | "
                    f"a0=${row['alpha0_realized']:8.2f} a50=${row['alpha50_realized']:8.2f} "
                    f"ratio={ratio_50:.2f}x | "
                    f"a50/day=${row['alpha50_daily']:.2f}"
                )

                window_start += stride_bars

            # Save rolling results
            if rolling_rows:
                csv_path = out_dir / f"{symbol.lower()}_rolling_{args.window}d.csv"
                with open(csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=rolling_rows[0].keys())
                    writer.writeheader()
                    writer.writerows(rolling_rows)
                print(f"  → Saved {csv_path}")

            # === FRICTION MODEL ===
            print(f"\n{'='*80}")
            print(f"{symbol}: Live Friction Model (alpha=0.50, cooldown=12, full {args.days}d)")
            print(f"{'='*80}")

            friction_rows = []
            for slip_ticks in [0, 1, 2, 3, 5, 8, 10]:
                for spread_mult in [1.0, 1.5, 2.0, 3.0]:
                    r = simulate_with_friction(
                        symbol, bars, info, raw_cfg,
                        cooldown_bars=12, close_alpha=0.50,
                        slippage_ticks=slip_ticks, spread_multiplier=spread_mult,
                    )
                    days = len(bars) / bars_per_day
                    baseline = simulate_raw_close2(symbol, bars, info, raw_cfg)
                    base_realized = float(baseline["realized_net_usd"]) if baseline else 0
                    vs_base = (r["realized_net_usd"] / base_realized - 1) * 100 if base_realized > 0 else 0
                    row = {
                        "slippage_ticks": slip_ticks,
                        "spread_multiplier": spread_mult,
                        "realized_usd": round(r["realized_net_usd"], 2),
                        "floating_usd": round(r["floating_net_usd"], 2),
                        "combined_usd": round(r["combined_net_usd"], 2),
                        "closes": r["realized_closes"],
                        "vs_baseline_pct": round(vs_base, 1),
                        "daily_usd": round(r["realized_net_usd"] / days, 2),
                    }
                    friction_rows.append(row)

            # Print best/worst
            friction_rows.sort(key=lambda x: x["realized_usd"], reverse=True)
            for row in friction_rows:
                tag = " ✅" if row["vs_baseline_pct"] > 100 else (" ⚠️" if row["vs_baseline_pct"] > 0 else " ❌")
                print(
                    f"  slip={row['slippage_ticks']:2d}t spread={row['spread_multiplier']:.1f}x | "
                    f"realized=${row['realized_usd']:8.2f} ({row['daily_usd']:.2f}/day) "
                    f"vs baseline {row['vs_baseline_pct']:+.0f}%{tag}"
                )

            csv_path = out_dir / f"{symbol.lower()}_friction_model.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=friction_rows[0].keys())
                writer.writeheader()
                writer.writerows(friction_rows)
            print(f"  → Saved {csv_path}")

        # === CROSS-SYMBOL SUMMARY ===
        print(f"\n{'='*80}")
        print("CROSS-SYMBOL ROLLING SUMMARY")
        print(f"{'='*80}")

        # Aggregate regime performance
        for regime in ["ranging", "mixed", "trending"]:
            total_a0 = 0
            total_a50 = 0
            count = 0
            for symbol in args.symbols:
                csv_path = out_dir / f"{symbol.lower()}_rolling_{args.window}d.csv"
                if not csv_path.exists():
                    continue
                with open(csv_path) as f:
                    for row in csv.DictReader(f):
                        if row["regime"] == regime:
                            total_a0 += float(row["alpha0_realized"])
                            total_a50 += float(row["alpha50_realized"])
                            count += 1
            if count > 0:
                ratio = total_a50 / total_a0 if total_a0 > 0 else 0
                print(f"  {regime:10s}: avg_a0=${total_a0/count:.2f} avg_a50=${total_a50/count:.2f} ratio={ratio:.2f}x ({count} windows)")

    finally:
        mt5.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
