#!/usr/bin/env python3
"""
FX MULTI-TIMEFRAME STACKING TEST — M5+M15+H1 cascade + counter-trend

Tests the qwen-lattice-003 $326/hr claim on FX pairs with honest fills.

Architecture:
- M5: captures micro-reversals (every 5 min)
- M15: captures macro-reversals (every 15 min)  
- H1: captures mega-reversals (every hour)
- Counter-trend: opens opposite positions during cascade closes

Each timeframe runs INDEPENDENTLY with its own anchor and position set.
Combined $/hr = sum of individual $/hr (should be nearly additive).

Tested on: GBPUSD, EURUSD, AUDUSD, NZDUSD, USDJPY, USDCAD
"""
import MetaTrader5 as mt5
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from penetration_lattice_lab_v2 import spread_price, unit_pnl_usd
from dataclasses import dataclass, field
from datetime import datetime, timezone

mt5.initialize()

@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int

@dataclass
class SymbolState:
    symbol: str
    timeframe: str
    realized_closes: int = 0
    realized_net_usd: float = 0.0
    anchor_resets: int = 0
    max_open_total: int = 0
    final_open: int = 0
    counter_opens: int = 0
    counter_closes: int = 0


def load_bars(symbol: str, timeframe: int, days: int = 30) -> list:
    """Load historical bars for symbol/timeframe."""
    total_bars = 24 * 4 * days  # M15 bars
    if timeframe == mt5.TIMEFRAME_M5:
        total_bars = 24 * 12 * days
    elif timeframe == mt5.TIMEFRAME_H1:
        total_bars = 24 * days
    
    bars_raw = mt5.copy_rates_from_pos(symbol, timeframe, 0, total_bars)
    if bars_raw is None or len(bars_raw) == 0:
        return []
    
    return [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4])} for r in bars_raw]


def run_cascade_lattice(symbol: str, bars: list, cfg: dict, tf_name: str) -> SymbolState:
    """Run a single cascade lattice on given bars.
    
    cfg keys:
    - step_px: step size in price units
    - max_open_per_side: max positions per side
    - hold_frontier: how many outermost positions to NOT close (0 = close all)
    - counter_trend: whether to open opposite positions during cascade
    """
    if not bars or len(bars) < 100:
        return SymbolState(symbol=symbol, timeframe=tf_name)

    info = mt5.symbol_info(symbol)
    if info is None:
        return SymbolState(symbol=symbol, timeframe=tf_name)

    spread_px = spread_price(info)
    step_px = cfg.get("step_px", 0.0005)  # 0.5 pip default for FX
    max_open = cfg.get("max_open_per_side", 60)
    hold_frontier = cfg.get("hold_frontier", 0)
    counter_trend = cfg.get("counter_trend", True)

    tickets = []
    realized = 0.0
    closes = 0
    max_open_total = 0
    anchor_resets = 0
    counter_opens = 0
    counter_closes = 0
    last_bar_time = int(bars[0]["time"])

    anchor = bars[0]["close"]
    next_sell_level = 1
    next_buy_level = 1

    for idx in range(1, len(bars)):
        bar = bars[idx]
        if int(bar["time"]) <= last_bar_time:
            continue
        last_bar_time = int(bar["time"])

        # === Open positions ===
        sell_count = sum(1 for t in tickets if t.direction == "SELL")
        buy_count = sum(1 for t in tickets if t.direction == "BUY")

        # Open SELLs as price rises above anchor
        while bar["high"] >= anchor + (next_sell_level * step_px) and sell_count < max_open:
            entry = anchor + (next_sell_level * step_px)
            tickets.append(Ticket(direction="SELL", entry_price=entry, opened_idx=idx))
            sell_count += 1
            next_sell_level += 1

        # Open BUYs as price drops below anchor
        while bar["low"] <= anchor - (next_buy_level * step_px) and buy_count < max_open:
            entry = anchor - (next_buy_level * step_px)
            tickets.append(Ticket(direction="BUY", entry_price=entry, opened_idx=idx))
            buy_count += 1
            next_buy_level += 1

        # === CASCADE CLOSE: close all when price reverses to anchor area ===
        # Close SELLs if price drops back to anchor area
        sell_tickets = sorted([t for t in tickets if t.direction == "SELL"], key=lambda t: t.entry_price)
        if sell_tickets and bar["low"] <= sell_tickets[0].entry_price:
            # Determine which to close (hold frontier if configured)
            if hold_frontier > 0 and len(sell_tickets) > hold_frontier:
                to_close = sell_tickets[:-hold_frontier]  # Keep outermost
            else:
                to_close = sell_tickets

            for t in to_close:
                pnl = unit_pnl_usd(symbol, "SELL", t.entry_price, bar["low"], spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1

                # COUNTER-TREND: open BUY at bar low during SELL cascade close
                if counter_trend and buy_count < max_open:
                    tickets.append(Ticket(direction="BUY", entry_price=bar["low"], opened_idx=idx))
                    counter_opens += 1
                    buy_count += 1

        # Close BUYs if price rises back to anchor area
        buy_tickets = sorted([t for t in tickets if t.direction == "BUY"], key=lambda t: t.entry_price, reverse=True)
        if buy_tickets and bar["high"] >= buy_tickets[0].entry_price:
            if hold_frontier > 0 and len(buy_tickets) > hold_frontier:
                to_close = buy_tickets[:-hold_frontier]
            else:
                to_close = buy_tickets

            for t in to_close:
                pnl = unit_pnl_usd(symbol, "BUY", t.entry_price, bar["high"], spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1

                # COUNTER-TREND: open SELL at bar high during BUY cascade close
                if counter_trend and sell_count < max_open:
                    tickets.append(Ticket(direction="SELL", entry_price=bar["high"], opened_idx=idx))
                    counter_opens += 1
                    sell_count += 1

        # === Anchor reset: if price trends far enough, rebase ===
        if abs(bar["close"] - anchor) >= step_px * 10:
            # Close all remaining positions at current price
            for t in list(tickets):
                if t.direction == "SELL":
                    pnl = unit_pnl_usd(symbol, "SELL", t.entry_price, bar["close"], spread_px)
                else:
                    pnl = unit_pnl_usd(symbol, "BUY", t.entry_price, bar["close"], spread_px)
                realized += pnl
                tickets.remove(t)
                closes += 1

            anchor = bar["close"]
            next_sell_level = 1
            next_buy_level = 1
            anchor_resets += 1

        max_open_total = max(max_open_total, len(tickets))

    return SymbolState(
        symbol=symbol, timeframe=tf_name, realized_closes=closes,
        realized_net_usd=round(realized, 3), anchor_resets=anchor_resets,
        max_open_total=max_open_total, final_open=len(tickets),
        counter_opens=counter_opens, counter_closes=0,
    )


def main():
    symbols = {
        "GBPUSD": {"step_m5": 0.0005, "step_m15": 0.0005, "step_h1": 0.0020, "pip": 0.0001},
        "EURUSD": {"step_m5": 0.0005, "step_m15": 0.0005, "step_h1": 0.0020, "pip": 0.0001},
        "AUDUSD": {"step_m5": 0.0005, "step_m15": 0.0005, "step_h1": 0.0020, "pip": 0.0001},
        "NZDUSD": {"step_m5": 0.0005, "step_m15": 0.0005, "step_h1": 0.0020, "pip": 0.0001},
        "USDJPY": {"step_m5": 0.010, "step_m15": 0.010, "step_h1": 0.050, "pip": 0.01},
        "USDCAD": {"step_m5": 0.0005, "step_m15": 0.0005, "step_h1": 0.0020, "pip": 0.0001},
    }

    days = 30
    print(f"FX MULTI-TIMEFRAME STACKING TEST — {days} days")
    print(f"Architecture: M5 + M15 + H1 cascade + counter-trend")
    print(f"Testing: {', '.join(symbols.keys())}")
    print()

    all_results = []

    for symbol, params in symbols.items():
        print(f"=== {symbol} ===")

        # Load all three timeframes
        bars_m5 = load_bars(symbol, mt5.TIMEFRAME_M5, days)
        bars_m15 = load_bars(symbol, mt5.TIMEFRAME_M15, days)
        bars_h1 = load_bars(symbol, mt5.TIMEFRAME_H1, days)

        if not bars_m5 or not bars_m15 or not bars_h1:
            print(f"  Insufficient data — skipping")
            continue

        print(f"  Bars loaded: M5={len(bars_m5)}, M15={len(bars_m15)}, H1={len(bars_h1)}")

        # Test each timeframe independently
        results = {}
        for tf_name, bars, step_px in [
            ("M5", bars_m5, params["step_m5"]),
            ("M15", bars_m15, params["step_m15"]),
            ("H1", bars_h1, params["step_h1"]),
        ]:
            cfg = {
                "step_px": step_px,
                "max_open_per_side": 60,
                "hold_frontier": 0,  # FX: close all (opposite of BTC)
                "counter_trend": True,
            }
            
            # Calculate hours for this timeframe
            if tf_name == "M5":
                total_hrs = len(bars) * 5 / 60
            elif tf_name == "M15":
                total_hrs = len(bars) * 15 / 60
            else:
                total_hrs = len(bars) * 60 / 60

            state = run_cascade_lattice(symbol, bars, cfg, tf_name)
            closes = state.realized_closes
            net = state.realized_net_usd
            avg = net / closes if closes > 0 else 0
            per_hr = net / total_hrs

            results[tf_name] = {
                "closes": closes, "net": net, "avg": avg, "per_hr": per_hr,
                "resets": state.anchor_resets, "max_open": state.max_open_total,
                "final_open": state.final_open, "counter_opens": state.counter_opens,
            }
            
            print(f"  {tf_name}: ${per_hr:.2f}/hr, {closes}c, ${avg:.4f}/close, {state.anchor_resets} resets, {state.counter_opens} counter-opens")

        # Combined total
        total_per_hr = sum(r["per_hr"] for r in results.values())
        total_closes = sum(r["closes"] for r in results.values())
        total_net = sum(r["net"] for r in results.values())
        
        print(f"  COMBINED: ${total_per_hr:.2f}/hr, {total_closes}c, ${total_net:.2f} net")
        print()

        all_results.append({
            "symbol": symbol,
            "results": results,
            "total_per_hr": total_per_hr,
            "total_closes": total_closes,
            "total_net": total_net,
        })

    # Grand summary
    print("=" * 80)
    print("FX MULTI-TIMEFRAME STACKING — GRAND SUMMARY")
    print("=" * 80)
    print()
    print(f"{'Symbol':<10} {'M5 $/hr':>10} {'M15 $/hr':>10} {'H1 $/hr':>10} {'COMBINED':>10} {'Total Closes':>12}")
    print("-" * 72)
    
    grand_total = 0
    for r in all_results:
        m5 = r["results"].get("M5", {}).get("per_hr", 0)
        m15 = r["results"].get("M15", {}).get("per_hr", 0)
        h1 = r["results"].get("H1", {}).get("per_hr", 0)
        print(f"{r['symbol']:<10} ${m5:>9.2f} ${m15:>9.2f} ${h1:>9.2f} ${r['total_per_hr']:>9.2f} {r['total_closes']:>12}")
        grand_total += r["total_per_hr"]
    
    print("-" * 72)
    print(f"{'TOTAL':<10} {'':>10} {'':>10} {'':>10} ${grand_total:>9.2f}")
    print()
    print(f"Per day:  ${grand_total * 24:.2f}")
    print(f"Per month: ${grand_total * 24 * 30:.2f}")

    # Compare to single-TF baseline
    single_tf_total = sum(r["results"].get("M15", {}).get("per_hr", 0) for r in all_results)
    print(f"\nSingle-TF (M15 only): ${single_tf_total:.2f}/hr")
    print(f"Multi-TF (M5+M15+H1): ${grand_total:.2f}/hr")
    print(f"Improvement: {(grand_total/single_tf_total - 1)*100:.0f}%")

    # Write results to JSON
    import json
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "symbols": {r["symbol"]: {
            "M5": r["results"].get("M5", {}),
            "M15": r["results"].get("M15", {}),
            "H1": r["results"].get("H1", {}),
            "combined_per_hr": r["total_per_hr"],
            "combined_closes": r["total_closes"],
            "combined_net": r["total_net"],
        } for r in all_results},
        "grand_total_per_hr": grand_total,
    }
    
    out_path = Path("reports/fx_multi_tf_stacking_results.json")
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nWrote {out_path}")

    # Also write markdown summary
    md_path = Path("reports/fx_multi_tf_stacking_results.md")
    lines = [
        "# FX Multi-Timeframe Stacking Results",
        "",
        f"Generated: {output['generated_at']}",
        f"Days tested: {days}",
        "",
        "## Per-Symbol Results",
        "",
        f"| Symbol | M5 $/hr | M15 $/hr | H1 $/hr | COMBINED | Total Closes |",
        f"|--------|---------|----------|---------|----------|-------------|",
    ]
    for r in all_results:
        m5 = r["results"].get("M5", {})
        m15 = r["results"].get("M15", {})
        h1 = r["results"].get("H1", {})
        lines.append(f"| {r['symbol']} | ${m5.get('per_hr', 0):.2f} | ${m15.get('per_hr', 0):.2f} | ${h1.get('per_hr', 0):.2f} | ${r['total_per_hr']:.2f} | {r['total_closes']} |")
    
    lines.extend([
        "",
        f"**Grand Total: ${grand_total:.2f}/hr**",
        f"Per day: ${grand_total * 24:.2f}",
        f"Per month: ${grand_total * 24 * 30:.2f}",
        "",
        f"vs Single-TF (M15 only): ${single_tf_total:.2f}/hr",
        f"Improvement: {(grand_total/single_tf_total - 1)*100:.0f}%",
    ])
    
    md_path.write_text("\n".join(lines))
    print(f"Wrote {md_path}")

    mt5.shutdown()


if __name__ == "__main__":
    main()
