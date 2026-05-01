#!/usr/bin/env python3
"""
Parameter Optimizer — Find optimal RSI MR params per coin.

Grid searches: RSI period (2-5), TP (15-40%), max hold (20-80 bars)
Tests on each coin and finds the OPTIMAL params, not just RAVE's params.

Output: reports/parameter_optimization_results.json
"""
import json
import os
import sys
import time
import statistics
from datetime import datetime, timezone
from pathlib import Path
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from benchmark_shared import BUILTIN_FILL_MODELS, FEE_TIERS, framework_execution_kwargs

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "reports" / "parameter_optimization_results.json"

BTC = "BTC-USD"
DEFAULT_COINS = ["RAVE-USD", "BAL-USD", "IOTX-USD", "BLUR-USD"]
WINDOW_DAYS = 30
STARTING_CASH = 48.0

# Parameter grid
RSI_PERIODS = [2, 3, 4, 5]
TP_PCTS = [15, 20, 25, 30, 35, 40]
MAX_HOLDS = [20, 30, 40, 48, 60, 80]

FILL_MODEL = "measured_forward"


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


def get_fee_rate(total_volume):
    if total_volume >= 50000:
        return 0.0015
    elif total_volume >= 10000:
        return 0.0025
    return 0.0040


def apply_slippage(price, slippage_bps, direction="entry"):
    """Apply slippage: entry slippage is adverse (buy higher), exit is adverse (sell lower for TP)."""
    slip_pct = slippage_bps / 10000.0
    if direction == "entry":
        return price * (1 + slip_pct)
    else:
        return price * (1 - slip_pct)


def run_backtest(candles, btc_candles, rsi_period, tp_pct, max_hold, fee_rate, fill_model):
    """Run RSI MR backtest with given params."""
    cash = STARTING_CASH
    position = None
    realized_net = 0.0
    closes = 0
    wins = 0
    losses = 0
    total_volume = 0.0
    total_fees = 0.0
    history = []
    signals = 0

    fill_prob = fill_model.get("fill_prob", 1.0)
    entry_slip_bps = fill_model.get("entry_slippage_bps", 0.0)
    exit_slip_bps = fill_model.get("exit_slippage_bps", 0.0)

    for i, candle in enumerate(candles):
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
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
                exit_price = apply_slippage(position["tp"], exit_slip_bps, "exit")
                exit_reason = "tp"
            elif position["hold"] >= max_hold:
                exit_price = apply_slippage(close, exit_slip_bps, "exit")
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
                total_volume += position["q"] + (exit_price * units)
                total_fees += entry_fee + exit_fee

                if net > 0:
                    wins += 1
                else:
                    losses += 1

                position = None

        # ENTRY
        if position is None and cash >= 10.0 and len(history) >= rsi_period + 2:
            rsi_val = compute_rsi(history[:-1], rsi_period)

            if rsi_val <= 30:
                signals += 1

                # Fill probability check (deterministic for grid search: use threshold)
                # For fill_prob < 1.0, skip trades that wouldn't fill
                if fill_prob < 1.0:
                    # Deterministic proxy: skip based on signal index
                    if (signals * 7 + i * 3) % 100 >= fill_prob * 100:
                        continue

                deploy = cash
                entry_price = apply_slippage(open_price, entry_slip_bps, "entry")
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

    wr = wins / max(1, closes) * 100
    total_pnl = cash + (position["q"] if position else 0) - STARTING_CASH
    return_pct = total_pnl / STARTING_CASH * 100

    return {
        "net_pnl": round(total_pnl, 2),
        "return_pct": round(return_pct, 1),
        "win_rate": round(wr, 1),
        "closes": closes,
        "wins": wins,
        "losses": losses,
        "signals": signals,
        "total_fees": round(total_fees, 2),
        "avg_pnl_per_trade": round(total_pnl / max(1, closes), 2),
    }


def main():
    client = CoinbaseAdvancedClient()

    now = int(time.time())
    start = now - WINDOW_DAYS * 86400

    coins = DEFAULT_COINS
    print(f"=" * 70, flush=True)
    print(f"PARAMETER OPTIMIZER — {len(coins)} coins, {len(RSI_PERIODS)*len(TP_PCTS)*len(MAX_HOLDS)} combos each", flush=True)
    print(f"=" * 70, flush=True)

    all_results = []
    fill_model = dict(BUILTIN_FILL_MODELS[FILL_MODEL])

    for coin_idx, coin in enumerate(coins, 1):
        print(f"\n[{coin_idx}/{len(coins)}] Fetching {WINDOW_DAYS}d candles for {coin}...", flush=True)
        candles = fetch_candles(client, coin, start, now)
        btc_candles = fetch_candles(client, BTC, start, now)
        print(f"  {coin}: {len(candles)} candles, {len(btc_candles)} BTC candles", flush=True)

        if len(candles) < 100:
            print(f"  SKIP: insufficient data ({len(candles)} candles)", flush=True)
            continue

        best_result = None
        best_params = None
        best_pnl = -999999
        combo_count = 0

        for rsi_p, tp_p, mh in product(RSI_PERIODS, TP_PCTS, MAX_HOLDS):
            fee_rate = 0.0040  # Start at 40bps
            result = run_backtest(candles, btc_candles, rsi_p, tp_p, mh, fee_rate, fill_model)

            combo_count += 1
            if result["net_pnl"] > best_pnl and result["closes"] >= 20:
                best_pnl = result["net_pnl"]
                best_params = {"rsi_period": rsi_p, "tp_pct": tp_p, "max_hold": mh}
                best_result = result

            if combo_count % 50 == 0:
                print(f"  ... {combo_count}/{len(RSI_PERIODS)*len(TP_PCTS)*len(MAX_HOLDS)} combos", flush=True)

        print(f"  DONE: {combo_count} combos tested", flush=True)
        print(f"  BEST: RSI({best_params['rsi_period']}) TP={best_params['tp_pct']}% Hold={best_params['max_hold']}bars", flush=True)
        print(f"  PnL: ${best_result['net_pnl']:.2f} WR: {best_result['win_rate']}% Trades: {best_result['closes']}", flush=True)

        # Also run RAVE baseline params for comparison
        rave_result = run_backtest(candles, btc_candles, 3, 25, 48, 0.0040, fill_model)
        delta = best_result["net_pnl"] - rave_result["net_pnl"]

        all_results.append({
            "coin": coin,
            "candle_count": len(candles),
            "rave_params": {"rsi_period": 3, "tp_pct": 25, "max_hold": 48},
            "rave_result": rave_result,
            "optimal_params": best_params,
            "optimal_result": best_result,
            "delta_vs_rave": round(delta, 2),
            "optimization_wins": delta > 0,
        })

    # Rank by optimal PnL
    all_results.sort(key=lambda r: r["optimal_result"]["net_pnl"], reverse=True)

    # Summary
    print(f"\n{'='*70}", flush=True)
    print("RANKED RESULTS (by optimal PnL)", flush=True)
    print(f"{'='*70}", flush=True)
    print(f"{'Rank':>4} | {'Coin':<12} | {'Opt PnL':>8} | {'Opt Params':<25} | {'RAVE PnL':>8} | {'Delta':>7} | {'WR':>5}", flush=True)
    print(f"{'-'*4}-+-{'-'*12}-+-{'-'*8}-+-{'-'*25}-+-{'-'*8}-+-{'-'*7}-+-{'-'*5}", flush=True)

    for i, r in enumerate(all_results, 1):
        p = r["optimal_params"]
        params_str = f"RSI({p['rsi_period']}) TP{p['tp_pct']} H{p['max_hold']}"
        print(f"{i:>4} | {r['coin']:<12} | ${r['optimal_result']['net_pnl']:>7.2f} | {params_str:<25} | "
              f"${r['rave_result']['net_pnl']:>7.2f} | ${r['delta_vs_rave']:>6.2f} | "
              f"{r['optimal_result']['win_rate']:>4.1f}%", flush=True)

    # Summary stats
    coins_better = sum(1 for r in all_results if r["optimization_wins"])
    coins_worse = sum(1 for r in all_results if not r["optimization_wins"])
    avg_delta = statistics.mean(r["delta_vs_rave"] for r in all_results) if all_results else 0

    print(f"\n  Coins where optimal > RAVE: {coins_better}/{len(all_results)}", flush=True)
    print(f"  Avg delta (optimal - RAVE): ${avg_delta:+.2f}", flush=True)

    if coins_better > 0:
        print(f"\n  TOP OPTIMIZATION GAINERS:", flush=True)
        for r in all_results:
            if r["optimization_wins"]:
                p = r["optimal_params"]
                print(f"    {r['coin']}: +${r['delta_vs_rave']:.2f} with RSI({p['rsi_period']}), TP{p['tp_pct']}%, Hold{p['max_hold']}b", flush=True)

    # Save report
    report = {
        "run_at": utc_now_iso(),
        "window_days": WINDOW_DAYS,
        "fill_model": FILL_MODEL,
        "fee_rate": 0.0040,
        "param_grid": {
            "rsi_periods": RSI_PERIODS,
            "tp_pcts": TP_PCTS,
            "max_holds": MAX_HOLDS,
            "total_combos": len(RSI_PERIODS) * len(TP_PCTS) * len(MAX_HOLDS),
        },
        "results": all_results,
        "summary": {
            "coins_tested": len(all_results),
            "coins_better_than_rave": coins_better,
            "coins_worse_than_rave": coins_worse,
            "avg_delta_vs_rave": round(avg_delta, 2),
        },
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)


if __name__ == "__main__":
    main()
