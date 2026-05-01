#!/usr/bin/env python3
"""
Black Market Research v2 — Lot Scaling + Adaptive Steps + Combo Stacking

Hypothesis: the $126K asymmetric gap result is the NEW baseline.
Stacking orthogonal improvements could push past $200K.

Dimensions:
1. LOT SCALING / PYRAMIDING — scale position size on consecutive levels
   (base 0.01, pyramid 0.015, 0.02, etc.)
2. ADAPTIVE STEP SIZING — widen steps in high vol, tighten in low vol
3. COMBO STACKING — asymmetric gap + alpha75 + cooldown
4. DIRECTIONAL ASYMMETRY — different alphas per direction
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ["GBPUSD", "EURUSD", "NZDUSD"]


@dataclass(frozen=True)
class BlackV2:
    name: str
    momentum_gate: bool = True
    close_alpha_sell: float = 0.50
    close_alpha_buy: float = 0.50
    sell_gap: int = 2
    buy_gap: int = 2
    cooldown_bars: int = 0
    # Lot scaling: multiplier per level depth
    lot_scale_factor: float = 1.0  # 1.0 = no scaling, 1.5 = 50% more per level
    # Adaptive step: widen steps when many positions open (beyond standard adaptive)
    adaptive_step_boost: float = 0.0  # additional multiplier on top of standard adaptive


VARIANTS = [
    # Baseline replication (should match previous $91,812)
    BlackV2(name="baseline_replicate", momentum_gate=True, close_alpha_sell=0.50, close_alpha_buy=0.50),

    # Asymmetric gap replication (should match $126,727)
    BlackV2(name="asym_gap_rep", momentum_gate=True, close_alpha_sell=0.50, close_alpha_buy=0.50, sell_gap=2, buy_gap=1),

    # --- Dimension: Lot Scaling ---
    # Each deeper level = bigger position. Level 1 = 0.01, Level 2 = 0.015, etc.
    BlackV2(name="lot_scale_1.2", momentum_gate=True, close_alpha_sell=0.50, close_alpha_buy=0.50, lot_scale_factor=1.2),
    BlackV2(name="lot_scale_1.5", momentum_gate=True, close_alpha_sell=0.50, close_alpha_buy=0.50, lot_scale_factor=1.5),
    BlackV2(name="lot_scale_2.0", momentum_gate=True, close_alpha_sell=0.50, close_alpha_buy=0.50, lot_scale_factor=2.0),

    # Lot scaling + asymmetric gap (combo)
    BlackV2(name="asym_gap_lot1.5", momentum_gate=True, close_alpha_sell=0.50, close_alpha_buy=0.50, sell_gap=2, buy_gap=1, lot_scale_factor=1.5),
    BlackV2(name="asym_gap_lot2.0", momentum_gate=True, close_alpha_sell=0.50, close_alpha_buy=0.50, sell_gap=2, buy_gap=1, lot_scale_factor=2.0),

    # --- Dimension: Directional Alpha Asymmetry ---
    # BUYs get deeper alpha (bar-edge), SELLs stay conservative
    BlackV2(name="sell_a50_buy_a75", momentum_gate=True, close_alpha_sell=0.50, close_alpha_buy=0.75),
    BlackV2(name="sell_a50_buy_a100", momentum_gate=True, close_alpha_sell=0.50, close_alpha_buy=1.00),
    BlackV2(name="sell_a75_buy_a100", momentum_gate=True, close_alpha_sell=0.75, close_alpha_buy=1.00),

    # --- Dimension: Combo Stacking ---
    BlackV2(name="mega_stack_1", momentum_gate=True, close_alpha_sell=0.75, close_alpha_buy=1.00, sell_gap=2, buy_gap=1, cooldown_bars=6),
    BlackV2(name="mega_stack_2", momentum_gate=True, close_alpha_sell=0.75, close_alpha_buy=1.00, sell_gap=2, buy_gap=1, lot_scale_factor=1.5),
    BlackV2(name="mega_stack_3", momentum_gate=True, close_alpha_sell=1.00, close_alpha_buy=1.00, sell_gap=2, buy_gap=1),
    BlackV2(name="mega_stack_4", momentum_gate=True, close_alpha_sell=1.00, close_alpha_buy=1.00, sell_gap=2, buy_gap=1, cooldown_bars=6),
]


def simulate_black_v2(
    symbol: str, bars: list[dict], symbol_info, cfg: RawConfig, variant: BlackV2
) -> dict:
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px

    open_tickets: list[Ticket] = []
    ticket_levels: list[int] = []  # level depth for each ticket (for lot scaling)
    realized_pnls: list[float] = []
    rearm_opens = 0
    max_open = 0
    level_reuse: dict[float, int] = {}
    rearm_tokens: list[dict] = []

    adapt_cfg = type("Cfg", (), {
        "adaptive_step_threshold_1": 10,
        "adaptive_step_threshold_2": 20,
        "adaptive_step_multiplier_1": 1.5,
        "adaptive_step_multiplier_2": 2.0,
    })()

    for idx in range(1, len(bars)):
        bar = bars[idx]

        open_buy = sum(1 for t in open_tickets if t.direction == "BUY")
        open_sell = sum(1 for t in open_tickets if t.direction == "SELL")

        current_sell_step = dynamic_step(base_step_px, open_sell, adapt_cfg)
        current_buy_step = dynamic_step(base_step_px, open_buy, adapt_cfg)

        # Apply additional adaptive boost
        if variant.adaptive_step_boost > 0:
            current_sell_step *= (1 + variant.adaptive_step_boost)
            current_buy_step *= (1 + variant.adaptive_step_boost)

        # --- Entries ---
        while bar["high"] >= next_sell_level and open_sell < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            ticket_levels.append(open_sell + 1)  # depth level
            open_sell += 1
            current_sell_step = dynamic_step(base_step_px, open_sell, adapt_cfg)
            if variant.adaptive_step_boost > 0:
                current_sell_step *= (1 + variant.adaptive_step_boost)
            next_sell_level += current_sell_step

        while bar["low"] <= next_buy_level and open_buy < cfg.max_open_per_side:
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            ticket_levels.append(open_buy + 1)
            open_buy += 1
            current_buy_step = dynamic_step(base_step_px, open_buy, adapt_cfg)
            if variant.adaptive_step_boost > 0:
                current_buy_step *= (1 + variant.adaptive_step_boost)
            next_buy_level -= current_buy_step

        # --- Rearm token arming ---
        for token in rearm_tokens:
            if token["armed"]:
                continue
            if variant.cooldown_bars > 0 and idx < token["cooldown_until"]:
                continue
            if token["direction"] == "SELL":
                away = token["level"] - (1 * base_step_px)
                if bar["low"] <= away:
                    token["armed"] = True
            else:
                away = token["level"] + (1 * base_step_px)
                if bar["high"] >= away:
                    token["armed"] = True

        # --- Consume rearm tokens ---
        for token in list(rearm_tokens):
            if not token["armed"]:
                continue
            if token["direction"] == "SELL" and open_sell >= cfg.max_open_per_side:
                break
            if token["direction"] == "BUY" and open_buy >= cfg.max_open_per_side:
                break

            if variant.momentum_gate:
                if token["direction"] == "SELL" and bar["close"] >= token["level"]:
                    continue
                if token["direction"] == "BUY" and bar["close"] <= token["level"]:
                    continue

            if token["direction"] == "SELL" and bar["high"] >= token["level"]:
                open_tickets.append(Ticket(direction="SELL", entry_price=token["level"], opened_idx=idx))
                ticket_levels.append(token["level_idx"])
                rearm_tokens.remove(token)
                open_sell += 1
                rearm_opens += 1
            elif token["direction"] == "BUY" and bar["low"] <= token["level"]:
                open_tickets.append(Ticket(direction="BUY", entry_price=token["level"], opened_idx=idx))
                ticket_levels.append(token["level_idx"])
                rearm_tokens.remove(token)
                open_buy += 1
                rearm_opens += 1

        # --- Lot size function ---
        def lot_multiplier(level_depth: int) -> float:
            """Scale position size based on level depth."""
            return variant.lot_scale_factor ** (level_depth - 1)

        # --- SELL Closes ---
        sell_list = [t for t in open_tickets if t.direction == "SELL"]
        sell_list.sort(key=lambda t: t.entry_price, reverse=True)
        gap = variant.sell_gap
        while len(sell_list) > gap and bar["low"] <= sell_list[gap].entry_price:
            outer = sell_list[0]
            outer_idx = open_tickets.index(outer)
            ref_level = sell_list[gap].entry_price
            close_px = ref_level + (bar["low"] - ref_level) * variant.close_alpha_sell
            lot_mult = lot_multiplier(ticket_levels[outer_idx])
            realized_pnls.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, close_px, spread_px) * lot_mult)
            open_tickets.remove(outer)
            ticket_levels.pop(outer_idx)
            level_idx = int(round((outer.entry_price - anchor) / base_step_px))
            if level_idx >= 2:
                reuse = level_reuse.get(outer.entry_price, 0)
                rearm_tokens.append({
                    "direction": "SELL", "level": outer.entry_price, "level_idx": level_idx,
                    "armed": False, "cooldown_until": idx + variant.cooldown_bars,
                })
                level_reuse[outer.entry_price] = reuse + 1
            sell_list = [t for t in open_tickets if t.direction == "SELL"]
            sell_list.sort(key=lambda t: t.entry_price, reverse=True)

        # --- BUY Closes ---
        buy_list = [t for t in open_tickets if t.direction == "BUY"]
        buy_list.sort(key=lambda t: t.entry_price)
        buy_indices = [open_tickets.index(t) for t in buy_list]
        gap = variant.buy_gap
        while len(buy_list) > gap and bar["high"] >= buy_list[gap].entry_price:
            outer = buy_list[0]
            outer_idx = buy_indices[0]
            ref_level = buy_list[gap].entry_price
            close_px = ref_level + (bar["high"] - ref_level) * variant.close_alpha_buy
            lot_mult = lot_multiplier(ticket_levels[outer_idx])
            realized_pnls.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, close_px, spread_px) * lot_mult)
            open_tickets.remove(outer)
            ticket_levels.pop(outer_idx)
            level_idx = int(round((anchor - outer.entry_price) / base_step_px))
            if level_idx >= 2:
                reuse = level_reuse.get(outer.entry_price, 0)
                rearm_tokens.append({
                    "direction": "BUY", "level": outer.entry_price, "level_idx": level_idx,
                    "armed": False, "cooldown_until": idx + variant.cooldown_bars,
                })
                level_reuse[outer.entry_price] = reuse + 1
            buy_list = [t for t in open_tickets if t.direction == "BUY"]
            buy_list.sort(key=lambda t: t.entry_price)
            buy_indices = [open_tickets.index(t) for t in buy_list]

        max_open = max(max_open, len(open_tickets))

        if not open_tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px
            rearm_tokens = []
            level_reuse = {}
            ticket_levels = []

    last_close = bars[-1]["close"]
    floating = [
        unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px) * lot_multiplier(ticket_levels[i])
        for i, t in enumerate(open_tickets)
    ]

    realized = sum(realized_pnls)
    floating_net = sum(floating)
    return {
        "combined_net_usd": round(realized + floating_net, 3),
        "realized_net_usd": round(realized, 3),
        "floating_net_usd": round(floating_net, 3),
        "realized_closes": len(realized_pnls),
        "max_open_total": max_open,
        "rearm_opens": rearm_opens,
    }


def main() -> int:
    mt5.initialize()
    cfg_map = default_raw_configs()

    print(f"\n{'='*100}")
    print(f"  BLACK MARKET RESEARCH v2 — Lot Scaling + Alpha Asymmetry + Combo Stacking")
    print(f"{'='*100}")

    baseline_total = 0
    for sym in SYMBOLS:
        info = mt5.symbol_info(sym)
        bars = load_bars(sym, 60)
        cfg = RawConfig(
            step_pips=cfg_map[sym].step_pips,
            max_open_per_side=cfg_map[sym].max_open_per_side,
            close_mode="two_level",
        )
        bl = simulate_raw_close2(sym, bars, info, cfg)
        baseline_total += float(bl["combined_net_usd"])

    print(f"\nBaseline total (3 symbols, 60d): ${baseline_total:,.2f}\n")

    all_rows = []

    for v in VARIANTS:
        total = 0.0
        details = {}
        for sym in SYMBOLS:
            info = mt5.symbol_info(sym)
            bars = load_bars(sym, 60)
            cfg = RawConfig(
                step_pips=cfg_map[sym].step_pips,
                max_open_per_side=cfg_map[sym].max_open_per_side,
                close_mode="two_level",
            )
            r = simulate_black_v2(sym, bars, info, cfg, v)
            val = float(r["combined_net_usd"])
            details[sym] = val
            total += val

        delta = total - baseline_total
        mult = total / baseline_total if baseline_total > 0 else 0
        gbp = details.get("GBPUSD", 0)
        eur = details.get("EURUSD", 0)
        nzd = details.get("NZDUSD", 0)

        all_rows.append({
            "variant": v.name,
            "total": round(total, 2),
            "delta": round(delta, 2),
            "mult": round(mult, 2),
            "GBPUSD": round(gbp, 2),
            "EURUSD": round(eur, 2),
            "NZDUSD": round(nzd, 2),
        })

        flag = " 🚀" if delta > 50000 else " ✨" if delta > 20000 else ""
        print(f"  {v.name:<35} ${total:>14,.2f}  Δ=${delta:>12,.2f}  {mult:.2f}x  | G=${gbp:,.0f} E=${eur:,.0f} N=${nzd:,.0f}{flag}")

    print(f"\n{'='*100}")
    print(f"  LEADERBOARD (sorted)")
    print(f"{'='*100}")
    sorted_rows = sorted(all_rows, key=lambda r: r["total"], reverse=True)
    for i, r in enumerate(sorted_rows, 1):
        medal = "🏆" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "  "
        print(f"  {medal} {i:>2}. {r['variant']:<35} ${r['total']:>14,.2f}  ({r['mult']:.2f}x)  Δ=${r['delta']:>+12,.2f}")

    out = ROOT / "reports" / "black_market_research_v2_sweep.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["variant", "total", "delta", "mult", "GBPUSD", "EURUSD", "NZDUSD"])
        w.writeheader()
        w.writerows(all_rows)
    print(f"\nWrote {out}")

    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
