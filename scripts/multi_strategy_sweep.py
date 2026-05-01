#!/usr/bin/env python3
"""
Multi-Strategy Sweep — Find what works across diverse market structures.

Tests 4 strategy types across 20 coins to break the RAVE monoculture.

Strategies:
1. RSI Mean Reversion — RSI(3)<30, buy dip, TP target
2. Momentum Breakout — Buy when price breaks above N-bar high
3. EMA Pullback — Buy RSI dips ONLY when price > EMA200 (uptrend filter)
4. Volatility Squeeze — Trade BB squeeze breakouts

Usage:
    python scripts/multi_strategy_sweep.py --window 30d
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from benchmark_regime_segmented import (
    fetch_candles_coinbase,
    normalize_candles,
    FEE_TIERS,
)

ROOT = Path(__file__).resolve().parent.parent

COINS = [
    "RAVE-USD", "BAL-USD", "IOTX-USD", "BLUR-USD",
    "SOL-USD", "DOGE-USD", "XRP-USD", "PEPE-USD",
    "WIF-USD", "AAVE-USD", "LINK-USD", "UNI-USD",
    "AVAX-USD", "NEAR-USD", "FET-USD", "RENDER-USD",
    "TIA-USD", "SEI-USD", "SUI-USD", "ONDO-USD",
]


def compute_rsi(closes, period=3):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l > 0:
        rs = avg_g / avg_l
        return 100 - 100 / (1 + rs)
    return 100.0


def compute_ema(closes, period):
    if len(closes) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def compute_bb(closes, period=20, std_mult=2.0):
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    sma = sum(window) / period
    variance = sum((x - sma) ** 2 for x in window) / period
    std = variance ** 0.5
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    return sma, upper, lower


def run_backtest(candles, strategy_fn, params, fee_rate, starting_cash=100.0, seed=42):
    rng = random.Random(seed)
    cash = starting_cash
    pos = None
    closes_count = 0
    wins = 0
    losses = 0
    peak = starting_cash
    max_dd = 0.0
    history = []

    fill_prob = params.get("fill_prob", 1.0)
    entry_slip = params.get("entry_slip", 0.0008)
    exit_slip = params.get("exit_slip", 0.0)

    for i in range(len(candles)):
        c = candles[i]
        close = c["close"]
        high = c["high"]
        low = c["low"]
        candle_open = c["open"]
        history.append(close)
        if len(history) > 500:
            history = history[-500:]

        ts = c["start"]
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_open = hour not in {0, 6, 12, 19}

        # EXIT
        if pos:
            pos["hold"] += 1
            exit_price = None
            if high >= pos["tp"]:
                exit_price = pos["tp"]
            elif pos["sl"] > 0 and low <= pos["sl"]:
                exit_price = pos["sl"]
            elif pos["hold"] >= pos["max_hold"]:
                exit_price = close

            if exit_price is not None:
                actual_exit = exit_price * (1 - exit_slip)
                units = pos["units"]
                gross = (actual_exit - pos["ep"]) * units
                entry_fee = pos["entry_fee"]
                exit_fee = actual_exit * units * fee_rate
                net = gross - entry_fee - exit_fee
                cash += pos["q"] + net
                closes_count += 1
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                peak = max(peak, cash)
                dd = (peak - cash) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
                pos = None

        # ENTRY
        if pos is None and session_open and cash >= 10.0:
            signal = strategy_fn(history, candles, i, params)
            if signal:
                if rng.random() > fill_prob:
                    continue
                actual_entry = candle_open * (1 + entry_slip)
                deploy = cash
                entry_fee = deploy * fee_rate
                units = (deploy - entry_fee) / actual_entry
                tp_price = actual_entry * (1 + params["tp_pct"] / 100.0)
                sl_price = actual_entry * (1 - params["sl_pct"] / 100.0) if params["sl_pct"] > 0 else 0
                cash -= deploy
                pos = {
                    "ep": actual_entry, "q": deploy, "hold": 0,
                    "tp": tp_price, "sl": sl_price, "units": units,
                    "entry_fee": entry_fee, "max_hold": params["max_hold"],
                }

    if pos:
        cash += pos["q"]
    net = cash - starting_cash
    wr = wins / max(closes_count, 1) * 100
    return {
        "net_pnl": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 2),
        "trades": closes_count,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr, 1),
        "max_drawdown": round(max_dd * 100, 1),
    }


# ---- Strategy definitions ----

def rsi_mr_strategy(history, candles, idx, params):
    """RSI Mean Reversion: RSI(period) < threshold → buy"""
    period = params.get("rsi_period", 3)
    thresh = params.get("os_thresh", 30)
    if len(history) < period + 2:
        return False
    rsi = compute_rsi(history, period)
    return rsi <= thresh


def momentum_breakout_strategy(history, candles, idx, params):
    """Momentum Breakout: close > highest high of last N bars → buy"""
    lookback = params.get("breakout_lookback", 20)
    if len(history) < lookback + 1:
        return False
    # Need high data, not just close
    if idx < lookback:
        return False
    current_high = candles[idx]["high"]
    highest = max(candles[j]["high"] for j in range(idx - lookback, idx))
    return current_high > highest


def ema_pullback_strategy(history, candles, idx, params):
    """EMA Pullback: price > EMA200 (uptrend) AND RSI(3) < 40 (pullback) → buy"""
    ema_period = params.get("ema_period", 200)
    rsi_period = params.get("rsi_period", 3)
    rsi_thresh = params.get("rsi_thresh", 40)
    
    if len(history) < ema_period + 10:
        return False
    
    ema = compute_ema(history, ema_period)
    if ema is None:
        return False
    
    current_price = history[-1]
    if current_price <= ema:  # Not in uptrend
        return False
    
    rsi = compute_rsi(history, rsi_period)
    return rsi <= rsi_thresh


def volatility_squeeze_strategy(history, candles, idx, params):
    """Volatility Squeeze: BB width narrows, then price breaks out → buy"""
    bb_period = params.get("bb_period", 20)
    squeeze_thresh = params.get("squeeze_thresh", 2.0)  # BB width % threshold
    
    if len(history) < bb_period + 1:
        return False
    
    # Current BB width
    _, upper, lower = compute_bb(history, bb_period)
    if upper is None or lower is None:
        return False
    
    current_price = history[-1]
    bb_width = (upper - lower) / current_price * 100
    
    # Was squeezed (narrow), now breaking out
    if bb_width < squeeze_thresh:
        # Price breaking above the middle band = bullish breakout
        sma, _, _ = compute_bb(history, bb_period)
        if sma and current_price > sma:
            return True
    return False


STRATEGIES = {
    "rsi_mr": {
        "name": "RSI Mean Reversion",
        "fn": rsi_mr_strategy,
        "param_sets": [
            {"rsi_period": 3, "os_thresh": 30, "tp_pct": 25, "sl_pct": 0, "max_hold": 48, "label": "rsi_3_25tp"},
            {"rsi_period": 3, "os_thresh": 30, "tp_pct": 10, "sl_pct": 5, "max_hold": 48, "label": "rsi_3_10tp_5sl"},
            {"rsi_period": 3, "os_thresh": 30, "tp_pct": 15, "sl_pct": 0, "max_hold": 48, "label": "rsi_3_15tp"},
        ],
    },
    "momentum": {
        "name": "Momentum Breakout",
        "fn": momentum_breakout_strategy,
        "param_sets": [
            {"breakout_lookback": 20, "tp_pct": 5, "sl_pct": 3, "max_hold": 48, "label": "mom_20_5tp_3sl"},
            {"breakout_lookback": 20, "tp_pct": 10, "sl_pct": 5, "max_hold": 96, "label": "mom_20_10tp_5sl"},
            {"breakout_lookback": 50, "tp_pct": 10, "sl_pct": 5, "max_hold": 96, "label": "mom_50_10tp_5sl"},
            {"breakout_lookback": 50, "tp_pct": 15, "sl_pct": 5, "max_hold": 144, "label": "mom_50_15tp_5sl"},
        ],
    },
    "ema_pullback": {
        "name": "EMA Pullback",
        "fn": ema_pullback_strategy,
        "param_sets": [
            {"ema_period": 200, "rsi_period": 3, "rsi_thresh": 40, "tp_pct": 5, "sl_pct": 5, "max_hold": 48, "label": "ema200_rsi40_5tp_5sl"},
            {"ema_period": 200, "rsi_period": 3, "rsi_thresh": 40, "tp_pct": 10, "sl_pct": 5, "max_hold": 48, "label": "ema200_rsi40_10tp_5sl"},
            {"ema_period": 100, "rsi_period": 3, "rsi_thresh": 40, "tp_pct": 5, "sl_pct": 5, "max_hold": 48, "label": "ema100_rsi40_5tp_5sl"},
            {"ema_period": 200, "rsi_period": 3, "rsi_thresh": 30, "tp_pct": 10, "sl_pct": 10, "max_hold": 48, "label": "ema200_rsi30_10tp_10sl"},
        ],
    },
    "vol_squeeze": {
        "name": "Volatility Squeeze",
        "fn": volatility_squeeze_strategy,
        "param_sets": [
            {"bb_period": 20, "squeeze_thresh": 2.0, "tp_pct": 5, "sl_pct": 3, "max_hold": 48, "label": "bb20_2pct_5tp_3sl"},
            {"bb_period": 20, "squeeze_thresh": 5.0, "tp_pct": 5, "sl_pct": 3, "max_hold": 48, "label": "bb20_5pct_5tp_3sl"},
            {"bb_period": 20, "squeeze_thresh": 2.0, "tp_pct": 10, "sl_pct": 5, "max_hold": 96, "label": "bb20_2pct_10tp_5sl"},
            {"bb_period": 50, "squeeze_thresh": 5.0, "tp_pct": 10, "sl_pct": 5, "max_hold": 96, "label": "bb50_5pct_10tp_5sl"},
        ],
    },
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", default="30d")
    parser.add_argument("--coins", nargs="+", default=None)
    parser.add_argument("--fee-tier", default="40bps")
    parser.add_argument("--starting-cash", type=float, default=100.0)
    args = parser.parse_args()

    days = 7 if args.window == "7d" else 30
    fee_rate = FEE_TIERS.get(args.fee_tier, 0.004)
    coins = args.coins or COINS

    # Fetch candles
    print(f"Fetching candles for {len(coins)} coins ({args.window})...")
    all_candles = {}
    for coin in coins:
        print(f"  {coin}...", end=" ", flush=True)
        try:
            candles = normalize_candles(fetch_candles_coinbase(coin, days))
            all_candles[coin] = candles
            print(f"{len(candles)} candles")
        except Exception as e:
            print(f"ERROR: {e}")

    total_combos = sum(len(s["param_sets"]) for s in STRATEGIES.values()) * len(all_candles)
    print(f"\nRunning multi-strategy sweep: {total_combos} backtests")

    # Sweep
    qualifying = []  # All combos with net > 0 and WR >= 40%
    coin_results = {}  # coin -> {strategy -> best_result}

    for coin_idx, (coin, candles) in enumerate(all_candles.items()):
        if len(candles) < 100:
            continue

        print(f"\n[{coin_idx+1}/{len(all_candles)}] {coin} ({len(candles)} candles)...")
        coin_best = {}

        for strat_key, strat_info in STRATEGIES.items():
            strat_best = None
            strat_best_net = -999

            for pset in strat_info["param_sets"]:
                r = run_backtest(candles, strat_info["fn"], pset, fee_rate, args.starting_cash)
                r["strategy"] = strat_key
                r["label"] = pset["label"]

                if r["net_pnl"] > strat_best_net:
                    strat_best_net = r["net_pnl"]
                    strat_best = r

                if r["net_pnl"] > 0 and r["win_rate"] >= 40:
                    qualifying.append({
                        "coin": coin,
                        "strategy": strat_key,
                        "label": pset["label"],
                        "net_pnl": r["net_pnl"],
                        "win_rate": r["win_rate"],
                        "trades": r["trades"],
                        "max_drawdown": r["max_drawdown"],
                    })

            coin_best[strat_key] = strat_best
            marker = "🔥" if strat_best and strat_best["net_pnl"] > 0 else ""
            print(f"  {strat_info['name']:25s} best: ${strat_best['net_pnl']:+.2f} WR={strat_best['win_rate']}% T={strat_best['trades']} DD={strat_best['max_drawdown']}% [{strat_best['label']}] {marker}")

        coin_results[coin] = coin_best

    # Results
    qualifying.sort(key=lambda x: x["net_pnl"], reverse=True)

    print(f"\n{'='*120}")
    print(f"MULTI-STRATEGY SWEEP — {args.window} ({args.fee_tier})")
    print(f"{'='*120}")
    print(f"Total combos tested: {total_combos}")
    print(f"Qualifying (net>0, WR>=40%): {len(qualifying)}")

    if qualifying:
        print(f"\nALL QUALIFYING COMBOS:")
        hdr = f"{'#':<3} {'Coin':<15} {'Strategy':<18} {'Config':<25} {'Net':<10} {'WR%':<6} {'Trades':<7} {'DD%':<6}"
        print(hdr)
        print("-" * 120)
        for i, q in enumerate(qualifying):
            print(f"{i+1:<3} {q['coin']:<15} {q['strategy']:<18} {q['label']:<25} "
                  f"${q['net_pnl']:<9.2f} {q['win_rate']:<6.1f} {q['trades']:<7} {q['max_drawdown']:<6.1f}")

        # Per-coin: which strategy won?
        print(f"\n{'='*120}")
        print(f"PER-COIN WINNING STRATEGY")
        print(f"{'='*120}")
        hdr = f"{'Coin':<15} {'Best Strategy':<18} {'Config':<25} {'Net':<10} {'WR%':<6} {'DD%':<6}"
        print(hdr)
        print("-" * 120)
        for coin, cbest in coin_results.items():
            best_overall = max(cbest.values(), key=lambda x: x["net_pnl"])
            marker = "🔥" if best_overall["net_pnl"] > 0 else ""
            print(f"{coin:<15} {best_overall['strategy']:<18} {best_overall['label']:<25} "
                  f"${best_overall['net_pnl']:<9.2f} {best_overall['win_rate']:<6.1f} {best_overall['max_drawdown']:<6.1f} {marker}")

        # Count coins with at least one profitable strategy
        coins_with_edge = set()
        for coin, cbest in coin_results.items():
            for strat, result in cbest.items():
                if result and result["net_pnl"] > 0 and result["win_rate"] >= 40:
                    coins_with_edge.add(coin)
                    break

        print(f"\nCoins with at least one profitable strategy (WR>=40%): {len(coins_with_edge)}/{len(all_candles)}")
        for c in sorted(coins_with_edge):
            print(f"  ✅ {c}")

    # Save report
    report = {
        "window": args.window,
        "fee_tier": args.fee_tier,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_combos": total_combos,
        "qualifying_count": len(qualifying),
        "qualifying": qualifying[:50],
        "per_coin_winner": {
            coin: {strat: r for strat, r in cbest.items()}
            for coin, cbest in coin_results.items()
        },
    }

    output_dir = ROOT / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"multi_strategy_sweep_{args.window}.json"
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport: {output_path}")


if __name__ == "__main__":
    main()
