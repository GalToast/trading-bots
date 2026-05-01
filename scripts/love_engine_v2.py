#!/usr/bin/env python3
"""
Love Engine — Consensus Extension for the Hypergrowth Router

Extends the existing router board with consensus gating:
- For coins with dual-live allowed (RAVE, NOM), only enter when BOTH strategies agree
- Uses the existing edge_registry for strategy definitions
- Uses the existing correlation matrix for diversification weights
- Builds ON the router, not from scratch

Usage:
    python scripts/love_engine_v2.py --coin RAVE-USD
    python scripts/love_engine_v2.py --coin NOM-USD --consensus 2
    python scripts/love_engine_v2.py --all  # Run all dual-live coins
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from coinbase_advanced_client import CoinbaseAdvancedClient
    HAS_CLIENT = True
except ImportError:
    HAS_CLIENT = False

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

# Load existing router board
ROUTER_BOARD_PATH = REPORTS / "coinbase_spot_hypergrowth_router_board.json"
EDGE_REGISTRY_PATH = REPORTS / "edge_registry.json"
MULTI_STRATEGY_PATH = REPORTS / "multi_strategy_portfolio_results.json"

# Strategy configs (from edge_registry)
STRATEGY_CONFIGS = {
    "fibonacci": {"lookback": 20, "fib_level": 0.618, "min_breakout_pct": 0.02, "tp_pct": 0.08, "sl_pct": 0.03, "max_hold": 24},
    "momentum": {"lookback": 20, "threshold": 0.005, "tp_pct": 0.15, "sl_pct": 0.0, "max_hold": 48},
    "supertrend": {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 48},
    "rsi_mr": {"rsi_period": 3, "rsi_oversold": 30, "tp_pct": 0.05, "sl_pct": 0.03, "max_hold": 24},
    "time_decay": {"decay_period": 15, "tp_pct": 0.15, "sl_pct": 0.0, "max_hold": 48},
    "ma_atr": {"ma_period": 20, "atr_period": 14, "atr_mult": 1.5, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 24},
}


def load_json(path):
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def compute_atr(candles, period=14):
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(-period, 0):
        c = candles[i]
        p = candles[i - 1]
        h, l, pc = float(c["high"]), float(c["low"]), float(p["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def fibonacci_signal(candles, params):
    lookback = params.get("lookback", 20)
    if len(candles) < lookback + 5:
        return False, 0.0
    recent = candles[-lookback:]
    highs = [float(c["high"]) for c in recent]
    lows = [float(c["low"]) for c in recent]
    period_high = max(highs)
    period_low = min(lows)
    fib_level = params.get("fib_level", 0.618)
    fib_price = period_high - (period_high - period_low) * fib_level
    current = float(candles[-1]["close"])
    breakout_pct = (current - fib_price) / fib_price if fib_price > 0 else 0
    min_breakout = params.get("min_breakout_pct", 0.02)
    if breakout_pct < min_breakout:
        return False, breakout_pct
    if len(candles) >= 20:
        volumes = [float(c.get("volume", 0)) for c in candles[-20:]]
        avg_vol = sum(volumes) / len(volumes) if volumes else 0
        cur_vol = float(candles[-1].get("volume", 0))
        if avg_vol > 0 and cur_vol < avg_vol * 0.8:
            return False, breakout_pct
    if len(candles) >= 3:
        green = sum(1 for c in candles[-3:] if float(c["close"]) > float(c["open"]))
        if green < 2:
            return False, breakout_pct
    return True, breakout_pct


def momentum_signal(candles, params):
    lookback = params.get("lookback", 20)
    if len(candles) < lookback + 1:
        return False, 0.0
    closes = [float(c["close"]) for c in candles]
    recent_high = max(closes[-(lookback+1):-1])
    current = closes[-1]
    breakout_pct = (current - recent_high) / recent_high if recent_high > 0 else 0
    threshold = params.get("threshold", 0.005)
    if breakout_pct < threshold:
        return False, breakout_pct
    if len(candles) >= 20:
        volumes = [float(c.get("volume", 0)) for c in candles[-20:]]
        avg_vol = sum(volumes) / len(volumes) if volumes else 0
        cur_vol = float(candles[-1].get("volume", 0))
        if avg_vol > 0 and cur_vol < avg_vol * 0.5:
            return False, breakout_pct
    return True, breakout_pct


def supertrend_signal(candles, params):
    atr_period = params.get("atr_period", 10)
    atr_mult = params.get("atr_mult", 3.0)
    if len(candles) < atr_period + 1:
        return False, 0.0
    atr = compute_atr(candles, atr_period)
    hl2 = (float(candles[-1]["high"]) + float(candles[-1]["low"])) / 2
    st = hl2 - atr_mult * atr
    current = float(candles[-1]["close"])
    strength = (current - st) / st if st > 0 else 0
    return current > st, strength


def rsi_mr_signal(candles, params):
    rsi_period = params.get("rsi_period", 3)
    oversold = params.get("rsi_oversold", 30)
    if len(candles) < rsi_period + 2:
        return False, 0.0
    closes = [float(c["close"]) for c in candles]
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))
    if len(gains) < rsi_period:
        return False, 0.0
    avg_gain = sum(gains[-rsi_period:]) / rsi_period
    avg_loss = sum(losses[-rsi_period:]) / rsi_period
    if avg_loss == 0:
        rsi = 100
    else:
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
    strength = (oversold - rsi) / oversold if rsi < oversold else 0
    return rsi <= oversold, strength


SIGNAL_FUNCTIONS = {
    "fibonacci": fibonacci_signal,
    "momentum": momentum_signal,
    "supertrend": supertrend_signal,
    "rsi_mr": rsi_mr_signal,
}


def fetch_candles(coin, days=30):
    if not HAS_CLIENT:
        return []
    client = CoinbaseAdvancedClient()
    end = int(time.time())
    start = end - days * 86400
    chunk_sec = 300 * 5 * 60
    all_candles = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(coin, start=cs, end=ce, granularity="FIVE_MINUTE")
            cands = resp.get("candles", [])
            all_candles.extend(cands)
            cs = ce
            if not cands:
                break
            time.sleep(0.15)
        except Exception as e:
            print(f"  WARN fetch error for {coin} at {cs}: {e}", flush=True)
            cs += chunk_sec
    all_candles.sort(key=lambda c: int(c.get("start", c.get("time", 0))))
    return all_candles


def run_single_strategy_backtest(candles, strategy_name, params):
    """Simple backtest for a single strategy."""
    fn = SIGNAL_FUNCTIONS.get(strategy_name)
    if fn is None:
        return None

    cash = 100.0
    position = None
    trades = []
    wins = 0
    losses = 0
    signals_fired = 0

    for i in range(len(candles)):
        window = candles[:i+1]
        if len(window) < 30:
            continue

        # Exit check
        if position is not None:
            c = candles[i]
            high = float(c["high"])
            low = float(c["low"])
            close = float(c["close"])
            position["hold"] += 1

            if high >= position["tp"]:
                net = (position["tp"] - position["ep"]) / position["ep"] * 100
                trades.append(net)
                wins += 1
                cash += position["q"] + (position["tp"] - position["ep"]) * position["units"] - position["entry_fee"] - position["tp"] * position["units"] * 0.004
                position = None
            elif position["sl"] > 0 and low <= position["sl"]:
                net = (position["sl"] - position["ep"]) / position["ep"] * 100
                trades.append(net)
                losses += 1
                cash += position["q"] + (position["sl"] - position["ep"]) * position["units"] - position["entry_fee"] - position["sl"] * position["units"] * 0.004
                position = None
            elif position["hold"] >= position["max_hold"]:
                net = (close - position["ep"]) / position["ep"] * 100
                trades.append(net)
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                cash += position["q"] + (close - position["ep"]) * position["units"] - position["entry_fee"] - close * position["units"] * 0.004
                position = None

        # Entry check
        if position is None:
            fired, strength = fn(window, params)
            if fired:
                signals_fired += 1
                entry_price = float(candles[i]["close"])
                deploy = cash * 0.9
                entry_fee = deploy * 0.004
                units = (deploy - entry_fee) / entry_price
                tp = entry_price * (1 + params.get("tp_pct", 0.08))
                sl = entry_price * (1 - params.get("sl_pct", 0.03)) if params.get("sl_pct", 0.03) > 0 else 0
                cash -= deploy
                position = {
                    "ep": entry_price,
                    "q": deploy,
                    "units": units,
                    "tp": tp,
                    "sl": sl,
                    "hold": 0,
                    "entry_fee": entry_fee,
                    "max_hold": params.get("max_hold", 48),
                }

    total_pnl = sum(trades)
    wr = wins / len(trades) * 100 if trades else 0

    return {
        "strategy": strategy_name,
        "signals": signals_fired,
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr, 1),
        "total_pnl_pct": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / len(trades), 3) if trades else 0,
        "final_cash": round(cash, 2),
    }


def run_consensus_backtest(candles, strategy_pairs, consensus_threshold=2):
    """Backtest requiring consensus between strategies."""
    cash = 100.0
    position = None
    trades = []
    wins = 0
    losses = 0
    signals_fired = 0
    consensus_signals = 0

    for i in range(len(candles)):
        window = candles[:i+1]
        if len(window) < 30:
            continue

        # Exit check (same as single)
        if position is not None:
            c = candles[i]
            high = float(c["high"])
            low = float(c["low"])
            close = float(c["close"])
            position["hold"] += 1

            if high >= position["tp"]:
                net = (position["tp"] - position["ep"]) / position["ep"] * 100
                trades.append(net)
                wins += 1
                cash += position["q"] + (position["tp"] - position["ep"]) * position["units"] - position["entry_fee"] - position["tp"] * position["units"] * 0.004
                position = None
            elif position["sl"] > 0 and low <= position["sl"]:
                net = (position["sl"] - position["ep"]) / position["ep"] * 100
                trades.append(net)
                losses += 1
                cash += position["q"] + (position["sl"] - position["ep"]) * position["units"] - position["entry_fee"] - position["sl"] * position["units"] * 0.004
                position = None
            elif position["hold"] >= position["max_hold"]:
                net = (close - position["ep"]) / position["ep"] * 100
                trades.append(net)
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                cash += position["q"] + (close - position["ep"]) * position["units"] - position["entry_fee"] - close * position["units"] * 0.004
                position = None

        # Consensus entry check
        if position is None:
            votes = 0
            best_params = None
            best_strategy = None

            for strat_name, params in strategy_pairs:
                fn = SIGNAL_FUNCTIONS.get(strat_name)
                if fn is None:
                    continue
                fired, strength = fn(window, params)
                if fired:
                    votes += 1
                    if best_params is None or strength > 0:
                        best_params = params
                        best_strategy = strat_name

            signals_fired += votes

            if votes >= consensus_threshold and best_params is not None:
                consensus_signals += 1
                entry_price = float(candles[i]["close"])
                deploy = cash * 0.9
                entry_fee = deploy * 0.004
                units = (deploy - entry_fee) / entry_price
                tp = entry_price * (1 + best_params.get("tp_pct", 0.08))
                sl = entry_price * (1 - best_params.get("sl_pct", 0.03)) if best_params.get("sl_pct", 0.03) > 0 else 0
                cash -= deploy
                position = {
                    "ep": entry_price,
                    "q": deploy,
                    "units": units,
                    "tp": tp,
                    "sl": sl,
                    "hold": 0,
                    "entry_fee": entry_fee,
                    "max_hold": best_params.get("max_hold", 48),
                    "strategies_voted": votes,
                    "primary_strategy": best_strategy,
                }

    total_pnl = sum(trades)
    wr = wins / len(trades) * 100 if trades else 0

    return {
        "consensus_threshold": consensus_threshold,
        "total_signals_seen": signals_fired,
        "consensus_signals_fired": consensus_signals,
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr, 1),
        "total_pnl_pct": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / len(trades), 3) if trades else 0,
        "final_cash": round(cash, 2),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Love Engine v2 — Consensus Extension for Hypergrowth Router")
    parser.add_argument("--coin", default=None)
    parser.add_argument("--consensus", type=int, default=2)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--all", action="store_true", help="Run all dual-live coins from router board")
    args = parser.parse_args()

    print("=" * 80)
    print("  LOVE ENGINE v2 — Consensus Extension for Hypergrowth Router")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 80)

    # Load existing router board
    router_board = load_json(ROUTER_BOARD_PATH)
    edge_registry = load_json(EDGE_REGISTRY_PATH)
    multi_strategy = load_json(MULTI_STRATEGY_PATH)

    if router_board is None:
        print(f"  ERROR: Router board not found at {ROUTER_BOARD_PATH}", flush=True)
        return

    print(f"\n  Loaded router board: {len(router_board.get('rows', []))} coins", flush=True)
    print(f"  Loaded edge registry: {len((edge_registry or {}).get('strategies', {}))} strategies", flush=True)
    print(f"  Loaded multi-strategy portfolio: {len((multi_strategy or {}).get('equal_allocation', {}).get('individual', []))} strategies", flush=True)

    # Identify dual-live coins from router board
    dual_live_coins = []
    for row in router_board.get("rows", []):
        if row.get("same_coin_stack_policy") and "dual" in row.get("same_coin_stack_policy", "").lower():
            dual_live_coins.append({
                "coin": row["coin"],
                "primary": row.get("primary_lane", ""),
                "primary_family": row.get("primary_family", ""),
                "secondary": row.get("secondary_lane", ""),
                "secondary_family": row.get("secondary_family", ""),
                "max_live_lanes": row.get("max_live_lanes", 1),
            })

    print(f"\n  Dual-live coins from router: {len(dual_live_coins)}", flush=True)
    for c in dual_live_coins:
        print(f"    {c['coin']}: {c['primary_family']} + {c['secondary_family']}", flush=True)

    # Determine which coins to run
    if args.all:
        coins_to_run = dual_live_coins
    elif args.coin:
        coin_row = next((c for c in dual_live_coins if c["coin"] == args.coin), None)
        if coin_row is None:
            print(f"\n  ERROR: {args.coin} not in dual-live list", flush=True)
            return
        coins_to_run = [coin_row]
    else:
        print(f"\n  No coin specified. Use --coin NOM-USD or --all", flush=True)
        return

    results = []

    for coin_info in coins_to_run:
        coin = coin_info["coin"]
        print(f"\n{'─' * 80}", flush=True)
        print(f"  {coin}: {coin_info['primary_family']} + {coin_info['secondary_family']}", flush=True)
        print(f"{'─' * 80}", flush=True)

        # Fetch candles
        print(f"  Fetching {args.days}d candles...", flush=True)
        candles = fetch_candles(coin, args.days)
        if not candles:
            print(f"  ERROR: No candles for {coin}", flush=True)
            continue
        print(f"  Got {len(candles)} candles", flush=True)

        # Get strategy params from registry
        strategies_to_test = []
        for family in [coin_info["primary_family"], coin_info["secondary_family"]]:
            if family in STRATEGY_CONFIGS:
                strategies_to_test.append((family, STRATEGY_CONFIGS[family]))
            elif "momentum" in family:
                strategies_to_test.append(("momentum", STRATEGY_CONFIGS["momentum"]))
            elif "fibonacci" in family or "breakout" in family:
                strategies_to_test.append(("fibonacci", STRATEGY_CONFIGS["fibonacci"]))
            elif "rsi" in family:
                strategies_to_test.append(("rsi_mr", STRATEGY_CONFIGS["rsi_mr"]))
            elif "supertrend" in family:
                strategies_to_test.append(("supertrend", STRATEGY_CONFIGS["supertrend"]))

        if len(strategies_to_test) < 2:
            print(f"  WARNING: Only {len(strategies_to_test)} strategies available, skipping consensus test", flush=True)
            continue

        # Run single-strategy backtests
        print(f"\n  Single-strategy backtests:", flush=True)
        single_results = {}
        for strat_name, params in strategies_to_test:
            result = run_single_strategy_backtest(candles, strat_name, params)
            if result:
                single_results[strat_name] = result
                marker = "✅" if result["total_pnl_pct"] > 0 else "❌"
                print(f"    {marker} {strat_name:<12s}: {result['trades']:4d} trades, "
                      f"WR={result['win_rate']:5.1f}%, PnL={result['total_pnl_pct']:+7.2f}%", flush=True)

        # Run consensus backtest
        print(f"\n  Consensus backtest ({args.consensus}+ strategies must agree):", flush=True)
        consensus_result = run_consensus_backtest(candles, strategies_to_test, args.consensus)
        marker = "✅" if consensus_result["total_pnl_pct"] > 0 else "❌"
        print(f"    {marker} CONSENSUS: {consensus_result['trades']:4d} trades, "
              f"WR={consensus_result['win_rate']:5.1f}%, PnL={consensus_result['total_pnl_pct']:+7.2f}%", flush=True)
        print(f"    Signals seen: {consensus_result['total_signals_seen']}, Consensus fired: {consensus_result['consensus_signals_fired']}", flush=True)

        # Compare
        best_single = max(single_results.values(), key=lambda x: x["total_pnl_pct"]) if single_results else None
        if best_single:
            delta_pnl = consensus_result["total_pnl_pct"] - best_single["total_pnl_pct"]
            delta_trades = consensus_result["trades"] - best_single["trades"]
            delta_wr = consensus_result["win_rate"] - best_single["win_rate"]
            print(f"\n  Consensus vs Best Single ({best_single['strategy']}):", flush=True)
            print(f"    ΔPnL: {delta_pnl:+.2f}%  ΔTrades: {delta_trades:+d}  ΔWR: {delta_wr:+.1f}pp", flush=True)

        results.append({
            "coin": coin,
            "strategies": [s[0] for s in strategies_to_test],
            "single_results": single_results,
            "consensus_result": consensus_result,
            "router_info": coin_info,
        })

    # Save results
    output = REPORTS / f"love_engine_v2_consensus.json"
    with open(output, "w") as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "consensus_threshold": args.consensus,
            "results": results,
        }, f, indent=2)
    print(f"\n  Results saved: {output}", flush=True)


if __name__ == "__main__":
    main()
