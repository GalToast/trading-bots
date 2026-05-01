#!/usr/bin/env python3
"""
Novel Strategy Sweep — Finding strategies that work on non-RAVE coins
======================================================================
Tests 5 fundamentally different strategy types across ALL coins.

Strategies:
1. RSI Mean Reversion — baseline (RSI(3)<30, 25% TP, 48-bar max)
2. Momentum Breakout — Buy N-bar high breakout, 8% TP, 3% SL
3. EMA Trend Pullback — EMA50>EMA200 uptrend, buy pullback to EMA20, 10% TP, 5% SL
4. Bollinger Band Reversion — Touch lower BB, sell at middle BB
5. Volatility Squeeze — BB width contracts <75th percentile then expands, 12% TP, 6% SL
6. Open-Range Breakout — Buy breakout of first 12-bar range, 6% TP, 3% SL

Same fills (8bps entry, 0bps exit, 40bps fees), same 30d window, same starting cash.
Goal: find ANY coin-strategy combo with WR>=45% and net>0.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

ROOT = Path(__file__).resolve().parent.parent

# Strategy params
FEE_BPS = 40
FILL_ENTRY_BPS = 8
FILL_EXIT_BPS = 0
STARTING_CASH = 48.0
WINDOW_DAYS = 30
GRANULARITY = "FIVE_MINUTE"

COINS = [
    "RAVE-USD", "BAL-USD", "IOTX-USD", "BLUR-USD", "ALEPH-USD",
    "SOL-USD", "DOGE-USD", "XRP-USD", "LINK-USD", "FET-USD",
    "RENDER-USD", "UNI-USD", "AAVE-USD", "AVAX-USD", "NEAR-USD",
    "PEPE-USD", "WIF-USD", "TIA-USD", "SEI-USD", "SUI-USD", "ONDO-USD",
]

BTC = "BTC-USD"


def _ema(closes: list[float], period: int) -> list[float]:
    k = 2.0 / (period + 1)
    ema = [closes[0]]
    for i in range(1, len(closes)):
        ema.append(closes[i] * k + ema[-1] * (1 - k))
    return ema


def _bb(closes: list[float], period: int = 20, mult: float = 2.0) -> tuple[list[float], list[float], list[float]]:
    """Returns (upper, middle, lower) bands."""
    upper, middle, lower = [], [], []
    for i in range(len(closes)):
        if i < period - 1:
            upper.append(closes[i])
            middle.append(closes[i])
            lower.append(closes[i])
            continue
        window = closes[i - period + 1:i + 1]
        mid = statistics.mean(window)
        std = statistics.pstdev(window)
        middle.append(mid)
        upper.append(mid + mult * std)
        lower.append(mid - mult * std)
    return upper, middle, lower


def run_backtest_generic(candles: list[dict], signals: list[tuple[int, str, dict]]) -> dict:
    """
    Generic backtest engine.
    signals: list of (bar_idx, signal_type, params_dict)
    params_dict must have tp_pct, sl_pct (0 = no SL)
    """
    fee_rate = FEE_BPS / 10000
    entry_slip = FILL_ENTRY_BPS / 10000
    exit_slip = FILL_EXIT_BPS / 10000

    cash = STARTING_CASH
    position = None
    trades = []
    peak_cash = cash
    max_dd = 0.0

    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]

    # Build signal lookup
    signal_map = {}
    for idx, stype, params in signals:
        if idx not in signal_map:
            signal_map[idx] = []
        signal_map[idx].append((stype, params))

    for i in range(len(candles)):
        if position:
            hold_bars = i - position["entry_bar"]
            entry_price = position["entry_price"]
            tp_price = entry_price * (1 + position["tp_pct"] / 100)
            sl_price = entry_price * (1 - position["sl_pct"] / 100) if position["sl_pct"] > 0 else 0

            current_high = highs[i]
            current_low = lows[i]

            # Check SL first, then TP
            exit_price = None
            reason = None
            if sl_price > 0 and current_low <= sl_price:
                exit_price = sl_price
                reason = "sl"
            elif current_high >= tp_price:
                exit_price = tp_price
                reason = "tp"
            elif hold_bars >= position["max_hold"]:
                exit_price = closes[i]
                reason = "timeout"

            if exit_price is not None:
                effective_entry = entry_price * (1 + entry_slip)
                effective_exit = exit_price * (1 - exit_slip)

                pnl = (effective_exit - effective_entry) / effective_entry
                fees = fee_rate * 2
                net_pnl = pnl - fees
                net_usd = position["deployed_cash"] * net_pnl

                cash += net_usd
                if cash > peak_cash:
                    peak_cash = cash
                dd = (peak_cash - cash) / peak_cash * 100 if peak_cash > 0 else 0
                if dd > max_dd:
                    max_dd = dd

                trades.append({
                    "entry_bar": position["entry_bar"],
                    "exit_bar": i,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "net_usd": round(net_usd, 2),
                    "win": net_usd > 0,
                    "reason": reason,
                    "hold_bars": hold_bars,
                })
                position = None

        # Check for signals at this bar
        if i in signal_map and position is None:
            stype, params = signal_map[i][0]  # Take first signal
            position = {
                "entry_price": closes[i],
                "entry_bar": i,
                "deployed_cash": cash,
                "tp_pct": params["tp_pct"],
                "sl_pct": params.get("sl_pct", 0),
                "max_hold": params.get("max_hold", 48),
                "signal_type": stype,
            }

    # Close open position
    if position:
        entry_price = position["entry_price"]
        exit_price = closes[-1]
        effective_entry = entry_price * (1 + entry_slip)
        effective_exit = exit_price * (1 - exit_slip)
        pnl = (effective_exit - effective_entry) / effective_entry
        net_pnl = pnl - fee_rate * 2
        net_usd = position["deployed_cash"] * net_pnl
        cash += net_usd
        trades.append({
            "entry_bar": position["entry_bar"],
            "exit_bar": len(candles) - 1,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "net_usd": round(net_usd, 2),
            "win": net_usd > 0,
            "reason": "end_of_data",
            "hold_bars": len(candles) - 1 - position["entry_bar"],
        })

    total_net = round(cash - STARTING_CASH, 2)
    total_wr = round(sum(1 for t in trades if t["win"]) / len(trades) * 100, 1) if trades else 0

    return {
        "total_net_usd": total_net,
        "total_trades": len(trades),
        "total_wr": total_wr,
        "wins": sum(1 for t in trades if t["win"]),
        "losses": len(trades) - sum(1 for t in trades if t["win"]),
        "max_dd": round(max_dd, 1),
        "final_cash": round(cash, 2),
    }


def strategy_rsi_mr(candles: list[dict]) -> list[tuple[int, str, dict]]:
    """RSI(3)<30, 25% TP, no SL, 48-bar max."""
    closes = [float(c["close"]) for c in candles]
    period = 3
    rsi = [50.0] * len(closes)
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(0, d) for d in deltas]
    losses = [max(0, -d) for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    if avg_l > 0:
        rsi[period] = 100 - 100 / (1 + avg_g / avg_l)
    else:
        rsi[period] = 100.0
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        if avg_l > 0:
            rsi[i + 1] = 100 - 100 / (1 + avg_g / avg_l)
        else:
            rsi[i + 1] = 100.0

    signals = []
    for i in range(1, len(candles)):
        if rsi[i] is not None and rsi[i] < 30:
            signals.append((i, "rsi_mr", {"tp_pct": 25, "sl_pct": 0, "max_hold": 48}))
    return signals


def strategy_momentum_breakout(candles: list[dict]) -> list[tuple[int, str, dict]]:
    """Buy when price breaks above 20-bar high. 8% TP, 3% SL, 24-bar max."""
    signals = []
    lookback = 20
    for i in range(lookback, len(candles)):
        high_20 = max(float(candles[j]["high"]) for j in range(i - lookback, i))
        current_high = float(candles[i]["high"])
        if current_high > high_20:
            signals.append((i, "momentum", {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}))
    return signals


def strategy_ema_pullback(candles: list[dict]) -> list[tuple[int, str, dict]]:
    """EMA50>EMA200 uptrend, buy pullback to EMA20. 10% TP, 5% SL, 30-bar max."""
    closes = [float(c["close"]) for c in candles]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    ema200 = _ema(closes, 200)

    signals = []
    for i in range(200, len(candles)):
        # Uptrend: EMA50 > EMA200
        if ema50[i] > ema200[i]:
            # Pullback: price near EMA20 (within 1%)
            dist_to_ema = abs(closes[i] - ema20[i]) / ema20[i]
            if dist_to_ema < 0.01:
                signals.append((i, "ema_pullback", {"tp_pct": 10, "sl_pct": 5, "max_hold": 30}))
    return signals


def strategy_bb_reversion(candles: list[dict]) -> list[tuple[int, str, dict]]:
    """Buy when price touches lower BB, sell at middle BB. 0% TP (uses BB middle), 5% SL, 48-bar max."""
    closes = [float(c["close"]) for c in candles]
    upper, middle, lower = _bb(closes, 20, 2.0)

    signals = []
    for i in range(20, len(candles)):
        if closes[i] <= lower[i]:
            # TP is the middle band, target % = (middle - entry) / entry
            tp_pct = max(0, (middle[i] - closes[i]) / closes[i] * 100)
            if tp_pct > 0:
                signals.append((i, "bb_reversion", {"tp_pct": tp_pct, "sl_pct": 5, "max_hold": 48}))
    return signals


def strategy_volatility_squeeze(candles: list[dict]) -> list[tuple[int, str, dict]]:
    """Buy when BB width expands after squeezing. 12% TP, 6% SL, 36-bar max."""
    closes = [float(c["close"]) for c in candles]
    upper, middle, lower = _bb(closes, 20, 2.0)

    # BB width = (upper - lower) / middle
    widths = []
    for i in range(len(closes)):
        if i < 19:
            widths.append(0)
        else:
            w = (upper[i] - lower[i]) / middle[i] if middle[i] > 0 else 0
            widths.append(w)

    # Squeeze = width < 75th percentile of recent width
    signals = []
    for i in range(40, len(candles)):
        recent_widths = [w for w in widths[i - 20:i] if w > 0]
        if recent_widths:
            p75 = sorted(recent_widths)[int(len(recent_widths) * 0.75)]
            was_squeeze = widths[i - 1] < p75 * 0.8  # Was squeezed
            now_expanding = widths[i] > widths[i - 1] * 1.1  # Now expanding
            if was_squeeze and now_expanding:
                signals.append((i, "vol_squeeze", {"tp_pct": 12, "sl_pct": 6, "max_hold": 36}))
    return signals


def strategy_open_range_breakout(candles: list[dict]) -> list[tuple[int, str, dict]]:
    """Buy breakout of first 12-bar range. 6% TP, 3% SL, 24-bar max."""
    signals = []
    range_len = 12

    for day_start in range(0, len(candles) - range_len, range_len * 4):
        # Define range
        range_high = max(float(candles[j]["high"]) for j in range(day_start, min(day_start + range_len, len(candles))))
        range_low = min(float(candles[j]["low"]) for j in range(day_start, min(day_start + range_len, len(candles))))

        # Look for breakout in the next 24 bars
        for i in range(day_start + range_len, min(day_start + range_len + 24, len(candles))):
            current_high = float(candles[i]["high"])
            if current_high > range_high:
                signals.append((i, "range_breakout", {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}))
                break

    return signals


STRATEGIES = {
    "rsi_mr": strategy_rsi_mr,
    "momentum": strategy_momentum_breakout,
    "ema_pullback": strategy_ema_pullback,
    "bb_reversion": strategy_bb_reversion,
    "vol_squeeze": strategy_volatility_squeeze,
    "range_breakout": strategy_open_range_breakout,
}


def main():
    print("=" * 80)
    print("  NOVEL STRATEGY SWEEP — Finding strategies for non-RAVE coins")
    print("=" * 80)
    print(f"\nCoins: {len(COINS)}, Strategies: {len(STRATEGIES)}, Total combos: {len(COINS) * len(STRATEGIES)}")
    print(f"Fills: {FILL_ENTRY_BPS}bps entry, {FILL_EXIT_BPS}bps exit, {FEE_BPS}bps fees")
    print()

    all_results = []

    for coin_idx, coin in enumerate(COINS, 1):
        print(f"--- [{coin_idx}/{len(COINS)}] {coin} ---")
        t0 = time.time()

        try:
            candles = load_candles(coin, GRANULARITY, WINDOW_DAYS, max_age_minutes=WINDOW_DAYS * 24 * 60)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        if not candles or len(candles) < 200:
            print(f"  SKIP: {len(candles) if candles else 0} candles")
            continue

        print(f"  Loaded {len(candles)} candles")

        for strat_name, strat_fn in STRATEGIES.items():
            t1 = time.time()
            signals = strat_fn(candles)
            if not signals:
                continue

            result = run_backtest_generic(candles, signals)
            elapsed = time.time() - t1

            result["coin"] = coin
            result["strategy"] = strat_name
            result["signals"] = len(signals)
            result["candle_count"] = len(candles)

            rar = result["total_net_usd"] / max(result["max_dd"], 1)
            result["rar"] = round(rar, 2)

            qual = "[OK]" if result["total_net_usd"] > 0 and result["total_wr"] >= 45 else "[--]"
            print(f"  {strat_name:<18}: {result['total_trades']:>3} trades, WR {result['total_wr']:>5.1f}%, "
                  f"Net ${result['total_net_usd']:>+8.2f}, DD {result['max_dd']:>5.1f}%, "
                  f"RAR {rar:>5.2f} {qual}")

            all_results.append(result)

    # Sort by net PnL
    all_results.sort(key=lambda x: x["total_net_usd"], reverse=True)

    # Print summary
    print(f"\n{'='*80}")
    print(f"  RANKED RESULTS (by net PnL)")
    print(f"{'='*80}")

    print(f"\n  {'#':>3} {'COIN':<12} {'STRATEGY':<16} {'Trades':>6} {'WR%':>5} "
          f"{'Net $':>9} {'DD%':>5} {'RAR':>5} {'Status'}")
    print(f"  {'-'*3} {'-'*12} {'-'*16} {'-'*6} {'-'*5} {'-'*9} {'-'*5} {'-'*5} {'-'*8}")

    qualifying = []
    for rank, r in enumerate(all_results, 1):
        qual = "[OK]" if r["total_net_usd"] > 0 and r["total_wr"] >= 45 else ""
        if qual:
            qualifying.append(r)
        print(f"  {rank:>3} {r['coin']:<12} {r['strategy']:<16} {r['total_trades']:>6} "
              f"{r['total_wr']:>5.1f}% ${r['total_net_usd']:>8.2f} {r['max_dd']:>5.1f}% "
              f"{r['rar']:>5.2f} {qual}")

    print(f"\n{'='*80}")
    print(f"  QUALIFYING COMBOS (WR>=45%, Net>0): {len(qualifying)}")
    print(f"{'='*80}")

    for r in qualifying:
        print(f"  {r['coin']} + {r['strategy']}: ${r['total_net_usd']:+.2f}, {r['total_wr']}% WR, "
              f"{r['total_trades']} trades, {r['max_dd']}% DD")

    # Save report
    report = {
        "params": {
            "fee_bps": FEE_BPS,
            "fill_entry_bps": FILL_ENTRY_BPS,
            "fill_exit_bps": FILL_EXIT_BPS,
            "starting_cash": STARTING_CASH,
            "window_days": WINDOW_DAYS,
            "granularity": GRANULARITY,
        },
        "total_combos": len(all_results),
        "qualifying_count": len(qualifying),
        "qualifying": [{"coin": r["coin"], "strategy": r["strategy"], "net_usd": r["total_net_usd"],
                         "wr": r["total_wr"], "trades": r["total_trades"], "dd": r["max_dd"],
                         "rar": r["rar"]} for r in qualifying],
        "all_results": all_results,
    }

    output_path = ROOT / "reports" / "novel_strategy_sweep.json"
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n  Report saved: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
