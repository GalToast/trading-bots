#!/usr/bin/env python3
"""
NOM Strategy Overlap Analysis — Range Breakout vs Momentum.

Do these two strategies fire on the same signals or different ones?
If different, running both doubles the edge on NOM.

Range Breakout: lb=10, TP=10%, SL=1%, MH=24
Momentum: lb=30, TP=8%, SL=8%, MH=12
"""
import json
import os
import sys
import time
import statistics
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "reports" / "nom_strategy_overlap_analysis.json"
COIN = "NOM-USD"
WINDOW_DAYS = 30
FEE_RATE = 0.0040
STARTING_CASH = 48.0


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=granularity)
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands:
                break
            time.sleep(0.1)
        except Exception:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c


def run_strategy(candles, strategy_name, params):
    """Run a strategy, return list of (timestamp, entry_price, exit_price, net_pnl) tuples."""
    entries = []
    cash = STARTING_CASH
    position = None
    history = []
    candle_history = []
    closes = 0
    wins = 0
    losses = 0
    total_fees = 0.0

    for candle in candles:
        ts = int(candle["start"])
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        open_price = float(candle["open"])

        history.append(close)
        candle_history.append(candle)
        if len(history) > 500:
            history = history[-500:]

        # EXIT
        if position:
            position["hold"] += 1
            exit_price = None
            exit_reason = None

            if high >= position["tp"]:
                exit_price = position["tp"]
                exit_reason = "tp"
            elif params.get("sl_pct", 0) > 0 and low <= position["sl"]:
                exit_price = position["sl"]
                exit_reason = "stop"
            elif position["hold"] >= params.get("max_hold", 48):
                exit_price = close
                exit_reason = "timeout"

            if exit_price is not None:
                units = position["units"]
                gross = (exit_price - position["ep"]) * units
                entry_fee = position["entry_fee"]
                exit_fee = exit_price * units * FEE_RATE
                net = gross - entry_fee - exit_fee

                cash += position["q"] + net
                closes += 1
                total_fees += entry_fee + exit_fee

                if net > 0:
                    wins += 1
                else:
                    losses += 1

                entries.append({
                    "entry_ts": position["entry_ts"],
                    "entry_price": position["ep"],
                    "exit_ts": ts,
                    "exit_price": exit_price,
                    "net_pnl": round(net, 4),
                    "hold_bars": position["hold"],
                    "reason": exit_reason,
                })

                position = None

        # ENTRY
        if position is None and cash >= 10.0:
            signal = False

            if strategy_name == "range_breakout":
                # Range breakout: N-bar high breakout
                lb = params.get("lookback", 10)
                if len(candle_history) > lb + 1:
                    recent_high = max(float(c["high"]) for c in candle_history[-(lb+1):-1])
                    if high > recent_high:
                        signal = True

            elif strategy_name == "momentum":
                lb = params.get("lookback", 30)
                if len(candle_history) > lb + 1:
                    recent_high = max(float(c["high"]) for c in candle_history[-(lb+1):-1])
                    if high > recent_high:
                        signal = True

            if signal:
                deploy = cash * 0.95
                entry_price = open_price
                entry_fee = deploy * FEE_RATE
                units = (deploy - entry_fee) / entry_price

                tp = entry_price * (1 + params.get("tp_pct", 0.10))
                sl = entry_price * (1 - params.get("sl_pct", 0)) if params.get("sl_pct", 0) > 0 else 0

                cash -= deploy
                position = {
                    "ep": entry_price,
                    "q": deploy,
                    "units": units,
                    "tp": tp,
                    "sl": sl,
                    "hold": 0,
                    "entry_fee": entry_fee,
                    "entry_ts": ts,
                }

    # Close remaining
    if position:
        last_close = float(candles[-1]["close"])
        units = position["units"]
        gross = (last_close - position["ep"]) * units
        entry_fee = position["entry_fee"]
        exit_fee = last_close * units * FEE_RATE
        net = gross - entry_fee - exit_fee
        cash += position["q"] + net
        closes += 1
        total_fees += entry_fee + exit_fee
        if net > 0:
            wins += 1
        else:
            losses += 1
        entries.append({
            "entry_ts": position["entry_ts"],
            "entry_price": position["ep"],
            "exit_ts": int(candles[-1]["start"]),
            "exit_price": last_close,
            "net_pnl": round(net, 4),
            "hold_bars": position["hold"],
            "reason": "close_at_end",
        })

    total_pnl = cash - STARTING_CASH
    wr = wins / max(1, closes) * 100

    return {
        "entries": entries,
        "total_pnl": round(total_pnl, 2),
        "return_pct": round(total_pnl / STARTING_CASH * 100, 1),
        "win_rate": round(wr, 1),
        "closes": closes,
        "wins": wins,
        "losses": losses,
        "total_fees": round(total_fees, 2),
    }


def analyze_overlap(rb_entries, mom_entries, overlap_bars=1):
    """
    Analyze signal overlap between range breakout and momentum.

    overlap_bars: consider signals within N bars (N*5 minutes) as overlapping
    """
    rb_ts = set(e["entry_ts"] for e in rb_entries)
    mom_ts = set(e["entry_ts"] for e in mom_entries)

    overlap_window = overlap_bars * 300  # 5-min bars in seconds

    overlapping_rb = 0
    overlapping_mom = 0
    rb_only = 0
    mom_only = 0
    both = 0

    for rb_t in rb_ts:
        # Check if any momentum entry is within overlap_window
        is_overlapping = any(abs(rb_t - mom_t) <= overlap_window for mom_t in mom_ts)
        if is_overlapping:
            overlapping_rb += 1
            both += 1
        else:
            rb_only += 1

    for mom_t in mom_ts:
        is_overlapping = any(abs(mom_t - rb_t) <= overlap_window for rb_t in rb_ts)
        if is_overlapping:
            overlapping_mom += 1
        # mom_only counted as len(mom_ts) - overlapping_mom
    mom_only = len(mom_ts) - overlapping_mom

    total_unique = len(rb_ts) + len(mom_ts) - both
    overlap_pct = both / total_unique * 100 if total_unique > 0 else 0

    return {
        "range_breakout_total": len(rb_ts),
        "momentum_total": len(mom_ts),
        "overlapping_signals": both,
        "rb_only_signals": rb_only,
        "mom_only_signals": mom_only,
        "total_unique_signals": total_unique,
        "overlap_pct": round(overlap_pct, 1),
        "overlap_window_bars": overlap_bars,
        "overlap_window_minutes": overlap_bars * 5,
    }


def main():
    client = CoinbaseAdvancedClient()

    now = int(time.time())
    start = now - WINDOW_DAYS * 86400

    print(f"=" * 70, flush=True)
    print(f"NOM STRATEGY OVERLAP ANALYSIS — {WINDOW_DAYS}d", flush=True)
    print(f"=" * 70, flush=True)

    print(f"\nFetching {WINDOW_DAYS}d candles for {COIN}...", flush=True)
    candles = fetch_candles(client, COIN, start, now)
    print(f"  {COIN}: {len(candles)} candles", flush=True)

    if len(candles) < 100:
        print("  ERROR: Insufficient data", flush=True)
        return

    # Run Range Breakout
    print(f"\nRunning Range Breakout (lb=10, TP=10%, SL=1%, MH=24)...", flush=True)
    rb_result = run_strategy(candles, "range_breakout", {
        "lookback": 10, "tp_pct": 0.10, "sl_pct": 0.01, "max_hold": 24
    })
    print(f"  PnL: ${rb_result['total_pnl']:.2f} | WR: {rb_result['win_rate']:.1f}% | "
          f"Trades: {rb_result['closes']}", flush=True)

    # Run Momentum
    print(f"\nRunning Momentum (lb=30, TP=8%, SL=8%, MH=12)...", flush=True)
    mom_result = run_strategy(candles, "momentum", {
        "lookback": 30, "tp_pct": 0.08, "sl_pct": 0.08, "max_hold": 12
    })
    print(f"  PnL: ${mom_result['total_pnl']:.2f} | WR: {mom_result['win_rate']:.1f}% | "
          f"Trades: {mom_result['closes']}", flush=True)

    # Analyze overlap at different windows
    print(f"\n{'='*70}", flush=True)
    print("SIGNAL OVERLAP ANALYSIS", flush=True)
    print(f"{'='*70}", flush=True)

    for bars in [1, 2, 3, 6, 12]:
        overlap = analyze_overlap(rb_result["entries"], mom_result["entries"], overlap_bars=bars)
        print(f"\n  Overlap window: {bars} bar ({bars*5} min):", flush=True)
        print(f"    Range Breakout signals: {overlap['range_breakout_total']}", flush=True)
        print(f"    Momentum signals:       {overlap['momentum_total']}", flush=True)
        print(f"    Overlapping:            {overlap['overlapping_signals']} ({overlap['overlap_pct']:.1f}%)", flush=True)
        print(f"    RB only:                {overlap['rb_only_signals']}", flush=True)
        print(f"    MOM only:               {overlap['mom_only_signals']}", flush=True)
        print(f"    Total unique signals:   {overlap['total_unique_signals']}", flush=True)

    # Combined strategy: run both, shared bankroll
    print(f"\n{'='*70}", flush=True)
    print("COMBINED STRATEGY SIMULATION (both strategies, shared $48)", flush=True)
    print(f"{'='*70}", flush=True)

    # Merge entries, process in timestamp order with shared bankroll
    all_entries = []
    for e in rb_result["entries"]:
        all_entries.append({**e, "strategy": "range_breakout"})
    for e in mom_result["entries"]:
        all_entries.append({**e, "strategy": "momentum"})
    all_entries.sort(key=lambda x: x["entry_ts"])

    # Calculate combined PnL (approximate: just sum individual PnLs since bankroll is shared)
    combined_pnl = rb_result["total_pnl"] + mom_result["total_pnl"]
    combined_trades = rb_result["closes"] + mom_result["closes"]
    avg_wr = (rb_result["wins"] + mom_result["wins"]) / max(1, combined_trades) * 100

    print(f"\n  Combined PnL: ${combined_pnl:.2f}", flush=True)
    print(f"  Combined trades: {combined_trades}", flush=True)
    print(f"  Combined WR: {avg_wr:.1f}%", flush=True)

    # The key question: does running both give more than either alone?
    rb_pnl = rb_result["total_pnl"]
    mom_pnl = mom_result["total_pnl"]

    if combined_pnl > max(rb_pnl, mom_pnl):
        improvement = (combined_pnl - max(rb_pnl, mom_pnl)) / max(1, max(rb_pnl, mom_pnl)) * 100
        print(f"\n  → RUNNING BOTH IS BETTER by {improvement:.1f}% over best single strategy", flush=True)
        if rb_pnl > 0 and mom_pnl > 0:
            print(f"  → Both strategies are independently profitable → strong case for running both", flush=True)
    else:
        print(f"\n  → Running both does NOT beat the best single strategy", flush=True)
        best = "Range Breakout" if rb_pnl > mom_pnl else "Momentum"
        print(f"  → Best single: {best} (${max(rb_pnl, mom_pnl):.2f})", flush=True)

    # Recommendation
    overlap_1bar = analyze_overlap(rb_result["entries"], mom_result["entries"], overlap_bars=1)
    print(f"\n{'='*70}", flush=True)
    print("RECOMMENDATION", flush=True)
    print(f"{'='*70}", flush=True)

    if overlap_1bar["overlap_pct"] < 30:
        print(f"\n  → LOW OVERLAP ({overlap_1bar['overlap_pct']:.1f}%): Run BOTH strategies", flush=True)
        print(f"  → Combined: ${combined_pnl:.2f}/month, {combined_trades} trades", flush=True)
        print(f"  → {overlap_1bar['rb_only_signals']} RB-only signals + {overlap_1bar['mom_only_signals']} MOM-only signals", flush=True)
    elif overlap_1bar["overlap_pct"] < 70:
        print(f"\n  → MODERATE OVERLAP ({overlap_1bar['overlap_pct']:.1f}%): Consider running BOTH", flush=True)
        print(f"  → Diversification benefit but some redundancy", flush=True)
    else:
        print(f"\n  → HIGH OVERLAP ({overlap_1bar['overlap_pct']:.1f}%): Pick the best one", flush=True)
        best = "Range Breakout" if rb_pnl > mom_pnl else "Momentum"
        print(f"  → Best: {best} (${max(rb_pnl, mom_pnl):.2f})", flush=True)

    # Save report
    report = {
        "run_at": utc_now_iso(),
        "coin": COIN,
        "window_days": WINDOW_DAYS,
        "range_breakout": {
            "params": {"lookback": 10, "tp_pct": 0.10, "sl_pct": 0.01, "max_hold": 24},
            "total_pnl": rb_result["total_pnl"],
            "win_rate": rb_result["win_rate"],
            "closes": rb_result["closes"],
            "wins": rb_result["wins"],
            "losses": rb_result["losses"],
        },
        "momentum": {
            "params": {"lookback": 30, "tp_pct": 0.08, "sl_pct": 0.08, "max_hold": 12},
            "total_pnl": mom_result["total_pnl"],
            "win_rate": mom_result["win_rate"],
            "closes": mom_result["closes"],
            "wins": mom_result["wins"],
            "losses": mom_result["losses"],
        },
        "combined": {
            "total_pnl": combined_pnl,
            "total_trades": combined_trades,
            "avg_win_rate": round(avg_wr, 1),
        },
        "overlap_analysis": {
            f"{bars}bar_{bars*5}min": analyze_overlap(rb_result["entries"], mom_result["entries"], overlap_bars=bars)
            for bars in [1, 2, 3, 6, 12]
        },
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)


if __name__ == "__main__":
    main()
