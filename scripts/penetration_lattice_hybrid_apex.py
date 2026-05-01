#!/usr/bin/env python3
"""
Penetration Lattice Hybrid Apex — tests the hybrid strategy:
- Self-healers (GBPUSD, EURUSD, NZDUSD) → raw close2 penetration
- Hostiles (USDJPY, USDCHF) → V3 bounded with breakout kills

Sweeps step_pips across ALL symbols on 60d to find the apex per-symbol,
then computes the combined basket PnL for different symbol subsets.
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
from penetration_lattice_lab_v3_bounded import (
    recent_range,
    simulate_symbol as simulate_v3_bounded,
)


@dataclass(frozen=True)
class RawConfig:
    step_pips: float
    max_open_per_side: int
    close_mode: str  # "one_level" or "two_level"
    step_is_price_units: bool = False  # If True, step_pips is a raw price (for crypto H1), not pips


@dataclass(frozen=True)
class V3Config:
    step_pips: float
    max_open_per_side: int
    max_floating_loss_usd: float = -10.0
    vwap_lookback: int = 20
    regime_lookback_bars: int = 60
    max_range_pips: float = 18.0
    breakout_buffer_pips: float = 3.0
    max_lattice_window_bars: int = 240
    cooldown_bars: int = 60
    adaptive_step_threshold_1: int = 10
    adaptive_step_threshold_2: int = 20
    adaptive_step_multiplier_1: float = 1.5
    adaptive_step_multiplier_2: float = 2.0


DEFAULT_SYMBOLS = ["USDJPY", "GBPUSD", "EURUSD", "USDCHF", "NZDUSD"]
VOLUME = 0.01


def simulate_raw_close2(symbol: str, bars: list[dict], symbol_info, cfg: RawConfig) -> dict:
    """Raw close2 penetration — no stops, no resets, no kills."""
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px

    open_tickets: list[Ticket] = []
    realized_pnls: list[float] = []
    max_open = 0
    max_open_buy = 0
    max_open_sell = 0
    anchor_resets = 0
    last_reset_idx = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]

        open_buy = sum(1 for t in open_tickets if t.direction == "BUY")
        open_sell = sum(1 for t in open_tickets if t.direction == "SELL")

        current_sell_step = dynamic_step(base_step_px, open_sell, type("Cfg", (), {
            "adaptive_step_threshold_1": 10,
            "adaptive_step_threshold_2": 20,
            "adaptive_step_multiplier_1": 1.5,
            "adaptive_step_multiplier_2": 2.0,
        })())
        current_buy_step = dynamic_step(base_step_px, open_buy, type("Cfg", (), {
            "adaptive_step_threshold_1": 10,
            "adaptive_step_threshold_2": 20,
            "adaptive_step_multiplier_1": 1.5,
            "adaptive_step_multiplier_2": 2.0,
        })())

        while bar["high"] >= next_sell_level and open_sell < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            open_sell += 1
            current_sell_step = dynamic_step(base_step_px, open_sell, type("Cfg", (), {
                "adaptive_step_threshold_1": 10,
                "adaptive_step_threshold_2": 20,
                "adaptive_step_multiplier_1": 1.5,
                "adaptive_step_multiplier_2": 2.0,
            })())
            next_sell_level += current_sell_step

        while bar["low"] <= next_buy_level and open_buy < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            open_buy += 1
            current_buy_step = dynamic_step(base_step_px, open_buy, type("Cfg", (), {
                "adaptive_step_threshold_1": 10,
                "adaptive_step_threshold_2": 20,
                "adaptive_step_multiplier_1": 1.5,
                "adaptive_step_multiplier_2": 2.0,
            })())
            next_buy_level -= current_buy_step

        gap = 1 if cfg.close_mode == "one_level" else 2

        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > gap and bar["low"] <= sells[gap].entry_price:
            outer = sells[0]
            close_ref = sells[gap].entry_price
            realized_pnls.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, spread_px))
            open_tickets.remove(outer)
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > gap and bar["high"] >= buys[gap].entry_price:
            outer = buys[0]
            close_ref = buys[gap].entry_price
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
            anchor_resets += 1
            last_reset_idx = idx

    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px)
        for t in open_tickets
    ]

    realized_net = sum(realized_pnls)
    floating_net = sum(floating_pnls)
    combined_net = realized_net + floating_net
    total_closes = len(realized_pnls)
    wins = sum(1 for p in realized_pnls if p > 0)

    return {
        "mode": "raw_close2",
        "realized_closes": len(realized_pnls),
        "total_closes": total_closes,
        "wr_pct": round(wins / total_closes * 100.0, 1) if total_closes else 0.0,
        "realized_net_usd": round(realized_net, 3),
        "open_tickets_left": len(open_tickets),
        "floating_net_usd": round(floating_net, 3),
        "worst_floating_usd": round(min(floating_pnls), 3) if floating_pnls else 0.0,
        "combined_net_usd": round(combined_net, 3),
        "max_open_total": max_open,
        "max_open_buy": max_open_buy,
        "max_open_sell": max_open_sell,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hybrid apex: raw for self-healers, V3 for hostiles.")
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--step-pips", nargs="*", type=float, default=[0.5, 1.0, 1.5, 2.0, 3.0, 5.0])
    parser.add_argument("--raw-caps", nargs="*", type=int, default=[12, 15, 20])
    parser.add_argument(
        "--self-healers", nargs="*", default=["GBPUSD", "EURUSD", "NZDUSD"],
        help="Symbols using raw close2"
    )
    parser.add_argument(
        "--hostiles", nargs="*", default=["USDJPY", "USDCHF"],
        help="Symbols using V3 bounded"
    )
    parser.add_argument("--hostile-max-range-pips", nargs="*", type=float, default=[18.0, 24.0])
    parser.add_argument("--hostile-breakout-buffer-pips", nargs="*", type=float, default=[3.0, 5.0])
    parser.add_argument("--hostile-max-lattice-window-bars", nargs="*", type=int, default=[120, 240])
    parser.add_argument("--hostile-cooldown-bars", nargs="*", type=int, default=[60])
    parser.add_argument("--output-csv", default=str(ROOT / "reports" / "penetration_lattice_hybrid_apex.csv"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    self_healers = set(args.self_healers)
    hostiles = set(args.hostiles)

    try:
        rows: list[dict] = []
        for symbol in args.symbols:
            info = mt5.symbol_info(symbol)
            if info is None:
                continue
            bars = load_bars(symbol, args.days)
            if not bars:
                continue

            best_combined = -999999.0
            best_row = None

            for step_pips in args.step_pips:
                if symbol in self_healers:
                    for raw_cap in args.raw_caps:
                        cfg = RawConfig(step_pips=step_pips, max_open_per_side=raw_cap, close_mode="two_level")
                        result = simulate_raw_close2(symbol, bars, info, cfg)
                        result["symbol"] = symbol
                        result["step_pips"] = step_pips
                        result["mode"] = "raw_close2"
                        result["max_open_per_side"] = raw_cap
                        score = result["combined_net_usd"]
                        if score > best_combined:
                            best_combined = score
                            best_row = result

                        print(
                            f"{symbol:<7} step={step_pips:<4.1f} cap={raw_cap:<2} mode={result['mode']:<12} "
                            f"combined={result['combined_net_usd']:+.2f} "
                            f"realized={result['realized_net_usd']:+.2f} "
                            f"worst={result.get('worst_floating_usd', 0):+.2f} "
                            f"max_open={result['max_open_total']:>3}"
                        )

                        rows.append(result)
                    continue

                for max_range_pips in args.hostile_max_range_pips:
                    for breakout_buffer_pips in args.hostile_breakout_buffer_pips:
                        for max_lattice_window_bars in args.hostile_max_lattice_window_bars:
                            for cooldown_bars in args.hostile_cooldown_bars:
                                cfg = type("V3Cfg", (), {
                                    "step_pips": step_pips,
                                    "max_open_per_side": 20,
                                    "max_floating_loss_usd": -10.0,
                                    "vwap_lookback": 20,
                                    "regime_lookback_bars": 60,
                                    "max_range_pips": max_range_pips,
                                    "breakout_buffer_pips": breakout_buffer_pips,
                                    "max_lattice_window_bars": max_lattice_window_bars,
                                    "cooldown_bars": cooldown_bars,
                                    "adaptive_step_threshold_1": 10,
                                    "adaptive_step_threshold_2": 20,
                                    "adaptive_step_multiplier_1": 1.5,
                                    "adaptive_step_multiplier_2": 2.0,
                                })()
                                result = simulate_v3_bounded(symbol, bars, info, cfg)
                                result["symbol"] = symbol
                                result["step_pips"] = step_pips
                                result["mode"] = "v3_bounded"
                                result["max_range_pips"] = max_range_pips
                                result["breakout_buffer_pips"] = breakout_buffer_pips
                                result["max_lattice_window_bars"] = max_lattice_window_bars
                                result["cooldown_bars"] = cooldown_bars
                                score = result["combined_net_usd"]

                                if score > best_combined:
                                    best_combined = score
                                    best_row = result

                                print(
                                    f"{symbol:<7} step={step_pips:<4.1f} mode={result['mode']:<12} "
                                    f"range={max_range_pips:<4.0f} buffer={breakout_buffer_pips:<3.0f} "
                                    f"window={max_lattice_window_bars:<3} combined={result['combined_net_usd']:+.2f} "
                                    f"realized={result['realized_net_usd']:+.2f} "
                                    f"worst={result.get('worst_floating_usd', 0):+.2f} "
                                    f"max_open={result['max_open_total']:>3}"
                                )

                                rows.append(result)

            if best_row:
                print(f"  *** {symbol} APEX: step={best_row['step_pips']} combined={best_row['combined_net_usd']:+.2f} ***\n")

        # Compute basket totals for different combos
        symbol_map = {r["symbol"]: r for r in rows if r.get("step_pips") and r.get("combined_net_usd")}

        # Find best step per symbol
        best_per_symbol = {}
        for symbol in args.symbols:
            symbol_rows = [r for r in rows if r["symbol"] == symbol]
            if symbol_rows:
                # Use the one with highest combined_net
                best = max(symbol_rows, key=lambda r: r["combined_net_usd"])
                best_per_symbol[symbol] = best

        # Basket combinations
        baskets = {
            "all_5": ["USDJPY", "GBPUSD", "EURUSD", "USDCHF", "NZDUSD"],
            "self_healers_3": ["GBPUSD", "EURUSD", "NZDUSD"],
            "gbp_eur_2": ["GBPUSD", "EURUSD"],
            "gbp_eur_nzd_3": ["GBPUSD", "EURUSD", "NZDUSD"],
        }

        print("\n=== BASKET TOTALS (best step per symbol) ===")
        for basket_name, basket_symbols in baskets.items():
            total = sum(best_per_symbol[s]["combined_net_usd"] for s in basket_symbols if s in best_per_symbol)
            daily = total / args.days
            worst_floating = max(best_per_symbol[s].get("worst_floating_usd", 0) for s in basket_symbols if s in best_per_symbol)
            mode_summary = {s: best_per_symbol[s]["mode"] for s in basket_symbols if s in best_per_symbol}
            step_summary = {s: best_per_symbol[s]["step_pips"] for s in basket_symbols if s in best_per_symbol}
            print(
                f"{basket_name:<20} ${total:>8.2f}/60d  ${daily:>6.2f}/day  "
                f"worst_float={worst_floating:+.2f}"
            )
            for s in basket_symbols:
                if s in best_per_symbol:
                    r = best_per_symbol[s]
                    print(f"  {s}: {r['mode']} step={r['step_pips']} ${r['combined_net_usd']:+.2f} worst={r.get('worst_floating_usd', 0):+.2f}")

        # Save full CSV
        output_path = Path(args.output_csv)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if rows:
            all_keys = set()
            for r in rows:
                all_keys.update(r.keys())
            fieldnames = sorted(all_keys)
            with output_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            print(f"\nSaved {output_path}")

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
