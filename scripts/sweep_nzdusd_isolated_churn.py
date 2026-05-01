#!/usr/bin/env python3
"""
NZDUSD inside-churn v2: True dual-lattice with re-arm isolation.

Key insight: The problem with all previous re-arm attempts was that re-arm entries
went into the SAME ticket list as baseline entries, changing which positions are
outermost/innermost and thus changing the close geometry.

This version:
1. Baseline lattice runs 100% independently (proven $2.05/close on NZDUSD)
2. When a baseline position closes, we record the closed level price
3. A SEPARATE churn lattice tries to trade that level:
   - Re-enter at the closed level price
   - Close at a FIXED profit target (N steps in profit)
   - Never interacts with baseline tickets
4. Total PnL = baseline PnL + churn PnL

The churn lattice is effectively a separate mini-bot that scalps closed levels.
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
class ChurnPos:
    direction: str
    entry_price: float
    opened_idx: int
    take_profit: float  # absolute price to close at
    stop_loss: float = 0.0  # optional stop


@dataclass
class Variant:
    name: str
    churn_close_steps: int = 1  # steps in profit for churn close
    churn_rearm_min_level: int = 1  # min baseline level index to re-arm
    churn_max_per_side: int = 10  # max concurrent churn positions per side
    churn_use_stop: bool = False  # use stop loss at next level
    churn_entry_offset_steps: float = 0.0  # offset entry from closed level (steps)


VARIANTS = [
    # Baseline churn: re-arm at closed level, close 1 step in profit
    Variant(name="churn_1step", churn_close_steps=1),
    # Close 2 steps in profit (wider target)
    Variant(name="churn_2step", churn_close_steps=2),
    # Close 0.5 steps (quick scalp)
    Variant(name="churn_halfstep", churn_close_steps=1, churn_entry_offset_steps=0.5),
    # Only re-arm levels >= 2
    Variant(name="churn_1step_lvl2", churn_close_steps=1, churn_rearm_min_level=2),
    # Only re-arm levels >= 3
    Variant(name="churn_1step_lvl3", churn_close_steps=1, churn_rearm_min_level=3),
    # With stop loss at 1 step
    Variant(name="churn_1step_stop1", churn_close_steps=1, churn_use_stop=True),
    # Max 5 per side (more conservative)
    Variant(name="churn_1step_max5", churn_close_steps=1, churn_max_per_side=5),
    # Max 20 per side (more aggressive)
    Variant(name="churn_1step_max20", churn_close_steps=1, churn_max_per_side=20),
    # Re-arm only levels >= 2, close 2 steps
    Variant(name="churn_2step_lvl2", churn_close_steps=2, churn_rearm_min_level=2),
    # Baseline only
    Variant(name="baseline_only"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NZDUSD isolated churn sweep.")
    parser.add_argument("--symbol", default="NZDUSD")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--output-csv", default=str(ROOT / "reports" / "nzdusd_isolated_churn_sweep.csv"))
    return parser.parse_args()


def simulate_isolated_churn(
    symbol: str, bars: list[dict], symbol_info, cfg: RawConfig, variant: Variant
) -> dict:
    """Dual-lattice: baseline + churn, completely isolated."""
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size

    # === BASELINE ===
    bl_anchor = bars[0]["close"]
    bl_next_sell = bl_anchor + base_step_px
    bl_next_buy = bl_anchor - base_step_px
    bl_tickets: list[Ticket] = []
    bl_realized: list[float] = []

    # === CHURN (completely separate) ===
    churn_positions: list[ChurnPos] = []
    churn_realized: list[float] = []
    churn_rearm_opens = 0
    churn_level_cooldowns: dict[tuple, int] = {}  # (direction, level_price) -> bars_remaining

    for idx in range(1, len(bars)):
        bar = bars[idx]

        # --- Baseline entries ---
        bl_os = sum(1 for t in bl_tickets if t.direction == "SELL")
        bl_ob = sum(1 for t in bl_tickets if t.direction == "BUY")

        bl_ss = dynamic_step(base_step_px, bl_os, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
        bl_bs = dynamic_step(base_step_px, bl_ob, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())

        while bar["high"] >= bl_next_sell and bl_os < cfg.max_open_per_side:
            bl_tickets.append(Ticket(direction="SELL", entry_price=bl_next_sell, opened_idx=idx))
            bl_os += 1
            bl_ss = dynamic_step(base_step_px, bl_os, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
            bl_next_sell += bl_ss
        while bar["low"] <= bl_next_buy and bl_ob < cfg.max_open_per_side:
            bl_tickets.append(Ticket(direction="BUY", entry_price=bl_next_buy, opened_idx=idx))
            bl_ob += 1
            bl_bs = dynamic_step(base_step_px, bl_ob, type("Cfg",(),{"adaptive_step_threshold_1":10,"adaptive_step_threshold_2":20,"adaptive_step_multiplier_1":1.5,"adaptive_step_multiplier_2":2.0})())
            bl_next_buy -= bl_bs

        # --- Baseline closes ---
        gap = 2
        closed_this_bar: list[tuple[str, float, int]] = []  # (direction, entry_price, level_idx)

        bl_sells = sorted([t for t in bl_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)
        while len(bl_sells) > gap and bar["low"] <= bl_sells[gap].entry_price:
            outer = bl_sells[0]
            ref = bl_sells[gap].entry_price
            bl_realized.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, ref, spread_px))
            level_idx = int(round((outer.entry_price - bl_anchor) / base_step_px))
            closed_this_bar.append(("SELL", outer.entry_price, level_idx))
            bl_tickets.remove(outer)
            bl_sells = sorted([t for t in bl_tickets if t.direction=="SELL"], key=lambda t:t.entry_price, reverse=True)

        bl_buys = sorted([t for t in bl_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)
        while len(bl_buys) > gap and bar["high"] >= bl_buys[gap].entry_price:
            outer = bl_buys[0]
            ref = bl_buys[gap].entry_price
            bl_realized.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, ref, spread_px))
            level_idx = int(round((bl_anchor - outer.entry_price) / base_step_px))
            closed_this_bar.append(("BUY", outer.entry_price, level_idx))
            bl_tickets.remove(outer)
            bl_buys = sorted([t for t in bl_tickets if t.direction=="BUY"], key=lambda t:t.entry_price)

        # --- Baseline anchor reset ---
        if not bl_tickets and abs(bar["close"] - bl_anchor) >= base_step_px:
            bl_anchor = bar["close"]
            bl_next_sell = bl_anchor + base_step_px
            bl_next_buy = bl_anchor - base_step_px

        # --- CHURN: Re-arm at closed levels ---
        for direction, closed_price, level_idx in closed_this_bar:
            if level_idx < variant.churn_rearm_min_level:
                continue
            cd_key = (direction, round(closed_price, 5))
            if cd_key in churn_level_cooldowns:
                continue

            churn_count = sum(1 for p in churn_positions if p.direction == direction)
            if churn_count >= variant.churn_max_per_side:
                continue

            # Entry: at the closed level, optionally offset
            entry = closed_price
            if variant.churn_entry_offset_steps > 0:
                offset = variant.churn_entry_offset_steps * base_step_px
                if direction == "SELL":
                    entry -= offset  # enter slightly lower (better for sell)
                else:
                    entry += offset  # enter slightly higher (better for buy)

            # Take profit: N steps in profit direction
            tp_distance = variant.churn_close_steps * base_step_px
            if direction == "SELL":
                take_profit = entry - tp_distance
            else:
                take_profit = entry + tp_distance

            # Stop loss: 1 step beyond entry (opposite direction)
            stop = 0.0
            if variant.churn_use_stop:
                if direction == "SELL":
                    stop = entry + base_step_px
                else:
                    stop = entry - base_step_px

            churn_positions.append(ChurnPos(
                direction=direction, entry_price=entry, opened_idx=idx,
                take_profit=take_profit, stop_loss=stop
            ))
            churn_rearm_opens += 1
            churn_level_cooldowns[cd_key] = 5  # 5-bar cooldown per level

        # --- CHURN: Decrement cooldowns ---
        for key in list(churn_level_cooldowns.keys()):
            churn_level_cooldowns[key] -= 1
            if churn_level_cooldowns[key] <= 0:
                del churn_level_cooldowns[key]

        # --- CHURN: Close positions ---
        for pos in list(churn_positions):
            if pos.direction == "SELL":
                # Profit: price goes down
                if bar["low"] <= pos.take_profit:
                    pnl = unit_pnl_usd(symbol, "SELL", pos.entry_price, pos.take_profit, spread_px)
                    churn_realized.append(pnl)
                    churn_positions.remove(pos)
                # Stop: price goes up
                elif pos.stop_loss > 0 and bar["high"] >= pos.stop_loss:
                    pnl = unit_pnl_usd(symbol, "SELL", pos.entry_price, pos.stop_loss, spread_px)
                    churn_realized.append(pnl)
                    churn_positions.remove(pos)
            else:
                # Profit: price goes up
                if bar["high"] >= pos.take_profit:
                    pnl = unit_pnl_usd(symbol, "BUY", pos.entry_price, pos.take_profit, spread_px)
                    churn_realized.append(pnl)
                    churn_positions.remove(pos)
                # Stop: price goes down
                elif pos.stop_loss > 0 and bar["low"] <= pos.stop_loss:
                    pnl = unit_pnl_usd(symbol, "BUY", pos.entry_price, pos.stop_loss, spread_px)
                    churn_realized.append(pnl)
                    churn_positions.remove(pos)

    bl_floating = [unit_pnl_usd(symbol, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in bl_tickets]
    churn_floating = [unit_pnl_usd(symbol, p.direction, p.entry_price, bars[-1]["close"], spread_px) for p in churn_positions]

    bl_combined = sum(bl_realized) + sum(bl_floating)
    churn_combined = sum(churn_realized) + sum(churn_floating)

    return {
        "bl_realized": sum(bl_realized), "bl_floating": sum(bl_floating), "bl_combined": bl_combined,
        "bl_closes": len(bl_realized),
        "churn_realized": sum(churn_realized), "churn_floating": sum(churn_floating), "churn_combined": churn_combined,
        "churn_closes": len(churn_realized),
        "churn_rearm_opens": churn_rearm_opens,
        "total_combined": bl_combined + churn_combined,
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
        bars = load_bars(symbol, args.days)
        raw_cfg = RawConfig(step_pips=cfg_map[symbol].step_pips, max_open_per_side=cfg_map[symbol].max_open_per_side, close_mode="two_level")

        true_bl = simulate_raw_close2(symbol, bars, info, raw_cfg)
        baseline_usd = float(true_bl["combined_net_usd"])

        print(f"\n{'='*72}")
        print(f"  NZDUSD Isolated Churn Sweep — {args.days}d baseline ${baseline_usd:.2f}")
        print(f"{'='*72}")

        rows = []
        for v in VARIANTS:
            if v.name == "baseline_only":
                r = {"bl_combined": baseline_usd, "bl_closes": true_bl["realized_closes"],
                     "churn_combined": 0, "churn_realized": 0, "churn_floating": 0,
                     "churn_closes": 0, "churn_rearm_opens": 0,
                     "total_combined": baseline_usd, "bl_realized": float(true_bl["realized_net_usd"]),
                     "bl_floating": float(true_bl["floating_net_usd"])}
            else:
                r = simulate_isolated_churn(symbol, bars, info, raw_cfg, v)

            delta = r["total_combined"] - baseline_usd
            rows.append({
                "variant": v.name, "baseline_usd": round(baseline_usd, 3),
                "bl_combined": round(r["bl_combined"], 3),
                "churn_combined": round(r["churn_combined"], 3),
                "churn_realized": round(r["churn_realized"], 3),
                "churn_floating": round(r["churn_floating"], 3),
                "churn_closes": r["churn_closes"],
                "churn_rearm_opens": r["churn_rearm_opens"],
                "total_combined": round(r["total_combined"], 3),
                "delta_usd": round(delta, 3),
                "beats": delta > 0,
            })
            m = "✅" if delta > 0 else "❌"
            churn_str = f"churn=${r['churn_combined']:+.2f}({r['churn_closes']}c,{r['churn_rearm_opens']}rearms)" if v.name != "baseline_only" else "churn=OFF"
            print(f"  {m} {v.name:25s} total=${r['total_combined']:>10.2f}  delta=${delta:>+8.2f}  bl=${r['bl_combined']:.2f}  {churn_str}")

        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)

        winners = [r for r in rows if r["beats"]]
        if winners:
            best = max(winners, key=lambda r: r["delta_usd"])
            print(f"\n🏆 Best: {best['variant']} → ${best['total_combined']:.2f} (+${best['delta_usd']:.2f})")
        else:
            print(f"\n⚠️  No churn variant beat baseline. Trying next approach...")

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
