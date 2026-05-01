#!/usr/bin/env python3
"""
Standardized Benchmark Harness — Lane 5: Benchmark

Tests ANY strategy across ANY coin with IDENTICAL fee/execution assumptions.
No more comparing apples to oranges.

Usage:
    python benchmark_harness.py --strategy rsi_mr --coins RAVE-USD BAL-USD IOTX-USD
    python benchmark_harness.py --strategy strict_warp --coins IOTX-USD --fill_model realistic
"""
import argparse
import json
import os
import sys
import time
import statistics
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from benchmark_shared import BUILTIN_FILL_MODELS, FEE_TIERS, RAVE_RSI_MR_BASELINE_PARAMS

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "reports" / "benchmark_harness_results.json"
DEFAULT_EMPIRICAL_PATH = ROOT / "reports" / "empirical_execution_snapshot.json"

# Strategy configurations
STRATEGIES = {
    "rsi_mr": {
        "name": "RSI Mean Reversion",
        "params": dict(RAVE_RSI_MR_BASELINE_PARAMS),
    },
    "rsi_mr_strict": {
        "name": "RSI MR (Strict)",
        "params": {**RAVE_RSI_MR_BASELINE_PARAMS, "sl_pct": 5},
    },
    "rsi_mr_wide": {
        "name": "RSI MR Wide (RSI<45)",
        "params": {**RAVE_RSI_MR_BASELINE_PARAMS, "os_thresh": 45},
    },
    "rsi_mr_optimized": {
        "name": "RSI MR Optimized (RSI4/40%)",
        "params": {"rsi_period": 4, "os_thresh": 30, "tp_pct": 40, "max_hold": 48, "sl_pct": 0},
    },
    "rsi_mr_conservative": {
        "name": "RSI MR Conservative (RSI4/15%)",
        "params": {"rsi_period": 4, "os_thresh": 30, "tp_pct": 15, "max_hold": 48, "sl_pct": 0},
    },
}
FILL_MODELS = {key: dict(value) for key, value in BUILTIN_FILL_MODELS.items()}

# Load empirical fill models from snapshot if available
EMPIRICAL_SNAPSHOT = ROOT / "reports" / "empirical_execution_snapshot.json"
if EMPIRICAL_SNAPSHOT.exists():
    try:
        emp_data = json.loads(EMPIRICAL_SNAPSHOT.read_text(encoding="utf-8"))
        for model_name, model_data in emp_data.get("fill_models", {}).items():
            resolved = model_data.get("resolved_for_benchmark", {})
            if resolved:
                FILL_MODELS[model_name] = {
                    "fill_prob": resolved.get("fill_prob", 0.75),
                    "entry_slippage_bps": resolved.get("entry_slippage_bps", 50),
                    "exit_slippage_bps": resolved.get("exit_slippage_bps", 50),
                }
    except Exception:
        pass

DEFAULT_COINS = ["RAVE-USD", "BAL-USD", "IOTX-USD", "BLUR-USD"]
BTC = "BTC-USD"


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def resolve_fill_model(fill_model_name: str, *, empirical_fill_model: str | None, empirical_path: Path) -> tuple[str, dict]:
    if empirical_fill_model:
        payload = load_json(empirical_path) or {}
        fill_models = payload.get("fill_models") if isinstance(payload.get("fill_models"), dict) else {}
        model = fill_models.get(empirical_fill_model) if isinstance(fill_models, dict) else None
        if not isinstance(model, dict):
            raise ValueError(f"Empirical fill model '{empirical_fill_model}' not found in {empirical_path}")
        resolved = model.get("resolved_for_benchmark") if isinstance(model.get("resolved_for_benchmark"), dict) else {}
        fill_prob = float(resolved.get("fill_prob") or 0.0)
        entry_slippage_bps = float(resolved.get("entry_slippage_bps") or 0.0)
        exit_slippage_bps = float(resolved.get("exit_slippage_bps") or 0.0)
        return empirical_fill_model, {
            "fill_prob": fill_prob,
            "entry_slippage_bps": entry_slippage_bps,
            "exit_slippage_bps": exit_slippage_bps,
            "execution_provenance": str(resolved.get("execution_provenance") or ""),
            "forward_event_count": int(resolved.get("forward_event_count") or 0),
            "total_events": int(resolved.get("total_events") or 0),
            "warning": str(resolved.get("warning") or ""),
        }
    return fill_model_name, dict(FILL_MODELS[fill_model_name])


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
        except:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c


def compute_rsi(closes, period=3):
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


def run_benchmark(candles, btc_candles, strategy_params, fee_bps, fill_model,
                  starting_cash=48.0):
    """
    Run a single benchmark test with standardized assumptions.

    Returns dict with net, closes, win_rate, max_dd, etc.
    """
    import random
    random.seed(42)

    cash = starting_cash
    pos = None
    closes_count = 0
    wins = 0
    losses = 0
    total_volume = 0.0
    total_fees = 0.0
    history = []
    peak = starting_cash
    max_dd = 0.0
    signals = 0
    filled = 0
    session_filtered = 0

    fill_prob = fill_model["fill_prob"]
    entry_slip = fill_model["entry_slippage_bps"] / 10000.0
    exit_slip = fill_model["exit_slippage_bps"] / 10000.0
    fee_rate = fee_bps  # Already a decimal (0.004 for 40bps)

    for i in range(len(candles)):
        c = candles[i]
        ts = int(c.get("start", c.get("time", 0)))
        close = float(c["close"])
        high = float(c["high"])
        low = float(c["low"])
        candle_open = float(c["open"])

        history.append(close)
        if len(history) > 500:
            history = history[-500:]

        # Session gate
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_open = hour not in {0, 6, 12, 19}

        # EXIT
        if pos:
            pos["hold"] += 1
            exit_price = None
            exit_reason = None

            if high >= pos["tp"]:
                exit_price = pos["tp"]
                exit_reason = "tp"
            elif pos["sl"] > 0 and low <= pos["sl"]:
                exit_price = pos["sl"]
                exit_reason = "sl"
            elif pos["hold"] >= pos["max_hold"]:
                exit_price = close
                exit_reason = "timeout"

            if exit_price is not None:
                # Apply exit slippage
                actual_exit = exit_price * (1 - exit_slip)

                units = pos["units"]
                gross = (actual_exit - pos["ep"]) * units
                entry_fee = pos["entry_fee"]
                exit_fee = actual_exit * units * fee_rate
                net = gross - entry_fee - exit_fee

                cash += pos["q"] + net
                closes_count += 1
                total_volume += pos["q"] + (actual_exit * units)
                total_fees += entry_fee + exit_fee

                if net > 0:
                    wins += 1
                else:
                    losses += 1

                peak = max(peak, cash)
                dd = (peak - cash) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)

                pos = None

        # ENTRY
        if pos is None and cash >= 10.0 and session_open:
            rsi_period = strategy_params.get("rsi_period", 3)
            if len(history) >= rsi_period + 2:
                rsi_val = compute_rsi(history[:-1], rsi_period)

                if rsi_val <= strategy_params["os_thresh"]:
                    signals += 1

                    # Fill probability check
                    if random.random() > fill_prob:
                        session_filtered += 1  # Not filled (counts as filtered for simplicity)
                        continue

                    # Apply entry slippage
                    actual_entry = candle_open * (1 + entry_slip)

                    deploy = cash
                    entry_fee = deploy * fee_rate
                    units = (deploy - entry_fee) / actual_entry
                    tp = actual_entry * (1 + strategy_params["tp_pct"] / 100.0)
                    sl = actual_entry * (1 - strategy_params["sl_pct"] / 100.0) if strategy_params["sl_pct"] > 0 else 0

                    cash -= deploy
                    pos = {
                        "ep": actual_entry,
                        "q": deploy,
                        "hold": 0,
                        "tp": tp,
                        "sl": sl,
                        "units": units,
                        "entry_fee": entry_fee,
                        "max_hold": strategy_params.get("max_hold", 48),
                    }
                    filled += 1

    # Close remaining position
    if pos:
        cash += pos["q"]  # Return position value at cost (conservative)

    net = cash - starting_cash
    wr = wins / max(1, closes_count) * 100

    return {
        "net": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 1),
        "closes": closes_count,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr, 1),
        "total_volume": round(total_volume, 2),
        "total_fees": round(total_fees, 2),
        "max_dd": round(max_dd * 100, 1),
        "signals": signals,
        "filled": filled,
        "session_filtered": session_filtered,
        "fill_rate": round(filled / max(1, signals) * 100, 1),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", default="rsi_mr", choices=list(STRATEGIES.keys()))
    parser.add_argument("--coins", nargs="+", default=DEFAULT_COINS)
    parser.add_argument("--window", default="30d", choices=["7d", "30d"])
    parser.add_argument("--fill-model", default="perfect", choices=list(FILL_MODELS.keys()))
    parser.add_argument("--empirical-fill-model", default=None)
    parser.add_argument("--empirical-path", default=str(DEFAULT_EMPIRICAL_PATH))
    parser.add_argument("--starting-cash", type=float, default=48.0)
    args = parser.parse_args()

    client = CoinbaseAdvancedClient()
    now = int(time.time())
    days = 7 if args.window == "7d" else 30
    start = now - days * 24 * 3600

    resolved_fill_model_name, fill_model = resolve_fill_model(
        args.fill_model,
        empirical_fill_model=args.empirical_fill_model,
        empirical_path=Path(args.empirical_path),
    )

    print(f"Benchmark Harness - Strategy: {args.strategy}, Window: {args.window}, Fill: {resolved_fill_model_name}", flush=True)
    print(f"Coins: {args.coins}", flush=True)

    # Fetch BTC
    btc = fetch_candles(client, BTC, start, now)
    btc_close = btc[-1]["close"] if btc else 0

    results = []
    strategy_params = STRATEGIES[args.strategy]["params"]
    for coin in args.coins:
        print(f"\n--- {coin} ---", flush=True)
        try:
            candles = fetch_candles(client, coin, start, now)
            if len(candles) < 100:
                print(f"  Insufficient data ({len(candles)} candles)", flush=True)
                results.append({"coin": coin, "error": f"insufficient_data", "candles": len(candles)})
                continue

            # Test across all fee tiers
            coin_results = {}
            for tier_name, fee_bps in FEE_TIERS.items():
                r = run_benchmark(candles, btc, strategy_params, fee_bps, fill_model, args.starting_cash)
                coin_results[tier_name] = r
                print(f"  {tier_name}: net=${r['net']:.2f} closes={r['closes']} wr={r['win_rate']}% dd={r['max_dd']}%", flush=True)

            results.append({"coin": coin, "candles": len(candles), "results": coin_results})
        except Exception as e:
            print(f"  ERROR: {e}", flush=True)
            results.append({"coin": coin, "error": str(e)})

    # Summary table
    print(f"\n{'=' * 100}", flush=True)
    print(f"SUMMARY - {STRATEGIES[args.strategy]['name']} ({resolved_fill_model_name} fill, {args.window})", flush=True)
    print(f"{'=' * 100}", flush=True)

    for coin_data in results:
        if "error" in coin_data:
            print(f"  {coin_data['coin']}: {coin_data['error']}", flush=True)
            continue

        print(f"\n  {coin_data['coin']} ({coin_data['candles']} candles):", flush=True)
        for tier_name, r in coin_data["results"].items():
            status = "OK" if r["net"] > 0 else "BAD"
            print(f"    {status} {tier_name}: ${r['net']:>8.2f} | {r['closes']:>4} trades | "
                  f"{r['win_rate']:>5.1f}% WR | {r['max_dd']:>5.1f}% DD | "
                  f"${r['total_fees']:.2f} fees", flush=True)

    # Save
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "strategy": args.strategy,
        "window": args.window,
        "fill_model": resolved_fill_model_name,
        "fill_model_params": fill_model,
        "empirical_path": args.empirical_path if args.empirical_fill_model else None,
        "starting_cash": args.starting_cash,
        "results": results,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(f"\nResults saved to {OUTPUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
