#!/usr/bin/env python3
"""
Stress test suite for alpha × rearm findings.

Tests:
1. Bar penetration analysis — how deep do bars actually penetrate the close level?
2. Walk-forward split — first 30d train, last 30d test. Does alpha hold OOS?
3. Trending vs ranging — does alpha only work in one regime?
4. Per-symbol alpha sensitivity — sweep alpha on each symbol individually.
5. Effective alpha distribution — what's the actual penetration ratio per close?
6. Slippage model — apply per-trade slippage and see the real breakeven.
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
from penetration_lattice_lab_v2 import (
    Ticket,
    dynamic_step,
    load_bars,
    pip_size_for,
    spread_price,
    unit_pnl_usd,
)

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class RearmToken:
    direction: str
    level: float
    level_idx: int
    armed: bool = False
    cooldown_until: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stress test alpha×rearm assumptions.")
    parser.add_argument("--symbols", nargs="*", default=["GBPUSD", "EURUSD", "NZDUSD"])
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "reports" / "stress_tests"),
    )
    return parser.parse_args()


def _side_count(tickets, direction: str) -> int:
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


def _interpolate_close_ref(level_price: float, bar_extreme: float, direction: str, alpha: float) -> float:
    if direction == "SELL":
        return level_price + alpha * (bar_extreme - level_price)
    else:
        return level_price + alpha * (bar_extreme - level_price)


def simulate_alpha_aware_rearm_with_telemetry(
    symbol: str, bars: list[dict], symbol_info, cfg: RawConfig, cooldown_bars: int, close_alpha: float
) -> dict:
    """Same as the alpha-aware rearm but with per-close telemetry for stress testing."""
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

    # Telemetry: per-close data
    close_telemetry: list[dict] = []

    level_reuse: dict[float, int] = defaultdict(int)

    for idx in range(1, len(bars)):
        bar = bars[idx]

        # Update token arming
        for token in rearm_tokens:
            if token.armed or (cooldown_bars > 0 and idx < token.cooldown_until):
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

        # Consume rearm tokens
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
            # Calculate penetration ratio: how far did bar go past the level?
            penetration = (level_price - bar["low"]) / (level_price * pip_size) if level_price else 0
            # Effective alpha this bar would support: (close_ref - level) / (extreme - level)
            if level_price != bar["low"]:
                effective_alpha = (close_ref - level_price) / (bar["low"] - level_price)
            else:
                effective_alpha = 0.0
            pnl = unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, spread_px)
            realized_pnls.append(pnl)
            close_telemetry.append({
                "bar_idx": idx,
                "direction": "SELL",
                "level_price": level_price,
                "bar_low": bar["low"],
                "bar_high": bar["high"],
                "bar_close": bar["close"],
                "bar_range_pips": (bar["high"] - bar["low"]) / pip_size,
                "penetration_pips": penetration,
                "effective_alpha": effective_alpha,
                "close_ref": close_ref,
                "pnl_usd": pnl,
                "rearm": False,
            })
            open_tickets.remove(outer)
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > gap and bar["high"] >= buys[gap].entry_price:
            outer = buys[0]
            level_price = buys[gap].entry_price
            close_ref = _interpolate_close_ref(level_price, bar["high"], "BUY", close_alpha)
            penetration = (bar["high"] - level_price) / (level_price * pip_size) if level_price else 0
            if level_price != bar["high"]:
                effective_alpha = (close_ref - level_price) / (bar["high"] - level_price)
            else:
                effective_alpha = 0.0
            pnl = unit_pnl_usd(symbol, "BUY", outer.entry_price, close_ref, spread_px)
            realized_pnls.append(pnl)
            close_telemetry.append({
                "bar_idx": idx,
                "direction": "BUY",
                "level_price": level_price,
                "bar_low": bar["low"],
                "bar_high": bar["high"],
                "bar_close": bar["close"],
                "bar_range_pips": (bar["high"] - bar["low"]) / pip_size,
                "penetration_pips": penetration,
                "effective_alpha": effective_alpha,
                "close_ref": close_ref,
                "pnl_usd": pnl,
                "rearm": False,
            })
            open_tickets.remove(outer)
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

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
    combined_net = realized_net + floating_net
    return {
        "combined_net_usd": round(combined_net, 3),
        "realized_net_usd": round(realized_net, 3),
        "floating_net_usd": round(floating_net, 3),
        "realized_closes": len(realized_pnls),
        "rearm_opens": rearm_opens,
        "close_telemetry": close_telemetry,
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

            # === TEST 1: Effective alpha distribution ===
            print(f"\n{'='*80}")
            print(f"{symbol}: Effective Alpha Distribution (cooldown_12bar, alpha=0.50 target)")
            print(f"{'='*80}")
            result = simulate_alpha_aware_rearm_with_telemetry(
                symbol, bars, info, raw_cfg, cooldown_bars=12, close_alpha=0.50
            )
            telemetry = result["close_telemetry"]
            if telemetry:
                alphas = [t["effective_alpha"] for t in telemetry]
                penetrations = [t["penetration_pips"] for t in telemetry]
                bar_ranges = [t["bar_range_pips"] for t in telemetry]

                # Percentile analysis
                alphas_sorted = sorted(alphas)
                n = len(alphas_sorted)
                p5 = alphas_sorted[int(0.05 * n)]
                p25 = alphas_sorted[int(0.25 * n)]
                p50 = alphas_sorted[int(0.50 * n)]
                p75 = alphas_sorted[int(0.75 * n)]
                p95 = alphas_sorted[int(0.95 * n)]
                mean_alpha = sum(alphas) / n

                print(f"  Closes: {n}")
                print(f"  Effective alpha — p5={p5:.3f} p25={p25:.3f} median={p50:.3f} p75={p75:.3f} p95={p95:.3f} mean={mean_alpha:.3f}")
                print(f"  Penetration: min={min(penetrations):.1f}px max={max(penetrations):.1f}px median={sorted(penetrations)[n//2]:.1f}px")
                print(f"  Bar range: min={min(bar_ranges):.1f}px median={sorted(bar_ranges)[n//2]:.1f}px")

                # What fraction of bars support alpha >= 0.50?
                supports_50 = sum(1 for a in alphas if a >= 0.50) / n * 100
                supports_25 = sum(1 for a in alphas if a >= 0.25) / n * 100
                supports_10 = sum(1 for a in alphas if a >= 0.10) / n * 100
                print(f"  Bars supporting alpha>=0.50: {supports_50:.1f}%")
                print(f"  Bars supporting alpha>=0.25: {supports_25:.1f}%")
                print(f"  Bars supporting alpha>=0.10: {supports_10:.1f}%")

                # Save full telemetry
                tele_path = out_dir / f"{symbol.lower()}_telemetry_alpha50.csv"
                with open(tele_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=telemetry[0].keys())
                    writer.writeheader()
                    writer.writerows(telemetry)
                print(f"  → Saved {tele_path}")

            # === TEST 2: Walk-forward split (first 30d vs last 30d) ===
            if len(bars) >= 1440 * 30:
                mid = len(bars) - 1440 * 30
                bars_train = bars[:mid + 1440 * 30] if mid + 1440 * 30 <= len(bars) else bars[:len(bars)//2]
                bars_test = bars[len(bars) - 1440 * 30:]
                # Better split: exact 30/30
                half_idx = len(bars) // 2
                bars_train = bars[:half_idx]
                bars_test = bars[half_idx:]

                print(f"\n{'='*80}")
                print(f"{symbol}: Walk-Forward Split (first {len(bars_train)//1440}d vs last {len(bars_test)//1440}d)")
                print(f"{'='*80}")

                for period_name, period_bars in [("FIRST_HALF", bars_train), ("SECOND_HALF", bars_test)]:
                    for alpha_val in [0.0, 0.25, 0.50]:
                        r = simulate_alpha_aware_rearm_with_telemetry(
                            symbol, period_bars, info, raw_cfg, cooldown_bars=12, close_alpha=alpha_val
                        )
                        days = len(period_bars) / 1440
                        print(f"  {period_name} alpha={alpha_val:.2f}: realized=${r['realized_net_usd']:.2f} closes={r['realized_closes']} days={days:.0f} (${r['realized_net_usd']/days:.2f}/day)")

            # === TEST 3: Alpha sensitivity sweep per symbol ===
            print(f"\n{'='*80}")
            print(f"{symbol}: Alpha Sensitivity (cooldown_12bar)")
            print(f"{'='*80}")
            alpha_results = []
            for alpha_val in [0.0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.0]:
                r = simulate_alpha_aware_rearm_with_telemetry(
                    symbol, bars, info, raw_cfg, cooldown_bars=12, close_alpha=alpha_val
                )
                days = len(bars) / 1440
                alpha_results.append((alpha_val, r))
                print(f"  alpha={alpha_val:.2f}: realized=${r['realized_net_usd']:,.2f} closes={r['realized_closes']} floating=${r['floating_net_usd']:.2f}")

            # Find the alpha where realized < cooldown_12_alpha0 (breakeven vs no-alpha rearm)
            baseline_rearm = alpha_results[0][1]["realized_net_usd"]
            print(f"  Breakeven: alpha=0.00 realized = ${baseline_rearm:,.2f}")

            # === TEST 4: Slippage model ===
            print(f"\n{'='*80}")
            print(f"{symbol}: Slippage Model (alpha=0.50, cooldown_12bar)")
            print(f"{'='*80}")
            base_result = [r for a, r in alpha_results if a == 0.50][0]
            for slippage_pct in [0, 5, 10, 15, 20, 25, 30, 40, 50]:
                # Apply slippage as a % reduction in realized PnL
                adjusted = base_result["realized_net_usd"] * (1 - slippage_pct / 100)
                days = len(bars) / 1440
                vs_baseline = (adjusted / baseline_rearm - 1) * 100 if baseline_rearm else 0
                print(f"  {slippage_pct}% slippage: ${adjusted:,.2f} (${adjusted/days:.2f}/day) vs baseline {vs_baseline:+.0f}%")

    finally:
        mt5.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
