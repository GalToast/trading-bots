#!/usr/bin/env python3
"""Audit the trailing_partial and partial_close variants for PnL correctness."""
from __future__ import annotations

import MetaTrader5 as mt5

from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd

SYMBOL = "NZDUSD"
DAYS = 60


class Pos:
    def __init__(self, direction, entry_price, opened_idx, size=1.0):
        self.direction = direction
        self.entry_price = entry_price
        self.opened_idx = opened_idx
        self.size_remaining = size
        self.partial_closes = []  # track each partial close for audit


def audit_trailing_partial():
    mt5.initialize()
    cfg_map = default_raw_configs()
    info = mt5.symbol_info(SYMBOL)
    bars = load_bars(SYMBOL, DAYS)
    raw_cfg = RawConfig(
        step_pips=cfg_map[SYMBOL].step_pips,
        max_open_per_side=cfg_map[SYMBOL].max_open_per_side,
        close_mode=cfg_map[SYMBOL].close_mode,
    )

    pip_size = pip_size_for(info)
    spread_px = spread_price(info)
    base_step_px = raw_cfg.step_pips * pip_size

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px

    positions: list[Pos] = []
    realized_pnls: list[tuple[float, str]] = []  # (pnl, reason)
    consecutive_sell = 0
    consecutive_buy = 0
    max_open = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]

        open_buy = sum(1 for p in positions if p.direction == "BUY" and p.size_remaining > 0.01)
        open_sell = sum(1 for p in positions if p.direction == "SELL" and p.size_remaining > 0.01)

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

        while bar["high"] >= next_sell_level and open_sell < raw_cfg.max_open_per_side:
            positions.append(Pos(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            open_sell += 1
            current_sell_step = dynamic_step(base_step_px, open_sell, type("Cfg", (), {
                "adaptive_step_threshold_1": 10,
                "adaptive_step_threshold_2": 20,
                "adaptive_step_multiplier_1": 1.5,
                "adaptive_step_multiplier_2": 2.0,
            })())
            next_sell_level += current_sell_step

        while bar["low"] <= next_buy_level and open_buy < raw_cfg.max_open_per_side:
            positions.append(Pos(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            open_buy += 1
            current_buy_step = dynamic_step(base_step_px, open_buy, type("Cfg", (), {
                "adaptive_step_threshold_1": 10,
                "adaptive_step_threshold_2": 20,
                "adaptive_step_multiplier_1": 1.5,
                "adaptive_step_multiplier_2": 2.0,
            })())
            next_buy_level -= current_buy_step

        gap = 2  # two_level

        # Normal closes
        sells = sorted([p for p in positions if p.direction == "SELL" and p.size_remaining > 0.01],
                       key=lambda p: p.entry_price, reverse=True)
        while len(sells) > gap:
            outer = sells[0]
            close_ref = sells[gap].entry_price
            pnl = unit_pnl_usd(SYMBOL, "SELL", outer.entry_price, close_ref, spread_px) * outer.size_remaining
            realized_pnls.append((pnl, "normal_close"))
            outer.partial_closes.append(("close", pnl, outer.size_remaining))
            outer.size_remaining = 0
            positions = [p for p in positions if p.size_remaining > 0.01]
            sells = sorted([p for p in positions if p.direction == "SELL" and p.size_remaining > 0.01],
                           key=lambda p: p.entry_price, reverse=True)
            consecutive_sell += 1
            consecutive_buy = 0

        buys = sorted([p for p in positions if p.direction == "BUY" and p.size_remaining > 0.01],
                      key=lambda p: p.entry_price)
        while len(buys) > gap:
            outer = buys[0]
            close_ref = buys[gap].entry_price
            pnl = unit_pnl_usd(SYMBOL, "BUY", outer.entry_price, close_ref, spread_px) * outer.size_remaining
            realized_pnls.append((pnl, "normal_close"))
            outer.partial_closes.append(("close", pnl, outer.size_remaining))
            outer.size_remaining = 0
            positions = [p for p in positions if p.size_remaining > 0.01]
            buys = sorted([p for p in positions if p.direction == "BUY" and p.size_remaining > 0.01],
                          key=lambda p: p.entry_price)
            consecutive_buy += 1
            consecutive_sell = 0

        # Trailing partial: after 3 consecutive same-direction closes, partial-close newest at 1-step profit
        if consecutive_sell >= 3:
            sells_active = [p for p in positions if p.direction == "SELL" and p.size_remaining > 0.01]
            if sells_active:
                newest = max(sells_active, key=lambda p: p.opened_idx)
                profit_level = newest.entry_price - base_step_px
                if bar["low"] <= profit_level and newest.entry_price > profit_level:
                    pnl = unit_pnl_usd(SYMBOL, "SELL", newest.entry_price, profit_level, spread_px)
                    pnl_scaled = pnl * 0.5 * newest.size_remaining
                    realized_pnls.append((pnl_scaled, "trailing_partial_sell"))
                    newest.partial_closes.append(("trailing_partial", pnl_scaled, newest.size_remaining * 0.5))
                    newest.size_remaining *= 0.5

        if consecutive_buy >= 3:
            buys_active = [p for p in positions if p.direction == "BUY" and p.size_remaining > 0.01]
            if buys_active:
                newest = max(buys_active, key=lambda p: p.opened_idx)
                profit_level = newest.entry_price + base_step_px
                if bar["high"] >= profit_level and newest.entry_price < profit_level:
                    pnl = unit_pnl_usd(SYMBOL, "BUY", newest.entry_price, profit_level, spread_px)
                    pnl_scaled = pnl * 0.5 * newest.size_remaining
                    realized_pnls.append((pnl_scaled, "trailing_partial_buy"))
                    newest.partial_closes.append(("trailing_partial", pnl_scaled, newest.size_remaining * 0.5))
                    newest.size_remaining *= 0.5

        max_open = max(max_open, len(positions))

        if not positions and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px

    # Analyze
    normal_pnls = [p for p, r in realized_pnls if r == "normal_close"]
    trailing_pnls = [p for p, r in realized_pnls if "trailing" in r]

    print(f"\n{'='*60}")
    print(f"  NZDUSD trailing_partial AUDIT")
    print(f"{'='*60}")
    print(f"  Total closes: {len(realized_pnls)}")
    print(f"  Normal closes: {len(normal_pnls)}, total ${sum(normal_pnls):.2f}, avg ${sum(normal_pnls)/len(normal_pnls):.4f}" if normal_pnls else "  No normal closes")
    print(f"  Trailing partials: {len(trailing_pnls)}, total ${sum(trailing_pnls):.2f}, avg ${sum(trailing_pnls)/len(trailing_pnls):.4f}" if trailing_pnls else "  No trailing partials")
    print(f"  Combined total: ${sum(p for p, _ in realized_pnls):.2f}")
    print(f"  Max open: {max_open}")

    # Show sample of first few trailing partials
    print(f"\n  First 10 trailing partial closes:")
    count = 0
    for pnl, reason in realized_pnls:
        if "trailing" in reason and count < 10:
            print(f"    {reason}: ${pnl:.4f}")
            count += 1

    # Also run true baseline for comparison
    true_bl = simulate_raw_close2(SYMBOL, bars, info, raw_cfg)
    print(f"\n  True baseline: ${true_bl['combined_net_usd']:.2f} ({true_bl['realized_closes']} closes)")

    mt5.shutdown()


if __name__ == "__main__":
    audit_trailing_partial()
