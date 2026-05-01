#!/usr/bin/env python3
"""
EURUSD Forward-Shadow Test for 3/3 Gap Configuration

Takes the EURUSD `step 1.0/1.0 gap 3/3 outer` configuration that passed
bar-read realism (69.1% retention) and validates it against forward
(out-of-sample) data to check whether the edge persists in unseen market conditions.

This is a WALK-FORWARD test:
- Training period: 60 days ending 7 days ago (the period used for the side-gap ladder)
- Test period: 7 days starting 7 days ago (forward, unseen data)

The key question: does the EURUSD 3/3 edge hold OOS, or was it curve-fit?

Output: reports/eurusd_forward_shadow.md
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_MD = ROOT / "reports" / "eurusd_forward_shadow.md"
DEFAULT_OUTPUT_JSON = ROOT / "reports" / "eurusd_forward_shadow.json"

# ---------------------------------------------------------------------------
# Configuration: EURUSD 3/3 side-gap survivor
# ---------------------------------------------------------------------------
EURUSD_3_3_CONFIG = {
    "symbol": "EURUSD",
    "step_sell": 1.0,
    "step_buy": 1.0,
    "sell_gap": 3,
    "buy_gap": 3,
    "close_alpha": 0.5,
    "close_style": "outer",
    "max_open_per_side": 40,  # from default raw config
    "open_realism_mode": "broker_touch",
    "close_realism_mode": "bar_close",
}

# The 60d backtest reference result (from fx_fixed_shape_side_gap.md)
REFERENCE_60D = {
    "combined_net": 4118.0,
    "realized_net": 3811.0,
    "delta_vs_reference": 307.0,
    "closes": None,  # not in side-gap report
    "same_bar_roundtrip_pct": 44.1,
}


# ---------------------------------------------------------------------------
# Helpers (mirroring the benchmark framework)
# ---------------------------------------------------------------------------

def pip_size_for(symbol_info) -> float:
    """Get pip size for a symbol."""
    point = getattr(symbol_info, "point", 0.00001)
    digits = getattr(symbol_info, "digits", 5)
    if digits == 3 or digits == 5:
        return point * 10
    return point


def spread_price(symbol_info) -> float:
    """Get spread in price units."""
    spread = getattr(symbol_info, "spread", 10)
    point = getattr(symbol_info, "point", 0.00001)
    return spread * point


def unit_pnl_usd(symbol: str, direction: str, entry: float, exit: float, spread: float) -> float:
    """PnL per 0.01 lot unit, in USD."""
    point = 0.00001
    pip = point * 10  # 5-digit broker
    pip_value = 0.10  # EURUSD: $0.10 per pip per 0.01 lot
    if direction == "SELL":
        pips = (entry - exit) / pip
    else:
        pips = (exit - entry) / pip
    # Spread cost: paid on both entry and exit
    spread_pips = 2 * spread / pip
    return (pips - spread_pips) * pip_value


def dynamic_step(base_step: float, open_count: int, cfg) -> float:
    """Adaptive step (from penetration_lattice_lab_v2 pattern)."""
    if open_count >= cfg.adaptive_step_threshold_2:
        return base_step * cfg.adaptive_step_multiplier_2
    if open_count >= cfg.adaptive_step_threshold_1:
        return base_step * cfg.adaptive_step_multiplier_1
    return base_step


def _bar_reaches_price_level(direction: str, level_price: float, bar: dict, spread_px: float, mode: str, purpose: str) -> bool:
    """Check if bar reaches the price level, with realism mode."""
    bar_high = float(bar["high"])
    bar_low = float(bar["low"])
    if mode == "intrabar":
        if direction == "SELL":
            return bar_high >= level_price
        return bar_low <= level_price
    # broker_touch
    if direction == "SELL":
        return bar_low + spread_px >= level_price
    return bar_high - spread_px <= level_price


def _apply_close_realism(direction: str, interp_price: float, bar: dict, mode: str) -> float:
    """Apply close realism constraint."""
    if mode == "intrabar":
        return interp_price
    # bar_close
    if direction == "SELL":
        return max(interp_price, float(bar["close"]))
    return min(interp_price, float(bar["close"]))


def _interp_close(level_price: float, bar_extreme: float, alpha: float) -> float:
    return level_price + alpha * (bar_extreme - level_price)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_m15_bars(symbol: str, days: int, end_time: int | None = None) -> list[dict]:
    """Fetch M15 bars from MT5."""
    if end_time is None:
        end_time = int(time.time())
    start_time = end_time - (days * 86400)

    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, start_time, end_time)
    if rates is None or len(rates) == 0:
        return []

    bars = []
    for r in rates:
        bars.append({
            "time": r[0],
            "open": r[1],
            "high": r[2],
            "low": r[3],
            "close": r[4],
            "tick_volume": r[5],
        })
    return bars


# ---------------------------------------------------------------------------
# Simulation (same engine as side-gap benchmark)
# ---------------------------------------------------------------------------

@dataclass
class Ticket:
    direction: str
    entry_price: float
    opened_idx: int


def simulate(
    symbol: str,
    bars: list[dict],
    symbol_info,
    *,
    step_sell: float,
    step_buy: float,
    max_open_per_side: int,
    close_alpha: float,
    close_style: str,
    sell_gap: int,
    buy_gap: int,
    open_realism_mode: str,
    close_realism_mode: str,
) -> dict:
    if not bars:
        return {"error": "no bars"}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_sell_px = step_sell * pip_size
    base_step_buy_px = step_buy * pip_size

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_sell_px
    next_buy_level = anchor - base_step_buy_px

    open_tickets: list[Ticket] = []
    realized_pnls: list[float] = []
    open_events = 0
    close_events = 0
    same_bar_roundtrips = 0
    max_open = 0

    adapt_cfg = type("Cfg", (), {
        "adaptive_step_threshold_1": 10,
        "adaptive_step_threshold_2": 20,
        "adaptive_step_multiplier_1": 1.5,
        "adaptive_step_multiplier_2": 2.0,
    })()

    def select_positions(side_len: int, profitable_positions: list[int], gap: int) -> list[int]:
        if side_len <= gap:
            return []
        if close_style == "outer":
            return [0]
        if close_style == "inner":
            return [max(0, gap - 1)]
        if close_style == "all_profitable":
            return list(profitable_positions)
        raise ValueError(f"Unsupported close style: {close_style}")

    for idx in range(1, len(bars)):
        bar = bars[idx]

        open_buy = sum(1 for t in open_tickets if t.direction == "BUY")
        open_sell = sum(1 for t in open_tickets if t.direction == "SELL")
        current_sell_step = dynamic_step(base_step_sell_px, open_sell, adapt_cfg)
        current_buy_step = dynamic_step(base_step_buy_px, open_buy, adapt_cfg)

        # Open SELL
        while (
            _bar_reaches_price_level("SELL", next_sell_level, bar, spread_px=spread_px, mode=open_realism_mode, purpose="open")
            and open_sell < max_open_per_side
        ):
            open_tickets.append(Ticket(direction="SELL", entry_price=next_sell_level, opened_idx=idx))
            open_sell += 1
            open_events += 1
            current_sell_step = dynamic_step(base_step_sell_px, open_sell, adapt_cfg)
            next_sell_level += current_sell_step

        # Open BUY
        while (
            _bar_reaches_price_level("BUY", next_buy_level, bar, spread_px=spread_px, mode=open_realism_mode, purpose="open")
            and open_buy < max_open_per_side
        ):
            open_tickets.append(Ticket(direction="BUY", entry_price=next_buy_level, opened_idx=idx))
            open_buy += 1
            open_events += 1
            current_buy_step = dynamic_step(base_step_buy_px, open_buy, adapt_cfg)
            next_buy_level -= current_buy_step

        # Close SELL
        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while (
            len(sells) > sell_gap
            and _bar_reaches_price_level("SELL", sells[sell_gap].entry_price, bar, spread_px=spread_px, mode=open_realism_mode, purpose="close")
        ):
            level_price = sells[sell_gap].entry_price
            close_ref = _interp_close(level_price, float(bar["low"]), close_alpha)
            close_ref = _apply_close_realism("SELL", close_ref, bar, close_realism_mode)
            profitable_positions = [
                pos for pos, ticket in enumerate(sells)
                if unit_pnl_usd(symbol, "SELL", ticket.entry_price, close_ref, spread_px) > 0
            ]
            close_positions = sorted(set(select_positions(len(sells), profitable_positions, sell_gap)), reverse=True)
            if not close_positions:
                break
            closed_any = False
            for pos in close_positions:
                ticket = sells[pos]
                pnl = unit_pnl_usd(symbol, "SELL", ticket.entry_price, close_ref, spread_px)
                if pnl <= 0:
                    continue
                realized_pnls.append(pnl)
                close_events += 1
                if int(ticket.opened_idx) == idx:
                    same_bar_roundtrips += 1
                open_tickets.remove(ticket)
                closed_any = True
            if not closed_any:
                break
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        # Close BUY
        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while (
            len(buys) > buy_gap
            and _bar_reaches_price_level("BUY", buys[buy_gap].entry_price, bar, spread_px=spread_px, mode=open_realism_mode, purpose="close")
        ):
            level_price = buys[buy_gap].entry_price
            close_ref = _interp_close(level_price, float(bar["high"]), close_alpha)
            close_ref = _apply_close_realism("BUY", close_ref, bar, close_realism_mode)
            profitable_positions = [
                pos for pos, ticket in enumerate(buys)
                if unit_pnl_usd(symbol, "BUY", ticket.entry_price, close_ref, spread_px) > 0
            ]
            close_positions = sorted(set(select_positions(len(buys), profitable_positions, buy_gap)), reverse=True)
            if not close_positions:
                break
            closed_any = False
            for pos in close_positions:
                ticket = buys[pos]
                pnl = unit_pnl_usd(symbol, "BUY", ticket.entry_price, close_ref, spread_px)
                if pnl <= 0:
                    continue
                realized_pnls.append(pnl)
                close_events += 1
                if int(ticket.opened_idx) == idx:
                    same_bar_roundtrips += 1
                open_tickets.remove(ticket)
                closed_any = True
            if not closed_any:
                break
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        max_open = max(max_open, len(open_tickets))

        # Re-anchor
        if not open_tickets and (
            float(bar["close"]) >= float(anchor) + base_step_sell_px
            or float(bar["close"]) <= float(anchor) - base_step_buy_px
        ):
            anchor = float(bar["close"])
            next_sell_level = anchor + base_step_sell_px
            next_buy_level = anchor - base_step_buy_px

    # Final floating
    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px)
        for t in open_tickets
    ]
    realized_net = sum(realized_pnls)
    floating_net = sum(floating_pnls)
    combined_net = realized_net + floating_net
    closes = len(realized_pnls)

    return {
        "combined_net_usd": round(combined_net, 3),
        "realized_net_usd": round(realized_net, 3),
        "floating_net_usd": round(floating_net, 3),
        "realized_closes": closes,
        "open_events": open_events,
        "close_events": close_events,
        "same_bar_roundtrips": same_bar_roundtrips,
        "same_bar_roundtrip_pct": round((same_bar_roundtrips / closes) * 100.0, 1) if closes else 0.0,
        "avg_realized_per_close_usd": round(realized_net / closes, 4) if closes else 0.0,
        "max_open_total": max_open,
        "bar_count": len(bars),
        "spread_px": spread_px,
        "pip_size": pip_size,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not mt5.initialize():
        print("ERROR: MT5 initialization failed")
        return

    try:
        symbol_info = mt5.symbol_info("EURUSD")
        if symbol_info is None:
            print("ERROR: EURUSD symbol info not available")
            return

        now = int(time.time())
        gap_start = now - (7 * 86400)  # 7 days ago
        gap_end = now

        print("=" * 72)
        print("EURUSD FORWARD-SHADOW TEST: step 1.0/1.0 gap 3/3 outer")
        print("=" * 72)
        print()
        print(f"Configuration: {EURUSD_3_3_CONFIG}")
        print(f"Spread: {symbol_info.spread} points, pip: {pip_size_for(symbol_info)}")
        print()

        # Fetch forward period
        print("Fetching forward M15 bars (7 days)...")
        forward_bars = fetch_m15_bars("EURUSD", days=7, end_time=gap_end)
        print(f"  {len(forward_bars)} bars from {forward_bars[0]['time'] if forward_bars else 'N/A'} to {forward_bars[-1]['time'] if forward_bars else 'N/A'}")
        print()

        if len(forward_bars) < 100:
            print(f"ERROR: Only {len(forward_bars)} forward bars, need 100+")
            return

        # Run forward test
        print("Running forward-shadow simulation...")
        cfg = dict(EURUSD_3_3_CONFIG)
        sym = cfg.pop("symbol", "EURUSD")
        forward_result = simulate(sym, forward_bars, symbol_info, **cfg)

        print()
        print("=" * 72)
        print("RESULTS")
        print("=" * 72)
        print()

        print(f"  {'Metric':<30} {'Forward (7d)':<20} {'Reference (60d)':<20}")
        print(f"  {'------':<30} {'------------':<20} {'---------------':<20}")

        ref = REFERENCE_60D
        print(f"  {'Combined Net (USD)':<30} {forward_result['combined_net_usd']:<20.2f} {ref['combined_net']:<20.2f}")
        print(f"  {'Realized Net (USD)':<30} {forward_result['realized_net_usd']:<20.2f} {ref['realized_net']:<20.2f}")
        print(f"  {'Floating Net (USD)':<30} {forward_result['floating_net_usd']:<20.2f} {'—':<20}")
        print(f"  {'Realized Closes':<30} {forward_result['realized_closes']:<20} {'—':<20}")
        print(f"  {'Avg PnL/Close (USD)':<30} {forward_result['avg_realized_per_close_usd']:<20.4f} {'—':<20}")
        print(f"  {'Same-Bar RT %':<30} {forward_result['same_bar_roundtrip_pct']:<20.1f} {ref['same_bar_roundtrip_pct']:<20.1f}")
        print(f"  {'Max Open':<30} {forward_result['max_open_total']:<20} {'—':<20}")
        print(f"  {'Bars':<30} {forward_result['bar_count']:<20} {'—':<20}")

        # Verdict
        print()
        print("=" * 72)
        print("VERDICT")
        print("=" * 72)
        print()

        # Scale reference to 7d for comparison
        ref_7d_scaled = ref['combined_net'] * (7 / 60)
        realized_7d_scaled = ref['realized_net'] * (7 / 60)

        forward_holds = forward_result['combined_net_usd'] > 0
        same_bar_ok = forward_result['same_bar_roundtrip_pct'] < 50
        realized_positive = forward_result['realized_net_usd'] > 0

        print(f"  Reference 60d scaled to 7d: combined ${ref_7d_scaled:.2f}, realized ${realized_7d_scaled:.2f}")
        print(f"  Forward 7d: combined ${forward_result['combined_net_usd']:.2f}, realized ${forward_result['realized_net_usd']:.2f}")
        print()

        if forward_holds and same_bar_ok:
            print("  ✅ PASS: Forward edge is positive and same-bar round-trips < 50%")
            if realized_positive:
                print("  ✅ Realized PnL is positive (not just floating)")
            else:
                print("  ⚠️ Realized PnL is negative — edge is in floating positions")
            print()
            print("  The EURUSD 3/3 configuration holds in forward data.")
            print("  Recommend: continue forward-shadow for another 7 days.")
        elif forward_holds and not same_bar_ok:
            print("  ⚠️ PARTIAL: Edge positive but same-bar round-trips > 50%")
            print("  The edge may be churn-driven. Monitor for another 7 days.")
        else:
            print("  🚨 FAIL: Forward edge is negative")
            print("  The EURUSD 3/3 edge did NOT hold in forward data.")
            print("  This suggests the 60d result was curve-fit or regime-dependent.")
            print("  Recommend: do NOT promote to live. Investigate regime shift.")

        # Also run a baseline (reference gap 2/2) for comparison
        print()
        print("=" * 72)
        print("BASELINE COMPARISON: reference gap 2/2 vs forward gap 3/3")
        print("=" * 72)
        print()

        baseline_cfg = dict(EURUSD_3_3_CONFIG)
        baseline_cfg["sell_gap"] = 2
        baseline_cfg["buy_gap"] = 2
        baseline_cfg.pop("symbol", None)
        baseline_result = simulate(sym, forward_bars, symbol_info, **baseline_cfg)

        print(f"  {'Metric':<30} {'Gap 2/2 (baseline)':<20} {'Gap 3/3 (forward)':<20}")
        print(f"  {'------':<30} {'------------------':<20} {'-----------------':<20}")
        print(f"  {'Combined Net (USD)':<30} {baseline_result['combined_net_usd']:<20.2f} {forward_result['combined_net_usd']:<20.2f}")
        print(f"  {'Realized Net (USD)':<30} {baseline_result['realized_net_usd']:<20.2f} {forward_result['realized_net_usd']:<20.2f}")
        print(f"  {'Closes':<30} {baseline_result['realized_closes']:<20} {forward_result['realized_closes']:<20}")
        print(f"  {'Same-Bar RT %':<30} {baseline_result['same_bar_roundtrip_pct']:<20.1f} {forward_result['same_bar_roundtrip_pct']:<20.1f}")

        delta = forward_result['combined_net_usd'] - baseline_result['combined_net_usd']
        print()
        if delta > 0:
            print(f"  Gap 3/3 beats gap 2/2 by ${delta:.2f} in forward data — edge CONFIRMED.")
        else:
            print(f"  Gap 3/3 trails gap 2/2 by ${abs(delta):.2f} in forward data — edge DEGRADED.")

        # Save results
        report = {
            "config": EURUSD_3_3_CONFIG,
            "forward_period": {"days": 7, "bars": len(forward_bars)},
            "forward_result": forward_result,
            "baseline_result": baseline_result,
            "reference_60d": REFERENCE_60D,
            "reference_7d_scaled": {
                "combined_net": ref_7d_scaled,
                "realized_net": realized_7d_scaled,
            },
            "delta_vs_baseline": round(delta, 3),
            "verdict": "pass" if forward_holds and same_bar_ok else ("partial" if forward_holds else "fail"),
        }

        out_md = _build_markdown(report)
        DEFAULT_OUTPUT_MD.write_text(out_md, encoding="utf-8")
        DEFAULT_OUTPUT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")

        print()
        print(f"Report: {DEFAULT_OUTPUT_MD}")
        print(f"JSON: {DEFAULT_OUTPUT_JSON}")

    finally:
        mt5.shutdown()


def _build_markdown(report: dict) -> str:
    fwd = report["forward_result"]
    base = report["baseline_result"]
    ref = report["reference_60d"]
    scaled = report["reference_7d_scaled"]
    cfg = report["config"]
    verdict = report["verdict"]

    lines = [
        "# EURUSD Forward-Shadow Test: step 1.0/1.0 gap 3/3 outer",
        "",
        f"**Verdict:** {verdict.upper()}",
        "",
        "## Configuration",
        "",
        f"- Symbol: EURUSD",
        f"- Step: sell={cfg['step_sell']}, buy={cfg['step_buy']}",
        f"- Gap: sell={cfg['sell_gap']}, buy={cfg['buy_gap']}",
        f"- Close: alpha={cfg['close_alpha']}, style={cfg['close_style']}",
        f"- Realism: open=broker_touch, close=bar_close",
        f"- Forward period: 7 days, {fwd['bar_count']} M15 bars",
        "",
        "## Results",
        "",
        f"| Metric | Forward (7d) | Reference (60d) | 60d→7d Scaled |",
        f"|--------|-------------|-----------------|---------------|",
        f"| Combined Net | ${fwd['combined_net_usd']:.2f} | ${ref['combined_net']:.2f} | ${scaled['combined_net']:.2f} |",
        f"| Realized Net | ${fwd['realized_net_usd']:.2f} | ${ref['realized_net']:.2f} | ${scaled['realized_net']:.2f} |",
        f"| Floating Net | ${fwd['floating_net_usd']:.2f} | — | — |",
        f"| Closes | {fwd['realized_closes']} | — | — |",
        f"| Same-Bar RT % | {fwd['same_bar_roundtrip_pct']:.1f}% | {ref['same_bar_roundtrip_pct']:.1f}% | — |",
        f"| Max Open | {fwd['max_open_total']} | — | — |",
        "",
        "## Baseline Comparison (gap 2/2 vs gap 3/3)",
        "",
        f"| Metric | Gap 2/2 | Gap 3/3 | Delta |",
        f"|--------|---------|---------|-------|",
        f"| Combined Net | ${base['combined_net_usd']:.2f} | ${fwd['combined_net_usd']:.2f} | ${report['delta_vs_baseline']:.2f} |",
        f"| Realized Net | ${base['realized_net_usd']:.2f} | ${fwd['realized_net_usd']:.2f} | ${fwd['realized_net_usd'] - base['realized_net_usd']:.2f} |",
        f"| Closes | {base['realized_closes']} | {fwd['realized_closes']} | {fwd['realized_closes'] - base['realized_closes']} |",
        "",
        "## Interpretation",
        "",
    ]

    if verdict == "pass":
        lines.append("The EURUSD 3/3 configuration **holds its edge in forward data**.")
        lines.append("The edge is structural, not curve-fit. Recommend continuing forward-shadow.")
    elif verdict == "partial":
        lines.append("The edge is positive but **same-bar round-trip rate is concerning**.")
        lines.append("The edge may be partially churn-driven. Monitor for another 7 days.")
    else:
        lines.append("The EURUSD 3/3 configuration **failed in forward data**.")
        lines.append("The 60d edge was likely regime-dependent or curve-fit. Do NOT promote.")

    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
