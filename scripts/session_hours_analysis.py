#!/usr/bin/env python3
"""
Session Hours Analysis — Supertrend Signal by UTC Hour

For each coin (RAVE-USD, TRU-USD):
1. Run supertrend backtest (atr_period=10, atr_mult=3.0, max_hold=48)
   - RAVE: tp=10%, sl=5%
   - TRU: tp=10%, sl=3%
2. Record UTC entry hour for every trade
3. Group by hour: PnL, win rate, trade count, avg PnL
4. Identify top profitable hours and toxic hours
5. Compare: trade only top hours vs all active hours (excluding dead hours {0,6,12,19})
"""

import json
import math
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # trading-bots root
sys.path.insert(0, str(BASE / "scripts"))

SESSION_DEAD_HOURS = {0, 6, 12, 19}
FEE_RATE = 0.004
STARTING_CASH = 100.0
ENTRY_SLIP = 0.0008


def compute_atr(candles, period=10):
    """Compute ATR over `period` bars ending at the last candle in the list."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(len(candles) - period, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        c_prev = float(candles[i - 1]["close"])
        trs.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))
    return sum(trs) / period if trs else None


def supertrend_signal(candles_hist, closes, candle, params):
    """
    Supertrend signal logic:
    - Compute ATR over atr_period
    - HL2 = (high + low) / 2
    - Supertrend lower band = HL2 - atr_mult * ATR
    - Signal fires when close > lower band AND close > previous close (rising)
    """
    atr_period = params.get("atr_period", 10)
    atr_mult = params.get("atr_mult", 3.0)

    if len(candles_hist) < atr_period + 2:
        return False

    atr = compute_atr(candles_hist[:-1], atr_period)
    if atr is None:
        return False

    last_full = candles_hist[-2]  # previous completed candle
    hl2 = (float(last_full["high"]) + float(last_full["low"])) / 2
    lower_band = hl2 - atr_mult * atr

    current_close = float(candle["close"])
    prev_close = float(candles_hist[-2]["close"])

    return current_close > lower_band and current_close > prev_close


def backtest_with_hours(candles, entry_fn, params, fee_rate=FEE_RATE,
                        starting_cash=STARTING_CASH, entry_slip=ENTRY_SLIP,
                        allow_dead_hours=False):
    """Backtest that records per-trade entry hour and returns hourly breakdown."""
    cash = starting_cash
    pos = None
    trades = []
    closes_history = []
    candles_history = []
    hourly = {}  # hour -> list of {pnl, win}

    tp_pct = params.get("tp_pct", 10)
    sl_pct = params.get("sl_pct", 5)
    max_hold = params.get("max_hold", 48)

    for i in range(len(candles)):
        c = candles[i]
        close = float(c["close"])
        high = float(c["high"])
        low = float(c["low"])
        candle_open = float(c["open"])

        closes_history.append(close)
        candles_history.append(dict(c))
        if len(closes_history) > 500:
            closes_history = closes_history[-500:]
            candles_history = candles_history[-500:]

        ts = int(c.get("start", c.get("time", 0)))
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour

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
                actual_exit = exit_price * (1 - 0.0)  # no exit slip
                units = pos["units"]
                gross = (actual_exit - pos["ep"]) * units
                entry_fee = pos["entry_fee"]
                exit_fee = actual_exit * units * fee_rate
                net = gross - entry_fee - exit_fee

                cash += pos["q"] + net
                is_win = net > 0
                entry_hour = pos["entry_hour"]

                if entry_hour not in hourly:
                    hourly[entry_hour] = []
                hourly[entry_hour].append({"pnl": net, "win": is_win})

                trades.append({
                    "entry_hour": entry_hour,
                    "pnl": round(net, 4),
                    "win": is_win,
                    "exit_reason": exit_reason,
                })
                pos = None

        # ENTRY
        if pos is None:
            signal = entry_fn(candles_history, closes_history, c, params)
            if signal:
                session_ok = allow_dead_hours or (hour not in SESSION_DEAD_HOURS)
                if not session_ok:
                    continue
                if cash < 10.0:
                    continue

                actual_entry = candle_open * (1 + entry_slip)
                deploy = cash
                entry_fee = deploy * fee_rate
                units = (deploy - entry_fee) / actual_entry
                tp = actual_entry * (1 + tp_pct / 100.0)
                sl = actual_entry * (1 - sl_pct / 100.0) if sl_pct > 0 else 0

                cash -= deploy
                pos = {
                    "ep": actual_entry,
                    "q": deploy,
                    "hold": 0,
                    "tp": tp,
                    "sl": sl,
                    "units": units,
                    "entry_fee": entry_fee,
                    "max_hold": max_hold,
                    "entry_hour": hour,
                }

    # Close open position at last candle
    if pos:
        last_close = float(candles[-1]["close"])
        actual_exit = last_close * (1 - 0.0)
        units = pos["units"]
        gross = (actual_exit - pos["ep"]) * units
        entry_fee = pos["entry_fee"]
        exit_fee = actual_exit * units * fee_rate
        net = gross - entry_fee - exit_fee
        cash += pos["q"] + net
        is_win = net > 0
        entry_hour = pos["entry_hour"]
        if entry_hour not in hourly:
            hourly[entry_hour] = []
        hourly[entry_hour].append({"pnl": net, "win": is_win})
        trades.append({"entry_hour": entry_hour, "pnl": round(net, 4), "win": is_win, "exit_reason": "close"})

    # Aggregate hourly stats
    hourly_stats = {}
    for h in sorted(hourly.keys()):
        entries = hourly[h]
        total_pnl = sum(e["pnl"] for e in entries)
        wins = sum(1 for e in entries if e["win"])
        count = len(entries)
        hourly_stats[h] = {
            "trade_count": count,
            "total_pnl": round(total_pnl, 2),
            "wins": wins,
            "losses": count - wins,
            "win_rate": round(wins / count * 100, 1) if count > 0 else 0,
            "avg_pnl": round(total_pnl / count, 4) if count > 0 else 0,
        }

    total_pnl = cash - starting_cash
    total_trades = len(trades)
    total_wins = sum(1 for t in trades if t["win"])

    return {
        "total_pnl": round(total_pnl, 2),
        "return_pct": round(total_pnl / starting_cash * 100, 2),
        "trades": total_trades,
        "wins": total_wins,
        "losses": total_trades - total_wins,
        "win_rate": round(total_wins / max(total_trades, 1) * 100, 1),
        "hourly": hourly_stats,
        "trades": trades,
    }


def analyze_hourly(result, coin, sl_pct, all_active_hours=None, top_hours=None):
    """Print and return hourly analysis for a coin."""
    hourly = result["hourly"]
    print(f"\n{'='*70}")
    print(f"  {coin} — Hourly Supertrend Breakdown (ATR period=10, mult=3.0, TP=10%, SL={sl_pct}%, max_hold=48)")
    print(f"{'='*70}")
    print(f"{'Hour':>4} | {'Trades':>6} | {'Total PnL':>10} | {'Avg PnL':>9} | {'Win%':>6} | {'W':>3} | {'L':>3}")
    print(f"{'-'*4}-+-{'-'*6}-+-{'-'*10}-+-{'-'*9}-+-{'-'*6}-+-{'-'*3}-+-{'-'*3}")

    for h in range(24):
        if h in hourly:
            s = hourly[h]
            print(f"{h:02d}:00 | {s['trade_count']:6} | ${s['total_pnl']:>8.2f} | ${s['avg_pnl']:>7.4f} | {s['win_rate']:5.1f}% | {s['wins']:3} | {s['losses']:3}")
        else:
            print(f"{h:02d}:00 | {'—':>6} | {'—':>10} | {'—':>9} | {'—':>6} | {'—':>3} | {'—':>3}")

    # Identify top profitable and toxic hours
    profitable_hours = {h: s for h, s in hourly.items() if s["total_pnl"] > 0 and s["trade_count"] >= 2}
    toxic_hours = {h: s for h, s in hourly.items() if s["total_pnl"] < 0 and s["trade_count"] >= 2}
    dead_hours_seen = {h for h in hourly if h in SESSION_DEAD_HOURS}

    sorted_profitable = sorted(profitable_hours.items(), key=lambda x: x[1]["total_pnl"], reverse=True)
    sorted_toxic = sorted(toxic_hours.items(), key=lambda x: x[1]["total_pnl"])

    print(f"\n  Active hours seen (excluding dead {sorted(SESSION_DEAD_HOURS)}): {sorted([h for h in hourly if h not in SESSION_DEAD_HOURS])}")
    if dead_hours_seen:
        print(f"  Dead hours with trades (should be 0): {sorted(dead_hours_seen)}")

    print(f"\n  Top profitable hours (PnL > 0, min 2 trades):")
    for h, s in sorted_profitable[:5]:
        print(f"    {h:02d}:00 — PnL: ${s['total_pnl']:.2f}  WR: {s['win_rate']:.1f}%  Trades: {s['trade_count']}  Avg: ${s['avg_pnl']:.4f}")

    print(f"\n  Toxic hours (PnL < 0, min 2 trades):")
    for h, s in sorted_toxic[:5]:
        print(f"    {h:02d}:00 — PnL: ${s['total_pnl']:.2f}  WR: {s['win_rate']:.1f}%  Trades: {s['trade_count']}  Avg: ${s['avg_pnl']:.4f}")

    # Scenario: only trade top profitable hours
    active_hours = {h for h in hourly if h not in SESSION_DEAD_HOURS}

    # Top 3 profitable active hours
    active_profitable = [(h, s) for h, s in sorted_profitable if h not in SESSION_DEAD_HOURS]
    top3_hours = [h for h, _ in active_profitable[:3]] if active_profitable else []

    # Compare: top hours vs all active hours
    if top3_hours:
        all_active_pnl = sum(hourly[h]["total_pnl"] for h in active_hours)
        all_active_trades = sum(hourly[h]["trade_count"] for h in active_hours)
        top_pnl = sum(hourly[h]["total_pnl"] for h in top3_hours)
        top_trades = sum(hourly[h]["trade_count"] for h in top3_hours)
        top_wr_total = sum(hourly[h]["wins"] for h in top3_hours)

        print(f"\n  SCENARIO: Trade only top {len(top3_hours)} hours {sorted(top3_hours)} vs all active hours")
        print(f"    All active hours ({len(active_hours)} hours, {all_active_trades} trades): Total PnL = ${all_active_pnl:.2f}")
        print(f"    Top {len(top3_hours)} hours only ({top_trades} trades):      Total PnL = ${top_pnl:.2f}")
        print(f"    Trade reduction: {all_active_trades - top_trades} fewer trades ({(1 - top_trades/all_active_trades)*100:.0f}% less)")
        scenario_comparison = {
            "all_active_hours": sorted(active_hours),
            "all_active_trades": all_active_trades,
            "all_active_pnl": round(all_active_pnl, 2),
            "top_hours": sorted(top3_hours),
            "top_hours_trades": top_trades,
            "top_hours_pnl": round(top_pnl, 2),
            "trade_reduction": all_active_trades - top_trades,
            "trade_reduction_pct": round((1 - top_trades / all_active_trades) * 100, 1) if all_active_trades > 0 else 0,
        }
    else:
        scenario_comparison = {"note": "No profitable active hours with min 2 trades"}

    return {
        "coin": coin,
        "atr_period": 10,
        "atr_mult": 3.0,
        "tp_pct": 10,
        "sl_pct": sl_pct,
        "max_hold": 48,
        "overall": {
            "total_pnl": result["total_pnl"],
            "return_pct": result["return_pct"],
            "trades": result["trades"],
            "wins": result["wins"],
            "losses": result["losses"],
            "win_rate": result["win_rate"],
        },
        "hourly": hourly,
        "top_profitable_hours": [{"hour": h, **s} for h, s in sorted_profitable],
        "toxic_hours": [{"hour": h, **s} for h, s in sorted_toxic],
        "dead_hours_seen": sorted(dead_hours_seen),
        "scenario": scenario_comparison,
    }


def main():
    configs = [
        {"coin": "RAVE-USD", "file": "reports/candle_cache/RAVE_USD_FIVE_MINUTE_30d.json", "sl_pct": 5},
        {"coin": "TRU-USD", "file": "reports/candle_cache/TRU_USD_FIVE_MINUTE_30d.json", "sl_pct": 3},
    ]

    results = []

    for cfg in configs:
        coin = cfg["coin"]
        sl_pct = cfg["sl_pct"]
        cache_path = BASE / cfg["file"]

        print(f"\nLoading {coin} from {cache_path}")
        with open(cache_path) as f:
            data = json.load(f)

        candles = data["candles"]
        print(f"  {len(candles)} candles loaded")

        params = {
            "atr_period": 10,
            "atr_mult": 3.0,
            "tp_pct": 10,
            "sl_pct": sl_pct,
            "max_hold": 48,
        }

        result = backtest_with_hours(candles, supertrend_signal, params)
        analysis = analyze_hourly(result, coin, sl_pct)
        results.append(analysis)

    # Save outputs
    for r in results:
        coin_short = r["coin"].replace("-USD", "")
        out_path = BASE / f"reports/session_hours_{coin_short}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(r, f, indent=2)
        print(f"\n  Saved: {out_path}")

    print(f"\n{'='*70}")
    print("ANALYSIS COMPLETE")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
