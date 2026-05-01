#!/usr/bin/env python3
"""
Multi-Coin Rotation Backtest — Combine RAVE + BLUR + BAL into one engine.

Instead of trading only RAVE, split capital across coins and trade the best signals.
Tests whether diversification improves risk-adjusted returns.

Strategies (from parameter optimizer):
- RAVE: RSI(4)/40%/48 bars
- BLUR: RSI(5)/35%/80 bars
- BAL: RSI(4)/20%/40 bars
- IOTX: RSI(5)/15%/80 bars (marginal, optional)

Modes:
1. **Equal split:** Cash divided equally, each coin trades independently
2. **Priority queue:** All coins generate signals, trade the strongest signal first
3. **RAVE-only baseline:** For comparison

Output: reports/multi_coin_rotation_results.json
"""
import json
import os
import sys
import time
import statistics
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "reports" / "multi_coin_rotation_results.json"

BTC = "BTC-USD"
WINDOW_DAYS = 30
STARTING_CASH = 48.0
FEE_RATE = 0.0040

# Optimized params per coin
COIN_STRATEGIES = {
    "RAVE-USD": {"rsi_period": 4, "os_thresh": 30, "tp_pct": 40, "max_hold": 48},
    "BLUR-USD": {"rsi_period": 5, "os_thresh": 30, "tp_pct": 35, "max_hold": 80},
    "BAL-USD": {"rsi_period": 4, "os_thresh": 30, "tp_pct": 20, "max_hold": 40},
    "IOTX-USD": {"rsi_period": 5, "os_thresh": 30, "tp_pct": 15, "max_hold": 80},
}

# Fill model
ENTRY_SLIPPAGE_BPS = 6.2
EXIT_SLIPPAGE_BPS = 0.0


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=granularity)
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands:
                break
            time.sleep(0.1)
        except Exception:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c


def compute_rsi(closes, period):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l > 0:
        rs = avg_g / avg_l
        return 100 - 100 / (1 + rs)
    return 100.0


def apply_slippage(price, slippage_bps, direction="entry"):
    slip_pct = slippage_bps / 10000.0
    if direction == "entry":
        return price * (1 + slip_pct)
    else:
        return price * (1 - slip_pct)


def run_single_coin_backtest(candles, params, fee_rate, starting_cash):
    """Backtest a single coin strategy."""
    cash = starting_cash
    position = None
    realized_net = 0.0
    closes = 0
    wins = 0
    losses = 0
    total_fees = 0.0
    history = []
    signals = 0
    max_dd_pct = 0.0
    peak_cash = starting_cash
    equity_curve = []

    rsi_period = params["rsi_period"]
    tp_pct = params["tp_pct"]
    max_hold = params["max_hold"]

    for i, candle in enumerate(candles):
        close = float(candle["close"])
        high = float(candle["high"])
        open_price = float(candle["open"])

        history.append(close)
        if len(history) > 500:
            history = history[-500:]

        # EXIT
        if position:
            position["hold"] += 1
            exit_price = None
            exit_reason = None

            if high >= position["tp"]:
                exit_price = apply_slippage(position["tp"], EXIT_SLIPPAGE_BPS, "exit")
                exit_reason = "tp"
            elif position["hold"] >= max_hold:
                exit_price = apply_slippage(close, EXIT_SLIPPAGE_BPS, "exit")
                exit_reason = "timeout"

            if exit_price is not None:
                units = position["units"]
                gross = (exit_price - position["ep"]) * units
                entry_fee = position["entry_fee"]
                exit_fee = exit_price * units * fee_rate
                net = gross - entry_fee - exit_fee

                cash += position["q"] + net
                realized_net += net
                closes += 1
                total_fees += entry_fee + exit_fee

                if net > 0:
                    wins += 1
                else:
                    losses += 1

                # Track DD
                current_equity = cash + (position["q"] if position else 0)
                if current_equity > peak_cash:
                    peak_cash = current_equity
                dd = (peak_cash - current_equity) / peak_cash * 100
                if dd > max_dd_pct:
                    max_dd_pct = dd

                position = None

        # ENTRY
        if position is None and cash >= 10.0 and len(history) >= rsi_period + 2:
            rsi_val = compute_rsi(history[:-1], rsi_period)

            if rsi_val <= 30:
                signals += 1

                deploy = cash
                entry_price = apply_slippage(open_price, ENTRY_SLIPPAGE_BPS, "entry")
                entry_fee = deploy * fee_rate
                units = (deploy - entry_fee) / entry_price
                tp = entry_price * (1 + tp_pct / 100.0)

                cash -= deploy
                position = {
                    "ep": entry_price,
                    "q": deploy,
                    "hold": 0,
                    "tp": tp,
                    "units": units,
                    "entry_fee": entry_fee,
                }

        # Track equity
        current_equity = cash + (position["q"] if position else 0)
        if current_equity > peak_cash:
            peak_cash = current_equity
        dd = (peak_cash - current_equity) / peak_cash * 100
        if dd > max_dd_pct:
            max_dd_pct = dd
        equity_curve.append(current_equity)

    total_pnl = cash + (position["q"] if position else 0) - starting_cash
    return_pct = total_pnl / starting_cash * 100
    wr = wins / max(1, closes) * 100

    return {
        "net_pnl": round(total_pnl, 2),
        "return_pct": round(return_pct, 1),
        "win_rate": round(wr, 1),
        "closes": closes,
        "wins": wins,
        "losses": losses,
        "signals": signals,
        "total_fees": round(total_fees, 2),
        "max_dd_pct": round(max_dd_pct, 1),
        "avg_pnl_per_trade": round(total_pnl / max(1, closes), 2),
        "final_equity": round(cash + (position["q"] if position else 0), 2),
        "position_active": position is not None,
    }


def run_rotation_backtest(coin_candles, strategies, fee_rate, starting_cash, mode="priority"):
    """
    Multi-coin rotation backtest.

    mode="equal_split": Divide cash equally across coins, each trades independently
    mode="priority": Single pool, trade the strongest signal (lowest RSI) first
    """
    if mode == "equal_split":
        return _run_equal_split(coin_candles, strategies, fee_rate, starting_cash)
    elif mode == "priority":
        return _run_priority_queue(coin_candles, strategies, fee_rate, starting_cash)


def _run_equal_split(coin_candles, strategies, fee_rate, starting_cash):
    """Each coin gets an equal share of the bankroll."""
    n_coins = len(coin_candles)
    coin_cash = starting_cash / n_coins

    results = {}
    total_equity = starting_cash
    peak_equity = starting_cash
    max_dd = 0.0

    for coin, candles in coin_candles.items():
        params = strategies[coin]
        result = run_single_coin_backtest(candles, params, fee_rate, coin_cash)
        results[coin] = result
        total_equity += result["net_pnl"]

    total_pnl = total_equity - starting_cash
    return_pct = total_pnl / starting_cash * 100

    # Approximate max DD as worst single-coin DD (conservative)
    max_dd = max(r["max_dd_pct"] for r in results.values())

    total_closes = sum(r["closes"] for r in results.values())
    total_wins = sum(r["wins"] for r in results.values())
    total_losses = sum(r["losses"] for r in results.values())
    total_signals = sum(r["signals"] for r in results.values())
    total_fees = sum(r["total_fees"] for r in results.values())
    avg_wr = total_wins / max(1, total_closes) * 100

    return {
        "mode": "equal_split",
        "n_coins": n_coins,
        "net_pnl": round(total_pnl, 2),
        "return_pct": round(return_pct, 1),
        "win_rate": round(avg_wr, 1),
        "closes": total_closes,
        "wins": total_wins,
        "losses": total_losses,
        "signals": total_signals,
        "total_fees": round(total_fees, 2),
        "max_dd_pct": round(max_dd, 1),
        "avg_pnl_per_trade": round(total_pnl / max(1, total_closes), 2),
        "per_coin": results,
    }


def _run_priority_queue(coin_candles, strategies, fee_rate, starting_cash):
    """
    Single pool of cash. Each bar, check all coins for signals.
    Trade the coin with the LOWEST RSI (strongest oversold signal) first.
    If that coin is already in a position, try the next strongest signal.
    """
    cash = starting_cash
    positions = {}  # coin -> position
    histories = {coin: [] for coin in coin_candles}
    total_closes = 0
    total_wins = 0
    total_losses = 0
    total_signals = 0
    total_fees = 0.0
    peak_equity = starting_cash
    max_dd = 0.0

    # Align candles by timestamp
    all_timestamps = set()
    for candles in coin_candles.values():
        for c in candles:
            all_timestamps.add(int(c["start"]))
    sorted_timestamps = sorted(all_timestamps)

    # Build lookup
    candle_lookup = {}
    for coin, candles in coin_candles.items():
        candle_lookup[coin] = {int(c["start"]): c for c in candles}

    for ts in sorted_timestamps:
        for coin, candles in coin_candles.items():
            c = candle_lookup[coin].get(ts)
            if c is None:
                continue

            close = float(c["close"])
            high = float(c["high"])
            open_price = float(c["open"])

            histories[coin].append(close)
            if len(histories[coin]) > 500:
                histories[coin] = histories[coin][-500:]

            params = strategies[coin]
            max_hold = params["max_hold"]
            tp_pct = params["tp_pct"]
            rsi_period = params["rsi_period"]

            # EXIT
            if coin in positions:
                pos = positions[coin]
                pos["hold"] += 1
                exit_price = None
                exit_reason = None

                if high >= pos["tp"]:
                    exit_price = apply_slippage(pos["tp"], EXIT_SLIPPAGE_BPS, "exit")
                    exit_reason = "tp"
                elif pos["hold"] >= max_hold:
                    exit_price = apply_slippage(close, EXIT_SLIPPAGE_BPS, "exit")
                    exit_reason = "timeout"

                if exit_price is not None:
                    units = pos["units"]
                    gross = (exit_price - pos["ep"]) * units
                    entry_fee = pos["entry_fee"]
                    exit_fee = exit_price * units * fee_rate
                    net = gross - entry_fee - exit_fee

                    cash += pos["q"] + net
                    total_closes += 1
                    total_fees += entry_fee + exit_fee

                    if net > 0:
                        total_wins += 1
                    else:
                        total_losses += 1

                    del positions[coin]

        # ENTRY: Check all coins for signals, prioritize by lowest RSI
        signals_this_bar = []
        for coin, candles in coin_candles.items():
            c = candle_lookup[coin].get(ts)
            if c is None or coin in positions:
                continue

            params = strategies[coin]
            rsi_period = params["rsi_period"]

            if len(histories[coin]) >= rsi_period + 2:
                rsi_val = compute_rsi(histories[coin][:-1], rsi_period)
                if rsi_val <= 30:
                    signals_this_bar.append((coin, rsi_val, c))

        # Sort by RSI (lowest = strongest oversold)
        signals_this_bar.sort(key=lambda x: x[1])

        for coin, rsi_val, c in signals_this_bar:
            if cash < 10.0:
                break  # Not enough cash for another position

            total_signals += 1
            open_price = float(c["open"])
            params = strategies[coin]
            tp_pct = params["tp_pct"]

            deploy = cash * 0.5  # Cap at 50% per position for diversification
            deploy = min(deploy, cash - 10.0)  # Keep at least $10 reserve
            if deploy < 10.0:
                continue

            entry_price = apply_slippage(open_price, ENTRY_SLIPPAGE_BPS, "entry")
            entry_fee = deploy * fee_rate
            units = (deploy - entry_fee) / entry_price
            tp = entry_price * (1 + tp_pct / 100.0)

            cash -= deploy
            positions[coin] = {
                "ep": entry_price,
                "q": deploy,
                "hold": 0,
                "tp": tp,
                "units": units,
                "entry_fee": entry_fee,
            }

        # Track equity
        position_value = sum(pos["q"] for pos in positions.values())
        equity = cash + position_value
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100
        if dd > max_dd:
            max_dd = dd

    total_pnl = cash + sum(pos["q"] for pos in positions.values()) - starting_cash
    return_pct = total_pnl / starting_cash * 100
    wr = total_wins / max(1, total_closes) * 100

    return {
        "mode": "priority",
        "n_coins": len(coin_candles),
        "net_pnl": round(total_pnl, 2),
        "return_pct": round(return_pct, 1),
        "win_rate": round(wr, 1),
        "closes": total_closes,
        "wins": total_wins,
        "losses": total_losses,
        "signals": total_signals,
        "total_fees": round(total_fees, 2),
        "max_dd_pct": round(max_dd, 1),
        "avg_pnl_per_trade": round(total_pnl / max(1, total_closes), 2),
        "final_positions": list(positions.keys()),
        "position_count": len(positions),
    }


def main():
    client = CoinbaseAdvancedClient()

    now = int(time.time())
    start = now - WINDOW_DAYS * 86400

    print(f"=" * 70, flush=True)
    print(f"MULTI-COIN ROTATION BACKTEST — {WINDOW_DAYS}d, $48 bankroll", flush=True)
    print(f"=" * 70, flush=True)

    # Fetch candles for all coins
    coins = list(COIN_STRATEGIES.keys())
    coin_candles = {}
    for coin in coins:
        print(f"Fetching {WINDOW_DAYS}d candles for {coin}...", flush=True)
        candles = fetch_candles(client, coin, start, now)
        coin_candles[coin] = candles
        print(f"  {coin}: {len(candles)} candles", flush=True)

    # Run single-coin baselines
    print(f"\n{'='*70}", flush=True)
    print("SINGLE-COIN BASELINES", flush=True)
    print(f"{'='*70}", flush=True)

    baselines = {}
    for coin, params in COIN_STRATEGIES.items():
        result = run_single_coin_backtest(coin_candles[coin], params, FEE_RATE, STARTING_CASH)
        baselines[coin] = result
        print(f"  {coin}: PnL=${result['net_pnl']:.2f} WR={result['win_rate']}% "
              f"Trades={result['closes']} DD={result['max_dd_pct']}%", flush=True)

    # Run rotation modes
    print(f"\n{'='*70}", flush=True)
    print("ROTATION MODES", flush=True)
    print(f"{'='*70}", flush=True)

    # Equal split
    equal_result = run_rotation_backtest(coin_candles, COIN_STRATEGIES, FEE_RATE, STARTING_CASH, mode="equal_split")
    print(f"\n  Equal Split: PnL=${equal_result['net_pnl']:.2f} WR={equal_result['win_rate']}% "
          f"Trades={equal_result['closes']} DD={equal_result['max_dd_pct']}%", flush=True)

    # Priority queue
    priority_result = run_rotation_backtest(coin_candles, COIN_STRATEGIES, FEE_RATE, STARTING_CASH, mode="priority")
    print(f"  Priority:    PnL=${priority_result['net_pnl']:.2f} WR={priority_result['win_rate']}% "
          f"Trades={priority_result['closes']} DD={priority_result['max_dd_pct']}%", flush=True)

    # Comparison table
    print(f"\n{'='*70}", flush=True)
    print("COMPARISON TABLE", flush=True)
    print(f"{'='*70}", flush=True)
    print(f"{'Strategy':<25} | {'PnL':>8} | {'WR':>5} | {'Trades':>6} | {'DD':>5} | {'PnL/Trade':>9}", flush=True)
    print(f"{'-'*25}-+-{'-'*8}-+-{'-'*5}-+-{'-'*6}-+-{'-'*5}-+-{'-'*9}", flush=True)

    for coin, r in baselines.items():
        print(f"{coin:<25} | ${r['net_pnl']:>7.2f} | {r['win_rate']:>4.1f}% | "
              f"{r['closes']:>6} | {r['max_dd_pct']:>4.1f}% | ${r['avg_pnl_per_trade']:>8.2f}", flush=True)

    print(f"{'-'*25}-+-{'-'*8}-+-{'-'*5}-+-{'-'*6}-+-{'-'*5}-+-{'-'*9}", flush=True)
    print(f"Equal Split             | ${equal_result['net_pnl']:>7.2f} | {equal_result['win_rate']:>4.1f}% | "
          f"{equal_result['closes']:>6} | {equal_result['max_dd_pct']:>4.1f}% | ${equal_result['avg_pnl_per_trade']:>8.2f}", flush=True)
    print(f"Priority Queue          | ${priority_result['net_pnl']:>7.2f} | {priority_result['win_rate']:>4.1f}% | "
          f"{priority_result['closes']:>6} | {priority_result['max_dd_pct']:>4.1f}% | ${priority_result['avg_pnl_per_trade']:>8.2f}", flush=True)

    # Verdict
    best_baseline = max(baselines.values(), key=lambda r: r["net_pnl"])
    print(f"\n{'='*70}", flush=True)
    print("VERDICT", flush=True)
    print(f"{'='*70}", flush=True)

    print(f"\n  Best single coin: {max(baselines, key=lambda c: baselines[c]['net_pnl'])} "
          f"(${best_baseline['net_pnl']:.2f})", flush=True)
    print(f"  Equal split: ${equal_result['net_pnl']:.2f} "
          f"(delta vs best: ${equal_result['net_pnl'] - best_baseline['net_pnl']:+.2f})", flush=True)
    print(f"  Priority: ${priority_result['net_pnl']:.2f} "
          f"(delta vs best: ${priority_result['net_pnl'] - best_baseline['net_pnl']:+.2f})", flush=True)

    if equal_result["net_pnl"] > best_baseline["net_pnl"]:
        print(f"\n  → EQUAL SPLIT WINS — Diversification adds ${equal_result['net_pnl'] - best_baseline['net_pnl']:.2f}", flush=True)
    elif priority_result["net_pnl"] > best_baseline["net_pnl"]:
        print(f"\n  → PRIORITY WINS — Rotation adds ${priority_result['net_pnl'] - best_baseline['net_pnl']:.2f}", flush=True)
    else:
        print(f"\n  → SINGLE-COIN WINS — RAVE alone is best. Rotation dilutes the edge.", flush=True)

    if equal_result["max_dd_pct"] < best_baseline["max_dd_pct"]:
        print(f"  → DD improvement: {best_baseline['max_dd_pct']:.1f}% → {equal_result['max_dd_pct']:.1f}% "
              f"({best_baseline['max_dd_pct'] - equal_result['max_dd_pct']:.1f}pp reduction)", flush=True)

    # Save report
    report = {
        "run_at": utc_now_iso(),
        "window_days": WINDOW_DAYS,
        "starting_cash": STARTING_CASH,
        "fee_rate": FEE_RATE,
        "coin_strategies": COIN_STRATEGIES,
        "baselines": baselines,
        "equal_split": equal_result,
        "priority": priority_result,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)


if __name__ == "__main__":
    main()
