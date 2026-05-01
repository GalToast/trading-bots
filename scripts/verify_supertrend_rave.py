#!/usr/bin/env python3
"""
Independent Verification — Supertrend on RAVE
==============================================

@qwen-strategies-tester claims supertrend on RAVE earns $3,505 at $100 with:
- atr_period=10, atr_mult=3.0, TP=10%, SL=3%, max_hold=48
- 56.6% WR, 242 trades, profit factor 4.40

My earlier validation found $1,095 with same params but possibly different entry logic.

This script runs the EXACT same entry function at both $5.33 and $100 to verify.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

COIN = "RAVE-USD"
PARAMS = {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 48}

FEE_RATE = 0.004
MIN_CASH = 2.0
DEPLOY_FRACTION = 0.90
SESSION_DEAD = {0, 6, 12, 19}

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "reports" / "verify_supertrend_rave.json"


def supertrend_entry(candles_hist, closes, candle, params):
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
    current_price = float(candle["close"])
    return current_price > supertrend and len(closes) > 1 and closes[-1] > closes[-2]


def simulate(candles, starting_cash):
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

    tp_pct = PARAMS["tp_pct"] / 100.0
    sl_pct = PARAMS["sl_pct"] / 100.0
    max_hold = PARAMS["max_hold"]

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
            if supertrend_entry(candle_history, history, candle, PARAMS):
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
        "starting_cash": starting_cash,
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
    print("  INDEPENDENT VERIFICATION — Supertrend on RAVE")
    print("=" * 80)
    print(f"Params: atr_period=10, atr_mult=3.0, TP=10%, SL=3%, max_hold=48")
    print()

    coin_file = "reports/candle_cache/RAVE_USD_FIVE_MINUTE_30d.json"
    data = json.loads(open(coin_file).read())
    candles = data["candles"]
    print(f"Loaded {len(candles)} candles for {COIN}")
    print()

    # Test at $5.33
    print(f"Testing at $5.33...", flush=True)
    r_5 = simulate(candles, 5.33)
    print(f"  Net PnL: ${r_5['net_pnl']:+.2f}", flush=True)
    print(f"  WR: {r_5['win_rate']:.1f}%", flush=True)
    print(f"  Trades: {r_5['trades']}", flush=True)
    print(f"  Signals: {r_5['signals']}", flush=True)
    print(f"  Fees: ${r_5['total_fees']:.2f}", flush=True)
    print()

    # Test at $100
    print(f"Testing at $100...", flush=True)
    r_100 = simulate(candles, 100.0)
    print(f"  Net PnL: ${r_100['net_pnl']:+.2f}", flush=True)
    print(f"  WR: {r_100['win_rate']:.1f}%", flush=True)
    print(f"  Trades: {r_100['trades']}", flush=True)
    print(f"  Signals: {r_100['signals']}", flush=True)
    print(f"  Fees: ${r_100['total_fees']:.2f}", flush=True)
    print()

    # Compare with claims
    print(f"{'='*80}", flush=True)
    print("  COMPARISON", flush=True)
    print(f"{'='*80}", flush=True)

    print(f"\n  Claim from risk assessment: $3,505 at $100, 56.6% WR, 242 trades", flush=True)
    print(f"  My validation result:       ${r_100['net_pnl']:+,.2f} at $100, {r_100['win_rate']:.1f}% WR, {r_100['trades']} trades", flush=True)

    pnl_diff = abs(r_100['net_pnl'] - 3505)
    if pnl_diff < 100:
        verdict = "✅ CONFIRMED (within $100)"
    elif pnl_diff < 500:
        verdict = "⚠️ CLOSE (within $500)"
    else:
        verdict = "❌ DISCREPANCY ($" + f"{pnl_diff:,.0f}" + " difference)"

    print(f"\n  Verdict: {verdict}", flush=True)

    # Save
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "coin": COIN,
        "params": PARAMS,
        "at_5_33": r_5,
        "at_100": r_100,
        "comparison": {
            "claimed_pnl": 3505,
            "claimed_wr": 56.6,
            "claimed_trades": 242,
            "verified_pnl": r_100['net_pnl'],
            "verified_wr": r_100['win_rate'],
            "verified_trades": r_100['trades'],
            "pnl_difference": round(r_100['net_pnl'] - 3505, 2),
            "wr_difference": round(r_100['win_rate'] - 56.6, 1),
            "trades_difference": r_100['trades'] - 242,
            "verdict": verdict,
        },
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
