#!/usr/bin/env python3
"""
Microstructure Regime Characterization — Standalone analysis of predatory events.

Analyzes 2,858 predatory shadow monitor events (13:52-15:35 UTC) to characterize
the live market regime without needing trade correlation.

Input: predatory_shadow_monitor_events.jsonl
Output: reports/microstructure_regime_report.json
"""
import json
import statistics
from datetime import datetime
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
EVENTS_PATH = ROOT / "reports" / "predatory_shadow_monitor_events.jsonl"
SIGNALS_PATH = ROOT / "reports" / "predatory_signals.jsonl"


def parse_ts(ts_str):
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return None


def load_events():
    events = []
    with open(EVENTS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                evt = json.loads(line)
                evt["_ts"] = parse_ts(evt.get("ts_utc"))
                events.append(evt)
    return events


def load_signals():
    signals = []
    with open(SIGNALS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                sig = json.loads(line)
                sig["_ts"] = parse_ts(sig.get("ts_utc"))
                signals.append(sig)
    return signals


def main():
    print("Loading events...", flush=True)
    events = load_events()
    print(f"  {len(events)} events loaded", flush=True)

    print("Loading signals...", flush=True)
    signals = load_signals()
    print(f"  {len(signals)} signals loaded", flush=True)

    # Filter to RAVE-USD only
    rave_events = [e for e in events if e.get("product_id") == "RAVE-USD"]
    rave_signals = [s for s in signals if s.get("product_id") == "RAVE-USD"]

    print(f"\n  RAVE-USD: {len(rave_events)} events, {len(rave_signals)} signals", flush=True)

    # Time range
    ts_list = [e["_ts"] for e in rave_events if e.get("_ts")]
    if ts_list:
        time_span_min = (max(ts_list) - min(ts_list)).total_seconds() / 60
        print(f"  Time span: {time_span_min:.1f} minutes", flush=True)

    # ===== 1. EVENT FREQUENCY DISTRIBUTION =====
    print("\n" + "=" * 70, flush=True)
    print("1. EVENT FREQUENCY DISTRIBUTION", flush=True)
    print("=" * 70, flush=True)

    action_counts = Counter(e.get("action", "unknown") for e in rave_events)
    print(f"\n{'Action':<45} {'Count':>6} {'Pct':>7} {'Per Hour':>9}", flush=True)
    print(f"{'-'*45}-{'-'*6}-{'-'*7}-{'-'*9}", flush=True)

    hours = time_span_min / 60 if time_span_min else 1
    for action, count in action_counts.most_common():
        pct = count / len(rave_events) * 100
        per_hour = count / hours
        print(f"{action:<45} {count:>6} {pct:>6.1f}% {per_hour:>8.1f}", flush=True)

    # ===== 2. DIRECTIONAL BIAS =====
    print(f"\n{'='*70}", flush=True)
    print("2. DIRECTIONAL BIAS (Sell vs Buy Pressure)", flush=True)
    print(f"{'='*70}", flush=True)

    sell_events = [e for e in rave_events if "sell" in e.get("action", "").lower()]
    buy_events = [e for e in rave_events if "buy" in e.get("action", "").lower()]
    neutral_events = [e for e in rave_events if "sell" not in e.get("action", "").lower()
                      and "buy" not in e.get("action", "").lower()]

    print(f"\n  Sell-pressure events: {len(sell_events)} ({len(sell_events)/len(rave_events)*100:.1f}%)", flush=True)
    print(f"  Buy-pressure events:  {len(buy_events)} ({len(buy_events)/len(rave_events)*100:.1f}%)", flush=True)
    print(f"  Neutral events:       {len(neutral_events)} ({len(neutral_events)/len(rave_events)*100:.1f}%)", flush=True)

    net_directional = len(buy_events) - len(sell_events)
    bias = "BULLISH" if net_directional > 0 else "BEARISH" if net_directional < 0 else "NEUTRAL"
    print(f"\n  Net directional bias: {bias} ({net_directional:+d} events)", flush=True)

    # ===== 3. DELTA BPS REALIZATION =====
    print(f"\n{'='*70}", flush=True)
    print("3. DELTA BPS REALIZATION (Do events move price?)", flush=True)
    print(f"{'='*70}", flush=True)

    # Events with delta_bps
    delta_events = [e for e in rave_events if e.get("delta_bps") is not None]

    sell_deltas = []
    buy_deltas = []
    if delta_events:
        sell_deltas = [e["delta_bps"] for e in sell_events if e.get("delta_bps") is not None]
        buy_deltas = [e["delta_bps"] for e in buy_events if e.get("delta_bps") is not None]

        print(f"\n  Iceberg Sells: n={len(sell_deltas)}", flush=True)
        if sell_deltas:
            print(f"    Mean delta: {statistics.mean(sell_deltas):.2f} bps", flush=True)
            print(f"    Median delta: {statistics.median(sell_deltas):.2f} bps", flush=True)
            print(f"    Std dev: {statistics.stdev(sell_deltas):.2f} bps" if len(sell_deltas) > 1 else "", flush=True)
            negative_pct = sum(1 for d in sell_deltas if d < 0) / len(sell_deltas) * 100
            print(f"    Negative (price down): {negative_pct:.1f}%", flush=True)

        print(f"\n  Iceberg Buys: n={len(buy_deltas)}", flush=True)
        if buy_deltas:
            print(f"    Mean delta: {statistics.mean(buy_deltas):.2f} bps", flush=True)
            print(f"    Median delta: {statistics.median(buy_deltas):.2f} bps", flush=True)
            print(f"    Std dev: {statistics.stdev(buy_deltas):.2f} bps" if len(buy_deltas) > 1 else "", flush=True)
            positive_pct = sum(1 for d in buy_deltas if d > 0) / len(buy_deltas) * 100
            print(f"    Positive (price up): {positive_pct:.1f}%", flush=True)

    # ===== 4. MAGNETIC WALL ANALYSIS =====
    print(f"\n{'='*70}", flush=True)
    print("4. MAGNETIC WALL ANALYSIS", flush=True)
    print(f"{'='*70}", flush=True)

    wall_events = [e for e in rave_events if "magnetic" in e.get("action", "").lower()]
    wall_signals = [s for s in rave_signals if s.get("magnetic_wall")]

    print(f"\n  Magnetic wall touch events: {len(wall_events)}", flush=True)
    print(f"  Signals with magnetic wall: {len(wall_signals)}", flush=True)

    walls = []
    prices = []
    if wall_signals:
        walls = [s["magnetic_wall"] for s in wall_signals if s.get("magnetic_wall")]
        prices = [s["price"] for s in wall_signals if s.get("price") and s.get("magnetic_wall")]

        if walls and prices:
            print(f"\n  Magnetic wall level: ${statistics.mean(walls):.4f}", flush=True)
            print(f"  Price range during window: ${min(prices):.4f} - ${max(prices):.4f}", flush=True)

            # How often is price near the wall?
            near_wall = [s for s in wall_signals if s.get("price") and s.get("magnetic_wall")
                         and abs(s["price"] - s["magnetic_wall"]) / s["magnetic_wall"] < 0.02]
            print(f"  Signals within 2% of wall: {len(near_wall)} ({len(near_wall)/len(wall_signals)*100:.1f}%)", flush=True)

            # Price distribution relative to wall
            above_wall = [s for s in wall_signals if s.get("price") and s.get("magnetic_wall")
                          and s["price"] > s["magnetic_wall"]]
            below_wall = [s for s in wall_signals if s.get("price") and s.get("magnetic_wall")
                          and s["price"] < s["magnetic_wall"]]
            print(f"  Price above wall: {len(above_wall)} ({len(above_wall)/len(wall_signals)*100:.1f}%)", flush=True)
            print(f"  Price below wall: {len(below_wall)} ({len(below_wall)/len(wall_signals)*100:.1f}%)", flush=True)

    # ===== 5. BTC DELTA CORRELATION =====
    print(f"\n{'='*70}", flush=True)
    print("5. BTC DELTA CORRELATION", flush=True)
    print(f"{'='*70}", flush=True)

    btc_deltas = [e.get("btc_delta_usd") for e in rave_events if e.get("btc_delta_usd") is not None]
    pos_btc = 0
    neg_btc = 0
    sell_btc = []
    buy_btc = []
    if btc_deltas:
        print(f"\n  BTC delta during RAVE events:", flush=True)
        print(f"    Mean: ${statistics.mean(btc_deltas):.2f}", flush=True)
        print(f"    Median: ${statistics.median(btc_deltas):.2f}", flush=True)

        pos_btc = sum(1 for d in btc_deltas if d > 0)
        neg_btc = sum(1 for d in btc_deltas if d < 0)
        print(f"    BTC up: {pos_btc} ({pos_btc/len(btc_deltas)*100:.1f}%)", flush=True)
        print(f"    BTC down: {neg_btc} ({neg_btc/len(btc_deltas)*100:.1f}%)", flush=True)

        # BTC delta during sell vs buy events
        sell_btc = [e.get("btc_delta_usd", 0) for e in sell_events if e.get("btc_delta_usd") is not None]
        buy_btc = [e.get("btc_delta_usd", 0) for e in buy_events if e.get("btc_delta_usd") is not None]

        if sell_btc and buy_btc:
            print(f"\n  BTC delta during SELL events: ${statistics.mean(sell_btc):.2f}", flush=True)
            print(f"  BTC delta during BUY events:  ${statistics.mean(buy_btc):.2f}", flush=True)

    # ===== 6. TEMPORAL CLUSTERING =====
    print(f"\n{'='*70}", flush=True)
    print("6. TEMPORAL CLUSTERING (Event Bursts)", flush=True)
    print(f"{'='*70}", flush=True)

    intervals = []
    bursts = []
    if ts_list:
        ts_sorted = sorted(ts_list)
        intervals = [(ts_sorted[i+1] - ts_sorted[i]).total_seconds()
                     for i in range(len(ts_sorted)-1)]

        if intervals:
            print(f"\n  Inter-event intervals:", flush=True)
            print(f"    Mean: {statistics.mean(intervals):.1f}s", flush=True)
            print(f"    Median: {statistics.median(intervals):.1f}s", flush=True)
            print(f"    Min: {min(intervals):.1f}s", flush=True)
            print(f"    Max: {max(intervals):.1f}s", flush=True)

            # Burst detection: events within 5 seconds
            bursts = [iv for iv in intervals if iv < 5]
            print(f"\n  Burst events (<5s apart): {len(bursts)} ({len(bursts)/len(intervals)*100:.1f}%)", flush=True)

            # Warp-specific bursts
            warp_events = [e for e in rave_events if "warp" in e.get("action", "").lower()]
            if warp_events:
                warp_ts = sorted([e["_ts"] for e in warp_events if e.get("_ts")])
                warp_intervals = [(warp_ts[i+1] - warp_ts[i]).total_seconds()
                                  for i in range(len(warp_ts)-1)]
                if warp_intervals:
                    print(f"\n  Warp-specific intervals:", flush=True)
                    print(f"    Mean: {statistics.mean(warp_intervals):.1f}s", flush=True)
                    print(f"    Min: {min(warp_intervals):.1f}s", flush=True)
                    rapid_warps = [iv for iv in warp_intervals if iv < 10]
                    print(f"    Rapid warps (<10s): {len(rapid_warps)}", flush=True)

    # ===== 7. REGIME DIAGNOSIS =====
    print(f"\n{'='*70}", flush=True)
    print("7. REGIME DIAGNOSIS", flush=True)
    print(f"{'='*70}", flush=True)

    # High event frequency = active/choppy regime
    # Dominant sell pressure = bearish trend
    # Magnetic wall proximity = ranging/magnetic regime
    # High BTC correlation = follower regime

    sell_ratio = len(sell_events) / len(rave_events) * 100
    buy_ratio = len(buy_events) / len(rave_events) * 100
    wall_pct = len(wall_signals) / len(rave_signals) * 100 if rave_signals else 0

    print(f"\n  Microstructure regime indicators:", flush=True)
    print(f"    Sell pressure: {sell_ratio:.1f}% of events", flush=True)
    print(f"    Buy pressure: {buy_ratio:.1f}% of events", flush=True)
    print(f"    Magnetic wall presence: {wall_pct:.1f}% of signals", flush=True)
    print(f"    Event rate: {len(rave_events)/hours:.1f} events/hour", flush=True)

    # Diagnosis
    if sell_ratio > buy_ratio * 1.5:
        print(f"\n  → BEARISH MICROSTRUCTURE: Sell pressure dominates 2:1+", flush=True)
    elif buy_ratio > sell_ratio * 1.5:
        print(f"\n  → BULLISH MICROSTRUCTURE: Buy pressure dominates 2:1+", flush=True)
    else:
        print(f"\n  → BALANCED MICROSTRUCTURE: Buy/sell pressure roughly equal", flush=True)

    if wall_pct > 50:
        print(f"  → MAGNETIC REGIME: Price frequently near wall (mean-reversion likely)", flush=True)
    elif wall_pct < 20:
        print(f"  → TRENDING REGIME: Price rarely near wall (momentum likely)", flush=True)

    if len(rave_events) / hours > 100:
        print(f"  → HIGH ACTIVITY: {len(rave_events)/hours:.0f} events/hr (choppy/volatile)", flush=True)
    elif len(rave_events) / hours < 30:
        print(f"  → LOW ACTIVITY: {len(rave_events)/hours:.0f} events/hr (quiet/trending)", flush=True)

    # ===== SAVE REPORT =====
    report = {
        "time_span_minutes": round(time_span_min, 1),
        "total_rave_events": len(rave_events),
        "total_rave_signals": len(rave_signals),
        "event_frequency": {
            action: {
                "count": count,
                "pct": round(count / len(rave_events) * 100, 1),
                "per_hour": round(count / hours, 1),
            }
            for action, count in action_counts.most_common()
        },
        "directional_bias": {
            "sell_events": len(sell_events),
            "buy_events": len(buy_events),
            "neutral_events": len(neutral_events),
            "sell_pct": round(sell_ratio, 1),
            "buy_pct": round(buy_ratio, 1),
            "net_bias": net_directional,
            "bias_label": bias,
        },
        "delta_realization": {
            "sell_mean_bps": round(statistics.mean(sell_deltas), 2) if sell_deltas else None,
            "sell_median_bps": round(statistics.median(sell_deltas), 2) if sell_deltas else None,
            "sell_negative_pct": round(sum(1 for d in sell_deltas if d < 0) / len(sell_deltas) * 100, 1) if sell_deltas else None,
            "buy_mean_bps": round(statistics.mean(buy_deltas), 2) if buy_deltas else None,
            "buy_median_bps": round(statistics.median(buy_deltas), 2) if buy_deltas else None,
            "buy_positive_pct": round(sum(1 for d in buy_deltas if d > 0) / len(buy_deltas) * 100, 1) if buy_deltas else None,
        },
        "magnetic_wall": {
            "touch_events": len(wall_events),
            "signals_with_wall": len(wall_signals),
            "wall_pct": round(wall_pct, 1),
            "mean_wall_level": round(statistics.mean(walls), 4) if walls else None,
            "price_range": f"${min(prices):.4f}-${max(prices):.4f}" if prices else None,
        },
        "btc_correlation": {
            "mean_delta_usd": round(statistics.mean(btc_deltas), 2) if btc_deltas else None,
            "btc_up_pct": round(pos_btc / len(btc_deltas) * 100, 1) if btc_deltas else None,
            "btc_down_pct": round(neg_btc / len(btc_deltas) * 100, 1) if btc_deltas else None,
        },
        "temporal_clustering": {
            "mean_interval_sec": round(statistics.mean(intervals), 1) if intervals else None,
            "median_interval_sec": round(statistics.median(intervals), 1) if intervals else None,
            "burst_pct": round(len(bursts) / len(intervals) * 100, 1) if intervals else None,
        },
        "regime_diagnosis": {
            "microstructure": f"{bias} microstructure",
            "magnetic_regime": "magnetic" if wall_pct > 50 else "trending" if wall_pct < 20 else "mixed",
            "activity_level": "high" if len(rave_events)/hours > 100 else "low" if len(rave_events)/hours < 30 else "moderate",
        },
    }

    report_path = ROOT / "reports" / "microstructure_regime_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\n\nReport saved: {report_path}", flush=True)
    print("\nDone. 🎯", flush=True)


if __name__ == "__main__":
    main()
