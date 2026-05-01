#!/usr/bin/env python3
"""Regime-Switching Architecture Prototype — BTCUSD H1"""
from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import Ticket, dynamic_step, pip_size_for, spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
SYMBOL = "BTCUSD"
DAYS = 90
TIMEFRAME = mt5.TIMEFRAME_H1


@dataclass
class RegimeConfig:
    name: str
    step: float
    max_open: int
    gap: int
    alpha: float
    momentum_gate: bool


REGIME_CONFIGS = {
    "ranging": RegimeConfig(name="ranging", step=50.0, max_open=40, gap=1, alpha=1.0, momentum_gate=True),
    "trending": RegimeConfig(name="trending", step=100.0, max_open=80, gap=2, alpha=0.5, momentum_gate=True),
    "vol_expansion": RegimeConfig(name="vol_expansion", step=150.0, max_open=60, gap=1, alpha=0.75, momentum_gate=True),
}


def load_h1_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, 24 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])}
        for r in rates
    ]


def compute_atr(bars: list[dict], period: int = 14) -> list[float]:
    trs = []
    for i in range(1, len(bars)):
        tr = max(bars[i]["high"] - bars[i]["low"],
                 abs(bars[i]["high"] - bars[i-1]["close"]),
                 abs(bars[i]["low"] - bars[i-1]["close"]))
        trs.append(tr)
    atr = []
    for i in range(len(trs)):
        if i < period:
            atr.append(sum(trs[:i+1]) / (i+1))
        else:
            atr.append((atr[-1] * (period-1) + trs[i]) / period)
    return atr


def compute_adx(bars: list[dict], period: int = 14) -> list[float]:
    # Simplified ADX using directional movement
    p_dm = []
    n_dm = []
    for i in range(1, len(bars)):
        up_move = bars[i]["high"] - bars[i-1]["high"]
        down_move = bars[i-1]["low"] - bars[i]["low"]
        p_dm.append(max(up_move, 0) if up_move > down_move else 0)
        n_dm.append(max(down_move, 0) if down_move > up_move else 0)
    
    atr = compute_atr(bars, period)
    p_di = []
    n_di = []
    for i in range(len(atr)):
        if atr[i] > 0:
            p_di.append(100 * sum(p_dm[:i+1]) / sum(atr[:i+1]))
            n_di.append(100 * sum(n_dm[:i+1]) / sum(atr[:i+1]))
        else:
            p_di.append(0)
            n_di.append(0)
    
    adx = []
    for i in range(len(atr)):
        if p_di[i] + n_di[i] > 0:
            dx = 100 * abs(p_di[i] - n_di[i]) / (p_di[i] + n_di[i])
        else:
            dx = 0
        if i < period:
            adx.append(sum([100 * abs(p_di[j] - n_di[j]) / max(p_di[j] + n_di[j], 0.001) for j in range(i+1)]) / (i+1))
        else:
            adx.append((adx[-1] * (period-1) + dx) / period)
    return adx


def classify_regime(bars: list[dict], atr: list[float], adx: list[float],
                    atr_threshold: float = 500.0, adx_threshold: float = 25.0) -> list[str]:
    """Classify each bar as ranging, trending, or vol_expansion."""
    regimes = ["ranging"]  # First bar has no indicators
    for i in range(len(atr)):
        if atr[i] > atr_threshold * 1.5:
            regimes.append("vol_expansion")
        elif adx[i] > adx_threshold:
            regimes.append("trending")
        else:
            regimes.append("ranging")
    return regimes


@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int
    regime: str = "ranging"


def simulate_regime_switching(symbol: str, bars: list[dict], info, regimes: list[str]) -> dict:
    """Run regime-switching simulation."""
    pip_size = pip_size_for(info)
    spread_px = spread_price(info)
    
    anchor = bars[0]["close"]
    next_sell = anchor + 50.0  # Default step
    next_buy = anchor - 50.0
    
    tickets: list[Ticket] = []
    realized: list[float] = []
    regime_switches = 0
    current_regime = "ranging"
    
    for idx in range(1, len(bars)):
        bar = bars[idx]
        regime = regimes[idx] if idx < len(regimes) else "ranging"
        
        if regime != current_regime:
            regime_switches += 1
            current_regime = regime
        
        cfg = REGIME_CONFIGS[regime]
        step = cfg.step
        
        # Adjust next levels based on regime change
        os = sum(1 for t in tickets if t.direction == "SELL")
        ob = sum(1 for t in tickets if t.direction == "BUY")
        
        # Entries
        while bar["high"] >= next_sell and os < cfg.max_open:
            tickets.append(Ticket(direction="SELL", entry_price=next_sell, opened_idx=idx, regime=regime))
            os += 1
            next_sell += step
        
        while bar["low"] <= next_buy and ob < cfg.max_open:
            tickets.append(Ticket(direction="BUY", entry_price=next_buy, opened_idx=idx, regime=regime))
            ob += 1
            next_buy -= step
        
        # Closes with alpha
        sl = sorted([t for t in tickets if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        while len(sl) > cfg.gap and bar["low"] <= sl[cfg.gap].entry_price:
            outer = sl[0]
            ref = sl[cfg.gap].entry_price
            close_px = ref + (bar["low"] - ref) * cfg.alpha
            pnl = unit_pnl_usd(symbol, "SELL", outer.entry_price, close_px, spread_px)
            realized.append(pnl)
            tickets.remove(outer)
            sl = sorted([t for t in tickets if t.direction == "SELL"], key=lambda t: t.entry_price, reverse=True)
        
        bl = sorted([t for t in tickets if t.direction == "BUY"], key=lambda t: t.entry_price)
        while len(bl) > cfg.gap and bar["high"] >= bl[cfg.gap].entry_price:
            outer = bl[0]
            ref = bl[cfg.gap].entry_price
            close_px = ref + (bar["high"] - ref) * cfg.alpha
            pnl = unit_pnl_usd(symbol, "BUY", outer.entry_price, close_px, spread_px)
            realized.append(pnl)
            tickets.remove(outer)
            bl = sorted([t for t in tickets if t.direction == "BUY"], key=lambda t: t.entry_price)
        
        # Anchor reset
        if not tickets and abs(bar["close"] - anchor) >= step:
            anchor = bar["close"]
            next_sell = anchor + step
            next_buy = anchor - step
    
    floating = [unit_pnl_usd(symbol, t.direction, t.entry_price, bars[-1]["close"], spread_px) for t in tickets]
    
    realized_net = sum(realized)
    floating_net = sum(floating)
    
    # Count regime distribution
    regime_counts = {}
    for r in regimes:
        regime_counts[r] = regime_counts.get(r, 0) + 1
    
    return {
        "combined_net_usd": round(realized_net + floating_net, 3),
        "realized_net_usd": round(realized_net, 3),
        "floating_net_usd": round(floating_net, 3),
        "realized_closes": len(realized),
        "regime_switches": regime_switches,
        "regime_distribution": regime_counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=SYMBOL)
    parser.add_argument("--days", type=int, default=DAYS)
    parser.add_argument("--atr-threshold", type=float, default=500.0)
    parser.add_argument("--adx-threshold", type=float, default=25.0)
    parser.add_argument("--output-csv", default=str(ROOT / "reports" / "regime_switching_sweep.csv"))
    args = parser.parse_args()
    
    mt5.initialize()
    
    info = mt5.symbol_info(args.symbol)
    bars = load_h1_bars(args.symbol, args.days)
    
    if not bars:
        print("No bars loaded")
        return 1
    
    atr = compute_atr(bars, 14)
    adx = compute_adx(bars, 14)
    
    # Baseline (no regime switching, fixed config)
    baseline_cfg = RawConfig(step_pips=50.0, max_open_per_side=40, close_mode="two_level")
    baseline = simulate_raw_close2(args.symbol, bars, info, baseline_cfg)
    baseline_total = float(baseline["combined_net_usd"])
    
    print(f"\n{'='*80}")
    print(f"  REGIME-SWITCHING ARCHITECTURE — {args.symbol} H1 {args.days}d")
    print(f"{'='*80}")
    print(f"\nBaseline (fixed config): ${baseline_total:,.2f}")
    print(f"ATR range: {min(atr):.1f} - {max(atr):.1f}")
    print(f"ADX range: {min(adx):.1f} - {max(adx):.1f}")
    
    # Test different thresholds
    results = []
    for atr_mult in [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]:
        for adx_val in [15, 20, 25, 30, 35]:
            atr_thresh = atr_mult * 500.0
            regimes = classify_regime(bars, atr, adx, atr_thresh, adx_val)
            result = simulate_regime_switching(args.symbol, bars, info, regimes)
            
            regime_dist = result["regime_distribution"]
            total_bars = sum(regime_dist.values())
            
            results.append({
                "atr_mult": atr_mult,
                "adx_threshold": adx_val,
                "atr_threshold": atr_thresh,
                "combined": result["combined_net_usd"],
                "realized": result["realized_net_usd"],
                "closes": result["realized_closes"],
                "switches": result["regime_switches"],
                "ranging_pct": regime_dist.get("ranging", 0) / total_bars * 100,
                "trending_pct": regime_dist.get("trending", 0) / total_bars * 100,
                "vol_expansion_pct": regime_dist.get("vol_expansion", 0) / total_bars * 100,
            })
    
    # Sort by combined
    results.sort(key=lambda r: r["combined"], reverse=True)
    
    print(f"\n{'ATR Mult':>8} {'ADX':>5} {'Ranging%':>9} {'Trend%':>7} {'VolExp%':>7} {'Combined':>12} {'Delta':>10} {'Closes':>7} {'Switches':>9}")
    print("-" * 80)
    
    for r in results[:20]:
        delta = r["combined"] - baseline_total
        print(f"{r['atr_mult']:>8.2f} {r['adx_threshold']:>5.0f} {r['ranging_pct']:>8.1f}% {r['trending_pct']:>6.1f}% {r['vol_expansion_pct']:>6.1f}% ${r['combined']:>11,.2f} ${delta:>+9,.2f} {r['closes']:>7} {r['switches']:>9}")
    
    # Save to CSV
    out_path = Path(args.output_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    
    print(f"\nWrote {out_path}")
    
    best = results[0]
    print(f"\n🏆 Best: ATR_mult={best['atr_mult']}, ADX={best['adx_threshold']}")
    print(f"   Regime split: {best['ranging_pct']:.1f}% ranging, {best['trending_pct']:.1f}% trending, {best['vol_expansion_pct']:.1f}% vol_expansion")
    print(f"   Combined: ${best['combined']:,.2f} vs baseline ${baseline_total:,.2f} (+${best['combined']-baseline_total:,.2f})")
    
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
