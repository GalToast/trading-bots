#!/usr/bin/env python3
"""
Runner-Modeled Backtest — Matches isolated runner behavior exactly.
===================================================================

The strategy_library backtest uses $10 min, 100% deploy — which doesn't match
the isolated runner ($2 min, 90% deploy). This script models the runner EXACTLY.

Answers: What does isolated REALLY earn at $48 bankroll ($5.33/coin)?
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

COINS = [
    "RAVE-USD", "NOM-USD", "GHST-USD", "TRU-USD", "SUP-USD",
    "A8-USD", "BAL-USD", "CFG-USD", "IOTX-USD",
]

COIN_STRATEGIES = {
    "RAVE-USD": "theil_sen", "NOM-USD": "momentum", "GHST-USD": "momentum",
    "TRU-USD": "momentum", "SUP-USD": "momentum", "A8-USD": "momentum",
    "BAL-USD": "momentum", "CFG-USD": "momentum", "IOTX-USD": "momentum",
}

COIN_PARAMS = {
    "RAVE-USD": {"reg_period": 20, "tp_pct": 0.10, "sl_pct": 0.00, "max_hold": 48},
    "NOM-USD": {"lookback": 20, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 48},
    "GHST-USD": {"lookback": 50, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 96},
    "TRU-USD": {"lookback": 10, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 48},
    "SUP-USD": {"lookback": 10, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 48},
    "A8-USD": {"lookback": 10, "tp_pct": 0.15, "sl_pct": 0.00, "max_hold": 48},
    "BAL-USD": {"lookback": 50, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 96},
    "CFG-USD": {"lookback": 50, "tp_pct": 0.15, "sl_pct": 0.00, "max_hold": 48},
    "IOTX-USD": {"lookback": 10, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 48},
}

# Runner constants (from multi_coin_isolated_runner.py)
FEE_RATE = 0.004
MIN_CASH_PER_POSITION = 2.0
DEPLOY_FRACTION = 0.90
SESSION_DEAD_HOURS = {0, 6, 12, 19}

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "reports" / "runner_modeled_backtest_48.json"


def momentum_signal(candle, candle_history):
    lookback = 15  # default for signal detection
    if len(candle_history) < lookback + 2:
        return False
    current_high = float(candle["high"])
    highest = max(float(c["high"]) for c in candle_history[-(lookback + 1):-1])
    return current_high > highest


def theil_sen_signal(closes, reg_period=20):
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


def get_fee_rate(volume):
    if volume >= 50000:
        return 0.0015
    elif volume >= 10000:
        return 0.0025
    return FEE_RATE


def simulate_isolated_runner(coin, candles, strategy, params, starting_cash):
    """Simulate the isolated runner EXACTLY for one coin."""
    from datetime import datetime as dt, timezone

    cash = starting_cash
    position = None
    history = []
    candle_history = []
    signals = 0
    closes_count = 0
    wins = 0
    losses = 0
    total_fees = 0
    total_volume = 0

    for i, candle in enumerate(candles):
        ts = int(candle.get("time", candle.get("start", 0)))
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        open_price = float(candle["open"])

        if open_price <= 0 or close <= 0 or high <= 0 or low <= 0:
            continue

        history.append(close)
        candle_history.append(candle)
        if len(history) > 500:
            history = history[-500:]
            candle_history = candle_history[-500:]

        hour = dt.fromtimestamp(ts, tz=timezone.utc).hour
        session_open = hour not in SESSION_DEAD_HOURS

        fee_rate = get_fee_rate(total_volume)

        # EXIT
        if position:
            position["hold"] += 1
            exit_price = None
            exit_reason = None

            if high >= position["tp"]:
                exit_price = position["tp"]
                exit_reason = "tp"
            elif position["sl"] > 0 and low <= position["sl"]:
                exit_price = position["sl"]
                exit_reason = "stop"
            elif position["hold"] >= position["max_hold"]:
                exit_price = close
                exit_reason = "timeout"

            if exit_price is not None:
                units = position["units"]
                gross = (exit_price - position["ep"]) * units
                entry_fee = position["entry_fee"]
                exit_fee = exit_price * units * fee_rate
                net = gross - entry_fee - exit_fee

                cash += position["deploy"] + net
                closes_count += 1
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                total_volume += position["deploy"] + (exit_price * units)
                total_fees += entry_fee + exit_fee
                position = None

        # ENTRY
        if position is None and cash >= MIN_CASH_PER_POSITION and session_open:
            signal_fired = False
            lookback = params.get("lookback", 15)

            if strategy == "momentum":
                if len(candle_history) > lookback + 1:
                    recent_high = max(float(c["high"]) for c in candle_history[-(lookback + 1):-1])
                    if high > recent_high:
                        signal_fired = True
            elif strategy == "theil_sen":
                reg_period = params.get("reg_period", 20)
                if len(history) >= reg_period + 5:
                    recent = history[-reg_period:]
                    n = len(recent)
                    x = list(range(n))
                    y = recent
                    slopes = []
                    for j in range(0, n - 1, 2):
                        if x[j + 1] - x[j] != 0:
                            slopes.append((y[j + 1] - y[j]) / (x[j + 1] - x[j]))
                    if slopes:
                        med_slope = sorted(slopes)[len(slopes) // 2]
                        med_y = sorted(y)[len(y) // 2]
                        med_x = sorted(x)[len(x) // 2]
                        intercept = med_y - med_slope * med_x
                        predicted = med_slope * n + intercept
                        actual = y[-1]
                        deviation = (predicted - actual) / actual
                        if deviation < -0.02:
                            signal_fired = True

            if signal_fired:
                signals += 1
                deploy = cash * DEPLOY_FRACTION
                entry_price = open_price
                if entry_price <= 0:
                    continue

                entry_fee = deploy * fee_rate
                units = (deploy - entry_fee) / entry_price
                tp = entry_price * (1 + params["tp_pct"])
                sl = entry_price * (1 - params["sl_pct"]) if params["sl_pct"] > 0 else 0

                cash -= deploy
                position = {
                    "ep": entry_price,
                    "deploy": deploy,
                    "units": units,
                    "tp": tp,
                    "sl": sl,
                    "hold": 0,
                    "entry_fee": entry_fee,
                    "max_hold": params["max_hold"],
                }

    # Close remaining position
    if position:
        last_close = float(candles[-1]["close"])
        fee_rate = get_fee_rate(total_volume)
        gross = (last_close - position["ep"]) * position["units"]
        exit_fee = last_close * position["units"] * fee_rate
        net = gross - position["entry_fee"] - exit_fee
        cash += position["deploy"] + net
        closes_count += 1
        total_volume += position["deploy"] + (last_close * position["units"])
        total_fees += position["entry_fee"] + exit_fee
        if net > 0:
            wins += 1
        else:
            losses += 1

    equity = cash
    pnl = equity - starting_cash
    wr = wins / max(1, closes_count) * 100 if closes_count > 0 else 0

    return {
        "starting_cash": starting_cash,
        "ending_cash": round(cash, 4),
        "net_pnl": round(pnl, 4),
        "win_rate": round(wr, 1),
        "trades": closes_count,
        "wins": wins,
        "losses": losses,
        "signals": signals,
        "total_fees": round(total_fees, 4),
        "total_volume": round(total_volume, 2),
    }


def main():
    print("=" * 80)
    print("  RUNNER-MODELED BACKTEST — Matches isolated runner EXACTLY")
    print("=" * 80)
    print(f"MIN_CASH_PER_POSITION: ${MIN_CASH_PER_POSITION}")
    print(f"DEPLOY_FRACTION: {DEPLOY_FRACTION*100:.0f}%")
    print(f"FEE_RATE: {FEE_RATE*100:.1f}%")
    print(f"Session dead hours: {SESSION_DEAD_HOURS}")
    print()

    # ========== $48 total ($5.33/coin) ==========
    print(f"{'='*80}", flush=True)
    print(f"  SCENARIO: Production bankroll — $48 total, $5.33/coin", flush=True)
    print(f"{'='*80}", flush=True)

    total_pnl_48 = 0
    results_48 = {}

    for coin_name in COINS:
        try:
            coin_file = f"reports/candle_cache/{coin_name.replace('-', '_')}_FIVE_MINUTE_30d.json"
            data = json.loads(open(coin_file).read())
            candles = data["candles"]

            strategy = COIN_STRATEGIES[coin_name]
            params = COIN_PARAMS[coin_name]
            starting = 48.0 / 9

            result = simulate_isolated_runner(coin_name, candles, strategy, params, starting)
            total_pnl_48 += result["net_pnl"]
            results_48[coin_name] = result

            status = "✅" if result["net_pnl"] > 0 else "❌"
            print(f"  {status} {coin_name}: ${result['net_pnl']:+.2f} (WR={result['win_rate']:.1f}%, "
                  f"{result['trades']} trades, {result['signals']} signals, "
                  f"fees=${result['total_fees']:.2f})", flush=True)
        except Exception as e:
            print(f"  ❌ {coin_name}: ERROR — {e}", flush=True)

    print(f"\n  TOTAL at $48: ${total_pnl_48:+.2f}", flush=True)

    # ========== $900 total ($100/coin) ==========
    print(f"\n{'='*80}", flush=True)
    print(f"  SCENARIO: Full capital — $900 total, $100/coin", flush=True)
    print(f"{'='*80}", flush=True)

    total_pnl_900 = 0
    results_900 = {}

    for coin_name in COINS:
        try:
            coin_file = f"reports/candle_cache/{coin_name.replace('-', '_')}_FIVE_MINUTE_30d.json"
            data = json.loads(open(coin_file).read())
            candles = data["candles"]

            strategy = COIN_STRATEGIES[coin_name]
            params = COIN_PARAMS[coin_name]
            starting = 100.0

            result = simulate_isolated_runner(coin_name, candles, strategy, params, starting)
            total_pnl_900 += result["net_pnl"]
            results_900[coin_name] = result

            status = "✅" if result["net_pnl"] > 0 else "❌"
            print(f"  {status} {coin_name}: ${result['net_pnl']:+.2f} (WR={result['win_rate']:.1f}%, "
                  f"{result['trades']} trades, {result['signals']} signals, "
                  f"fees=${result['total_fees']:.2f})", flush=True)
        except Exception as e:
            print(f"  ❌ {coin_name}: ERROR — {e}", flush=True)

    print(f"\n  TOTAL at $900: ${total_pnl_900:+.2f}", flush=True)

    # ========== COMPARISON ==========
    print(f"\n{'='*80}", flush=True)
    print(f"  COMPARISON: Runner-modeled results", flush=True)
    print(f"{'='*80}", flush=True)

    print(f"\n  {'Coin':<14} | {'$48 PnL':>10} | {'$48 WR':>7} | {'$900 PnL':>10} | {'$900 WR':>7}", flush=True)
    print(f"  {'-'*14}-+-{'-'*10}-+-{'-'*7}-+-{'-'*10}-+-{'-'*7}", flush=True)

    for coin_name in COINS:
        r48 = results_48.get(coin_name, {})
        r900 = results_900.get(coin_name, {})
        print(f"  {coin_name:<14} | ${r48.get('net_pnl', 0):>+9.2f} | {r48.get('win_rate', 0):>6.1f}% | "
              f"${r900.get('net_pnl', 0):>+9.2f} | {r900.get('win_rate', 0):>6.1f}%", flush=True)

    print(f"  {'-'*14}-+-{'-'*10}-+-{'-'*7}-+-{'-'*10}-+-{'-'*7}", flush=True)
    print(f"  {'TOTAL':<14} | ${total_pnl_48:>+9.2f} |           | ${total_pnl_900:>+9.2f} |", flush=True)

    # Save report
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "runner_params": {
            "min_cash": MIN_CASH_PER_POSITION,
            "deploy_fraction": DEPLOY_FRACTION,
            "fee_rate": FEE_RATE,
            "session_dead_hours": sorted(SESSION_DEAD_HOURS),
        },
        "scenarios": {
            "production_48": {
                "total_bankroll": 48.0,
                "per_coin": round(48.0 / 9, 4),
                "total_pnl": round(total_pnl_48, 2),
                "coins": {c: {k: round(v, 2) if isinstance(v, float) else v for k, v in r.items()} for c, r in results_48.items()},
            },
            "full_capital_900": {
                "total_bankroll": 900.0,
                "per_coin": 100.0,
                "total_pnl": round(total_pnl_900, 2),
                "coins": {c: {k: round(v, 2) if isinstance(v, float) else v for k, v in r.items()} for c, r in results_900.items()},
            },
        },
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
