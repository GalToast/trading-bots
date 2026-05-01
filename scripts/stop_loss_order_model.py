#!/usr/bin/env python3
"""Stop-loss order model — models what MT5 actually does with broker-side stops.

Three exit modes compared:
1. Bar-close exit: trail fires when bar CLOSES at/below trail level (current logic)
2. Stop-loss order: broker-side stop trails continuously, exits at stop price on touch
3. Counterfactual: theoretical ceiling = max(realized, peak * retain)

The stop-loss model is more realistic than bar-close because:
- MT5 stop orders trigger on price TOUCH, not bar close
- The stop trails up every bar (not just when it fires)
- Slippage may occur if price gaps through the stop

Usage: python scripts/stop_loss_order_model.py [--days 10] [--retain 0.60]

Author: local AI-assisted research pass
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from statistics import mean

import MetaTrader5 as mt5


SYMBOL = "USDJPY"
PIP = 0.01
UNITS_001_LOT = 1_000


@dataclass
class TradeResult:
    trade_idx: int
    direction: str
    entry_idx: int
    entry_price: float
    hold_bars: int
    peak_pips: float
    mfe_pips: float
    pnl_close_exit: float    # Bar-close trail exit
    pnl_stop_exit: float     # Stop-loss order exit
    pnl_counterfactual: float  # Theoretical ceiling
    capture_close: float     # pnl_close / counterfactual
    capture_stop: float      # pnl_stop / counterfactual
    stop_slippage_pips: float  # Difference between stop level and actual exit


def load_bars(days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M1, 0, 1440 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "tick_volume": int(r[5]),
        }
        for r in rates
    ]


def body_pips(bar: dict) -> float:
    return abs(bar["close"] - bar["open"]) / PIP


def range_pips(bar: dict) -> float:
    return max((bar["high"] - bar["low"]) / PIP, 0.01)


def avg_volume(bars: list[dict], start: int, end: int) -> float:
    window = bars[max(0, start):end]
    return mean(bar["tick_volume"] for bar in window) if window else 0.0


def avg_range(bars: list[dict], start: int, end: int) -> float:
    window = bars[max(0, start):end]
    return mean(range_pips(bar) for bar in window) if window else 0.0


def pnl_usd_001(direction: str, entry: float, exit_price: float, spread_pips: float) -> float:
    move = (exit_price - entry) if direction == "BUY" else (entry - exit_price)
    net = move - spread_pips * PIP
    raw_jpy = net * UNITS_001_LOT
    return raw_jpy / max(exit_price, 0.0001)


def detect_signal(bars: list[dict], idx: int, lookback: int, expansion_ratio: float,
                  min_body_pips: float, min_body_ratio: float, volume_burst_ratio: float) -> str | None:
    if idx < lookback:
        return None
    cur = bars[idx]
    prior = bars[idx - lookback:idx]
    prior_high = max(b["high"] for b in prior)
    prior_low = min(b["low"] for b in prior)
    body = body_pips(cur)
    ratio = body / range_pips(cur)
    vol = avg_volume(bars, idx - lookback, idx)
    avg_rng = avg_range(bars, idx - lookback, idx)
    burst = cur["tick_volume"] >= vol * volume_burst_ratio if vol > 0 else False
    expanded = (range_pips(cur) / avg_rng) >= expansion_ratio if avg_rng > 0 else True
    if body < min_body_pips or ratio < min_body_ratio or not burst or not expanded:
        return None
    if cur["close"] > prior_high:
        return "BUY"
    if cur["close"] < prior_low:
        return "SELL"
    return None


def find_confirmed_entry(bars: list[dict], idx: int, direction: str,
                         confirm_pips: float, confirm_window_bars: int) -> tuple[int, float] | None:
    signal_close = bars[idx]["close"]
    target = signal_close + confirm_pips * PIP if direction == "BUY" else signal_close - confirm_pips * PIP
    end_idx = min(len(bars), idx + 1 + confirm_window_bars)
    for entry_idx in range(idx + 1, end_idx):
        bar = bars[entry_idx]
        if direction == "BUY" and bar["high"] >= target:
            return entry_idx, target
        if direction == "SELL" and bar["low"] <= target:
            return entry_idx, target
    return None


def simulate_three_modes(
    bars: list[dict],
    entry_idx: int,
    entry_price: float,
    direction: str,
    max_hold_bars: int,
    retain_ratio: float,
    floor_pips: float,
    min_mfe_pips: float,
    spread_pips: float,
) -> TradeResult | None:
    """Simulate all three exit modes for a single trade."""
    peak_price = entry_price
    adverse_price = entry_price
    mfe_pips = 0.0
    mae_pips = 0.0

    # Stop-loss order state
    stop_price = None  # None = no stop placed yet

    # Bar-close exit state
    close_exit_idx = None
    close_exit_price = None

    # Stop-loss exit state
    stop_exit_idx = None
    stop_exit_price = None
    stop_slippage_pips = 0.0

    for j in range(entry_idx, min(len(bars) - 1, entry_idx + max_hold_bars + 1)):
        bar = bars[j]

        # Track favorable/adverse
        if direction == "BUY":
            favorable = (bar["high"] - entry_price) / PIP
            adverse = -(bar["low"] - entry_price) / PIP
            if bar["high"] > peak_price:
                peak_price = bar["high"]
            if bar["low"] < adverse_price:
                adverse_price = bar["low"]
        else:
            favorable = (entry_price - bar["low"]) / PIP
            adverse = -(entry_price - bar["high"]) / PIP
            if bar["low"] < peak_price:
                peak_price = bar["low"]
            if bar["high"] > adverse_price:
                adverse_price = bar["high"]

        mfe_pips = max(mfe_pips, favorable)
        mae_pips = max(mae_pips, adverse)

        # Compute trail level based on current MFE
        if mfe_pips >= min_mfe_pips:
            floor = max(floor_pips, mfe_pips * retain_ratio)
            if direction == "BUY":
                trail_level = entry_price + floor * PIP
            else:
                trail_level = entry_price - floor * PIP
        else:
            trail_level = None

        # ── Mode 1: Bar-close exit ────────────────────────────────
        if close_exit_idx is None and trail_level is not None:
            if direction == "BUY":
                if bar["close"] <= trail_level:
                    close_exit_idx = j
                    close_exit_price = trail_level
            else:
                if bar["close"] >= trail_level:
                    close_exit_idx = j
                    close_exit_price = trail_level

        # ── Mode 2: Stop-loss order exit ──────────────────────────
        if stop_exit_idx is None and trail_level is not None:
            # Update stop order to current trail level (trailing stop)
            if stop_price is None:
                stop_price = trail_level
            else:
                # Trail the stop in favorable direction only
                if direction == "BUY":
                    stop_price = max(stop_price, trail_level)
                else:
                    stop_price = min(stop_price, trail_level)

            # Check if bar's low/high hits the stop
            if direction == "BUY":
                if bar["low"] <= stop_price:
                    stop_exit_idx = j
                    # Exit at stop price, but if bar opened below stop,
                    # we get slippage — exit at open instead
                    if bar["open"] < stop_price:
                        stop_exit_price = bar["open"]
                        stop_slippage_pips = (stop_price - bar["open"]) / PIP
                    else:
                        stop_exit_price = stop_price
                        stop_slippage_pips = 0.0
            else:
                if bar["high"] >= stop_price:
                    stop_exit_idx = j
                    if bar["open"] > stop_price:
                        stop_exit_price = bar["open"]
                        stop_slippage_pips = (bar["open"] - stop_price) / PIP
                    else:
                        stop_exit_price = stop_price
                        stop_slippage_pips = 0.0

        # Early exit if both modes fired
        if close_exit_idx is not None and stop_exit_idx is not None:
            break

    # Time exits for modes that didn't fire
    last_idx = min(len(bars) - 1, entry_idx + max_hold_bars)
    last_close = bars[last_idx]["close"]

    if close_exit_idx is None:
        close_exit_idx = last_idx
        close_exit_price = last_close

    if stop_exit_idx is None:
        stop_exit_idx = last_idx
        stop_exit_price = last_close

    # ── Mode 3: Counterfactual ────────────────────────────────────
    realized_pips = ((last_close - entry_price) / PIP if direction == "BUY" else (entry_price - last_close) / PIP) - spread_pips
    if mfe_pips > 0:
        counterfactual_pips = max(realized_pips + spread_pips, mfe_pips * retain_ratio)
        cf_price = entry_price + counterfactual_pips * PIP if direction == "BUY" else entry_price - counterfactual_pips * PIP
        counterfactual_usd = pnl_usd_001(direction, entry_price, cf_price, spread_pips)
    else:
        counterfactual_usd = pnl_usd_001(direction, entry_price, last_close, spread_pips)

    # Compute PnL for all modes
    pnl_close = pnl_usd_001(direction, entry_price, close_exit_price, spread_pips)
    pnl_stop = pnl_usd_001(direction, entry_price, stop_exit_price, spread_pips)

    peak_pips = ((peak_price - entry_price) / PIP if direction == "BUY" else (entry_price - peak_price) / PIP)

    capture_close = (pnl_close / counterfactual_usd * 100.0) if counterfactual_usd > 0 else 0.0
    capture_stop = (pnl_stop / counterfactual_usd * 100.0) if counterfactual_usd > 0 else 0.0

    return TradeResult(
        trade_idx=0,
        direction=direction,
        entry_idx=entry_idx,
        entry_price=entry_price,
        hold_bars=max(close_exit_idx, stop_exit_idx) - entry_idx + 1,
        peak_pips=peak_pips,
        mfe_pips=max(0.0, mfe_pips),
        pnl_close_exit=pnl_close,
        pnl_stop_exit=pnl_stop,
        pnl_counterfactual=counterfactual_usd,
        capture_close=capture_close,
        capture_stop=capture_stop,
        stop_slippage_pips=stop_slippage_pips,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Stop-loss order model vs bar-close vs counterfactual")
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument("--spread-pips", type=float, default=0.6)
    parser.add_argument("--retain", type=float, default=0.60)
    parser.add_argument("--confirm-pips", type=float, default=1.5)
    parser.add_argument("--expansion", type=float, default=2.5)
    parser.add_argument("--floor-pips", type=float, default=0.5)
    parser.add_argument("--min-mfe-pips", type=float, default=1.0)
    args = parser.parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        bars = load_bars(args.days)
        if not bars:
            print("No bars loaded")
            return 1

        lookback = 8
        min_body_pips = 4.0
        min_body_ratio = 0.75
        volume_burst_ratio = 1.10
        confirm_window_bars = 1

        print("=" * 72)
        print(f"STOP-LOSS ORDER MODEL ({args.days} days, {SYMBOL})")
        print(f"Entry: confirmed displacement {args.confirm_pips}pip / {args.expansion}x ATR / 1 bar")
        print(f"Exit: {args.retain:.0%} retain trail, floor {args.floor_pips} pips, min MFE {args.min_mfe_pips} pips")
        print("=" * 72)
        print()

        # Find all signals and simulate
        trades: list[TradeResult] = []
        idx = lookback + 2
        trade_num = 0

        while idx < len(bars) - 2:
            direction = detect_signal(bars, idx, lookback, args.expansion,
                                     min_body_pips, min_body_ratio, volume_burst_ratio)
            if direction:
                entry_plan = find_confirmed_entry(bars, idx, direction,
                                                  args.confirm_pips, confirm_window_bars)
                if entry_plan:
                    entry_idx, entry_price = entry_plan
                    result = simulate_three_modes(
                        bars, entry_idx, entry_price, direction,
                        max_hold_bars=6,
                        retain_ratio=args.retain,
                        floor_pips=args.floor_pips,
                        min_mfe_pips=args.min_mfe_pips,
                        spread_pips=args.spread_pips,
                    )
                    if result:
                        trade_num += 1
                        result.trade_idx = trade_num
                        trades.append(result)
                    idx = entry_idx + 1
                    continue
            idx += 1

        if not trades:
            print("No trades found")
            return 0

        # Print per-trade results
        print(f"{'#':>3} {'Dir':>4} {'Peak':>7} {'MFE':>7} {'Close':>9} {'Stop':>9} {'CF':>9} {'Cap%':>6} {'Stop%':>6} {'Slip':>6}")
        print("-" * 82)

        for t in trades:
            slip = f"{t.stop_slippage_pips:.1f}p" if t.stop_slippage_pips > 0 else "—"
            print(
                f"{t.trade_idx:>3} {t.direction:>4} {t.peak_pips:>6.1f}p {t.mfe_pips:>6.1f}p "
                f"${t.pnl_close_exit:+8.2f} ${t.pnl_stop_exit:+8.2f} ${t.pnl_counterfactual:+8.2f} "
                f"{t.capture_close:>5.0f}% {t.capture_stop:>5.0f}% {slip:>6}"
            )

        print()

        # Summary
        net_close = sum(t.pnl_close_exit for t in trades)
        net_stop = sum(t.pnl_stop_exit for t in trades)
        net_cf = sum(t.pnl_counterfactual for t in trades)
        exp_close = mean(t.pnl_close_exit for t in trades)
        exp_stop = mean(t.pnl_stop_exit for t in trades)
        exp_cf = mean(t.pnl_counterfactual for t in trades)
        avg_cap_close = mean(t.capture_close for t in trades)
        avg_cap_stop = mean(t.capture_stop for t in trades)
        wins_stop = sum(1 for t in trades if t.pnl_stop_exit > 0)
        wins_close = sum(1 for t in trades if t.pnl_close_exit > 0)
        total_slip_pips = sum(t.stop_slippage_pips for t in trades if t.stop_slippage_pips > 0)
        slip_count = sum(1 for t in trades if t.stop_slippage_pips > 0)

        print("─" * 72)
        print("SUMMARY")
        print("─" * 72)
        print(f"  Trades: {len(trades)}")
        print()
        print(f"  {'Mode':<20} {'Net':>9} {'Exp/Trade':>10} {'WR':>6} {'Capture%':>9}")
        print(f"  {'─' * 20} {'─' * 9} {'─' * 10} {'─' * 6} {'─' * 9}")
        print(f"  {'Counterfactual':<20} ${net_cf:+8.2f} ${exp_cf:+9.2f} {'—':>6} {'—':>9}")
        print(f"  {'Stop-loss order':<20} ${net_stop:+8.2f} ${exp_stop:+9.2f} {wins_stop/len(trades)*100:>5.0f}% {avg_cap_stop:>8.0f}%")
        print(f"  {'Bar-close exit':<20} ${net_close:+8.2f} ${exp_close:+9.2f} {wins_close/len(trades)*100:>5.0f}% {avg_cap_close:>8.0f}%")
        print()

        if slip_count > 0:
            print(f"  Slippage events: {slip_count}/{len(trades)} trades, avg {total_slip_pips/slip_count:.1f} pips")
        else:
            print(f"  Slippage events: 0 (stop orders always filled at exact level)")
        print()

        # Gap analysis
        close_gap = net_cf - net_close
        stop_gap = net_cf - net_stop
        improvement = net_stop - net_close

        print("─" * 72)
        print("GAP ANALYSIS")
        print("─" * 72)
        print(f"  Counterfactual ceiling: ${net_cf:+.2f}")
        print(f"  Bar-close captures:     ${net_close:+.2f} ({avg_cap_close:.0f}% of ceiling)")
        print(f"  Stop-loss captures:     ${net_stop:+.2f} ({avg_cap_stop:.0f}% of ceiling)")
        print(f"  Stop-loss improvement:  ${improvement:+.2f} over bar-close")
        print()

        if avg_cap_stop >= 70:
            print(f"  🟢 Stop-loss orders capture {avg_cap_stop:.0f}% of counterfactual — VIABLE")
            print(f"     Wire the trail as a broker-side trailing stop, not bar-close logic.")
        elif avg_cap_stop >= 50:
            print(f"  🟡 Stop-loss orders capture {avg_cap_stop:.0f}% — PARTIALLY VIABLE")
            print(f"     Some gap remains but stop-loss is meaningfully better than bar-close.")
        else:
            print(f"  🔴 Stop-loss orders capture only {avg_cap_stop:.0f}% — NOT VIABLE")
            print(f"     Exit optimization has hit its ceiling even with stop orders.")

        print()
        print("=" * 72)
        return 0

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
