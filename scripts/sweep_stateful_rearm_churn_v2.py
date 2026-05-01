#!/usr/bin/env python3
"""
Stateful rearm v2: entry-decay + regime-tightness gate variants.

Entry decay:
  Each level tracks fire_count. First fire = full size. Second = lot_scale**1.
  Third = lot_scale**2, etc. With lot_scale < 1.0, later fires contribute less.

Regime-tightness gate:
  A rearm token only arms when the recent price action looks "ranging" not "trending".
  Measured as: recent_range / (ATR * K) < threshold. Lower = tighter range = good for rearm.
  When the market is trending (range >> ATR), rearm tokens stay disarmed.

Both can be combined.
"""
from __future__ import annotations

import argparse
import csv
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
DEFAULT_SYMBOLS = ["GBPUSD", "EURUSD", "NZDUSD"]


@dataclass
class RearmTokenV2:
    direction: str
    level: float
    level_idx: int
    armed: bool = False
    fire_count: int = 0  # tracks how many times this level has fired a rearm


@dataclass
class VariantV2:
    name: str
    min_level_idx: int = 1
    excursion_levels: int = 1
    lot_scale: float = 1.0          # <1.0 = entry decay per fire
    regime_window: int = 0          # 0 = no regime gate, >0 = use regime gate
    regime_threshold: float = 1.0   # range / (ATR * K) must be < this to arm
    regime_atr_mult: float = 1.0    # K factor for ATR in regime gate


VARIANTS = [
    # Baseline: same as original stateful rearm
    VariantV2(name="rearm_v2_baseline", min_level_idx=2, excursion_levels=2),
    # Entry decay only: lot_scale=0.6 per fire
    VariantV2(name="rearm_v2_decay_06", min_level_idx=2, excursion_levels=2, lot_scale=0.6),
    # Entry decay stronger: lot_scale=0.5 per fire
    VariantV2(name="rearm_v2_decay_05", min_level_idx=2, excursion_levels=2, lot_scale=0.5),
    # Regime gate only: 30-bar window, threshold=1.5
    VariantV2(name="rearm_v2_regime_30_15", min_level_idx=2, excursion_levels=2, regime_window=30, regime_threshold=1.5),
    # Regime gate tighter: threshold=1.0
    VariantV2(name="rearm_v2_regime_30_10", min_level_idx=2, excursion_levels=2, regime_window=30, regime_threshold=1.0),
    # Regime gate wider window: 60 bars
    VariantV2(name="rearm_v2_regime_60_15", min_level_idx=2, excursion_levels=2, regime_window=60, regime_threshold=1.5),
    # Combined: decay + regime gate
    VariantV2(name="rearm_v2_decay06_regime30_15", min_level_idx=2, excursion_levels=2, lot_scale=0.6, regime_window=30, regime_threshold=1.5),
    VariantV2(name="rearm_v2_decay05_regime30_10", min_level_idx=2, excursion_levels=2, lot_scale=0.5, regime_window=30, regime_threshold=1.0),
    # NZDUSD-specific: require bigger excursion + decay
    VariantV2(name="rearm_v2_nzd_lvl3_exc2_decay05", min_level_idx=3, excursion_levels=2, lot_scale=0.5),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sweep stateful rearm v2 variants: entry decay + regime-tightness gate."
    )
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "stateful_rearm_v2_sweep.csv"),
    )
    return parser.parse_args()


@dataclass
class ExtendedTicket:
    """Ticket with lot_multiplier for entry-decay variants."""
    direction: str
    entry_price: float
    opened_idx: int
    lot_multiplier: float = 1.0


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


def _compute_atr(bars: list[dict], idx: int, window: int = 14) -> float:
    """Simple ATR over `window` bars ending at idx (exclusive of idx)."""
    if idx < window + 1:
        return 0.0
    trs = []
    for i in range(idx - window, idx):
        high = bars[i]["high"]
        low = bars[i]["low"]
        prev_close = bars[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


def _is_ranging(bars: list[dict], idx: int, window: int, threshold: float, atr_mult: float) -> bool:
    """Check if recent price action is 'ranging' vs 'trending'.
    
    Returns True when recent_range / (ATR * atr_mult) < threshold.
    """
    if idx < window:
        return True  # not enough data, default to allowing rearm
    recent_high = max(bars[i]["high"] for i in range(idx - window, idx))
    recent_low = min(bars[i]["low"] for i in range(idx - window, idx))
    recent_range = recent_high - recent_low
    atr = _compute_atr(bars, idx, min(window, 14))
    if atr <= 0:
        return True
    ratio = recent_range / (atr * atr_mult)
    return ratio < threshold


def _update_token_arming_v2(
    tokens: list[RearmTokenV2],
    bar: dict,
    bars: list[dict],
    idx: int,
    base_step_px: float,
    variant: VariantV2,
) -> None:
    for token in tokens:
        if token.armed:
            continue
        # Regime gate check
        if variant.regime_window > 0:
            if not _is_ranging(bars, idx, variant.regime_window, variant.regime_threshold, variant.regime_atr_mult):
                continue  # trending market, don't arm rearm tokens
        if token.direction == "SELL":
            away_trigger = token.level - (variant.excursion_levels * base_step_px)
            if bar["low"] <= away_trigger:
                token.armed = True
        else:
            away_trigger = token.level + (variant.excursion_levels * base_step_px)
            if bar["high"] >= away_trigger:
                token.armed = True


def _consume_rearm_tokens_v2(
    *,
    tokens: list[RearmTokenV2],
    bar: dict,
    idx: int,
    tickets,  # list[ExtendedTicket]
    direction: str,
    max_open_per_side: int,
    lot_scale: float,
) -> int:
    open_count = _side_count(tickets, direction)
    opened = 0
    for token in list(tokens):
        if token.direction != direction or not token.armed:
            continue
        if open_count >= max_open_per_side:
            break
        if direction == "SELL" and bar["high"] >= token.level:
            effective_size = lot_scale ** token.fire_count
            tickets.append(
                ExtendedTicket(
                    direction="SELL",
                    entry_price=token.level,
                    opened_idx=idx,
                    lot_multiplier=effective_size,
                )
            )
            token.fire_count += 1
            tokens.remove(token)
            open_count += 1
            opened += 1
        elif direction == "BUY" and bar["low"] <= token.level:
            effective_size = lot_scale ** token.fire_count
            tickets.append(
                ExtendedTicket(
                    direction="BUY",
                    entry_price=token.level,
                    opened_idx=idx,
                    lot_multiplier=effective_size,
                )
            )
            token.fire_count += 1
            tokens.remove(token)
            open_count += 1
            opened += 1
    return opened


def simulate_stateful_rearm_v2(
    symbol: str, bars: list[dict], symbol_info, cfg: RawConfig, variant: VariantV2
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

    open_tickets: list[ExtendedTicket] = []
    realized_pnls: list[float] = []
    rearm_tokens: list[RearmTokenV2] = []
    rearm_opens = 0
    max_open = 0
    max_open_buy = 0
    max_open_sell = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]

        _update_token_arming_v2(rearm_tokens, bar, bars, idx, base_step_px, variant)

        open_buy = _side_count(open_tickets, "BUY")
        open_sell = _side_count(open_tickets, "SELL")

        current_sell_step = dynamic_step(base_step_px, open_sell, adapt_cfg)
        current_buy_step = dynamic_step(base_step_px, open_buy, adapt_cfg)

        while bar["high"] >= next_sell_level and open_sell < cfg.max_open_per_side:
            open_tickets.append(ExtendedTicket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            open_sell += 1
            current_sell_step = dynamic_step(base_step_px, open_sell, adapt_cfg)
            next_sell_level += current_sell_step

        while bar["low"] <= next_buy_level and open_buy < cfg.max_open_per_side:
            open_tickets.append(ExtendedTicket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            open_buy += 1
            current_buy_step = dynamic_step(base_step_px, open_buy, adapt_cfg)
            next_buy_level -= current_buy_step

        rearm_opens += _consume_rearm_tokens_v2(
            tokens=rearm_tokens,
            bar=bar,
            idx=idx,
            tickets=open_tickets,
            direction="SELL",
            max_open_per_side=cfg.max_open_per_side,
            lot_scale=variant.lot_scale,
        )
        rearm_opens += _consume_rearm_tokens_v2(
            tokens=rearm_tokens,
            bar=bar,
            idx=idx,
            tickets=open_tickets,
            direction="BUY",
            max_open_per_side=cfg.max_open_per_side,
            lot_scale=variant.lot_scale,
        )

        gap = 1 if cfg.close_mode == "one_level" else 2

        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > gap and bar["low"] <= sells[gap].entry_price:
            outer = sells[0]
            close_ref = sells[gap].entry_price
            lot_mult = getattr(outer, "lot_multiplier", 1.0)
            pnl = unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, spread_px) * lot_mult
            realized_pnls.append(pnl)
            open_tickets.remove(outer)
            level_idx = int(round((outer.entry_price - anchor) / base_step_px))
            if level_idx >= variant.min_level_idx:
                rearm_tokens.append(
                    RearmTokenV2(direction="SELL", level=outer.entry_price, level_idx=level_idx)
                )
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > gap and bar["high"] >= buys[gap].entry_price:
            outer = buys[0]
            close_ref = buys[gap].entry_price
            lot_mult = getattr(outer, "lot_multiplier", 1.0)
            pnl = unit_pnl_usd(symbol, "BUY", outer.entry_price, close_ref, spread_px) * lot_mult
            realized_pnls.append(pnl)
            open_tickets.remove(outer)
            level_idx = int(round((anchor - outer.entry_price) / base_step_px))
            if level_idx >= variant.min_level_idx:
                rearm_tokens.append(
                    RearmTokenV2(direction="BUY", level=outer.entry_price, level_idx=level_idx)
                )
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        max_open = max(max_open, len(open_tickets))
        max_open_buy = max(max_open_buy, _side_count(open_tickets, "BUY"))
        max_open_sell = max(max_open_sell, _side_count(open_tickets, "SELL"))

        if not open_tickets and abs(bar["close"] - anchor) >= base_step_px:
            anchor = bar["close"]
            next_sell_level = anchor + base_step_px
            next_buy_level = anchor - base_step_px
            rearm_tokens = []

    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px)
        * getattr(t, "lot_multiplier", 1.0)
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
        rows: list[dict] = []
        baseline_total = 0.0
        variant_totals = {v.name: 0.0 for v in VARIANTS}

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
            baseline = simulate_raw_close2(symbol, bars, info, raw_cfg)
            if not baseline:
                continue
            baseline_total += float(baseline["combined_net_usd"])

            for variant in VARIANTS:
                result = simulate_stateful_rearm_v2(symbol, bars, info, raw_cfg, variant)
                variant_totals[variant.name] += float(result["combined_net_usd"])
                rows.append(
                    {
                        "symbol": symbol,
                        "variant": variant.name,
                        "days": args.days,
                        "step_pips": raw_cfg.step_pips,
                        "max_open_per_side": raw_cfg.max_open_per_side,
                        "baseline_combined_usd": baseline["combined_net_usd"],
                        "baseline_closes": baseline["realized_closes"],
                        "variant_combined_usd": result["combined_net_usd"],
                        "variant_realized_usd": result["realized_net_usd"],
                        "variant_floating_usd": result["floating_net_usd"],
                        "variant_closes": result["realized_closes"],
                        "variant_max_open": result["max_open_total"],
                        "variant_rearm_opens": result["rearm_opens"],
                        "delta_combined_usd": round(
                            result["combined_net_usd"] - baseline["combined_net_usd"], 3
                        ),
                    }
                )

        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "symbol", "variant", "days", "step_pips", "max_open_per_side",
            "baseline_combined_usd", "baseline_closes",
            "variant_combined_usd", "variant_realized_usd", "variant_floating_usd",
            "variant_closes", "variant_max_open", "variant_rearm_opens",
            "delta_combined_usd",
        ]
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        summary_path = out_path.with_name("stateful_rearm_v2_summary.csv")
        with summary_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["variant", "baseline_total_usd", "variant_total_usd", "delta_total_usd"],
            )
            writer.writeheader()
            for variant in VARIANTS:
                writer.writerow(
                    {
                        "variant": variant.name,
                        "baseline_total_usd": round(baseline_total, 3),
                        "variant_total_usd": round(variant_totals[variant.name], 3),
                        "delta_total_usd": round(variant_totals[variant.name] - baseline_total, 3),
                    }
                )

        print(f"Wrote {out_path}")
        print(f"Wrote {summary_path}")
        print(f"\nBaseline total: ${baseline_total:,.2f}")
        for variant in VARIANTS:
            delta = variant_totals[variant.name] - baseline_total
            pct = (delta / baseline_total * 100) if baseline_total else 0
            print(f"  {variant.name}: ${variant_totals[variant.name]:,.2f}  delta=${delta:,.2f} ({pct:+.1f}%)")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
