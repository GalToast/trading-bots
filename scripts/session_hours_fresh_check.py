#!/usr/bin/env python3
"""
Session Hour Gating Verification: All-Active vs Top-6 Hours

For each of the 9 coins, runs TWO backtests:
  A) All active hours (exclude only dead hours {0,6,12,19})
  B) Top-6 profitable hours only (from existing session_hour_consolidation.py)

Answers: Are per-coin hour whitelists still helping or hurting?

Usage:
    python scripts/session_hours_fresh_check.py
"""
import json
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "reports" / "session_hours_fresh_check.json"

COINS = ["NOM", "GHST", "SUP", "RAVE", "TRU", "BAL", "IOTX", "A8", "CFG"]
COIN_SYMBOLS = {c: f"{c}-USD" for c in COINS}

CANDLE_FILES = {
    "NOM": "NOM_USD_FIVE_MINUTE_30d.json",
    "GHST": "GHST_USD_FIVE_MINUTE_30d.json",
    "SUP": "SUP_USD_FIVE_MINUTE_30d.json",
    "RAVE": "RAVE_USD_FIVE_MINUTE_30d.json",
    "TRU": "TRU_USD_FIVE_MINUTE_30d.json",
    "BAL": "BAL_USD_FIVE_MINUTE_30d.json",
    "IOTX": "IOTX_USD_FIVE_MINUTE_30d.json",
    "A8": "A8_USD_FIVE_MINUTE_30d.json",
    "CFG": "CFG_USD_FIVE_MINUTE_30d.json",
}

STRATEGY_CONFIG = {
    "NOM":  {"strategy": "fibonacci", "lookback": 20, "tp": 0.08, "sl": 0.03, "max_hold": 24},
    "GHST": {"strategy": "fibonacci", "lookback": 10, "tp": 0.08, "sl": 0.03, "max_hold": 96},
    "SUP":  {"strategy": "fibonacci", "lookback": 20, "tp": 0.08, "sl": 0.03, "max_hold": 24},
    "RAVE": {"strategy": "supertrend", "atr_period": 10, "atr_mult": 3.0, "tp": 0.10, "sl": 0.05, "max_hold": 48},
    "TRU":  {"strategy": "supertrend", "atr_period": 10, "atr_mult": 3.0, "tp": 0.10, "sl": 0.03, "max_hold": 48},
    "BAL":  {"strategy": "supertrend", "atr_period": 10, "atr_mult": 3.0, "tp": 0.10, "sl": 0.05, "max_hold": 96},
    "IOTX": {"strategy": "supertrend", "atr_period": 10, "atr_mult": 3.0, "tp": 0.10, "sl": 0.03, "max_hold": 48},
    "A8":   {"strategy": "momentum", "lookback": 10, "tp": 0.15, "sl": 0.00, "max_hold": 48},
    "CFG":  {"strategy": "momentum", "lookback": 50, "tp": 0.15, "sl": 0.00, "max_hold": 48},
}

# Top-6 hours from existing session_hour_consolidation.json
TOP6_HOURS = {
    "NOM":  [8, 10, 1, 5, 4, 11],
    "GHST": [4, 5, 7, 2, 3, 18],
    "SUP":  [16, 18, 23, 20, 5, 15],
    "RAVE": [15, 9, 18, 22, 2, 23],
    "TRU":  [7, 8, 15, 17, 5, 18],
    "BAL":  [1, 23, 20, 17, 22, 15],
    "IOTX": [15, 21, 10, 5, 9, 4],
    "A8":   [7, 15, 23, 11, 22, 17],
    "CFG":  [10, 8, 13, 1, 4, 20],
}

FEE_RATE = 0.004
DEPLOY_FRACTION = 0.90
MIN_CASH = 5.33
SESSION_DEAD = {0, 6, 12, 19}


def load_candles(coin_short):
    fname = CANDLE_FILES[coin_short]
    path = ROOT / "reports" / "candle_cache" / fname
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    candles = data.get("candles", data if isinstance(data, list) else [])
    normalized = []
    for c in candles:
        if isinstance(c, dict):
            normalized.append(c)
        elif isinstance(c, (list, tuple)):
            normalized.append({
                "time": c[0], "open": c[1], "high": c[2],
                "low": c[3], "close": c[4], "volume": c[5],
            })
    normalized.sort(key=lambda x: int(x.get("time", x.get("start", 0))))
    return normalized


def compute_atr(candles, period, idx):
    if idx < period:
        return 0
    trs = []
    for i in range(idx - period + 1, idx + 1):
        c = candles[i]
        cp = candles[i - 1]
        tr = max(
            float(c["high"]) - float(c["low"]),
            abs(float(c["high"]) - float(cp["close"])),
            abs(float(c["low"]) - float(cp["close"]))
        )
        trs.append(tr)
    return sum(trs) / len(trs)


def signal_fibonacci(candles_hist, closes, lookback):
    if len(candles_hist) < lookback + 5:
        return False
    window = candles_hist[-(lookback + 1):-1]
    if len(window) < lookback * 0.5:
        return False
    swing_high = max(float(c["high"]) for c in window)
    swing_low = min(float(c["low"]) for c in window)
    rng = swing_high - swing_low
    if rng <= 0:
        return False
    fib_618 = swing_high - 0.618 * rng
    current = float(candles_hist[-1]["close"])
    if current <= closes[-2]:
        return False
    if current <= fib_618:
        return False
    if len(candles_hist) >= 20:
        vols = [float(c.get("volume", 0)) for c in candles_hist[-20:-1]]
        avg_v = sum(vols) / len(vols)
        if avg_v > 0 and float(candles_hist[-1].get("volume", 0)) < 0.8 * avg_v:
            return False
    if len(closes) >= 4:
        green = sum(1 for i in range(-3, 0) if closes[i] > closes[i - 1])
        if green < 2:
            return False
    return True


def signal_supertrend(candles_hist, atr_period, atr_mult):
    if len(candles_hist) < atr_period + 2:
        return False
    atr = compute_atr(candles_hist, atr_period, len(candles_hist) - 1)
    if atr <= 0:
        return False
    last = candles_hist[-1]
    hl2 = (float(last["high"]) + float(last["low"])) / 2
    lower = hl2 - atr_mult * atr
    current = float(last["close"])
    if current <= lower:
        return False
    if current <= candles_hist[-2]["close"] if len(candles_hist) >= 2 else True:
        return False
    return current > lower and current > float(candles_hist[-2]["close"])


def signal_momentum(candles_hist, lookback):
    if len(candles_hist) < lookback + 1:
        return False
    recent = candles_hist[-(lookback):-1]
    if not recent:
        return False
    highest = max(float(c["high"]) for c in recent)
    return float(candles_hist[-1]["high"]) > highest


def backtest(candles, cfg, allowed_hours_set, starting_cash=MIN_CASH):
    """
    Run backtest with given hour whitelist.
    - Hours NOT in allowed_hours_set are treated as 'dead': no new entries,
      existing positions still managed (TP/SL/timeout).
    - This mirrors the SESSION_DEAD logic from the original script.
    """
    strat = cfg["strategy"]
    min_candles = 200

    position = None
    signals = 0
    total_trades = 0
    total_pnl = 0.0
    total_wins = 0

    for i in range(min_candles, len(candles)):
        candle = candles[i]
        candle_time = int(candle.get("time", candle.get("start", 0)))
        hour = datetime.fromtimestamp(candle_time, tz=timezone.utc).hour

        closes = [float(c["close"]) for c in candles[max(0, i - 500):i + 1]]
        candles_hist = candles[max(0, i - 500):i + 1]

        high = float(candle["high"])
        low = float(candle["low"])
        open_p = float(candle["open"])

        # Check if this hour is blocked
        is_blocked = hour not in allowed_hours_set

        if position:
            position["hold"] += 1
            if high >= position["tp"]:
                pnl = (position["tp"] - position["entry"]) * position["units"]
                fee = position["tp"] * position["units"] * FEE_RATE
                net = pnl - fee - position["entry_fee"]
                total_trades += 1
                total_pnl += net
                if net > 0:
                    total_wins += 1
                position = None
            elif position["sl"] > 0 and low <= position["sl"]:
                pnl = (position["sl"] - position["entry"]) * position["units"]
                fee = position["sl"] * position["units"] * FEE_RATE
                net = pnl - fee - position["entry_fee"]
                total_trades += 1
                total_pnl += net
                if net > 0:
                    total_wins += 1
                position = None
            elif position["hold"] >= cfg["max_hold"]:
                pnl = (open_p - position["entry"]) * position["units"]
                fee = open_p * position["units"] * FEE_RATE
                net = pnl - fee - position["entry_fee"]
                total_trades += 1
                total_pnl += net
                if net > 0:
                    total_wins += 1
                position = None

        # If blocked, skip new entries
        if is_blocked:
            continue

        if position is None:
            triggered = False
            if strat == "fibonacci":
                triggered = signal_fibonacci(candles_hist, closes, cfg["lookback"])
            elif strat == "supertrend":
                triggered = signal_supertrend(candles_hist, cfg["atr_period"], cfg["atr_mult"])
            elif strat == "momentum":
                triggered = signal_momentum(candles_hist, cfg["lookback"])

            if triggered:
                signals += 1
                deploy = starting_cash * DEPLOY_FRACTION
                entry_price = open_p
                units = deploy / entry_price if entry_price > 0 else 0
                entry_fee = deploy * FEE_RATE
                tp = entry_price * (1 + cfg["tp"])
                sl = entry_price * (1 - cfg["sl"]) if cfg["sl"] > 0 else 0
                position = {
                    "entry": entry_price, "units": units, "hold": 0,
                    "tp": tp, "sl": sl, "deploy": deploy, "entry_fee": entry_fee,
                }

    # Close any remaining position at last candle close
    if position:
        exit_price = float(candles[-1]["close"])
        pnl = (exit_price - position["entry"]) * position["units"]
        fee = exit_price * position["units"] * FEE_RATE
        net = pnl - fee - position["entry_fee"]
        total_trades += 1
        total_pnl += net
        if net > 0:
            total_wins += 1

    wr = total_wins / max(1, total_trades) * 100
    return {
        "total_trades": total_trades,
        "total_pnl": round(total_pnl, 4),
        "win_rate": round(wr, 1),
        "signals": signals,
    }


def main():
    print("=" * 85)
    print("  Session Hour Gating Verification: All-Active vs Top-6 Hours")
    print("=" * 85)

    # Load existing consolidation for reference
    consolidation_path = ROOT / "reports" / "session_hour_consolidation.json"
    existing = {}
    if consolidation_path.exists():
        with open(consolidation_path) as f:
            existing_data = json.load(f)
        for coin_sym in existing_data.get("coins_analyzed", []):
            r = existing_data["results"].get(coin_sym, {})
            short = coin_sym.replace("-USD", "")
            existing[short] = r

    results = {}

    print(f"\n  {'Coin':<6} {'Strategy':<12} {'All-hrs PnL':>12} {'All Trades':>9} {'All WR':>6} | {'Top-6 PnL':>10} {'T6 Trades':>9} {'T6 WR':>6} | {'Delta':>8} {'Verdict'}")
    print(f"  {'─' * 85}")

    for coin in COINS:
        symbol = COIN_SYMBOLS[coin]
        cfg = STRATEGY_CONFIG[coin]

        candles = load_candles(coin)
        if not candles:
            print(f"  {coin:<6} [ERROR] No candle data")
            continue

        top6 = set(TOP6_HOURS[coin])
        # All active hours = all 24 hours minus dead hours
        all_active = set(range(24)) - SESSION_DEAD

        result_all = backtest(candles, cfg, all_active)
        result_top6 = backtest(candles, cfg, top6)

        delta = result_top6["total_pnl"] - result_all["total_pnl"]
        delta_pct = (delta / abs(result_all["total_pnl"]) * 100) if result_all["total_pnl"] != 0 else 0

        if delta > 0.01:
            verdict = "REMOVE GATE (top-6 better)"
        elif delta < -0.01:
            verdict = "KEEP GATE (all-hrs better)"
        else:
            verdict = "NEGLIGIBLE"

        results[coin] = {
            "symbol": symbol,
            "strategy": cfg["strategy"],
            "all_hours": {
                "allowed_hours": sorted(list(all_active)),
                "total_pnl": result_all["total_pnl"],
                "total_trades": result_all["total_trades"],
                "win_rate": result_all["win_rate"],
                "signals": result_all["signals"],
            },
            "top6_hours": {
                "allowed_hours": sorted(list(top6)),
                "total_pnl": result_top6["total_pnl"],
                "total_trades": result_top6["total_trades"],
                "win_rate": result_top6["win_rate"],
                "signals": result_top6["signals"],
            },
            "delta_pnl": round(delta, 4),
            "delta_pct": round(delta_pct, 1),
            "verdict": verdict,
        }

        print(f"  {coin:<6} {cfg['strategy']:<12} ${result_all['total_pnl']:>10.2f} {result_all['total_trades']:>9} {result_all['win_rate']:>5.1f}% | ${result_top6['total_pnl']:>8.2f} {result_top6['total_trades']:>9} {result_top6['win_rate']:>5.1f}% | ${delta:>+7.2f} {verdict}")

    # Summary
    print(f"\n{'=' * 85}")
    print("  SUMMARY")
    print(f"{'=' * 85}")

    keep_count = sum(1 for r in results.values() if r["verdict"].startswith("KEEP"))
    remove_count = sum(1 for r in results.values() if r["verdict"].startswith("REMOVE"))
    negl_count = sum(1 for r in results.values() if r["verdict"] == "NEGLIGIBLE")

    print(f"\n  Keep gate:     {keep_count} coins")
    print(f"  Remove gate:   {remove_count} coins")
    print(f"  Negligible:    {negl_count} coins")

    if remove_count > 0:
        print(f"\n  Coins where top-6 gating is BETTER:")
        for coin, r in results.items():
            if r["verdict"].startswith("REMOVE"):
                print(f"    {coin}: +${r['delta_pnl']:.2f} ({r['delta_pct']:+.1f}%) with top-6 only")

    if keep_count > 0:
        print(f"\n  Coins where all-hours is BETTER (gate is hurting):")
        for coin, r in results.items():
            if r["verdict"].startswith("KEEP"):
                print(f"    {coin}: -${abs(r['delta_pnl']):.2f} ({r['delta_pct']:+.1f}%) with top-6 only")

    if negl_count > 0:
        print(f"\n  Coins with negligible difference:")
        for coin, r in results.items():
            if r["verdict"] == "NEGLIGIBLE":
                print(f"    {coin}: delta ${r['delta_pnl']:.2f}")

    # Overall portfolio comparison
    all_total = sum(r["all_hours"]["total_pnl"] for r in results.values())
    top6_total = sum(r["top6_hours"]["total_pnl"] for r in results.values())
    portfolio_delta = top6_total - all_total

    print(f"\n  {'Portfolio Total:':<20} All-hours: ${all_total:>8.2f}   Top-6: ${top6_total:>8.2f}   Delta: ${portfolio_delta:>+8.2f}")

    if portfolio_delta > 0:
        print(f"  >>> PORTFOLIO VERDICT: Top-6 gating is more profitable by ${portfolio_delta:.2f}. Keep per-coin hour whitelists.")
    else:
        print(f"  >>> PORTFOLIO VERDICT: All-hours is more profitable by ${abs(portfolio_delta):.2f}. Consider removing hour gating.")

    # Save
    report = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "coins": COINS,
        "dead_hours_excluded": sorted(list(SESSION_DEAD)),
        "results": results,
        "portfolio": {
            "all_hours_total_pnl": round(all_total, 4),
            "top6_hours_total_pnl": round(top6_total, 4),
            "delta": round(portfolio_delta, 4),
            "verdict": "keep_gate" if portfolio_delta > 0 else "remove_gate",
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  Full report: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
