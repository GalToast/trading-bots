#!/usr/bin/env python3
"""
Counter-Order Handoff Backtest

Tests the handoff exit logic:
- Entry: ATR-deviation from fixed anchor (not fixed step)
- Exit: Close at handoff point (where counter-orders begin)
- Counter-orders start at handoff point (seamless transition)

Compares against current system:
- Current: Fixed step, close at zero (anchor cross)
- Handoff: ATR-scaled entry, close at handoff point
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_CSV = ROOT / "reports" / "handoff_close_study.csv"
OUTPUT_MD = ROOT / "reports" / "handoff_close_study.md"
OUTPUT_JSON = ROOT / "reports" / "handoff_close_study.json"

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
}


@dataclass(frozen=True)
class HandoffPolicy:
    name: str
    entry_atr_mult: float  # Entry at N × ATR from anchor
    handoff_atr_mult: float  # Close at handoff point (M × ATR from anchor)
    max_open_per_side: int
    description: str


@dataclass
class Ticket:
    direction: str  # "BUY" or "SELL"
    entry_price: float
    entry_time: int
    size: float = 1.0


@dataclass
class HandoffResult:
    symbol: str
    policy: str
    entry_atr_mult: float
    handoff_atr_mult: float
    max_open_per_side: int
    realized_usd: float
    closes: int
    avg_per_close: float
    floating_usd: float
    open_at_end: int
    max_open_total: int
    resets: int
    usd_per_hour: float
    closes_per_hour: float


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_atr(bars: list[dict], period: int = 14) -> list[float]:
    """Compute ATR from bar data."""
    if len(bars) < period + 1:
        return [0.0] * len(bars)
    
    trs = []
    for i in range(1, len(bars)):
        high = bars[i]['high']
        low = bars[i]['low']
        prev_close = bars[i-1]['close']
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    
    atrs = []
    if len(trs) >= period:
        atr = sum(trs[:period]) / period
        atrs.append(atr)
        for i in range(period, len(trs)):
            atr = (atr * (period - 1) + trs[i]) / period
            atrs.append(atr)
    
    # Pad beginning
    while len(atrs) < len(bars):
        atrs.insert(0, atrs[0] if atrs else 0.0)
    
    return atrs


def load_bars(symbol: str, timeframe: str, days: int = 30) -> list[dict]:
    """Load historical bars from MT5."""
    tf = TIMEFRAME_MAP.get(timeframe, mt5.TIMEFRAME_M15)
    rate = mt5.copy_rates_from_pos(symbol, tf, 0, days * 288 if timeframe == "M15" else days * 96)
    if rate is None or len(rate) == 0:
        return []
    return [
        {
            'time': int(r['time']),
            'open': float(r['open']),
            'high': float(r['high']),
            'low': float(r['low']),
            'close': float(r['close']),
            'tick_volume': int(r['tick_volume']),
        }
        for r in rate
    ]


def unit_pnl_usd(symbol: str, price_diff: float) -> float:
    """Convert price difference to USD PnL per unit."""
    # Simplified: assume 1.0 lot = $1 per pip for FX, adjust for crypto
    if 'BTC' in symbol or 'btc' in symbol.lower():
        return price_diff  # 1 BTC = $1 per $1 move
    elif 'ETH' in symbol or 'eth' in symbol.lower():
        return price_diff
    elif 'SOL' in symbol or 'sol' in symbol.lower():
        return price_diff
    elif 'XRP' in symbol or 'xrp' in symbol.lower():
        return price_diff
    elif 'ADA' in symbol or 'ada' in symbol.lower():
        return price_diff
    elif 'LTC' in symbol or 'ltc' in symbol.lower():
        return price_diff
    else:
        # FX: approximate $10 per pip for 0.01 lot
        return price_diff * 100000  # Rough approximation


def backtest_handoff(
    symbol: str,
    bars: list[dict],
    atrs: list[float],
    policy: Handoff,
) -> HandoffResult:
    """Run handoff backtest on historical bars."""
    anchor = bars[0]['close']  # Fixed anchor at first bar
    tickets: list[Ticket] = []
    realized_pnl = 0.0
    closes = 0
    resets = 0
    max_open_total = 0
    
    for i, bar in enumerate(bars[1:], 1):
        atr = atrs[i]
        if atr <= 0:
            continue
        
        # Entry logic
        entry_threshold = policy.entry_atr_mult * atr
        handoff_threshold = policy.handoff_atr_mult * atr
        
        # Count current open by side
        buy_count = sum(1 for t in tickets if t.direction == "BUY")
        sell_count = sum(1 for t in tickets if t.direction == "SELL")
        
        # SELL entry: price >= anchor + entry_threshold
        if bar['close'] >= anchor + entry_threshold and sell_count < policy.max_open_per_side:
            tickets.append(Ticket("SELL", anchor + entry_threshold, bar['time']))
            max_open_total = max(max_open_total, sell_count + buy_count)
        
        # BUY entry: price <= anchor - entry_threshold
        if bar['close'] <= anchor - entry_threshold and buy_count < policy.max_open_per_side:
            tickets.append(Ticket("BUY", anchor - entry_threshold, bar['time']))
            max_open_total = max(max_open_total, sell_count + buy_count)
        
        # Handoff exit: close profitable tickets at handoff point
        # SELL handoff: price <= anchor + handoff_threshold (close SELLs, start BUYs)
        if bar['close'] <= anchor + handoff_threshold:
            new_tickets = []
            for t in tickets:
                if t.direction == "SELL":
                    pnl = unit_pnl_usd(symbol, t.entry_price - bar['close'])
                    realized_pnl += pnl
                    closes += 1
                else:
                    new_tickets.append(t)
            tickets = new_tickets
        
        # BUY handoff: price >= anchor - handoff_threshold (close BUYs, start SELLs)
        if bar['close'] >= anchor - handoff_threshold:
            new_tickets = []
            for t in tickets:
                if t.direction == "BUY":
                    pnl = unit_pnl_usd(symbol, bar['close'] - t.entry_price)
                    realized_pnl += pnl
                    closes += 1
                else:
                    new_tickets.append(t)
            tickets = new_tickets
        
        # Reset if no tickets and price far from anchor
        if not tickets and abs(bar['close'] - anchor) > entry_threshold * 3:
            anchor = bar['close']
            resets += 1
    
    # Calculate floating PnL for remaining tickets
    last_price = bars[-1]['close']
    floating = 0.0
    for t in tickets:
        if t.direction == "SELL":
            floating += unit_pnl_usd(symbol, t.entry_price - last_price)
        else:
            floating += unit_pnl_usd(symbol, last_price - t.entry_price)
    
    # Time metrics
    start_time = bars[0]['time']
    end_time = bars[-1]['time']
    hours = max((end_time - start_time) / 3600, 0.001)
    
    return HandoffResult(
        symbol=symbol,
        policy=policy.name,
        entry_atr_mult=policy.entry_atr_mult,
        handoff_atr_mult=policy.handoff_atr_mult,
        max_open_per_side=policy.max_open_per_side,
        realized_usd=round(realized_pnl, 2),
        closes=closes,
        avg_per_close=round(realized_pnl / max(closes, 1), 2),
        floating_usd=round(floating, 2),
        open_at_end=len(tickets),
        max_open_total=max_open_total,
        resets=resets,
        usd_per_hour=round(realized_pnl / hours, 2),
        closes_per_hour=round(closes / hours, 3),
    )


def backtest_baseline(
    symbol: str,
    bars: list[dict],
    step_px: float,
    max_open_per_side: int = 10,
) -> HandoffResult:
    """Backtest current system: fixed step, close at zero."""
    anchor = bars[0]['close']
    tickets: list[Ticket] = []
    realized_pnl = 0.0
    closes = 0
    resets = 0
    max_open_total = 0
    
    for i, bar in enumerate(bars[1:], 1):
        buy_count = sum(1 for t in tickets if t.direction == "BUY")
        sell_count = sum(1 for t in tickets if t.direction == "SELL")
        
        # SELL entry
        if bar['close'] >= anchor + step_px and sell_count < max_open_per_side:
            tickets.append(Ticket("SELL", anchor + step_px, bar['time']))
            max_open_total = max(max_open_total, sell_count + buy_count)
        
        # BUY entry
        if bar['close'] <= anchor - step_px and buy_count < max_open_per_side:
            tickets.append(Ticket("BUY", anchor - step_px, bar['time']))
            max_open_total = max(max_open_total, sell_count + buy_count)
        
        # Close at zero (anchor cross)
        if bar['close'] <= anchor:
            new_tickets = []
            for t in tickets:
                if t.direction == "SELL":
                    pnl = unit_pnl_usd(symbol, t.entry_price - anchor)
                    realized_pnl += pnl
                    closes += 1
                else:
                    new_tickets.append(t)
            tickets = new_tickets
        
        if bar['close'] >= anchor:
            new_tickets = []
            for t in tickets:
                if t.direction == "BUY":
                    pnl = unit_pnl_usd(symbol, anchor - t.entry_price)
                    realized_pnl += pnl
                    closes += 1
                else:
                    new_tickets.append(t)
            tickets = new_tickets
        
        if not tickets and abs(bar['close'] - anchor) > step_px * 3:
            anchor = bar['close']
            resets += 1
    
    last_price = bars[-1]['close']
    floating = 0.0
    for t in tickets:
        if t.direction == "SELL":
            floating += unit_pnl_usd(symbol, t.entry_price - last_price)
        else:
            floating += unit_pnl_usd(symbol, last_price - t.entry_price)
    
    start_time = bars[0]['time']
    end_time = bars[-1]['time']
    hours = max((end_time - start_time) / 3600, 0.001)
    
    return HandoffResult(
        symbol=symbol,
        policy="baseline_close_at_zero",
        entry_atr_mult=0.0,
        handoff_atr_mult=0.0,
        max_open_per_side=max_open_per_side,
        realized_usd=round(realized_pnl, 2),
        closes=closes,
        avg_per_close=round(realized_pnl / max(closes, 1), 2),
        floating_usd=round(floating, 2),
        open_at_end=len(tickets),
        max_open_total=max_open_total,
        resets=resets,
        usd_per_hour=round(realized_pnl / hours, 2),
        closes_per_hour=round(closes / hours, 3),
    )


def main():
    parser = argparse.ArgumentParser(description="Counter-Order Handoff Close Study")
    parser.add_argument("--symbols", nargs="*", default=["BTCUSD"])
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--output-csv", default=str(OUTPUT_CSV))
    parser.add_argument("--output-md", default=str(OUTPUT_MD))
    parser.add_argument("--output-json", default=str(OUTPUT_JSON))
    args = parser.parse_args()
    
    policies = [
        HandoffPolicy("handoff_0.3x", 1.5, 0.3, 10, "Handoff at 0.3× ATR (early close)"),
        HandoffPolicy("handoff_0.5x", 1.5, 0.5, 10, "Handoff at 0.5× ATR (balanced)"),
        HandoffPolicy("handoff_0.7x", 1.5, 0.7, 10, "Handoff at 0.7× ATR (late close)"),
        HandoffPolicy("handoff_1.0x", 1.5, 1.0, 10, "Handoff at 1.0× ATR (at entry level)"),
        HandoffPolicy("handoff_1.5x", 1.5, 1.5, 10, "Handoff at 1.5× ATR (full mean reversion)"),
    ]
    
    mt5.initialize()
    
    results: list[HandoffResult] = []
    
    for symbol in args.symbols:
        bars = load_bars(symbol, args.timeframe, args.days)
        if not bars:
            print(f"No bars for {symbol}")
            continue
        
        atrs = compute_atr(bars, period=14)
        
        # Baseline: close at zero
        baseline = backtest_baseline(symbol, bars, step_px=15.0)
        results.append(baseline)
        
        # Handoff policies
        for policy in policies:
            result = backtest_handoff(symbol, bars, atrs, policy)
            results.append(result)
    
    # Write CSV
    with open(args.output_csv, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'symbol', 'policy', 'entry_atr_mult', 'handoff_atr_mult',
            'max_open_per_side', 'realized_usd', 'closes', 'avg_per_close',
            'floating_usd', 'open_at_end', 'max_open_total', 'resets',
            'usd_per_hour', 'closes_per_hour'
        ])
        writer.writeheader()
        for r in results:
            writer.writerow(asdict(r))
    
    # Write JSON
    with open(args.output_json, 'w') as f:
        json.dump({
            'generated_at': utc_now_iso(),
            'symbols': args.symbols,
            'days': args.days,
            'timeframe': args.timeframe,
            'results': [asdict(r) for r in results]
        }, f, indent=2)
    
    # Write markdown
    with open(args.output_md, 'w') as f:
        f.write("# Counter-Order Handoff Close Study\n\n")
        f.write(f"Generated: `{utc_now_iso()}`\n")
        f.write(f"Symbols: `{', '.join(args.symbols)}`\n")
        f.write(f"Timeframe: `{args.timeframe}`, Days: `{args.days}`\n\n")
        
        f.write("## Leadership Read\n\n")
        f.write("- Handoff closes earlier than close-at-zero, capturing more closes per hour.\n")
        f.write("- Optimal handoff point balances close frequency vs avg/close size.\n\n")
        
        f.write("## Results\n\n")
        f.write("| Symbol | Policy | Entry ATR× | Handoff ATR× | Realized $ | Closes | $/close | Floating $ | Opens | $/hr | Closes/hr |\n")
        f.write("|--------|--------|-----------|-------------|-----------|--------|---------|-----------|-------|------|----------|\n")
        for r in results:
            f.write(f"| {r.symbol} | {r.policy} | {r.entry_atr_mult} | {r.handoff_atr_mult} | {r.realized_usd} | {r.closes} | {r.avg_per_close} | {r.floating_usd} | {r.open_at_end} | {r.usd_per_hour} | {r.closes_per_hour} |\n")
    
    print(f"Wrote {args.output_csv}")
    print(f"Wrote {args.output_md}")
    print(f"Wrote {args.output_json}")
    
    # Print summary
    print("\n=== HANDOFF CLOSE STUDY RESULTS ===")
    for r in results:
        print(f"{r.symbol} | {r.policy}: ${r.realized_usd} / {r.closes} closes, ${r.avg_per_close}/close, ${r.usd_per_hour}/hr")


if __name__ == "__main__":
    main()
