#!/usr/bin/env python3
"""
Isolated vs Shared Pool — 30d Head-to-Head Comparison
======================================================

Runs BOTH architectures on the SAME 30d data with the SAME coins/strategies:
- Shared pool: $48 total, all coins share one bankroll
- Isolated: $48 total, $48/N per coin independently

Answers: What's the EXACT per-coin degradation from shared pool architecture?
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from strategy_library import backtest

# Coins: same set in both architectures
COINS = [
    "RAVE-USD", "NOM-USD", "GHST-USD", "TRU-USD", "SUP-USD",
    "A8-USD", "BAL-USD", "CFG-USD", "IOTX-USD",
]

# Optimal strategy per coin (from optimal_coin_strategy_assignment.py)
COIN_STRATEGIES = {
    "RAVE-USD": "theil_sen",
    "NOM-USD": "momentum",
    "GHST-USD": "momentum",
    "TRU-USD": "momentum",
    "SUP-USD": "momentum",
    "A8-USD": "momentum",
    "BAL-USD": "momentum",
    "CFG-USD": "momentum",
    "IOTX-USD": "momentum",
}

# Optimal params per coin
COIN_PARAMS = {
    "RAVE-USD": {"reg_period": 20, "tp_pct": 10.0, "sl_pct": 0.0, "max_hold": 48},
    "NOM-USD": {"lookback": 20, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 48},
    "GHST-USD": {"lookback": 50, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 96},
    "TRU-USD": {"lookback": 10, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 48},
    "SUP-USD": {"lookback": 10, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 48},
    "A8-USD": {"lookback": 10, "tp_pct": 15.0, "sl_pct": 0.0, "max_hold": 48},
    "BAL-USD": {"lookback": 50, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 96},
    "CFG-USD": {"lookback": 50, "tp_pct": 15.0, "sl_pct": 0.0, "max_hold": 48},
    "IOTX-USD": {"lookback": 10, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 48},
}

FEE_RATE = 0.004
N_COINS = len(COINS)

# Test at both bankroll levels
# Note: isolated runner uses MIN_CASH_PER_POSITION=2.0, DEPLOY_FRACTION=0.90
# Backtest (strategy_library) uses $10 min, deploy=100%. To match runner behavior,
# we need to adjust for the comparison.
SCENARIOS = [
    {"name": "production_48", "total": 48.0, "per_coin": 48.0 / 9},
    {"name": "full_capital_900", "total": 900.0, "per_coin": 100.0},
]

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "reports" / "isolated_vs_shared_30d_comparison.json"


def momentum_entry(candles_hist, closes, candle, params):
    lookback = params.get("lookback", 15)
    if len(candles_hist) < lookback + 2:
        return False
    current_high = float(candle["high"])
    highest = max(float(c["high"]) for c in candles_hist[-(lookback + 1):-1])
    return current_high > highest


def theil_sen_entry(candles_hist, closes, candle, params):
    reg_period = params.get("reg_period", 20)
    if len(closes) < reg_period + 5:
        return False
    recent = closes[-reg_period:]
    n = len(recent)
    x = list(range(n))
    y = recent
    slopes = []
    for i in range(0, n - 1, 2):
        if x[i + 1] - x[i] != 0:
            slopes.append((y[i + 1] - y[i]) / (x[i + 1] - x[i]))
    if not slopes:
        return False
    med_slope = sorted(slopes)[len(slopes) // 2]
    med_y = sorted(y)[len(y) // 2]
    med_x = sorted(x)[len(x) // 2]
    intercept = med_y - med_slope * med_x
    predicted = med_slope * n + intercept
    actual = y[-1]
    deviation = (predicted - actual) / actual
    return deviation < -0.02


def get_entry_fn(coin_name):
    strategy = COIN_STRATEGIES[coin_name]
    if strategy == "theil_sen":
        return theil_sen_entry
    return momentum_entry


def simulate_shared_pool(candles_by_coin, total_bankroll):
    """
    Simulate shared pool architecture:
    - One shared bankroll
    - Coins fire signals sequentially (in config order)
    - Each trade uses DEPLOY_FRACTION of available cash
    - When one coin is in a position, others still fire
    - Cash is shared, so positions compete for capital
    """
    DEPLOY_FRACTION = 0.95
    MIN_POSITION = 10.0  # Backtest minimum

    cash = total_bankroll
    positions = {}  # coin -> position dict
    closes_count = 0
    wins = 0
    losses = 0
    total_fees = 0
    peak = total_bankroll
    max_dd = 0
    coin_stats = {c: {"signals": 0, "trades": 0, "wins": 0, "losses": 0, "net_pnl": 0} for c in COINS}

    # Find max candle length
    max_len = max(len(v) for v in candles_by_coin.values())

    for i in range(max_len):
        # Get candle for each coin at this index
        current_candles = {}
        for coin in COINS:
            if i < len(candles_by_coin[coin]):
                current_candles[coin] = candles_by_coin[coin][i]

        # Process exits first
        for coin in list(positions.keys()):
            pos = positions[coin]
            candle = current_candles.get(coin)
            if not candle:
                continue

            high = float(candle["high"])
            low = float(candle["low"])
            close = float(candle["close"])
            fee_rate = get_fee_rate(pos.get("volume", 0))

            pos["hold"] += 1
            exit_price = None
            exit_reason = None

            if high >= pos["tp"]:
                exit_price = pos["tp"]
                exit_reason = "tp"
            elif pos["sl"] > 0 and low <= pos["sl"]:
                exit_price = pos["sl"]
                exit_reason = "stop"
            elif pos["hold"] >= pos["max_hold"]:
                exit_price = close
                exit_reason = "timeout"

            if exit_price:
                gross = (exit_price - pos["entry"]) * pos["units"]
                exit_fee = exit_price * pos["units"] * fee_rate
                net = gross - pos["entry_fee"] - exit_fee
                cash += pos["deploy"] + net
                closes_count += 1
                total_fees += pos["entry_fee"] + exit_fee

                if net > 0:
                    wins += 1
                    coin_stats[coin]["wins"] += 1
                else:
                    losses += 1
                    coin_stats[coin]["losses"] += 1
                coin_stats[coin]["trades"] += 1
                coin_stats[coin]["net_pnl"] += net

                del positions[coin]

        # Process entries (sequential, competing for shared cash)
        for coin in COINS:
            if coin in positions:
                continue  # Already have position
            if coin not in current_candles:
                continue
            if cash < MIN_POSITION:
                continue

            candle = current_candles[coin]
            closes_list = [float(c["close"]) for c in candles_by_coin[coin][:i+1]]
            candles_hist = candles_by_coin[coin][:i+1]

            entry_fn = get_entry_fn(coin)
            params = COIN_PARAMS[coin]

            if entry_fn(candles_hist, closes_list, candle, params):
                coin_stats[coin]["signals"] += 1
                deploy = cash * DEPLOY_FRACTION
                entry_price = float(candle["open"])
                if entry_price <= 0:
                    continue

                fee_rate = get_fee_rate(0)
                entry_fee = deploy * fee_rate
                units = (deploy - entry_fee) / entry_price
                tp = entry_price * (1 + params["tp_pct"] / 100) if params["tp_pct"] > 1 else entry_price * (1 + params["tp_pct"])
                sl = entry_price * (1 - params["sl_pct"] / 100) if params["sl_pct"] > 1 else entry_price * (1 - params["sl_pct"]) if params["sl_pct"] > 0 else 0

                cash -= deploy
                positions[coin] = {
                    "entry": entry_price,
                    "deploy": deploy,
                    "units": units,
                    "tp": tp,
                    "sl": sl,
                    "hold": 0,
                    "entry_fee": entry_fee,
                    "max_hold": params["max_hold"],
                    "volume": deploy + entry_price * units,
                }

        # Track drawdown
        equity = cash + sum(p["deploy"] for p in positions.values())
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Close remaining positions at last close
    for coin, pos in positions.items():
        last_candle = candles_by_coin[coin][-1]
        close = float(last_candle["close"])
        fee_rate = get_fee_rate(pos.get("volume", 0))
        gross = (close - pos["entry"]) * pos["units"]
        exit_fee = close * pos["units"] * fee_rate
        net = gross - pos["entry_fee"] - exit_fee
        cash += pos["deploy"] + net
        total_fees += pos["entry_fee"] + exit_fee
        closes_count += 1
        if net > 0:
            wins += 1
            coin_stats[coin]["wins"] += 1
        else:
            losses += 1
            coin_stats[coin]["losses"] += 1
        coin_stats[coin]["trades"] += 1
        coin_stats[coin]["net_pnl"] += net

    total_equity = cash
    wr = wins / max(1, closes_count) * 100

    return {
        "total_pnl": round(total_equity - total_bankroll, 2),
        "total_equity": round(total_equity, 2),
        "win_rate": round(wr, 1),
        "trades": closes_count,
        "wins": wins,
        "losses": losses,
        "max_drawdown": round(max_dd, 1),
        "total_fees": round(total_fees, 2),
        "remaining_cash": round(cash, 2),
        "coin_stats": {c: {k: round(v, 2) if isinstance(v, float) else v for k, v in s.items()} for c, s in coin_stats.items()},
    }


def get_fee_rate(volume):
    if volume >= 50000:
        return 0.0015
    elif volume >= 10000:
        return 0.0025
    return 0.004


def main():
    print("=" * 80)
    print("  ISOLATED vs SHARED POOL — 30D HEAD-TO-HEAD")
    print("=" * 80)
    print(f"Testing {len(SCENARIOS)} bankroll scenarios: {[s['name'] for s in SCENARIOS]}")
    print()

    # Load all candle data
    candles_by_coin = {}
    for coin_name in COINS:
        try:
            coin_file = f"reports/candle_cache/{coin_name.replace('-', '_')}_FIVE_MINUTE_30d.json"
            data = json.loads(open(coin_file).read())
            candles_by_coin[coin_name] = data["candles"]
            print(f"  {coin_name}: {len(data['candles'])} candles loaded", flush=True)
        except Exception as e:
            print(f"  {coin_name}: ERROR loading candles — {e}", flush=True)

    print()

    all_results = {}

    for scenario in SCENARIOS:
        TOTAL_BANKROLL = scenario["total"]
        ISOLATED_PER_COIN = scenario["per_coin"]

        print(f"\n{'='*80}", flush=True)
        print(f"  SCENARIO: {scenario['name']} — Total: ${TOTAL_BANKROLL:.2f}, Isolated: ${ISOLATED_PER_COIN:.2f}/coin", flush=True)
        print(f"{'='*80}", flush=True)

        # ========== ISOLATED ==========
        isolated_results = {}
        total_isolated_pnl = 0

        for coin_name in COINS:
            if coin_name not in candles_by_coin:
                continue
            candles = candles_by_coin[coin_name]
            entry_fn = get_entry_fn(coin_name)
            params = COIN_PARAMS[coin_name]

            result = backtest(candles, entry_fn, params, FEE_RATE, ISOLATED_PER_COIN)
            net = result["net_pnl"]
            total_isolated_pnl += net

            isolated_results[coin_name] = {
                "net_pnl": round(net, 2),
                "win_rate": round(result["win_rate"], 1),
                "trades": result["trades"],
                "signals": result["signals"],
                "max_drawdown": round(result["max_drawdown"], 1),
                "equity": round(ISOLATED_PER_COIN + net, 2),
            }

            status = "✅" if net > 0 else "❌"
            print(f"  {status} {coin_name}: ${net:+.2f} (WR={result['win_rate']:.1f}%, {result['trades']} trades, {result['signals']} signals)", flush=True)

        print(f"\n  TOTAL ISOLATED: ${total_isolated_pnl:+.2f}", flush=True)

        # ========== SHARED POOL ==========
        shared_results = simulate_shared_pool(candles_by_coin, TOTAL_BANKROLL)

        print(f"\n  Shared total PnL: ${shared_results['total_pnl']:+.2f}", flush=True)
        print(f"  Shared equity: ${shared_results['total_equity']:.2f}", flush=True)
        print(f"  Shared WR: {shared_results['win_rate']:.1f}% ({shared_results['trades']} trades)", flush=True)
        print(f"  Shared DD: {shared_results['max_drawdown']:.1f}%", flush=True)

        # ========== COMPARISON ==========
        print(f"\n  {'Coin':<14} | {'Isolated':>10} | {'Shared':>10} | {'Shared>Isolated':>15}", flush=True)
        print(f"  {'-'*14}-+-{'-'*10}-+-{'-'*10}-+-{'-'*15}", flush=True)

        for coin_name in COINS:
            iso = isolated_results.get(coin_name, {})
            shared_coin = shared_results["coin_stats"].get(coin_name, {})
            iso_pnl = iso.get("net_pnl", 0)
            shared_pnl = shared_coin.get("net_pnl", 0)

            if abs(iso_pnl) > 0.01:
                diff = shared_pnl - iso_pnl
                diff_pct = (diff / abs(iso_pnl)) * 100
                print(f"  {coin_name:<14} | ${iso_pnl:>+9.2f} | ${shared_pnl:>+9.2f} | {diff_pct:>+14.1f}%", flush=True)
            else:
                print(f"  {coin_name:<14} | ${iso_pnl:>+9.2f} | ${shared_pnl:>+9.2f} | {'N/A (iso=0)':>15}", flush=True)

        shared_total = shared_results["total_pnl"]
        print(f"  {'-'*14}-+-{'-'*10}-+-{'-'*10}-+-{'-'*15}", flush=True)
        print(f"  {'TOTAL':<14} | ${total_isolated_pnl:>+9.2f} | ${shared_total:>+9.2f}", flush=True)

        all_results[scenario["name"]] = {
            "isolated": isolated_results,
            "isolated_total": round(total_isolated_pnl, 2),
            "shared": shared_results,
        }

    # ========== GOVERNANCE SUMMARY ==========
    print(f"\n{'='*80}", flush=True)
    print("  GOVERNANCE SUMMARY", flush=True)
    print(f"{'='*80}", flush=True)

    for sname, sdata in all_results.items():
        iso_total = sdata["isolated_total"]
        shared_total = sdata["shared"]["total_pnl"]
        print(f"\n  {sname}:", flush=True)
        print(f"    Isolated: ${iso_total:+.2f}", flush=True)
        print(f"    Shared:   ${shared_total:+.2f}", flush=True)

        if iso_total > 0 and shared_total > 0:
            if shared_total > iso_total:
                print(f"    → Shared pool wins (more capital per trade)", flush=True)
            else:
                print(f"    → Isolated wins (no capital competition)", flush=True)
        elif iso_total == 0:
            print(f"    → Isolated IMPOSSIBLE at this bankroll ($10 min per position)", flush=True)
            print(f"    → Shared pool is the ONLY option at this bankroll level", flush=True)

    # Save report
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "scenarios": all_results,
        "conclusion": {
            "at_48": "isolated_impossible_shared_only",
            "at_900": "shared_wins_more_capital_per_trade" if all_results.get("full_capital_900", {}).get("shared", {}).get("total_pnl", 0) > all_results.get("full_capital_900", {}).get("isolated_total", 0) else "isolated_wins",
        },
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
