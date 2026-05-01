#!/usr/bin/env python3
"""
Multi-Coin RSI Mean Reversion Scan.

Runs the EXACT same RSI MR strategy across a broad universe of coins
to find the next RAVE. Same params, same window, same fills.

Strategy: RSI(3) < 30, 25% TP, 48-bar max, no SL, session gate
Fills: empirical (8bps entry, 0bps exit, 100% fill prob)
Fee: 40bps tier

Usage:
    python scripts/multi_coin_rsi_scan.py --window 30d
    python scripts/multi_coin_rsi_scan.py --window 7d --coins RAVE-USD BAL-USD IOTX-USD
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from regime_detection import regime_score
from benchmark_regime_segmented import (
    fetch_candles_coinbase,
    normalize_candles,
    _align_btc_candles,
    compute_rsi,
    FEE_TIERS,
    FILL_MODELS,
)

ROOT = Path(__file__).resolve().parent.parent

# Default scan universe
DEFAULT_COINS = [
    "RAVE-USD",    # Benchmark — known winner
    "BAL-USD",     # Benchmark universe
    "IOTX-USD",    # Benchmark universe
    "BLUR-USD",    # Benchmark universe
    "SOL-USD",     # High volume L1
    "DOGE-USD",    # Meme, high volatility
    "XRP-USD",     # Deep order book
    "PEPE-USD",    # Meme, extreme volatility
    "WIF-USD",     # Meme
    "AAVE-USD",    # DeFi blue-chip
    "LINK-USD",    # Oracle, high liquidity
    "UNI-USD",     # DEX governance
    "AVAX-USD",    # L1
    "NEAR-USD",    # L1
    "FET-USD",     # AI narrative
    "RENDER-USD",  # AI/GPU narrative
    "TIA-USD",     # Modular L1
    "SEI-USD",     # L1
    "SUI-USD",     # L1
    "ONDO-USD",    # RWA narrative
]

# RAVE MR strategy params (standard)
STRATEGY_PARAMS = {
    "rsi_period": 3,
    "os_thresh": 30,
    "tp_pct": 25,
    "sl_pct": 0,
    "max_hold": 48,
}


def _score_to_regime(score: float) -> str:
    if score >= 70:
        return "hot"
    elif score >= 40:
        return "cold"
    else:
        return "choppy"


def classify_candles(candles: list[dict], btc_candles: list[dict], window: int = 30) -> list[str]:
    labels = []
    for i in range(len(candles)):
        if i < window - 1:
            wc = candles[:i + 1]
        else:
            wc = candles[i - window + 1:i + 1]
        aligned = _align_btc_candles(wc, btc_candles)
        score = regime_score(wc, aligned)
        labels.append(_score_to_regime(score["score"]))
    return labels


def run_backtest(
    candles: list[dict],
    regime_labels: list[str] | None,
    strategy_params: dict,
    fill_model: dict,
    fee_rate: float,
    starting_cash: float = 100.0,
    seed: int = 42,
) -> dict:
    rng = random.Random(seed)
    cash = starting_cash
    pos = None
    closes_count = 0
    wins = 0
    losses = 0
    peak = starting_cash
    max_dd = 0.0
    history = []
    signals = 0
    regime_trades = {"hot": 0, "cold": 0, "choppy": 0}
    regime_wins = {"hot": 0, "cold": 0, "choppy": 0}

    fill_prob = fill_model.get("fill_prob", 1.0)
    entry_slip = fill_model.get("entry_slippage_bps", 0) / 10000.0
    exit_slip = fill_model.get("exit_slippage_bps", 0) / 10000.0
    rsi_period = strategy_params["rsi_period"]
    os_thresh = strategy_params["os_thresh"]
    tp_pct = strategy_params["tp_pct"]
    sl_pct = strategy_params["sl_pct"]
    max_hold = strategy_params["max_hold"]

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
        current_regime = regime_labels[i] if regime_labels and i < len(regime_labels) else "cold"

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
                regime = pos.get("regime", "cold")
                regime_trades[regime] = regime_trades.get(regime, 0) + 1
                if net > 0:
                    wins += 1
                    regime_wins[regime] = regime_wins.get(regime, 0) + 1
                else:
                    losses += 1
                peak = max(peak, cash)
                dd = (peak - cash) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
                pos = None

        # ENTRY
        if pos is None and session_open:
            if len(history) >= rsi_period + 2:
                rsi_val = compute_rsi(history[:-1], rsi_period)
                if rsi_val <= os_thresh:
                    signals += 1
                    if rng.random() > fill_prob:
                        continue
                    actual_entry = candle_open * (1 + entry_slip)
                    deploy = cash
                    if deploy < 10.0:
                        continue
                    entry_fee = deploy * fee_rate
                    units = (deploy - entry_fee) / actual_entry
                    tp = actual_entry * (1 + tp_pct / 100.0)
                    sl = actual_entry * (1 - sl_pct / 100.0) if sl_pct > 0 else 0
                    cash -= deploy
                    pos = {
                        "ep": actual_entry, "q": deploy, "hold": 0,
                        "tp": tp, "sl": sl, "units": units,
                        "entry_fee": entry_fee, "max_hold": max_hold,
                        "regime": current_regime,
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
        "signals": signals,
        "fill_rate": round(closes_count / max(signals, 1) * 100, 1),
        "regime_trades": regime_trades,
        "regime_wins": regime_wins,
        "regime_wr": {
            r: round(regime_wins.get(r, 0) / max(regime_trades.get(r, 1), 1) * 100, 1)
            for r in ["hot", "cold", "choppy"]
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Multi-coin RSI MR scan")
    parser.add_argument("--window", default="30d")
    parser.add_argument("--coins", nargs="+", default=None)
    parser.add_argument("--fill-model", default="empirical")
    parser.add_argument("--fee-tier", default="40bps")
    parser.add_argument("--starting-cash", type=float, default=100.0)
    parser.add_argument("--no-regime", action="store_true", help="Skip regime classification (faster)")
    args = parser.parse_args()

    days = 7 if args.window == "7d" else 30
    fee_rate = FEE_TIERS.get(args.fee_tier, 0.004)
    fill_model = FILL_MODELS.get(args.fill_model, FILL_MODELS["realistic"])
    coins = args.coins or DEFAULT_COINS

    print(f"Fetching BTC-USD candles...")
    btc_candles = normalize_candles(fetch_candles_coinbase("BTC-USD", days))
    print(f"Loaded {len(btc_candles)} BTC candles.")

    results = []
    failed = []

    for idx, coin in enumerate(coins):
        print(f"\n[{idx+1}/{len(coins)}] Scanning {coin}...")
        try:
            candles = normalize_candles(fetch_candles_coinbase(coin, days))
            if len(candles) < 100:
                print(f"  SKIP: only {len(candles)} candles (insufficient data)")
                failed.append({"coin": coin, "reason": "insufficient_data", "candles": len(candles)})
                continue

            # Regime classification
            regime_labels = None
            regime_dist = {}
            if not args.no_regime:
                print(f"  Classifying regimes...")
                regime_labels = classify_candles(candles, btc_candles)
                counts = Counter(regime_labels)
                total = len(regime_labels)
                regime_dist = {r: {"count": counts.get(r, 0), "pct": round(counts.get(r, 0) / total * 100, 1)}
                               for r in ["hot", "cold", "choppy"]}

            # Backtest
            result = run_backtest(candles, regime_labels, STRATEGY_PARAMS, fill_model, fee_rate, args.starting_cash)
            result["coin"] = coin
            result["candles"] = len(candles)
            result["regime_distribution"] = regime_dist
            results.append(result)

            print(f"  {len(candles)} candles, {result['trades']} trades, "
                  f"WR={result['win_rate']}%, Net=${result['net_pnl']:+.2f}, "
                  f"DD={result['max_drawdown']}%")
            if regime_dist:
                print(f"  Regimes: HOT={regime_dist['hot']['pct']}% "
                      f"COLD={regime_dist['cold']['pct']}% "
                      f"CHOPPY={regime_dist['choppy']['pct']}%")

        except Exception as e:
            print(f"  ERROR: {e}")
            failed.append({"coin": coin, "reason": str(e)})

    # Sort by net PnL descending
    results.sort(key=lambda r: r["net_pnl"], reverse=True)

    # Print ranked table
    print(f"\n{'='*110}")
    print(f"MULTI-COIN RSI MR SCAN — {args.window} ({args.fill_model}, {args.fee_tier})")
    print(f"{'='*110}")

    hdr = f"{'#':<3} {'Coin':<15} {'Candles':<9} {'Trades':<7} {'WR%':<6} {'Net':<10} {'Ret%':<8} {'DD%':<6} {'Signals':<9} {'Fill%':<6}"
    print(hdr)
    print("-" * 110)

    for i, r in enumerate(results):
        marker = "🔥" if r["win_rate"] >= 60 and r["net_pnl"] > 100 else ""
        print(f"{i+1:<3} {r['coin']:<15} {r['candles']:<9} {r['trades']:<7} {r['win_rate']:<6.1f} "
              f"${r['net_pnl']:<9.2f} {r['return_pct']:<8.1f} {r['max_drawdown']:<6.1f} "
              f"{r['signals']:<9} {r['fill_rate']:<6.1f} {marker}")

    # Top performers with regime breakdown
    top = [r for r in results if r["win_rate"] >= 50 and r["net_pnl"] > 0]
    if top:
        print(f"\n{'='*110}")
        print(f"QUALIFYING COINS (WR≥50%, Net>0): {len(top)}")
        print(f"{'='*110}")
        for r in top[:10]:
            rr = r.get("regime_wr", {})
            rt = r.get("regime_trades", {})
            print(f"\n  {r['coin']}: WR={r['win_rate']}%, Net=${r['net_pnl']:+.2f}, DD={r['max_drawdown']}%, {r['trades']} trades")
            if rr:
                print(f"    Regime WR: HOT={rr.get('hot', 'N/A')}% ({rt.get('hot', 0)}t) | "
                      f"COLD={rr.get('cold', 'N/A')}% ({rt.get('cold', 0)}t) | "
                      f"CHOPPY={rr.get('choppy', 'N/A')}% ({rt.get('choppy', 0)}t)")

    if failed:
        print(f"\nFailed/Skipped: {len(failed)}")
        for f in failed:
            print(f"  {f['coin']}: {f['reason']}")

    # Save report
    report = {
        "window": args.window,
        "fill_model": args.fill_model,
        "fee_tier": args.fee_tier,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coins_scanned": len(results) + len(failed),
        "coins_succeeded": len(results),
        "coins_failed": len(failed),
        "qualifying_count": len(top),
        "results": results,
        "failed": failed,
    }

    output_dir = ROOT / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"multi_coin_rsi_scan_{args.window}.json"
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\nReport: {output_path}")


if __name__ == "__main__":
    main()
