#!/usr/bin/env python3
"""
Temporal Freshness Audit — Recompute Profitable Hours with Fresh 30d Data

For each fibonacci/momentum coin (NOM, GHST, SUP, A8, CFG):
1. Run hour-by-hour PnL backtest over fresh 30d candle data
2. Identify new top-6 profitable hours vs old whitelist
3. Compute revenue impact: old whitelist vs new whitelist vs all-hours
4. Flag stale whitelists that should be updated or removed

Usage:
    python scripts/temporal_freshness_audit.py

Output:
    reports/temporal_freshness_audit.json
"""
import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "reports" / "temporal_freshness_audit.json"

# --- Coins to analyze ---
COINS = ["NOM-USD", "GHST-USD", "SUP-USD", "A8-USD", "CFG-USD"]

CANDLE_FILES = {
    "NOM-USD":  "NOM_USD_FIVE_MINUTE_30d.json",
    "GHST-USD": "GHST_USD_FIVE_MINUTE_30d.json",
    "SUP-USD":  "SUP_USD_FIVE_MINUTE_30d.json",
    "A8-USD":   "A8_USD_FIVE_MINUTE_30d.json",
    "CFG-USD":  "CFG_USD_FIVE_MINUTE_30d.json",
}

# Strategy configs (matching live runner)
STRATEGY_CONFIG = {
    "NOM-USD":  {"strategy": "fibonacci", "lookback": 20, "tp": 0.08, "sl": 0.03, "max_hold": 24},
    "GHST-USD": {"strategy": "fibonacci", "lookback": 10, "tp": 0.08, "sl": 0.03, "max_hold": 96},
    "SUP-USD":  {"strategy": "fibonacci", "lookback": 20, "tp": 0.08, "sl": 0.03, "max_hold": 24},
    "A8-USD":   {"strategy": "momentum",    "lookback": 10, "tp": 0.15, "sl": 0.00, "max_hold": 48},
    "CFG-USD":  {"strategy": "momentum",    "lookback": 50, "tp": 0.15, "sl": 0.00, "max_hold": 48},
}

# Old whitelists from multi_coin_isolated_runner.py
OLD_WHITELISTS = {
    "NOM-USD":  {1, 4, 5, 8, 10, 11},
    "GHST-USD": {2, 3, 4, 5, 7, 18},
    "SUP-USD":  {5, 15, 16, 18, 20, 23},
    "A8-USD":   {7, 11, 15, 17, 22, 23},
    "CFG-USD":  {1, 4, 8, 10, 13, 20},
}

FEE_RATE = 0.004
DEPLOY_FRACTION = 0.90
MIN_CASH = 2.0  # Use min position size for realistic compounding
SESSION_DEAD = {0, 6, 12, 19}  # Current dead hours gate


def load_candles(coin):
    """Load 30d M5 candle data."""
    fname = CANDLE_FILES[coin]
    path = ROOT / "reports" / "candle_cache" / fname
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
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

    # Check data freshness
    if normalized:
        last_ts = int(normalized[-1].get("time", normalized[-1].get("start", 0)))
        last_dt = datetime.fromtimestamp(last_ts, tz=timezone.utc)
        return normalized, last_dt
    return None, None


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


def signal_momentum(candles_hist, lookback):
    if len(candles_hist) < lookback + 1:
        return False
    recent = candles_hist[-(lookback):-1]
    if not recent:
        return False
    highest = max(float(c["high"]) for c in recent)
    return float(candles_hist[-1]["high"]) > highest


def simulate_trades(candles, coin, hour_filter=None, skip_dead_hours=False):
    """
    Run backtest with optional hour filtering.
    hour_filter: set of allowed UTC hours, or None for all hours.
    skip_dead_hours: if True, skip SESSION_DEAD hours (applied before hour_filter).
    """
    cfg = STRATEGY_CONFIG[coin]
    strat = cfg["strategy"]
    starting_cash = MIN_CASH

    hourly_trades = {h: [] for h in range(24)}
    min_candles = 200

    position = None
    total_signals = 0

    for i in range(min_candles, len(candles)):
        candle = candles[i]
        candle_time = int(candle.get("time", candle.get("start", 0)))
        hour = datetime.fromtimestamp(candle_time, tz=timezone.utc).hour

        closes = [float(c["close"]) for c in candles[max(0, i - 500):i + 1]]
        candles_hist = candles[max(0, i - 500):i + 1]

        high = float(candle["high"])
        low = float(candle["low"])
        open_p = float(candle["open"])

        # Session gate: skip dead hours
        is_dead = hour in SESSION_DEAD
        if skip_dead_hours and is_dead:
            # Still manage open positions during dead hours
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
            continue

        # Hour filter (whitelist check)
        if hour_filter is not None and hour not in hour_filter:
            # Still manage open positions
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
            continue

        # Manage open position
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

        # Try new entry
        if position is None:
            triggered = False
            if strat == "fibonacci":
                triggered = signal_fibonacci(candles_hist, closes, cfg["lookback"])
            elif strat == "momentum":
                triggered = signal_momentum(candles_hist, cfg["lookback"])

            if triggered:
                total_signals += 1
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

    # Close any remaining position at last candle
    if position:
        exit_price = float(candles[-1]["close"])
        pnl = (exit_price - position["entry"]) * position["units"]
        fee = exit_price * position["units"] * FEE_RATE
        net = pnl - fee - position["entry_fee"]
        hourly_trades[position["entry_hour"]].append({"net": net, "reason": "end"})

    return hourly_trades, total_signals


def summarize_hourly(hourly_trades):
    """Convert raw trade list to per-hour summary."""
    summary = {}
    for h in range(24):
        trades = hourly_trades[h]
        if trades:
            total_pnl = sum(t["net"] for t in trades)
            wins = sum(1 for t in trades if t["net"] > 0)
            losses = len(trades) - wins
            wr = wins / len(trades) * 100
            avg_pnl = total_pnl / len(trades)
            summary[h] = {
                "trades": len(trades),
                "total_pnl": round(total_pnl, 4),
                "win_rate": round(wr, 1),
                "avg_pnl": round(avg_pnl, 4),
                "wins": wins,
                "losses": losses,
            }
        else:
            summary[h] = {"trades": 0, "total_pnl": 0, "win_rate": 0, "avg_pnl": 0, "wins": 0, "losses": 0}
    return summary


def top_n_hours(summary, n):
    """Get top N profitable hours by total PnL."""
    profitable = sorted(
        [h for h in summary if summary[h]["total_pnl"] > 0],
        key=lambda h: summary[h]["total_pnl"],
        reverse=True
    )
    return profitable[:n]


def compute_pnl_for_hours(summary, hours):
    """Compute total PnL and trade count for a given set of hours."""
    total_pnl = sum(summary[h]["total_pnl"] for h in hours)
    total_trades = sum(summary[h]["trades"] for h in hours)
    total_wins = sum(summary[h]["wins"] for h in hours)
    return {
        "hours": sorted(hours),
        "total_pnl": round(total_pnl, 4),
        "total_trades": total_trades,
        "total_wins": total_wins,
        "win_rate": round(total_wins / max(1, total_trades) * 100, 1),
    }


def analyze_coin(coin, candles, last_dt):
    """Full analysis for one coin."""
    cfg = STRATEGY_CONFIG[coin]
    old_whitelist = OLD_WHITELISTS.get(coin, set())

    # --- 1. All-hours (no dead hour skip, no whitelist) ---
    all_hourly, all_signals = simulate_trades(candles, coin, hour_filter=None, skip_dead_hours=False)
    all_summary = summarize_hourly(all_hourly)
    all_pnl = sum(all_summary[h]["total_pnl"] for h in range(24))
    all_trades = sum(all_summary[h]["trades"] for h in range(24))

    # --- 2. Dead-hours-gated (current live behavior) ---
    dead_gated_hourly, dead_gated_signals = simulate_trades(candles, coin, hour_filter=None, skip_dead_hours=True)
    dead_gated_summary = summarize_hourly(dead_gated_hourly)
    dead_gated_pnl = sum(dead_gated_summary[h]["total_pnl"] for h in range(24))
    dead_gated_trades = sum(dead_gated_summary[h]["trades"] for h in range(24))

    # --- 3. Old whitelist ---
    old_hourly, _ = simulate_trades(candles, coin, hour_filter=old_whitelist, skip_dead_hours=False)
    old_summary = summarize_hourly(old_hourly)
    old_result = compute_pnl_for_hours(old_summary, old_whitelist)

    # --- 4. New top-6 (from fresh data, all-hours basis) ---
    new_top6 = top_n_hours(all_summary, 6)
    new_top6_result = compute_pnl_for_hours(all_summary, new_top6)

    # --- 5. New top-6 (from fresh data, dead-hours-gated basis) ---
    new_top6_gated = top_n_hours(dead_gated_summary, 6)
    new_top6_gated_result = compute_pnl_for_hours(dead_gated_summary, new_top6_gated)

    # --- Build hourly table ---
    hourly_table = {}
    for h in range(24):
        s = all_summary[h]
        hourly_table[f"{h:02d}:00"] = {
            "hour": h,
            "trades": s["trades"],
            "total_pnl": s["total_pnl"],
            "win_rate": s["win_rate"],
            "avg_pnl": s["avg_pnl"],
            "is_old_whitelist": h in old_whitelist,
            "is_new_top6": h in new_top6,
            "is_new_top6_gated": h in new_top6_gated,
        }

    # --- Staleness analysis ---
    old_vs_new_overlap = len(old_whitelist & set(new_top6))
    old_vs_new_overlap_gated = len(old_whitelist & set(new_top6_gated))
    stale_hours_in_old = old_whitelist - set(new_top6)
    missed_hours_in_old = set(new_top6) - old_whitelist
    stale_hours_in_old_gated = old_whitelist - set(new_top6_gated)
    missed_hours_in_old_gated = set(new_top6_gated) - old_whitelist

    # Revenue impact
    old_vs_new_delta = new_top6_result["total_pnl"] - old_result["total_pnl"]
    all_vs_new_delta = all_pnl - new_top6_result["total_pnl"]
    dead_gated_vs_new = new_top6_gated_result["total_pnl"] - dead_gated_pnl

    # Should we remove the gate?
    all_vs_old = all_pnl - old_result["total_pnl"]
    remove_gate_beneficial = all_pnl > old_result["total_pnl"]

    # Verdict
    if old_vs_new_overlap <= 2:
        verdict = "STALE — whitelist is largely wrong"
    elif stale_hours_in_old and missed_hours_in_old:
        verdict = "MIXED — some hours stale, some profitable hours missed"
    elif abs(old_vs_new_delta) < 5:
        verdict = "OK — old whitelist still near-optimal"
    else:
        verdict = "SUBOPTIMAL — new top-6 would improve PnL"

    result = {
        "coin": coin,
        "strategy": cfg["strategy"],
        "candles_loaded": len(candles),
        "data_ends_utc": last_dt.isoformat(),
        "total_signals_all_hours": all_signals,

        "all_hours": {
            "total_pnl": round(all_pnl, 4),
            "total_trades": all_trades,
        },
        "dead_hours_gated": {
            "total_pnl": round(dead_gated_pnl, 4),
            "total_trades": dead_gated_trades,
        },
        "old_whitelist": {
            "hours": sorted(old_whitelist),
            **old_result,
        },
        "new_top6_all_hours": new_top6_result,
        "new_top6_dead_gated": new_top6_gated_result,

        "hourly_table": hourly_table,

        "staleness": {
            "old_vs_new_overlap": old_vs_new_overlap,
            "old_vs_new_overlap_gated": old_vs_new_overlap_gated,
            "stale_hours_in_old": sorted(stale_hours_in_old),
            "missed_hours_in_old": sorted(missed_hours_in_old),
            "stale_hours_in_old_gated": sorted(stale_hours_in_old_gated),
            "missed_hours_in_old_gated": sorted(missed_hours_in_old_gated),
        },
        "revenue_impact": {
            "new_top6_vs_old_delta": round(old_vs_new_delta, 4),
            "all_hours_vs_new_top6_delta": round(all_vs_new_delta, 4),
            "all_hours_vs_old_delta": round(all_vs_old, 4),
            "dead_gated_vs_new_top6_gated_delta": round(dead_gated_vs_new, 4),
            "remove_gate_beneficial": remove_gate_beneficial,
        },
        "verdict": verdict,
    }

    return result


def print_coin_report(result):
    """Pretty-print analysis for one coin."""
    coin = result["coin"]
    strat = result["strategy"]
    print(f"\n{'=' * 70}")
    print(f"  {coin} ({strat})")
    print(f"  Data ends: {result['data_ends_utc']}")
    print(f"  Verdict: {result['verdict']}")
    print(f"{'=' * 70}")

    # Hourly table
    print(f"\n  {'Hour':>6} | {'Trades':>6} | {'PnL':>10} | {'WR':>6} | Old | New | New*")
    print(f"  {'─' * 56}")
    for h in range(24):
        hs = result["hourly_table"][f"{h:02d}:00"]
        if hs["trades"] == 0:
            continue
        marker = ""
        if hs["total_pnl"] > 0:
            marker = " +"
        old = "Y" if hs["is_old_whitelist"] else " "
        new = "Y" if hs["is_new_top6"] else " "
        new_g = "Y" if hs["is_new_top6_gated"] else " "
        print(f"  {h:02d}:00 | {hs['trades']:>6} | ${hs['total_pnl']:>8.2f}{marker} | {hs['win_rate']:>5.1f}% |  {old}  |  {new}  |  {new_g}")

    # Scenarios
    print(f"\n  Scenario comparison:")
    ah = result["all_hours"]
    dg = result["dead_hours_gated"]
    ow = result["old_whitelist"]
    n6 = result["new_top6_all_hours"]
    n6g = result["new_top6_dead_gated"]
    ri = result["revenue_impact"]

    print(f"    All-hours:              {ah['total_trades']:>4} trades  ${ah['total_pnl']:>10.2f}")
    print(f"    Dead-hours gated:       {dg['total_trades']:>4} trades  ${dg['total_pnl']:>10.2f}")
    print(f"    Old whitelist:          {ow['total_trades']:>4} trades  ${ow['total_pnl']:>10.2f}  hours={ow['hours']}")
    print(f"    New top-6 (all-hours):  {n6['total_trades']:>4} trades  ${n6['total_pnl']:>10.2f}  hours={n6['hours']}")
    print(f"    New top-6 (dead-gated): {n6g['total_trades']:>4} trades  ${n6g['total_pnl']:>10.2f}  hours={n6g['hours']}")

    print(f"\n  Revenue deltas:")
    print(f"    New top-6 vs old whitelist:     ${ri['new_top6_vs_old_delta']:+.2f}")
    print(f"    All-hours vs new top-6:         ${ri['all_hours_vs_new_top6_delta']:+.2f}")
    print(f"    All-hours vs old whitelist:     ${ri['all_hours_vs_old_delta']:+.2f}")
    print(f"    Remove gate beneficial?         {ri['remove_gate_beneficial']}")

    # Staleness
    st = result["staleness"]
    print(f"\n  Staleness:")
    print(f"    Old vs new overlap:             {st['old_vs_new_overlap']}/6 hours")
    if st["stale_hours_in_old"]:
        print(f"    Stale hours in old whitelist:   {st['stale_hours_in_old']}")
    if st["missed_hours_in_old"]:
        print(f"    Missed profitable hours:        {st['missed_hours_in_old']}")


def main():
    print("=" * 70)
    print("  Temporal Freshness Audit — Profitable Hours Recomputation")
    print(f"  Run at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)

    all_results = {}

    for coin in COINS:
        loaded = load_candles(coin)
        if loaded[0] is None:
            print(f"\n  [SKIP] {coin}: no candle data found")
            continue
        candles, last_dt = loaded
        print(f"\n  [LOAD] {coin}: {len(candles)} candles, ends {last_dt.isoformat()}")

        result = analyze_coin(coin, candles, last_dt)
        all_results[coin] = result
        print_coin_report(result)

    # Portfolio summary
    print(f"\n{'=' * 70}")
    print(f"  PORTFOLIO SUMMARY")
    print(f"{'=' * 70}")
    print(f"\n  {'Coin':<12} {'Verdict':<35} {'Old PnL':>10} {'New PnL':>10} {'Delta':>10}")
    print(f"  {'─' * 77}")
    for coin in COINS:
        if coin not in all_results:
            continue
        r = all_results[coin]
        old_pnl = r["old_whitelist"]["total_pnl"]
        new_pnl = r["new_top6_all_hours"]["total_pnl"]
        delta = new_pnl - old_pnl
        print(f"  {coin:<12} {r['verdict']:<35} ${old_pnl:>8.2f} ${new_pnl:>8.2f} ${delta:>+8.2f}")

    # Recommendations
    print(f"\n{'=' * 70}")
    print(f"  RECOMMENDATIONS")
    print(f"{'=' * 70}")

    recommendations = {}
    for coin in COINS:
        if coin not in all_results:
            continue
        r = all_results[coin]
        ri = r["revenue_impact"]
        st = r["staleness"]

        if ri["remove_gate_beneficial"] and ri["all_hours_vs_old_delta"] > 10:
            rec = f"REMOVE gate — all-hours beats old whitelist by ${ri['all_hours_vs_old_delta']:.2f}"
            recommendations[coin] = {"action": "REMOVE_GATE", "reason": rec}
        elif st["old_vs_new_overlap"] <= 2:
            new_hours = r["new_top6_all_hours"]["hours"]
            rec = f"UPDATE whitelist — only {st['old_vs_new_overlap']}/6 overlap. New hours: {new_hours}"
            recommendations[coin] = {"action": "UPDATE_WHITELIST", "new_hours": new_hours, "reason": rec}
        elif abs(ri["new_top6_vs_old_delta"]) < 5:
            rec = "KEEP — old whitelist still near-optimal"
            recommendations[coin] = {"action": "KEEP", "reason": rec}
        else:
            new_hours = r["new_top6_all_hours"]["hours"]
            rec = f"CONSIDER UPDATE — new top-6 would add ${ri['new_top6_vs_old_delta']:+.2f}. Hours: {new_hours}"
            recommendations[coin] = {"action": "CONSIDER_UPDATE", "new_hours": new_hours, "reason": rec}

        print(f"  {coin}: {rec}")

    # Save full report
    report = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "coins_analyzed": list(all_results.keys()),
        "old_whitelists": {k: sorted(list(v)) for k, v in OLD_WHITELISTS.items()},
        "dead_hours": sorted(SESSION_DEAD),
        "results": all_results,
        "recommendations": recommendations,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n  Full report saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
