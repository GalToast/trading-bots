#!/usr/bin/env python3
"""
Per-Coin Session Hour Optimization — Consolidated Analysis

Aggregates hourly PnL analysis across all 9 coins and finds optimal
per-coin hour whitelists.

Results from individual analyses:
- NOM: scripts/nom_session_analysis.py → reports/nom_session_analysis.json
- RAVE: subagent analysis → saved below
- TRU: subagent analysis → saved below
- Others: computed inline

Usage:
    python scripts/session_hour_consolidation.py
"""
import json
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "reports" / "session_hour_consolidation.json"

COINS = ["NOM-USD", "GHST-USD", "SUP-USD", "RAVE-USD", "TRU-USD", "BAL-USD", "IOTX-USD", "A8-USD", "CFG-USD"]

CANDLE_FILES = {
    "NOM-USD": "NOM_USD_FIVE_MINUTE_30d.json",
    "GHST-USD": "GHST_USD_FIVE_MINUTE_30d.json",
    "SUP-USD": "SUP_USD_FIVE_MINUTE_30d.json",
    "RAVE-USD": "RAVE_USD_FIVE_MINUTE_30d.json",
    "TRU-USD": "TRU_USD_FIVE_MINUTE_30d.json",
    "BAL-USD": "BAL_USD_FIVE_MINUTE_30d.json",
    "IOTX-USD": "IOTX_USD_FIVE_MINUTE_30d.json",
    "A8-USD": "A8_USD_FIVE_MINUTE_30d.json",
    "CFG-USD": "CFG_USD_FIVE_MINUTE_30d.json",
}

STRATEGY_CONFIG = {
    "NOM-USD":  {"strategy": "fibonacci", "lookback": 20, "tp": 0.08, "sl": 0.03, "max_hold": 24},
    "GHST-USD": {"strategy": "fibonacci", "lookback": 10, "tp": 0.08, "sl": 0.03, "max_hold": 96},
    "SUP-USD":  {"strategy": "fibonacci", "lookback": 20, "tp": 0.08, "sl": 0.03, "max_hold": 24},
    "RAVE-USD": {"strategy": "supertrend", "atr_period": 10, "atr_mult": 3.0, "tp": 0.10, "sl": 0.05, "max_hold": 48},
    "TRU-USD":  {"strategy": "supertrend", "atr_period": 10, "atr_mult": 3.0, "tp": 0.10, "sl": 0.03, "max_hold": 48},
    "BAL-USD":  {"strategy": "supertrend", "atr_period": 10, "atr_mult": 3.0, "tp": 0.10, "sl": 0.05, "max_hold": 96},
    "IOTX-USD": {"strategy": "supertrend", "atr_period": 10, "atr_mult": 3.0, "tp": 0.10, "sl": 0.03, "max_hold": 48},
    "A8-USD":   {"strategy": "momentum", "lookback": 10, "tp": 0.15, "sl": 0.00, "max_hold": 48},
    "CFG-USD":  {"strategy": "momentum", "lookback": 50, "tp": 0.15, "sl": 0.00, "max_hold": 48},
}

FEE_RATE = 0.004
DEPLOY_FRACTION = 0.90
MIN_CASH = 5.33
SESSION_DEAD = {0, 6, 12, 19}


def load_candles(coin):
    fname = CANDLE_FILES[coin]
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
    # Volume confirmation
    if len(candles_hist) >= 20:
        vols = [float(c.get("volume", 0)) for c in candles_hist[-20:-1]]
        avg_v = sum(vols) / len(vols)
        if avg_v > 0 and float(candles_hist[-1].get("volume", 0)) < 0.8 * avg_v:
            return False
    # Momentum: 2 of last 3 green
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


def analyze_coin_hourly(coin, candles, starting_cash=MIN_CASH):
    cfg = STRATEGY_CONFIG[coin]
    strat = cfg["strategy"]
    
    hourly_trades = {h: [] for h in range(24)}
    
    min_candles = 200  # Need enough for any lookback + ATR
    
    position = None
    signals = 0
    
    for i in range(min_candles, len(candles)):
        candle = candles[i]
        candle_time = int(candle.get("time", candle.get("start", 0)))
        hour = datetime.fromtimestamp(candle_time, tz=timezone.utc).hour
        
        closes = [float(c["close"]) for c in candles[max(0, i - 500):i + 1]]
        candles_hist = candles[max(0, i - 500):i + 1]
        
        high = float(candle["high"])
        low = float(candle["low"])
        open_p = float(candle["open"])
        
        # Session gate
        if hour in SESSION_DEAD:
            if position:
                position["hold"] += 1
                if high >= position["tp"]:
                    pnl = (position["tp"] - position["entry"]) * position["units"]
                    fee = position["tp"] * position["units"] * FEE_RATE
                    net = pnl - fee - position["entry_fee"]
                    hourly_trades[position["entry_hour"]].append({"net": net, "reason": "tp"})
                    position = None
                elif low <= position["sl"] and position["sl"] > 0:
                    pnl = (position["sl"] - position["entry"]) * position["units"]
                    fee = position["sl"] * position["units"] * FEE_RATE
                    net = pnl - fee - position["entry_fee"]
                    hourly_trades[position["entry_hour"]].append({"net": net, "reason": "sl"})
                    position = None
                elif position["hold"] >= cfg["max_hold"]:
                    pnl = (open_p - position["entry"]) * position["units"]
                    fee = open_p * position["units"] * FEE_RATE
                    net = pnl - fee - position["entry_fee"]
                    hourly_trades[position["entry_hour"]].append({"net": net, "reason": "timeout"})
                    position = None
            continue
        
        if position:
            position["hold"] += 1
            if high >= position["tp"]:
                pnl = (position["tp"] - position["entry"]) * position["units"]
                fee = position["tp"] * position["units"] * FEE_RATE
                net = pnl - fee - position["entry_fee"]
                hourly_trades[position["entry_hour"]].append({"net": net, "reason": "tp"})
                position = None
            elif position["sl"] > 0 and low <= position["sl"]:
                pnl = (position["sl"] - position["entry"]) * position["units"]
                fee = position["sl"] * position["units"] * FEE_RATE
                net = pnl - fee - position["entry_fee"]
                hourly_trades[position["entry_hour"]].append({"net": net, "reason": "sl"})
                position = None
            elif position["hold"] >= cfg["max_hold"]:
                pnl = (open_p - position["entry"]) * position["units"]
                fee = open_p * position["units"] * FEE_RATE
                net = pnl - fee - position["entry_fee"]
                hourly_trades[position["entry_hour"]].append({"net": net, "reason": "timeout"})
                position = None
        
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
                    "entry_hour": hour,
                }
    
    if position:
        exit_price = float(candles[-1]["close"])
        pnl = (exit_price - position["entry"]) * position["units"]
        fee = exit_price * position["units"] * FEE_RATE
        net = pnl - fee - position["entry_fee"]
        hourly_trades[position["entry_hour"]].append({"net": net, "reason": "end"})
    
    # Summarize by hour
    hourly_summary = {}
    for h in range(24):
        trades = hourly_trades[h]
        if trades:
            total_pnl = sum(t["net"] for t in trades)
            wins = sum(1 for t in trades if t["net"] > 0)
            losses = len(trades) - wins
            wr = wins / len(trades) * 100
            avg_pnl = total_pnl / len(trades)
            hourly_summary[h] = {
                "trades": len(trades),
                "total_pnl": round(total_pnl, 4),
                "win_rate": round(wr, 1),
                "avg_pnl": round(avg_pnl, 4),
                "wins": wins,
                "losses": losses,
            }
        else:
            hourly_summary[h] = {"trades": 0, "total_pnl": 0, "win_rate": 0, "avg_pnl": 0, "wins": 0, "losses": 0}
    
    total_trades = sum(len(t) for t in hourly_trades.values())
    total_pnl = sum(sum(t["net"] for t in trades) for trades in hourly_trades.values())
    total_wins = sum(sum(1 for t in trades if t["net"] > 0) for trades in hourly_trades.values())
    
    # Find top profitable hours
    profitable_hours = sorted(
        [h for h in hourly_summary if hourly_summary[h]["total_pnl"] > 0],
        key=lambda h: hourly_summary[h]["total_pnl"],
        reverse=True
    )
    
    toxic_hours_list = sorted(
        [h for h in hourly_summary if hourly_summary[h]["total_pnl"] < 0],
        key=lambda h: hourly_summary[h]["total_pnl"]
    )
    
    # Test top-N scenarios
    best_result = None
    for n in [3, 4, 5, 6]:
        top_n = profitable_hours[:n]
        if not top_n:
            continue
        top_pnl = sum(hourly_summary[h]["total_pnl"] for h in top_n)
        top_trades = sum(hourly_summary[h]["trades"] for h in top_n)
        top_wins = sum(hourly_summary[h]["wins"] for h in top_n)
        top_wr = top_wins / top_trades * 100 if top_trades > 0 else 0
        
        if best_result is None or top_pnl > best_result["top_pnl"]:
            best_result = {
                "n": n,
                "hours": top_n,
                "top_pnl": round(top_pnl, 4),
                "top_trades": top_trades,
                "top_wr": round(top_wr, 1),
                "trade_reduction_pct": round((1 - top_trades / total_trades) * 100, 1) if total_trades > 0 else 0,
                "pnl_capture_pct": round(top_pnl / total_pnl * 100, 1) if total_pnl != 0 else 0,
            }
    
    return {
        "strategy": strat,
        "candles_loaded": len(candles),
        "total_trades": total_trades,
        "total_pnl": round(total_pnl, 4),
        "total_wins": total_wins,
        "win_rate": round(total_wins / max(1, total_trades) * 100, 1),
        "signals": signals,
        "hourly_summary": hourly_summary,
        "profitable_hours": profitable_hours,
        "toxic_hours": toxic_hours_list,
        "best_filter": best_result,
    }


def main():
    print("=" * 70)
    print("  Per-Coin Session Hour Optimization — Consolidated Analysis")
    print("=" * 70)
    
    all_results = {}
    
    for coin in COINS:
        print(f"\n{'─' * 50}")
        print(f"  {coin} ({STRATEGY_CONFIG[coin]['strategy']})")
        print(f"{'─' * 50}")
        
        candles = load_candles(coin)
        if not candles:
            print(f"  [ERROR] No candle data for {coin}")
            continue
        
        result = analyze_coin_hourly(coin, candles)
        all_results[coin] = result
        
        # Print hourly table
        print(f"  {'Hour':>6} | {'Trades':>6} | {'Total PnL':>10} | {'WR':>7} | {'Avg PnL':>9}")
        print(f"  {'─' * 48}")
        for h in range(24):
            hs = result["hourly_summary"][h]
            marker = ""
            if hs["trades"] > 0:
                if hs["total_pnl"] > 0:
                    marker = " ✅"
                else:
                    marker = " 🚨"
            print(f"  {h:02d}:00 | {hs['trades']:>6} | ${hs['total_pnl']:>8.2f} | {hs['win_rate']:>5.1f}% | ${hs['avg_pnl']:>7.2f}{marker}")
        
        # Print filter results
        bf = result.get("best_filter")
        if bf:
            print(f"\n  Best filter (top {bf['n']} hours): {', '.join(f'{h:02d}:00' for h in bf['hours'])}")
            print(f"    Trades: {bf['top_trades']} ({bf['trade_reduction_pct']}% reduction)")
            print(f"    PnL: ${bf['top_pnl']:.2f} ({bf['pnl_capture_pct']}% of total)")
            print(f"    WR: {bf['top_wr']:.1f}%")
    
    # Portfolio-level summary
    print(f"\n{'=' * 70}")
    print(f"  PORTFOLIO-LEVEL SUMMARY")
    print(f"{'=' * 70}")
    
    print(f"\n  {'Coin':<12} {'Strat':<12} {'Total PnL':>10} {'WR':>6} {'Top Hours':>20} {'Top PnL':>10} {'Capture':>8}")
    print(f"  {'─' * 70}")
    
    for coin in COINS:
        if coin not in all_results:
            continue
        r = all_results[coin]
        bf = r.get("best_filter", {})
        top_hours = ', '.join(f'{h:02d}' for h in bf.get("hours", []))
        print(f"  {coin:<12} {r['strategy']:<12} ${r['total_pnl']:>8.2f} {r['win_rate']:>5.1f}% {top_hours:>20} ${bf.get('top_pnl', 0):>8.2f} {bf.get('pnl_capture_pct', 0):>6.1f}%")
    
    # Compute recommended per-coin hour whitelists
    print(f"\n{'=' * 70}")
    print(f"  RECOMMENDED PER-COIN HOUR WHITELISTS")
    print(f"{'=' * 70}")
    
    whitelists = {}
    for coin in COINS:
        if coin not in all_results:
            continue
        r = all_results[coin]
        bf = r.get("best_filter")
        if bf:
            whitelists[coin] = bf["hours"]
            print(f"  {coin}: {bf['hours']}")
    
    # Save results
    report = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "coins_analyzed": list(all_results.keys()),
        "results": all_results,
        "recommended_whitelists": whitelists,
    }
    
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  Full report: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
