#!/usr/bin/env python3
"""
Black market research sweep — testing unexplored multiplier dimensions.

Hypothesis: the $94K momentum_alpha50 result is just the BASELINE. There are
orthogonal dimensions we haven't touched that could multiply it further.

Dimensions tested:
1. DIRECTIONAL BIAS — only SELLs or only BUYs per symbol
   (markets trend more than they range; one direction may be far more profitable)
2. TIME-OF-DAY GATING — only trade during specific hour windows
   (US open printed $30 in 2h, Asian session dead)
3. ASYMMETRIC CLOSE GAP — gap=1 for SELLs, gap=2 for BUYs (or vice versa)
   (different close geometry per direction)
4. AGGRESSIVE ALPHA — alpha=0.75 and alpha=1.0 (deeper fills if bar allows)
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import Ticket, dynamic_step, load_bars, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ["GBPUSD", "EURUSD", "NZDUSD"]


@dataclass
class ExtendedTicket:
    """Ticket with from_rearm tracking to prevent recursive rearm cycle."""
    direction: str
    entry_price: float
    opened_idx: int
    from_rearm: bool = False


@dataclass(frozen=True)
class BlackVariant:
    name: str
    # Core config
    momentum_gate: bool = True
    close_alpha: float = 0.50
    cooldown_bars: int = 0
    # Dimension 1: directional bias
    only_sells: bool = False
    only_buys: bool = False
    # Dimension 2: time-of-day gating (UTC hours to trade)
    trade_hours: set[int] | None = None  # None = all hours
    # Dimension 3: asymmetric close gap
    sell_gap: int = 2
    buy_gap: int = 2
    # Dimension 4: close side uses bar-lerp alpha toward extreme
    # (already handled in close logic below)


VARIANTS = [
    # Baseline: momentum + alpha50 (already verified at $94K)
    BlackVariant(name="baseline_mom_a50", momentum_gate=True, close_alpha=0.50),

    # --- Dimension 1: Directional Bias ---
    BlackVariant(name="sells_only", momentum_gate=True, close_alpha=0.50, only_sells=True),
    BlackVariant(name="buys_only", momentum_gate=True, close_alpha=0.50, only_buys=True),

    # --- Dimension 2: Time-of-Day Gating ---
    # London open: 7:00-11:00 UTC
    BlackVariant(name="london_only", momentum_gate=True, close_alpha=0.50, trade_hours=set(range(7, 12))),
    # US open: 13:00-17:00 UTC
    BlackVariant(name="us_open_only", momentum_gate=True, close_alpha=0.50, trade_hours=set(range(13, 18))),
    # London+US overlap: 13:00-16:00 UTC (best hours)
    BlackVariant(name="overlap_only", momentum_gate=True, close_alpha=0.50, trade_hours=set(range(13, 17))),
    # Kill zone: 7:00-11:00 + 13:00-17:00
    BlackVariant(name="kill_zones", momentum_gate=True, close_alpha=0.50, trade_hours=set(range(7, 12)) | set(range(13, 18))),
    # Asian session only (counter-hypothesis)
    BlackVariant(name="asian_only", momentum_gate=True, close_alpha=0.50, trade_hours=set(range(0, 7)) | set(range(22, 24))),

    # --- Dimension 3: Asymmetric Close Gap ---
    BlackVariant(name="sell_gap1_buy_gap2", momentum_gate=True, close_alpha=0.50, sell_gap=1, buy_gap=2),
    BlackVariant(name="sell_gap1_buy_gap3", momentum_gate=True, close_alpha=0.50, sell_gap=1, buy_gap=3),
    BlackVariant(name="sell_gap2_buy_gap1", momentum_gate=True, close_alpha=0.50, sell_gap=2, buy_gap=1),

    # qwen-main's $248K discovery: gap=1 both sides + alpha=1.0
    BlackVariant(name="sg1_bg1_a100", momentum_gate=True, close_alpha=1.00, sell_gap=1, buy_gap=1),
    BlackVariant(name="sg1_bg1_a075", momentum_gate=True, close_alpha=0.75, sell_gap=1, buy_gap=1),
    BlackVariant(name="sg1_bg1_a050", momentum_gate=True, close_alpha=0.50, sell_gap=1, buy_gap=1),

    # --- Dimension 4: Aggressive Alpha ---
    BlackVariant(name="mom_alpha75", momentum_gate=True, close_alpha=0.75),
    BlackVariant(name="mom_alpha100", momentum_gate=True, close_alpha=1.00),
    BlackVariant(name="mom_alpha75_cool6", momentum_gate=True, close_alpha=0.75, cooldown_bars=6),
    BlackVariant(name="mom_alpha100_cool6", momentum_gate=True, close_alpha=1.00, cooldown_bars=6),

    # --- COMBO: Best of each dimension ---
    BlackVariant(name="sells_alpha75", momentum_gate=True, close_alpha=0.75, only_sells=True),
    BlackVariant(name="sells_alpha100", momentum_gate=True, close_alpha=1.00, only_sells=True),
    BlackVariant(name="killzones_alpha75", momentum_gate=True, close_alpha=0.75, trade_hours=set(range(7, 12)) | set(range(13, 18))),
    BlackVariant(name="killzones_alpha100", momentum_gate=True, close_alpha=1.00, trade_hours=set(range(7, 12)) | set(range(13, 18))),
]


def simulate_black(
    symbol: str, bars: list[dict], symbol_info, cfg: RawConfig, variant: BlackVariant
) -> dict:
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size

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
    rearm_opens = 0
    max_open = 0
    level_reuse: dict[float, int] = {}
    rearm_tokens: list[dict] = []

    # Pre-compute bar hours for time-of-day gating
    # bars have bar_time field (Unix timestamp)
    import datetime
    bar_hours: list[int] = []
    for b in bars:
        if "bar_time" in b:
            bar_hours.append(datetime.datetime.fromtimestamp(b["bar_time"], tz=datetime.timezone.utc).hour)
        else:
            bar_hours.append(0)

    for idx in range(1, len(bars)):
        bar = bars[idx]
        hour = bar_hours[idx] if idx < len(bar_hours) else 12

        # Time-of-day gating
        if variant.trade_hours is not None and hour not in variant.trade_hours:
            # Still check closes for open positions, but don't open new ones
            pass  # We handle this per-entry below

        open_buy = sum(1 for t in open_tickets if t.direction == "BUY")
        open_sell = sum(1 for t in open_tickets if t.direction == "SELL")

        current_sell_step = dynamic_step(base_step_px, open_sell, adapt_cfg)
        current_buy_step = dynamic_step(base_step_px, open_buy, adapt_cfg)

        # --- Entries (respect time gating and directional bias) ---
        if variant.trade_hours is None or hour in variant.trade_hours:
            if not variant.only_buys:
                while bar["high"] >= next_sell_level and open_sell < cfg.max_open_per_side:
                    open_tickets.append(ExtendedTicket(direction="SELL", entry_price=next_sell_level, opened_idx=idx, from_rearm=False))
                    open_sell += 1
                    current_sell_step = dynamic_step(base_step_px, open_sell, adapt_cfg)
                    next_sell_level += current_sell_step

            if not variant.only_sells:
                while bar["low"] <= next_buy_level and open_buy < cfg.max_open_per_side:
                    open_tickets.append(ExtendedTicket(direction="BUY", entry_price=next_buy_level, opened_idx=idx, from_rearm=False))
                    open_buy += 1
                    current_buy_step = dynamic_step(base_step_px, open_buy, adapt_cfg)
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
            if variant.trade_hours is not None and hour not in variant.trade_hours:
                continue

            # Momentum gate
            if variant.momentum_gate:
                if token["direction"] == "SELL" and bar["close"] >= token["level"]:
                    continue
                if token["direction"] == "BUY" and bar["close"] <= token["level"]:
                    continue

            if token["direction"] == "SELL" and bar["high"] >= token["level"]:
                open_tickets.append(ExtendedTicket(direction="SELL", entry_price=token["level"], opened_idx=idx, from_rearm=True))
                rearm_tokens.remove(token)
                open_sell += 1
                rearm_opens += 1
            elif token["direction"] == "BUY" and bar["low"] <= token["level"]:
                open_tickets.append(ExtendedTicket(direction="BUY", entry_price=token["level"], opened_idx=idx, from_rearm=True))
                rearm_tokens.remove(token)
                open_buy += 1
                rearm_opens += 1

        # --- Closes (asymmetric gap) ---
        if not variant.only_buys:
            sells = sorted([t for t in open_tickets if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
            gap = variant.sell_gap
            while len(sells) > gap and bar["low"] <= sells[gap].entry_price:
                outer = sells[0]
                ref_level = sells[gap].entry_price
                close_px = ref_level + (bar["low"] - ref_level) * variant.close_alpha
                realized_pnls.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, close_px, spread_px))
                open_tickets.remove(outer)
                # Create rearm token ONLY from main lattice closes (not rearm-origin tickets)
                if not getattr(outer, 'from_rearm', False):
                    level_idx = int(round((outer.entry_price - anchor) / base_step_px))
                    if level_idx >= 2:
                        reuse = level_reuse.get(outer.entry_price, 0)
                        rearm_tokens.append({
                            "direction": "SELL", "level": outer.entry_price, "level_idx": level_idx,
                            "armed": False, "cooldown_until": idx + variant.cooldown_bars,
                        })
                        level_reuse[outer.entry_price] = reuse + 1
                sells = sorted([t for t in open_tickets if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)

        if not variant.only_sells:
            buys = sorted([t for t in open_tickets if t.direction == "BUY"], key=lambda t: t.entry_price)
            gap = variant.buy_gap
            while len(buys) > gap and bar["high"] >= buys[gap].entry_price:
                outer = buys[0]
                ref_level = buys[gap].entry_price
                close_px = ref_level + (bar["high"] - ref_level) * variant.close_alpha
                realized_pnls.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, close_px, spread_px))
                open_tickets.remove(outer)
                # Create rearm token ONLY from main lattice closes (not rearm-origin tickets)
                if not getattr(outer, 'from_rearm', False):
                    level_idx = int(round((anchor - outer.entry_price) / base_step_px))
                    if level_idx >= 2:
                        reuse = level_reuse.get(outer.entry_price, 0)
                        rearm_tokens.append({
                            "direction": "BUY", "level": outer.entry_price, "level_idx": level_idx,
                            "armed": False, "cooldown_until": idx + variant.cooldown_bars,
                        })
                        level_reuse[outer.entry_price] = reuse + 1
                buys = sorted([t for t in open_tickets if t.direction == "BUY"], key=lambda t: t.entry_price)

        max_open = max(max_open, len(open_tickets))

        if not open_tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px
            rearm_tokens = []
            level_reuse = {}

    last_close = bars[-1]["close"]
    floating = [unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px) for t in open_tickets]

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

    print(f"\n{'='*90}")
    print(f"  BLACK MARKET RESEARCH SWEEP — Multiplier Hunt")
    print(f"{'='*90}")

    # Get baseline total for comparison
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
    totals: dict[str, float] = {}
    by_symbol: dict[str, dict[str, float]] = {}

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
            r = simulate_black(sym, bars, info, cfg, v)
            val = float(r["combined_net_usd"])
            details[sym] = val
            total += val

        totals[v.name] = total
        by_symbol[v.name] = details
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

        flag = " 🚀" if delta > 20000 else " ✨" if delta > 10000 else ""
        print(f"  {v.name:<35} ${total:>12,.2f}  delta=${delta:>11,.2f}  {mult:.2f}x  | GBP=${gbp:,.0f} EUR=${eur:,.0f} NZD=${nzd:,.0f}{flag}")

    # Summary sorted by total
    print(f"\n{'='*90}")
    print(f"  LEADERBOARD (sorted)")
    print(f"{'='*90}")
    sorted_rows = sorted(all_rows, key=lambda r: r["total"], reverse=True)
    for i, r in enumerate(sorted_rows, 1):
        flag = "🏆" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "  "
        print(f"  {flag} {i:>2}. {r['variant']:<35} ${r['total']:>12,.2f}  ({r['mult']:.2f}x)  Δ=${r['delta']:>+11,.2f}")

    # Save CSV
    out = ROOT / "reports" / "black_market_research_sweep.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["variant", "total", "delta", "mult", "GBPUSD", "EURUSD", "NZDUSD"])
        w.writeheader()
        w.writerows(all_rows)
    print(f"\nWrote {out}")

    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
