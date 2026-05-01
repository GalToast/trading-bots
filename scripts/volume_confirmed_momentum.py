#!/usr/bin/env python3
"""
Volume-Confirmed Momentum — Does adding a volume filter improve momentum?

Hypothesis: Momentum breakouts with volume > avg_volume × multiplier
have higher WR and better PnL than raw momentum.

Test: Sweep volume multipliers on top 5 coins, runner-modeled ($2 min, 90% deploy).
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

COINS = ["RAVE-USD", "NOM-USD", "GHST-USD", "TRU-USD", "SUP-USD"]
VOLUME_MULTIPLIERS = [1.0, 1.5, 2.0, 2.5, 3.0]
AVG_VOLUME_BARS = 20  # Lookback for average volume

# Runner constants
FEE_RATE = 0.004
MIN_CASH = 2.0
DEPLOY_FRACTION = 0.90
SESSION_DEAD = {0, 6, 12, 19}
STARTING_CASH = 5.33  # $48 / 9 coins

# Strategy params
LOOKBACK = 15
TP_PCT = 0.10
SL_PCT = 0.00
MAX_HOLD = 48

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "reports" / "volume_confirmed_momentum.json"


def simulate(candles, volume_mult, starting_cash):
    """Runner-modeled simulation with volume filter."""
    from datetime import datetime as dt, timezone as tz

    cash = starting_cash
    position = None
    history = []
    candle_history = []
    volume_history = []
    signals = 0
    trades = 0
    wins = 0
    losses = 0
    total_fees = 0
    total_volume_val = 0

    for candle in candles:
        ts = int(candle.get("time", candle.get("start", 0)))
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        open_price = float(candle["open"])
        vol = float(candle.get("volume", 0))

        if open_price <= 0 or close <= 0:
            continue

        history.append(close)
        volume_history.append(vol)
        candle_history.append(candle)
        if len(history) > 500:
            history = history[-500:]
            volume_history = volume_history[-500:]
            candle_history = candle_history[-500:]

        hour = dt.fromtimestamp(ts, tz=tz.utc).hour
        session_open = hour not in SESSION_DEAD

        fee_rate = FEE_RATE  # Simplified

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
            elif position["hold"] >= position["max_hold"]:
                exit_price = close
                exit_reason = "timeout"

            if exit_price is not None:
                units = position["units"]
                gross = (exit_price - position["ep"]) * units
                net = gross - position["entry_fee"] - (exit_price * units * fee_rate)
                cash += position["deploy"] + net
                trades += 1
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                total_fees += position["entry_fee"] + exit_price * units * fee_rate
                total_volume_val += position["deploy"] + exit_price * units
                position = None

        # ENTRY
        if position is None and cash >= MIN_CASH and session_open:
            if len(candle_history) > LOOKBACK + 1:
                recent_high = max(float(c["high"]) for c in candle_history[-(LOOKBACK + 1):-1])
                if high > recent_high:
                    # Volume filter
                    if len(volume_history) >= AVG_VOLUME_BARS:
                        avg_vol = sum(volume_history[-AVG_VOLUME_BARS:]) / AVG_VOLUME_BARS
                        if vol >= avg_vol * volume_mult:
                            signals += 1
                            deploy = cash * DEPLOY_FRACTION
                            entry_price = open_price
                            if entry_price <= 0:
                                continue

                            entry_fee = deploy * fee_rate
                            units = (deploy - entry_fee) / entry_price
                            tp = entry_price * (1 + TP_PCT)
                            sl = entry_price * (1 - SL_PCT) if SL_PCT > 0 else 0

                            cash -= deploy
                            position = {
                                "ep": entry_price, "deploy": deploy, "units": units,
                                "tp": tp, "sl": sl, "hold": 0, "entry_fee": entry_fee,
                                "max_hold": MAX_HOLD,
                            }

    # Close remaining
    if position:
        last_close = float(candles[-1]["close"])
        gross = (last_close - position["ep"]) * position["units"]
        net = gross - position["entry_fee"] - (last_close * position["units"] * fee_rate)
        cash += position["deploy"] + net
        trades += 1
        total_fees += position["entry_fee"] + last_close * position["units"] * fee_rate
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
    print("  VOLUME-CONFIRMED MOMENTUM TEST")
    print("=" * 80)
    print(f"Volume multipliers: {VOLUME_MULTIPLIERS}")
    print(f"Avg volume lookback: {AVG_VOLUME_BARS} bars")
    print(f"Starting cash: ${STARTING_CASH}/coin")
    print()

    results = {}

    for coin_name in COINS:
        try:
            coin_file = f"reports/candle_cache/{coin_name.replace('-', '_')}_FIVE_MINUTE_30d.json"
            data = json.loads(open(coin_file).read())
            candles = data["candles"]

            print(f"\n{'='*60}", flush=True)
            print(f"  {coin_name}", flush=True)
            print(f"{'='*60}", flush=True)
            print(f"  {'Vol Mult':>9} | {'Net PnL':>9} | {'WR%':>5} | {'Trades':>7} | {'Signals':>8}", flush=True)
            print(f"  {'-'*9}-+-{'-'*9}-+-{'-'*5}-+-{'-'*7}-+-{'-'*8}", flush=True)

            coin_results = {}
            for mult in VOLUME_MULTIPLIERS:
                r = simulate(candles, mult, STARTING_CASH)
                coin_results[str(mult)] = r

                baseline = " ← baseline" if mult == 1.0 else ""
                best = " ← BEST" if mult == 1.0 else ""
                print(f"  {mult:>9.1f}x | ${r['net_pnl']:>+8.2f} | {r['win_rate']:>4.1f}% | {r['trades']:>7} | {r['signals']:>8}{baseline}{best}", flush=True)

                # Check if this is better than baseline
                if mult > 1.0:
                    base = coin_results["1.0"]
                    if r["net_pnl"] > base["net_pnl"]:
                        print(f"  {'':>9} | {'↑ BETTER':>9} | {'':>5} | {'':>7} | {'':>8}", flush=True)
                    elif r["net_pnl"] < base["net_pnl"]:
                        print(f"  {'':>9} | {'↓ WORSE':>9} | {'':>5} | {'':>7} | {'':>8}", flush=True)

            results[coin_name] = coin_results

        except Exception as e:
            print(f"  {coin_name}: ERROR — {e}", flush=True)

    # ========== SUMMARY ==========
    print(f"\n{'='*80}", flush=True)
    print("  SUMMARY", flush=True)
    print(f"{'='*80}", flush=True)

    for mult in VOLUME_MULTIPLIERS:
        total_pnl = sum(results[c].get(str(mult), {}).get("net_pnl", 0) for c in COINS if c in results)
        total_trades = sum(results[c].get(str(mult), {}).get("trades", 0) for c in COINS if c in results)
        avg_wr = sum(results[c].get(str(mult), {}).get("win_rate", 0) for c in COINS if c in results) / max(1, len(results))

        baseline = " ← BASELINE" if mult == 1.0 else ""
        print(f"  Volume {mult:.1f}x: Total PnL = ${total_pnl:+.2f}, Avg WR = {avg_wr:.1f}%, Total trades = {total_trades}{baseline}", flush=True)

    # Save
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "params": {
            "volume_multipliers": VOLUME_MULTIPLIERS,
            "avg_volume_bars": AVG_VOLUME_BARS,
            "starting_cash": STARTING_CASH,
            "momentum_lookback": LOOKBACK,
            "tp_pct": TP_PCT,
            "sl_pct": SL_PCT,
            "max_hold": MAX_HOLD,
        },
        "results": results,
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
