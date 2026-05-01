#!/usr/bin/env python3
"""
Backtest Reconciliation — Single reference engine for ALL strategies.

Uses the SAME candles (saved to file), SAME entry/exit logic, SAME fill model
for ALL strategies. This is the ground truth that resolves all discrepancies.

Tests:
1. RAVE RSI MR (RSI(3)<30, 25% TP, 48 bars)
2. RAVE Momentum (10-bar breakout, 10% TP, 10% SL)
3. IOTX BB Reversion (RSI<30, near BB lower, TP=middle, SL=5%)
4. BAL Momentum (50-bar, 10% TP, 3% SL)
5. BLUR Momentum (25-bar, 12% TP, 7% SL)

All use: 40bps flat fee, 100% fill, entry on candle OPEN, no slippage.
Starting cash: $48 (single) or $48 split equally across strategies.

Output: reports/backtest_reconciliation.json
"""
import json
import os
import sys
import time
import statistics
import math
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from benchmark_shared import framework_execution_kwargs

ROOT = Path(__file__).resolve().parent.parent
CANDLE_PATH = ROOT / "reports" / "reconciliation_candles.json"
OUTPUT_PATH = ROOT / "reports" / "backtest_reconciliation.json"

COINS = ["RAVE-USD", "IOTX-USD", "BAL-USD", "BLUR-USD"]
BTC = "BTC-USD"
WINDOW_DAYS = 30
STARTING_CASH = 48.0
FEE_RATE = 0.0040  # Flat 40bps for reconciliation
MIN_ENTRY_CASH = 10.0
USE_SNAPSHOT_ENV = "USE_RECONCILIATION_SNAPSHOT"


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def fetch_and_save_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    """Fetch candles and save to file for reproducibility."""
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


def save_candle_snapshot(path, coin_candles):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": utc_now_iso(),
        "coins": {
            coin: {
                "count": len(candles),
                "candles": candles,
            }
            for coin, candles in coin_candles.items()
        },
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


def load_candle_snapshot(path):
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return {
        str(coin): list(data.get("candles") or [])
        for coin, data in (payload.get("coins") or {}).items()
    }


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


def compute_bb(closes, period=20):
    if len(closes) < period:
        return None
    recent = closes[-period:]
    sma = statistics.mean(recent)
    std = statistics.stdev(recent) if len(recent) > 1 else 0
    return {"sma": sma, "lower": sma - 2 * std, "upper": sma + 2 * std}


# ============================================================
# UNIFIED BACKTEST ENGINE
# Entry on candle OPEN, no slippage, flat 40bps fee
# Full cash deployment per trade (compounding)
# ============================================================

def run_single_strategy_backtest(candles, strategy_type, params, starting_cash=48.0, min_entry_cash=MIN_ENTRY_CASH):
    """
    Unified backtest engine with FIXED semantics.
    All strategies use the same entry/exit/fee logic.
    """
    cash = starting_cash
    position = None
    closes = 0
    wins = 0
    losses = 0
    total_fees = 0.0
    total_volume = 0.0
    history = []
    candle_history = []
    signals = 0
    peak_equity = starting_cash
    max_dd = 0.0
    equity_curve = []

    for i, candle in enumerate(candles):
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        open_price = float(candle["open"])

        history.append(close)
        candle_history.append(candle)
        if len(history) > 500:
            history = history[-500:]

        # EXIT
        if position:
            position["hold"] += 1
            exit_price = None
            exit_reason = None

            if strategy_type == "rsi_mr":
                # RSI MR: TP or timeout, no SL
                if high >= position["tp"]:
                    exit_price = position["tp"]
                    exit_reason = "tp"
                elif position["hold"] >= params.get("max_hold", 48):
                    exit_price = close
                    exit_reason = "timeout"
            else:
                # All others: TP, SL, or timeout
                if high >= position["tp"]:
                    exit_price = position["tp"]
                    exit_reason = "tp"
                elif position.get("sl", 0) > 0 and low <= position["sl"]:
                    exit_price = position["sl"]
                    exit_reason = "stop"
                elif position["hold"] >= position.get("max_hold", 48):
                    exit_price = close
                    exit_reason = "timeout"

            if exit_price is not None:
                units = position["units"]
                gross = (exit_price - position["ep"]) * units
                entry_fee = position["entry_fee"]
                exit_fee = exit_price * units * FEE_RATE
                net = gross - entry_fee - exit_fee

                cash += position["q"] + net
                closes += 1
                total_volume += position["q"] + (exit_price * units)
                total_fees += entry_fee + exit_fee

                if net > 0:
                    wins += 1
                else:
                    losses += 1

                position = None

        # ENTRY
        if position is None and cash >= min_entry_cash:
            signal = False

            if strategy_type == "rsi_mr":
                # RSI Mean Reversion
                if len(history) > params.get("rsi_period", 3) + 1:
                    rsi_val = compute_rsi(history[:-1], params.get("rsi_period", 3))
                    if rsi_val <= params.get("os_thresh", 30):
                        signal = True

            elif strategy_type == "momentum":
                # Momentum Breakout: buy N-bar high breakout
                lookback = params.get("lookback", 10)
                if len(candle_history) > lookback + 1:
                    recent_high = max(float(c["high"]) for c in candle_history[-(lookback+1):-1])
                    if high > recent_high:
                        signal = True

            elif strategy_type == "bb_reversion":
                # BB Reversion: RSI<30 AND price near BB lower
                if len(history) > params.get("bb_period", 20) + 5:
                    rsi_val = compute_rsi(history[:-1], 3)
                    bb = compute_bb(history[:-1], params.get("bb_period", 20))
                    if bb and rsi_val <= params.get("rsi_thresh", 30):
                        dist = (close - bb["lower"]) / bb["sma"] * 100 if bb["sma"] > 0 else 999
                        if dist < 2.0:
                            signal = True

            if signal:
                signals += 1
                deploy = cash * 0.95
                entry_price = open_price
                entry_fee = deploy * FEE_RATE
                units = (deploy - entry_fee) / entry_price

                tp_mult = params.get("tp_pct", 0.25)
                sl_pct = params.get("sl_pct", 0)
                max_hold = params.get("max_hold", 48)

                if strategy_type == "bb_reversion":
                    # TP = BB middle band
                    bb = compute_bb(history[:-1], params.get("bb_period", 20))
                    tp = bb["sma"] if bb else entry_price * 1.05
                else:
                    tp = entry_price * (1 + tp_mult)

                sl = entry_price * (1 - sl_pct) if sl_pct > 0 else 0

                cash -= deploy
                position = {
                    "ep": entry_price,
                    "q": deploy,
                    "units": units,
                    "tp": tp,
                    "sl": sl,
                    "hold": 0,
                    "max_hold": max_hold,
                    "entry_fee": entry_fee,
                }

        # Track equity
        pos_value = position["q"] if position else 0
        equity = cash + pos_value
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100
        if dd > max_dd:
            max_dd = dd
        equity_curve.append(equity)

    # Close remaining
    if position:
        last_close = float(candles[-1]["close"])
        units = position["units"]
        gross = (last_close - position["ep"]) * units
        entry_fee = position["entry_fee"]
        exit_fee = last_close * units * FEE_RATE
        net = gross - entry_fee - exit_fee
        cash += position["q"] + net
        closes += 1
        total_volume += position["q"] + (last_close * units)
        total_fees += entry_fee + exit_fee
        if net > 0:
            wins += 1
        else:
            losses += 1

    total_pnl = cash - starting_cash
    return_pct = total_pnl / starting_cash * 100
    wr = wins / max(1, closes) * 100

    # Sharpe
    if len(equity_curve) > 1:
        returns = [(equity_curve[i] - equity_curve[i-1]) / equity_curve[i-1]
                   for i in range(1, len(equity_curve))
                   if equity_curve[i-1] > 0]
        if returns and len(returns) > 1:
            avg_ret = statistics.mean(returns)
            std_ret = statistics.stdev(returns)
            sharpe = (avg_ret / std_ret) * math.sqrt(8640) if std_ret > 0 else 0
        else:
            sharpe = 0
    else:
        sharpe = 0

    return {
        "net_pnl": round(total_pnl, 2),
        "return_pct": round(return_pct, 1),
        "win_rate": round(wr, 1),
        "closes": closes,
        "wins": wins,
        "losses": losses,
        "signals": signals,
        "max_dd": round(max_dd, 1),
        "total_fees": round(total_fees, 2),
        "total_volume": round(total_volume, 2),
        "sharpe": round(sharpe, 2),
        "avg_pnl_per_trade": round(total_pnl / max(1, closes), 2),
        "final_cash": round(cash, 2),
    }


def equal_split_feasibility(starting_cash, n_strategies, min_entry_cash=MIN_ENTRY_CASH):
    capital_per_strategy = starting_cash / max(1, n_strategies)
    feasible = capital_per_strategy >= min_entry_cash
    return {
        "starting_cash": round(starting_cash, 2),
        "n_strategies": int(n_strategies),
        "capital_per_strategy": round(capital_per_strategy, 2),
        "min_entry_cash": round(min_entry_cash, 2),
        "feasible": feasible,
        "reason": ""
        if feasible
        else (
            f"capital_per_strategy ${capital_per_strategy:.2f} is below "
            f"min_entry_cash ${min_entry_cash:.2f}; equal-split results would be a no-trade artifact"
        ),
    }


def main():
    client = CoinbaseAdvancedClient()

    now = int(time.time())
    start = now - WINDOW_DAYS * 86400

    print(f"=" * 70, flush=True)
    print(f"BACKTEST RECONCILIATION — {WINDOW_DAYS}d, ${STARTING_CASH}, 40bps flat", flush=True)
    print(f"=" * 70, flush=True)

    # Fetch or load candles
    coin_candles = {}
    candle_data = {}
    use_snapshot = os.environ.get(USE_SNAPSHOT_ENV, "").strip() == "1"
    if use_snapshot and CANDLE_PATH.exists():
        print(f"\nLoading candles from snapshot: {CANDLE_PATH}", flush=True)
        coin_candles = load_candle_snapshot(CANDLE_PATH)
        for coin in COINS:
            candles = coin_candles.get(coin) or []
            candle_data[coin] = {
                "count": len(candles),
                "first_ts": int(candles[0]["start"]) if candles else None,
                "last_ts": int(candles[-1]["start"]) if candles else None,
            }
            print(f"  {coin}: {len(candles)} candles (ts {candle_data[coin]['first_ts']}-{candle_data[coin]['last_ts']})", flush=True)
    else:
        print(f"\nFetching candles...", flush=True)
        for coin in COINS:
            candles = fetch_and_save_candles(client, coin, start, now)
            coin_candles[coin] = candles
            candle_data[coin] = {
                "count": len(candles),
                "first_ts": int(candles[0]["start"]) if candles else None,
                "last_ts": int(candles[-1]["start"]) if candles else None,
            }
            print(f"  {coin}: {len(candles)} candles (ts {candle_data[coin]['first_ts']}-{candle_data[coin]['last_ts']})", flush=True)
        save_candle_snapshot(CANDLE_PATH, coin_candles)
        print(f"  Snapshot saved: {CANDLE_PATH}", flush=True)

    # Save candle metadata for reproducibility
    meta_path = ROOT / "reports" / "reconciliation_candle_metadata.json"
    with open(meta_path, "w") as f:
        json.dump({"run_at": utc_now_iso(), "coins": candle_data}, f, indent=2)

    # Define ALL strategies with EXACT parameters
    strategies = [
        {"name": "RSI MR (RAVE)", "coin": "RAVE-USD", "type": "rsi_mr",
         "params": {"rsi_period": 3, "os_thresh": 30, "tp_pct": 0.25, "max_hold": 48}},
        {"name": "Momentum (RAVE)", "coin": "RAVE-USD", "type": "momentum",
         "params": {"lookback": 10, "tp_pct": 0.10, "sl_pct": 0.10, "max_hold": 48}},
        {"name": "BB Reversion (IOTX)", "coin": "IOTX-USD", "type": "bb_reversion",
         "params": {"rsi_thresh": 30, "bb_period": 20, "sl_pct": 0.05, "max_hold": 24}},
        {"name": "Momentum (BAL)", "coin": "BAL-USD", "type": "momentum",
         "params": {"lookback": 50, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 48}},
        {"name": "Momentum (BLUR)", "coin": "BLUR-USD", "type": "momentum",
         "params": {"lookback": 25, "tp_pct": 0.12, "sl_pct": 0.07, "max_hold": 48}},
    ]

    # Run each strategy individually with full $48
    print(f"\n{'='*70}", flush=True)
    print("INDIVIDUAL STRATEGY RESULTS (each gets full $48)", flush=True)
    print(f"{'='*70}", flush=True)

    individual_results = {}
    for strat in strategies:
        candles = coin_candles[strat["coin"]]
        result = run_single_strategy_backtest(candles, strat["type"], strat["params"], STARTING_CASH)
        individual_results[strat["name"]] = {**result, "strategy": strat}

        print(f"  {strat['name']:<22} | PnL=${result['net_pnl']:>8.2f} | WR={result['win_rate']:>5.1f}% | "
              f"Trades={result['closes']:>3} | Signals={result['signals']:>4} | "
              f"DD={result['max_dd']:>5.1f}% | Sharpe={result['sharpe']:>6.2f}", flush=True)

    # Run with equal split ($48 / N each)
    print(f"\n{'='*70}", flush=True)
    print("PORTFOLIO RESULTS (equal split: $9.60 per strategy)", flush=True)
    print(f"{'='*70}", flush=True)

    portfolio_plan = equal_split_feasibility(STARTING_CASH, len(strategies))
    portfolio_results = {}
    if portfolio_plan["feasible"]:
        for strat in strategies:
            candles = coin_candles[strat["coin"]]
            result = run_single_strategy_backtest(
                candles,
                strat["type"],
                strat["params"],
                portfolio_plan["capital_per_strategy"],
            )
            portfolio_results[strat["name"]] = result

            print(f"  {strat['name']:<22} | PnL=${result['net_pnl']:>8.2f} | WR={result['win_rate']:>5.1f}% | "
                  f"Trades={result['closes']:>3} | DD={result['max_dd']:>5.1f}%", flush=True)
        total_portfolio_pnl = sum(r["net_pnl"] for r in portfolio_results.values())
        print(f"\n  Portfolio Total: ${total_portfolio_pnl:.2f}", flush=True)
    else:
        print(f"  Infeasible equal split: {portfolio_plan['reason']}", flush=True)
        for strat in strategies:
            portfolio_results[strat["name"]] = {
                "feasible": False,
                "net_pnl": None,
                "win_rate": None,
                "closes": 0,
                "max_dd": None,
                "reason": portfolio_plan["reason"],
            }
        total_portfolio_pnl = None

    # Comparison with other agents' findings
    print(f"\n{'='*70}", flush=True)
    print("RECONCILIATION: My Results vs Other Agents", flush=True)
    print(f"{'='*70}", flush=True)

    comparison = {
        "RSI MR (RAVE)": {"mine": individual_results["RSI MR (RAVE)"]["net_pnl"],
                           "qwen_tb": "$270-344", "gap": "TBD"},
        "Momentum (RAVE)": {"mine": individual_results["Momentum (RAVE)"]["net_pnl"],
                             "qwen_tb": "$642", "gap": "TBD"},
        "BB Reversion (IOTX)": {"mine": individual_results["BB Reversion (IOTX)"]["net_pnl"],
                                 "qwen_tb": "$44", "gap": "TBD"},
        "Momentum (BAL)": {"mine": individual_results["Momentum (BAL)"]["net_pnl"],
                            "qwen_tb": "$76", "gap": "TBD"},
        "Momentum (BLUR)": {"mine": individual_results["Momentum (BLUR)"]["net_pnl"],
                             "qwen_tb": "$77", "gap": "TBD"},
    }

    for name, data in comparison.items():
        print(f"  {name:<22} | Mine: ${data['mine']:>8.2f} | Theirs: {data['qwen_tb']:>10} | "
              f"Gap: ${data['mine'] - float(data['qwen_tb'].replace('$','').split('-')[0]):>+8.2f}", flush=True)

    # Save report
    report = {
        "run_at": utc_now_iso(),
        "window_days": WINDOW_DAYS,
        "starting_cash": STARTING_CASH,
        "fee_rate": FEE_RATE,
        "fee_note": "Flat 40bps, no slippage, entry on candle OPEN",
        "min_entry_cash": MIN_ENTRY_CASH,
        "used_snapshot": use_snapshot and CANDLE_PATH.exists(),
        "candle_snapshot_path": str(CANDLE_PATH),
        "candle_metadata": candle_data,
        "individual_full_cash": {
            name: {
                "net_pnl": data["net_pnl"],
                "return_pct": data["return_pct"],
                "win_rate": data["win_rate"],
                "closes": data["closes"],
                "signals": data["signals"],
                "max_dd": data["max_dd"],
                "sharpe": data["sharpe"],
            }
            for name, data in individual_results.items()
        },
        "portfolio_equal_split_plan": portfolio_plan,
        "portfolio_equal_split": {
            name: {
                "net_pnl": data["net_pnl"],
                "win_rate": data["win_rate"],
                "closes": data["closes"],
                "max_dd": data["max_dd"],
                "feasible": data.get("feasible", True),
                "reason": data.get("reason", ""),
            }
            for name, data in portfolio_results.items()
        },
        "portfolio_total": total_portfolio_pnl,
        "comparison_with_other_agents": comparison,
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print(f"Candle metadata saved: {meta_path}", flush=True)
    print("\nDone.", flush=True)


if __name__ == "__main__":
    main()
