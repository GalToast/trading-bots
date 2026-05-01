#!/usr/bin/env python3
"""
30d Validation — Top 3 unverified edges from 500-strategy final report
=======================================================================

Validates on 30d data:
1. fibonacci_breakout — $2,180 on 7d
2. time_decay_signal — $2,140 on 7d
3. ma_atr — $1,954 on 7d

Also validates supertrend ($3,406 on 7d) that was added to isolated runner.

Uses runner-modeled backtest ($2 min, 90% deploy, session gate) to match live behavior.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

COINS = ["RAVE-USD", "NOM-USD", "GHST-USD", "TRU-USD", "SUP-USD"]

# Runner constants
FEE_RATE = 0.004
MIN_CASH = 2.0
DEPLOY_FRACTION = 0.90
SESSION_DEAD = {0, 6, 12, 19}
STARTING_CASH = 100.0

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "reports" / "validate_top3_edges_30d.json"


def _fibonacci_breakout_entry(candles_hist, closes, candle, params):
    if len(candles_hist) < 30:
        return False
    lookback = params.get("lookback", 20)
    highs = [float(c["high"]) for c in candles_hist[-lookback:]]
    lows = [float(c["low"]) for c in candles_hist[-lookback:]]
    swing_high = max(highs)
    swing_low = min(lows)
    range_val = swing_high - swing_low
    fib_618 = swing_high - 0.618 * range_val
    current_price = float(candle["close"])
    if current_price > fib_618 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _time_decay_entry(candles_hist, closes, candle, params):
    if len(candles_hist) < 30:
        return False
    decay_period = params.get("decay_period", 10)
    recent_returns = []
    for i in range(max(1, len(closes) - decay_period - 1), len(closes) - 1):
        if closes[i] > 0 and closes[i-1] > 0:
            recent_returns.append(abs(closes[i] / closes[i-1] - 1))
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
    if len(candles_hist) < 50:
        return False
    ma_period = params.get("ma_period", 20)
    atr_period = params.get("atr_period", 14)
    atr_mult = params.get("atr_mult", 1.5)
    if len(closes) < ma_period + 5:
        return False
    ma = sum(closes[-ma_period:]) / ma_period
    ma_prev = sum(closes[-ma_period-1:-1]) / ma_period
    current_price = closes[-1]
    ma_rising = ma > ma_prev
    price_above = current_price > ma
    trs = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        c_prev = float(candles_hist[i-1]["close"])
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    if len(trs) < atr_period + 1:
        return False
    current_atr = sum(trs[-atr_period:]) / atr_period
    prev_atr = sum(trs[-atr_period*2:-atr_period]) / atr_period if len(trs) >= atr_period * 2 else current_atr
    atr_expanding = current_atr > prev_atr * atr_mult if prev_atr > 0 else False
    if price_above and ma_rising and atr_expanding:
        return True
    return False


def _supertrend_entry(candles_hist, closes, candle, params):
    """Supertrend: price closes above supertrend line in uptrend."""
    atr_period = params.get("atr_period", 10)
    atr_mult = params.get("atr_mult", 3.0)
    if len(candles_hist) < atr_period + 10:
        return False
    # Simplified supertrend: ATR-based trailing support
    trs = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        c_prev = float(candles_hist[i-1]["close"])
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    if len(trs) < atr_period:
        return False
    atr = sum(trs[-atr_period:]) / atr_period
    hl2 = (float(candle["high"]) + float(candle["low"])) / 2
    supertrend = hl2 - atr_mult * atr
    current_price = float(candle["close"])
    if current_price > supertrend and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


STRATEGIES = {
    "fibonacci_breakout": {"entry": _fibonacci_breakout_entry, "params": {"lookback": 20, "tp_pct": 8.0, "sl_pct": 3.0, "max_hold": 24}},
    "time_decay_signal": {"entry": _time_decay_entry, "params": {"decay_period": 15, "tp_pct": 15.0, "sl_pct": 0.0, "max_hold": 48}},
    "ma_atr": {"entry": _ma_atr_entry, "params": {"ma_period": 20, "atr_period": 14, "atr_mult": 1.5, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 24}},
    "supertrend": {"entry": _supertrend_entry, "params": {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 48}},
}


def simulate(candles, entry_fn, params, starting_cash):
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
        session_open = hour not in SESSION_DEAD

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
        if position is None and cash >= MIN_CASH and session_open:
            if entry_fn(candle_history, history, candle, params):
                signals += 1
                deploy = cash * DEPLOY_FRACTION
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
        "ending_cash": round(cash, 2),
    }


def main():
    print("=" * 80)
    print("  30D VALIDATION — TOP 3 UNVERIFIED EDGES + SUPERTREND")
    print("=" * 80)
    print(f"Strategies: fibonacci_breakout, time_decay_signal, ma_atr, supertrend")
    print(f"Coins: {', '.join(COINS)}")
    print(f"Starting cash: ${STARTING_CASH}/coin")
    print()

    results = {}

    for strat_name, strat_info in STRATEGIES.items():
        print(f"\n{'='*60}", flush=True)
        print(f"  {strat_name.upper()} (30d validation)", flush=True)
        print(f"{'='*60}", flush=True)

        strat_results = {}
        total_pnl = 0

        for coin_name in COINS:
            try:
                coin_file = f"reports/candle_cache/{coin_name.replace('-', '_')}_FIVE_MINUTE_30d.json"
                data = json.loads(open(coin_file).read())
                candles = data["candles"]

                r = simulate(candles, strat_info["entry"], strat_info["params"], STARTING_CASH)
                strat_results[coin_name] = r
                total_pnl += r["net_pnl"]

                status = "✅" if r["net_pnl"] > 0 else "❌"
                print(f"  {status} {coin_name}: ${r['net_pnl']:+.2f} (WR={r['win_rate']}%, "
                      f"{r['trades']} trades, {r['signals']} signals)", flush=True)
            except Exception as e:
                print(f"  ❌ {coin_name}: ERROR — {e}", flush=True)

        results[strat_name] = {
            "coins": strat_results,
            "total_pnl": round(total_pnl, 2),
            "profitable_coins": sum(1 for r in strat_results.values() if r["net_pnl"] > 0),
        }

        print(f"\n  TOTAL: ${total_pnl:+.2f} ({strat_results and sum(1 for r in strat_results.values() if r['net_pnl'] > 0)}/{len(strat_results)} coins profitable)", flush=True)

    # ========== SUMMARY ==========
    print(f"\n{'='*80}", flush=True)
    print("  VALIDATION SUMMARY", flush=True)
    print(f"{'='*80}", flush=True)

    print(f"\n  {'Strategy':<25} | {'Total PnL':>10} | {'Profitable':>11} | Verdict", flush=True)
    print(f"  {'-'*25}-+-{'-'*10}-+-{'-'*11}-+-{'-'*12}", flush=True)

    for strat_name, sdata in results.items():
        profitable = sdata["profitable_coins"]
        total = len(COINS)
        verdict = "✅ VALIDATED" if profitable > 0 else "❌ FAILS"
        print(f"  {strat_name:<25} | ${sdata['total_pnl']:+10.2f} | {profitable:>5}/{total:>4} | {verdict}", flush=True)

    # Save
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "starting_cash": STARTING_CASH,
        "coins": COINS,
        "results": results,
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
