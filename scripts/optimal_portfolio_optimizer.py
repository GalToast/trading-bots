#!/usr/bin/env python3
"""
Optimal Portfolio Optimizer — Best strategy per coin, maximizing total PnL.
==========================================================================

Takes ALL 6 validated strategies, tests each on ALL available coins,
finds the optimal assignment (one strategy per coin) that maximizes total PnL.

Accounts for:
- Bankroll constraints ($48 total = $5.33/coin, or $900 total = $100/coin)
- Runner constants ($2 min, 90% deploy, session gate)
- No duplicate strategies on same coin (avoids interference)

Output: Exact config for the isolated runner.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# Runner constants
FEE_RATE = 0.004
MIN_CASH = 2.0
DEPLOY_FRACTION = 0.90
SESSION_DEAD = {0, 6, 12, 19}
CANONICAL_MIN_CASH = 10.0
CANONICAL_DEPLOY_FRACTION = 0.95

# All coins we have data for
ALL_COINS = [
    "RAVE-USD", "NOM-USD", "GHST-USD", "TRU-USD", "SUP-USD",
    "A8-USD", "BAL-USD", "CFG-USD", "IOTX-USD",
]

# All validated strategies with their 30d params
STRATEGIES = {
    "fibonacci_breakout": {
        "params": {"lookback": 20, "tp_pct": 8.0, "sl_pct": 3.0, "max_hold": 24},
    },
    "supertrend": {
        "params": {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 48},
    },
    "momentum": {
        "params": {"lookback": 15, "tp_pct": 10.0, "sl_pct": 0.0, "max_hold": 48},
    },
    "time_decay_signal": {
        "params": {"decay_period": 15, "tp_pct": 15.0, "sl_pct": 0.0, "max_hold": 48},
    },
    "ma_atr": {
        "params": {"ma_period": 20, "atr_period": 14, "atr_mult": 1.5, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 24},
    },
}

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "reports" / "optimal_portfolio_optimizer.json"


def native_assumptions_payload() -> dict:
    return {
        "fee_rate": FEE_RATE,
        "min_cash": MIN_CASH,
        "deploy_fraction": DEPLOY_FRACTION,
        "session_gate": True,
        "session_dead_hours_utc": sorted(SESSION_DEAD),
        "entry": "candle_open",
        "fills": "100%",
        "slippage_bps": 0.0,
    }


def report_semantics_payload() -> dict:
    return {
        "surface_kind": "native_gated_simulator",
        "comparable_to_canonical_without_reconciliation": False,
        "intended_use": "strategy-to-coin assignment ranking inside the native isolated-runner assumptions",
        "warning": "Do not compare these totals directly to canonical portfolio truth without using the reconciliation and drift-attribution artifacts.",
    }


def comparison_artifacts_payload() -> dict:
    return {
        "canonical_reconciliation_report": "reports/optimal_portfolio_optimizer_reconciliation.json",
        "drift_attribution_report": "reports/optimal_portfolio_drift_attribution.json",
        "drift_board": "reports/adaptive_optimizer_reconciliation_board.json",
    }


def canonical_assumptions_payload() -> dict:
    return {
        "fee_rate": FEE_RATE,
        "min_cash": CANONICAL_MIN_CASH,
        "deploy_fraction": CANONICAL_DEPLOY_FRACTION,
        "session_gate": False,
        "session_dead_hours_utc": sorted(SESSION_DEAD),
        "entry": "candle_open",
        "fills": "100%",
        "slippage_bps": 0.0,
    }


def canonical_reference_payload(scenarios: list[dict], drift_attribution: dict) -> dict:
    return {
        "available": True,
        "status": "reconciled_divergent",
        "source_mode": "native_inline_replay",
        "assumptions": canonical_assumptions_payload(),
        "scenarios": scenarios,
        "drift_attribution": drift_attribution,
    }


def _fibonacci_entry(candles_hist, closes, candle, params):
    lookback = params.get("lookback", 20)
    if len(candles_hist) < lookback:
        return False
    highs = [float(c["high"]) for c in candles_hist[-lookback:]]
    lows = [float(c["low"]) for c in candles_hist[-lookback:]]
    swing_high = max(highs)
    swing_low = min(lows)
    fib_618 = swing_high - 0.618 * (swing_high - swing_low)
    current_price = float(candle["close"])
    return current_price > fib_618 and len(closes) > 1 and closes[-1] > closes[-2]


def _supertrend_entry(candles_hist, closes, candle, params):
    atr_period = params.get("atr_period", 10)
    atr_mult = params.get("atr_mult", 3.0)
    if len(candles_hist) < atr_period + 10:
        return False
    trs = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        c_prev = float(candles_hist[i - 1]["close"])
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    if len(trs) < atr_period:
        return False
    atr = sum(trs[-atr_period:]) / atr_period
    hl2 = (float(candle["high"]) + float(candle["low"])) / 2
    supertrend = hl2 - atr_mult * atr
    return float(candle["close"]) > supertrend and len(closes) > 1 and closes[-1] > closes[-2]


def _momentum_entry(candles_hist, closes, candle, params):
    lookback = params.get("lookback", 15)
    if len(candles_hist) < lookback + 2:
        return False
    current_high = float(candle["high"])
    highest = max(float(c["high"]) for c in candles_hist[-(lookback + 1):-1])
    return current_high > highest


def _time_decay_entry(candles_hist, closes, candle, params):
    decay_period = params.get("decay_period", 15)
    if len(candles_hist) < 30:
        return False
    recent_returns = []
    for i in range(max(1, len(closes) - decay_period - 1), len(closes) - 1):
        if closes[i] > 0 and closes[i - 1] > 0:
            recent_returns.append(abs(closes[i] / closes[i - 1] - 1))
    if not recent_returns:
        return False
    avg_return = sum(recent_returns) / len(recent_returns)
    current_return = abs(closes[-1] / closes[-2] - 1) if len(closes) > 1 and closes[-2] > 0 else 0
    if avg_return > 0 and current_return > avg_return * 2.0:
        return True
    if len(recent_returns) >= 3:
        recent_avg = sum(recent_returns[-3:]) / 3
        if recent_avg > avg_return * 1.5 and current_return > avg_return * 1.2:
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    return False


def _ma_atr_entry(candles_hist, closes, candle, params):
    ma_period = params.get("ma_period", 20)
    atr_period = params.get("atr_period", 14)
    atr_mult = params.get("atr_mult", 1.5)
    if len(candles_hist) < 50 or len(closes) < ma_period + 5:
        return False
    ma = sum(closes[-ma_period:]) / ma_period
    ma_prev = sum(closes[-ma_period - 1:-1]) / ma_period
    current_price = closes[-1]
    ma_rising = ma > ma_prev
    price_above = current_price > ma
    trs = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        c_prev = float(candles_hist[i - 1]["close"])
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    if len(trs) < atr_period + 1:
        return False
    current_atr = sum(trs[-atr_period:]) / atr_period
    prev_atr = sum(trs[-atr_period * 2:-atr_period]) / atr_period if len(trs) >= atr_period * 2 else current_atr
    atr_expanding = current_atr > prev_atr * atr_mult if prev_atr > 0 else False
    return price_above and ma_rising and atr_expanding


ENTRY_FUNCS = {
    "fibonacci_breakout": _fibonacci_entry,
    "supertrend": _supertrend_entry,
    "momentum": _momentum_entry,
    "time_decay_signal": _time_decay_entry,
    "ma_atr": _ma_atr_entry,
}


def simulate(
    candles,
    entry_fn,
    params,
    starting_cash,
    *,
    min_cash=MIN_CASH,
    deploy_fraction=DEPLOY_FRACTION,
    session_gate=True,
):
    from datetime import datetime as dt, timezone as tz

    cash = starting_cash
    position = None
    history = []
    candle_history = []
    signals = 0
    trades = 0
    wins = 0
    losses = 0
    total_fees = 0

    tp_pct = params.get("tp_pct", 10.0) / 100.0
    sl_pct = params.get("sl_pct", 0.0) / 100.0
    max_hold = params.get("max_hold", 48)

    for candle in candles:
        ts = int(candle.get("time", candle.get("start", 0)))
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        open_price = float(candle["open"])

        if open_price <= 0 or close <= 0:
            continue

        history.append(close)
        candle_history.append(candle)
        if len(history) > 500:
            history = history[-500:]
            candle_history = candle_history[-500:]

        hour = dt.fromtimestamp(ts, tz=tz.utc).hour
        session_open = hour not in SESSION_DEAD if session_gate else True

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
                exit_reason = "sl"
            elif position["hold"] >= max_hold:
                exit_price = close
                exit_reason = "timeout"

            if exit_price is not None:
                units = position["units"]
                gross = (exit_price - position["ep"]) * units
                net = gross - position["entry_fee"] - (exit_price * units * FEE_RATE)
                cash += position["deploy"] + net
                trades += 1
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                total_fees += position["entry_fee"] + exit_price * units * FEE_RATE
                position = None

        # ENTRY
        if position is None and cash >= min_cash and session_open:
            if entry_fn(candle_history, history, candle, params):
                signals += 1
                deploy = cash * deploy_fraction
                entry_price = open_price
                if entry_price <= 0:
                    continue

                entry_fee = deploy * FEE_RATE
                units = (deploy - entry_fee) / entry_price
                tp = entry_price * (1 + tp_pct)
                sl = entry_price * (1 - sl_pct) if sl_pct > 0 else 0

                cash -= deploy
                position = {
                    "ep": entry_price, "deploy": deploy, "units": units,
                    "tp": tp, "sl": sl, "hold": 0, "entry_fee": entry_fee,
                }

    # Close remaining
    if position:
        last_close = float(candles[-1]["close"])
        gross = (last_close - position["ep"]) * position["units"]
        net = gross - position["entry_fee"] - (last_close * position["units"] * FEE_RATE)
        cash += position["deploy"] + net
        trades += 1
        total_fees += position["entry_fee"] + last_close * position["units"] * FEE_RATE
        if net > 0:
            wins += 1
        else:
            losses += 1

    wr = wins / max(1, trades) * 100 if trades > 0 else 0
    pnl = cash - starting_cash

    return {
        "net_pnl": round(pnl, 2),
        "win_rate": round(wr, 1),
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "signals": signals,
        "total_fees": round(total_fees, 2),
    }


def build_best_assignment(scenario_results):
    optimal = {}
    total_pnl = 0.0

    for coin in ALL_COINS:
        best_strat = None
        best_pnl = -999999.0

        for strat_name in STRATEGIES:
            r = scenario_results.get(strat_name, {}).get(coin, {})
            pnl = float(r.get("net_pnl", 0.0) or 0.0)
            if pnl > best_pnl:
                best_pnl = pnl
                best_strat = strat_name

        optimal[coin] = {"strategy": best_strat, "pnl": round(best_pnl, 2)}
        total_pnl += best_pnl

    return optimal, round(total_pnl, 2)


def replay_assignment(candles_by_coin, assignment, starting_cash, *, assumptions):
    canonical_total = 0.0
    feasible_count = 0
    per_coin = {}
    for coin, row in assignment.items():
        if float(starting_cash) < float(assumptions["min_cash"]):
            replay = {"net_pnl": 0.0, "trades": 0, "win_rate": None}
            feasible = False
        else:
            strategy_name = str(row["strategy"])
            replay = simulate(
                candles_by_coin[coin],
                ENTRY_FUNCS[strategy_name],
                dict(STRATEGIES[strategy_name]["params"]),
                starting_cash,
                min_cash=float(assumptions["min_cash"]),
                deploy_fraction=float(assumptions["deploy_fraction"]),
                session_gate=bool(assumptions["session_gate"]),
            )
            feasible = True
        if feasible:
            feasible_count += 1
        canonical_total += float(replay["net_pnl"])
        per_coin[coin] = {
            "strategy": row["strategy"],
            "projected_net_pnl": float(row["pnl"]),
            "canonical_net_pnl": float(replay["net_pnl"]),
            "delta_vs_projected": round(float(replay["net_pnl"]) - float(row["pnl"]), 2),
            "feasible": feasible,
            "canonical_trades": int(replay["trades"]),
            "canonical_win_rate": replay["win_rate"],
        }
    return {
        "feasible_count": feasible_count,
        "coin_count": len(assignment),
        "canonical_total_pnl": round(canonical_total, 4),
        "per_coin": per_coin,
    }


def build_drift_attribution(candles_by_coin, assignment, starting_cash):
    variants = [
        {"variant_id": "optimizer_native", "deploy_fraction": DEPLOY_FRACTION, "min_cash": MIN_CASH, "session_gate": True},
        {"variant_id": "session_gate_off", "deploy_fraction": DEPLOY_FRACTION, "min_cash": MIN_CASH, "session_gate": False},
        {"variant_id": "deploy_95", "deploy_fraction": CANONICAL_DEPLOY_FRACTION, "min_cash": MIN_CASH, "session_gate": True},
        {"variant_id": "min_cash_10", "deploy_fraction": DEPLOY_FRACTION, "min_cash": CANONICAL_MIN_CASH, "session_gate": True},
        {"variant_id": "canonical", "deploy_fraction": CANONICAL_DEPLOY_FRACTION, "min_cash": CANONICAL_MIN_CASH, "session_gate": False},
    ]
    totals = {}
    for variant in variants:
        total = 0.0
        for coin, row in assignment.items():
            strategy_name = str(row["strategy"])
            replay = simulate(
                candles_by_coin[coin],
                ENTRY_FUNCS[strategy_name],
                dict(STRATEGIES[strategy_name]["params"]),
                starting_cash,
                min_cash=float(variant["min_cash"]),
                deploy_fraction=float(variant["deploy_fraction"]),
                session_gate=bool(variant["session_gate"]),
            )
            total += float(replay["net_pnl"])
        totals[str(variant["variant_id"])] = round(total, 4)

    session_effect = round(totals["session_gate_off"] - totals["optimizer_native"], 4)
    deploy_effect = round(totals["deploy_95"] - totals["optimizer_native"], 4)
    min_cash_effect = round(totals["min_cash_10"] - totals["optimizer_native"], 4)
    canonical_total_shift = round(totals["canonical"] - totals["optimizer_native"], 4)
    interaction_effect = round(canonical_total_shift - session_effect - deploy_effect - min_cash_effect, 4)
    return {
        "scenario_name": "per_coin_100",
        "component_effects": {
            "session_gate_off": session_effect,
            "deploy_95": deploy_effect,
            "min_cash_10": min_cash_effect,
            "interaction_effect": interaction_effect,
            "canonical_total_shift": canonical_total_shift,
        },
    }


def main():
    print("=" * 80)
    print("  OPTIMAL PORTFOLIO OPTIMIZER")
    print("=" * 80)
    print(f"Strategies: {len(STRATEGIES)}")
    print(f"Coins: {len(ALL_COINS)}")
    print(f"Total backtests: {len(STRATEGIES) * len(ALL_COINS)}")
    print()

    # Load all candle data
    candles_by_coin = {}
    for coin_name in ALL_COINS:
        try:
            coin_file = f"reports/candle_cache/{coin_name.replace('-', '_')}_FIVE_MINUTE_30d.json"
            data = json.loads(open(coin_file).read())
            candles_by_coin[coin_name] = data["candles"]
            print(f"  {coin_name}: {len(data['candles'])} candles", flush=True)
        except Exception as e:
            print(f"  {coin_name}: ERROR — {e}", flush=True)

    print()

    # ========== Test all strategies on all coins ==========
    results = {}
    native_assignments = {}

    for scenario_name, starting_cash in [("per_coin_5_33", 5.33), ("per_coin_100", 100.0)]:
        print(f"\n{'='*80}", flush=True)
        print(f"  SCENARIO: {scenario_name} (${starting_cash}/coin)", flush=True)
        print(f"{'='*80}", flush=True)

        scenario_results = {}

        for strat_name, strat_info in STRATEGIES.items():
            entry_fn = ENTRY_FUNCS[strat_name]
            params = strat_info["params"]

            print(f"\n  Testing {strat_name}...", flush=True)
            strat_results = {}

            for coin_name in ALL_COINS:
                if coin_name not in candles_by_coin:
                    continue
                candles = candles_by_coin[coin_name]
                r = simulate(candles, entry_fn, params, starting_cash)
                strat_results[coin_name] = r

            scenario_results[strat_name] = strat_results

        results[scenario_name] = scenario_results

        # Print matrix
        print(f"\n  {'Strategy':<25}", end="", flush=True)
        for coin in ALL_COINS:
            print(f" | {coin[:4]:>8}", end="", flush=True)
        print(f" | {'TOTAL':>8}", flush=True)
        print(f"  {'-'*25}", end="", flush=True)
        for _ in ALL_COINS:
            print(f"-+-{'-'*8}", end="", flush=True)
        print(f"-+-{'-'*8}", flush=True)

        for strat_name in STRATEGIES:
            print(f"  {strat_name:<25}", end="", flush=True)
            total = 0
            for coin in ALL_COINS:
                r = scenario_results.get(strat_name, {}).get(coin, {})
                pnl = r.get("net_pnl", 0)
                total += pnl
                print(f" | ${pnl:>+7.2f}", end="", flush=True)
            print(f" | ${total:>+7.2f}", flush=True)

    # ========== Find optimal assignment ==========
    print(f"\n{'='*80}", flush=True)
    print("  OPTIMAL ASSIGNMENT (one strategy per coin, maximize total PnL)", flush=True)
    print(f"{'='*80}", flush=True)

    for scenario_name in results:
        scenario_results = results[scenario_name]
        optimal, total_pnl = build_best_assignment(scenario_results)
        native_assignments[scenario_name] = {
            "starting_cash": 5.33 if scenario_name == "per_coin_5_33" else 100.0,
            "assignment": optimal,
            "projected_total_pnl": total_pnl,
        }

        print(f"\n  Scenario: {scenario_name}", flush=True)
        print(f"  {'Coin':<14} | {'Best Strategy':<25} | {'PnL':>10}", flush=True)
        print(f"  {'-'*14}-+-{'-'*25}-+-{'-'*10}", flush=True)

        for coin in ALL_COINS:
            o = optimal[coin]
            print(f"  {coin:<14} | {o['strategy']:<25} | ${o['pnl']:+>9.2f}", flush=True)

        print(f"  {'-'*14}-+-{'-'*25}-+-{'-'*10}", flush=True)
        print(f"  {'TOTAL':<14} | {'':<25} | ${total_pnl:>+9.2f}", flush=True)

        # Group by strategy
        strat_coins = {}
        for coin, o in optimal.items():
            strat = o["strategy"]
            if strat not in strat_coins:
                strat_coins[strat] = []
            strat_coins[strat].append(coin)

        print(f"\n  Strategy assignment:", flush=True)
        for strat, coins in strat_coins.items():
            strat_pnl = sum(optimal[c]["pnl"] for c in coins)
            print(f"    {strat}: {', '.join(c.split('-')[0] for c in coins)} (${strat_pnl:+.2f})", flush=True)

    canonical_assumptions = canonical_assumptions_payload()
    canonical_scenarios = []
    for scenario_name, row in native_assignments.items():
        replay = replay_assignment(
            candles_by_coin,
            row["assignment"],
            row["starting_cash"],
            assumptions=canonical_assumptions,
        )
        canonical_scenarios.append(
            {
                "scenario_name": scenario_name,
                "projected_total_pnl": row["projected_total_pnl"],
                "canonical_total_pnl": replay["canonical_total_pnl"],
                "delta_vs_projected": round(replay["canonical_total_pnl"] - float(row["projected_total_pnl"]), 4),
                "feasible_count": replay["feasible_count"],
                "coin_count": replay["coin_count"],
                "per_coin": replay["per_coin"],
            }
        )

    drift_attribution = build_drift_attribution(
        candles_by_coin,
        native_assignments["per_coin_100"]["assignment"],
        native_assignments["per_coin_100"]["starting_cash"],
    )

    # Save report
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "report_semantics": report_semantics_payload(),
        "native_assumptions": native_assumptions_payload(),
        "comparison_artifacts": comparison_artifacts_payload(),
        "canonical_reference": canonical_reference_payload(canonical_scenarios, drift_attribution),
        "strategies": list(STRATEGIES.keys()),
        "coins": ALL_COINS,
        "results": {sc: {st: {c: r for c, r in coins.items()} for st, coins in strat.items()} for sc, strat in results.items()},
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
