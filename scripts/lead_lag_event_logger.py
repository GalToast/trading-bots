#!/usr/bin/env python3
"""
Lane 1: Lead-Lag Event Logger — Structured BTC/ETH Spike Detection

Instead of correlating every bar (which drowns signal in noise), this script:
1. Detects BTC/ETH spike events (price moves > X% in Y seconds)
2. For each spike, measures altcoin reaction time and magnitude
3. Writes structured JSONL events for post-hoc analysis
4. Can run in backfill mode (using cached M1 candles) or live mode (real-time tickers)

Output: reports/lead_lag_events.jsonl
"""
from __future__ import annotations

import json
import math
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports"
EVENTS_PATH = REPORT_DIR / "lead_lag_events.jsonl"
REPORT_DIR.mkdir(exist_ok=True)

# ── Configuration ──────────────────────────────────────────────────────

LEADERS = ["BTC-USD", "ETH-USD"]
LAGGERS = ["RAVE-USD", "IOTX-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD"]

# Event detection thresholds (for M1 data)
SPIKE_THRESHOLD_PCT = 0.3  # Leader must move >0.3% in one bar to count as a spike
MIN_REACTION_WINDOW = 1    # Minimum bars to wait for lagger reaction
MAX_REACTION_WINDOW = 5    # Maximum bars to look for lagger reaction

# Kraken API mapping
KRAKEN_PAIRS = {
    "BTC-USD": "XXBTZUSD",
    "ETH-USD": "XETHZUSD",
}


# ── Part 1: Backfill Event Detection (M1 candles) ─────────────────────

def detect_spike_events(candles_data):
    """
    Detect leader spike events and measure lagger reactions.

    For each bar where BTC or ETH moves > threshold:
    - Record the spike details
    - Look ahead MAX_REACTION_WINDOW bars for lagger response
    - Measure: did the lagger move in the same direction? How fast? How far?
    """
    events = []

    for leader in LEADERS:
        if leader not in candles_data:
            continue

        leader_candles = candles_data[leader]
        leader_closes = [float(c["close"]) for c in leader_candles]

        for lag_idx, lagger in enumerate(LAGGERS):
            if lagger not in candles_data:
                continue

            lagger_candles = candles_data[lagger]
            lagger_closes = [float(c["close"]) for c in lagger_candles]

            # Align lengths
            min_len = min(len(leader_closes), len(lagger_closes))

            for i in range(1, min_len - MAX_REACTION_WINDOW):
                # Detect spike
                leader_ret = (leader_closes[i] - leader_closes[i - 1]) / leader_closes[i - 1]
                leader_ret_pct = abs(leader_ret) * 100

                if leader_ret_pct < SPIKE_THRESHOLD_PCT:
                    continue

                leader_dir = "up" if leader_ret > 0 else "down"

                # Look for lagger reaction in next MAX_REACTION_WINDOW bars
                lagger_start = lagger_closes[i - 1] if i - 1 < len(lagger_closes) else None
                if lagger_start is None or lagger_start == 0:
                    continue

                best_reaction_bar = None
                best_reaction_ret = 0
                first_same_dir_bar = None

                for bar_offset in range(MIN_REACTION_WINDOW, MAX_REACTION_WINDOW + 1):
                    if i + bar_offset - 1 >= len(lagger_closes):
                        break

                    lagger_price = lagger_closes[i + bar_offset - 1]
                    lagger_ret = (lagger_price - lagger_start) / lagger_start
                    lagger_ret_pct = lagger_ret * 100

                    # Check if lagger moved in same direction
                    if (leader_dir == "up" and lagger_ret > 0) or (leader_dir == "down" and lagger_ret < 0):
                        if first_same_dir_bar is None:
                            first_same_dir_bar = bar_offset

                    # Track best reaction magnitude
                    if abs(lagger_ret_pct) > abs(best_reaction_ret * 100):
                        best_reaction_ret = lagger_ret
                        best_reaction_bar = bar_offset

                event = {
                    "event_type": "leader_spike",
                    "ts_event": leader_candles[i].get("time", i),
                    "leader": leader,
                    "lagger": lagger,
                    "leader_return_pct": round(leader_ret_pct, 3),
                    "leader_direction": leader_dir,
                    "lagger_start_price": round(lagger_start, 6),
                    "best_reaction_bar": best_reaction_bar,
                    "best_reaction_return_pct": round(best_reaction_ret * 100, 3),
                    "first_same_direction_bar": first_same_dir_bar,
                    "reaction_detected": first_same_dir_bar is not None,
                    "reaction_magnitude_pct": round(abs(best_reaction_ret) * 100, 3),
                    "reaction_speed_bar": first_same_dir_bar,
                }
                events.append(event)

    return events


def analyze_spike_events(events):
    """Summarize the spike event analysis."""
    if not events:
        return {"total_events": 0, "summary": "No spike events detected at current threshold."}

    # Group by leader-lagger pair
    pairs = {}
    for e in events:
        key = f"{e['leader']}→{e['lagger']}"
        pairs.setdefault(key, []).append(e)

    summary = {}
    for pair, pair_events in pairs.items():
        reacted = [e for e in pair_events if e["reaction_detected"]]
        not_reacted = [e for e in pair_events if not e["reaction_detected"]]

        reaction_speeds = [e["reaction_speed_bar"] for e in reacted if e["reaction_speed_bar"] is not None]
        reaction_magnitudes = [e["reaction_magnitude_pct"] for e in reacted]

        summary[pair] = {
            "total_spikes": len(pair_events),
            "reacted": len(reacted),
            "did_not_react": len(not_reacted),
            "reaction_rate_pct": round(len(reacted) / max(1, len(pair_events)) * 100, 1),
            "avg_reaction_speed_bar": round(statistics.mean(reaction_speeds), 2) if reaction_speeds else None,
            "avg_reaction_magnitude_pct": round(statistics.mean(reaction_magnitudes), 3) if reaction_magnitudes else None,
            "max_reaction_magnitude_pct": round(max(reaction_magnitudes), 3) if reaction_magnitudes else None,
        }

    return {
        "total_events": len(events),
        "spike_threshold_pct": SPIKE_THRESHOLD_PCT,
        "pairs": summary,
    }


# ── Part 2: Live Event Detection ──────────────────────────────────────

def fetch_kraken_ticker(pair):
    """Fetch current ticker from Kraken."""
    try:
        url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
        req = urllib.request.Request(url, headers={"User-Agent": "LeadLagLab/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if "result" in data and pair in data["result"]:
                ticker = data["result"][pair]
                return {
                    "last": float(ticker["c"][0]),
                    "bid": float(ticker["b"][0]),
                    "ask": float(ticker["a"][0]),
                    "vol_24h": float(ticker["v"][1]),
                    "ts": time.time(),
                }
    except Exception:
        pass
    return None


def fetch_coinbase_ticker(product_id):
    """Fetch current ticker from Coinbase."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from coinbase_advanced_client import CoinbaseAdvancedClient
        client = CoinbaseAdvancedClient()
        ticker = client.get_product(product_id)
        return {
            "last": float(ticker.get("price", 0)),
            "bid": float(ticker.get("bid", 0)),
            "ask": float(ticker.get("ask", 0)),
            "vol_24h": float(ticker.get("volume_24h", 0)),
            "ts": time.time(),
        }
    except Exception:
        return None


def run_live_event_detection(duration_seconds=300, interval_seconds=1.0):
    """
    Live mode: monitor BTC/ETH in real-time, detect spikes, measure altcoin reactions.
    Runs for `duration_seconds` at `interval_seconds` sampling rate.
    """
    print(f"\n{'='*80}")
    print(f"  LANE 1: LEAD-LAG — Live Event Detection")
    print(f"{'='*80}")
    print(f"  Duration: {duration_seconds}s, Interval: {interval_seconds}s")
    print(f"  Leaders: {', '.join(LEADERS)}")
    print(f"  Laggers: {', '.join(LAGGERS)}")

    events = []
    price_history = {}  # {product: [(ts, price), ...]}
    start_time = time.time()

    try:
        while time.time() - start_time < duration_seconds:
            loop_start = time.time()

            # Fetch all leader prices
            for leader in LEADERS:
                kraken_pair = KRAKEN_PAIRS.get(leader)
                kr_data = fetch_kraken_ticker(kraken_pair) if kraken_pair else None
                cb_data = fetch_coinbase_ticker(leader)

                if cb_data and cb_data["last"] > 0:
                    price_history.setdefault(leader, []).append((time.time(), cb_data["last"]))

            # Fetch all lagger prices
            for lagger in LAGGERS:
                cb_data = fetch_coinbase_ticker(lagger)
                if cb_data and cb_data["last"] > 0:
                    price_history.setdefault(lagger, []).append((time.time(), cb_data["last"]))

            # Detect spikes (look at last 60 seconds of leader data)
            for leader in LEADERS:
                history = price_history.get(leader, [])
                if len(history) < 10:
                    continue

                # Look back 30s and 60s for baseline
                now = time.time()
                recent = [(ts, p) for ts, p in history if now - ts < 30]
                baseline = [(ts, p) for ts, p in history if 30 <= now - ts < 90]

                if not recent or not baseline:
                    continue

                baseline_avg = statistics.mean([p for _, p in baseline])
                current_price = recent[-1][1]
                ret_pct = abs(current_price - baseline_avg) / baseline_avg * 100

                if ret_pct > SPIKE_THRESHOLD_PCT:
                    # Spike detected — measure lagger reactions
                    spike_event = {
                        "event_type": "live_spike",
                        "ts_event": round(now, 3),
                        "leader": leader,
                        "leader_price": round(current_price, 4),
                        "leader_baseline_price": round(baseline_avg, 4),
                        "leader_return_pct": round(ret_pct, 3),
                        "lagger_reactions": [],
                    }

                    for lagger in LAGGERS:
                        lagger_history = price_history.get(lagger, [])
                        lagger_recent = [(ts, p) for ts, p in lagger_history if now - ts < 30]
                        lagger_baseline = [(ts, p) for ts, p in lagger_history if 30 <= now - ts < 90]

                        if lagger_recent and lagger_baseline:
                            lagger_baseline_avg = statistics.mean([p for _, p in lagger_baseline])
                            lagger_current = lagger_recent[-1][1]
                            lagger_ret_pct = (lagger_current - lagger_baseline_avg) / lagger_baseline_avg * 100

                            spike_event["lagger_reactions"].append({
                                "lagger": lagger,
                                "lagger_price": round(lagger_current, 6),
                                "lagger_return_pct": round(lagger_ret_pct, 4),
                                "same_direction": (lagger_ret_pct > 0 and ret_pct > 0) or (lagger_ret_pct < 0 and ret_pct < 0),
                            })

                    events.append(spike_event)
                    print(f"  🔴 SPIKE: {leader} {ret_pct:+.3f}% in 60s")
                    for lr in spike_event["lagger_reactions"]:
                        direction = "✅" if lr["same_direction"] else "❌"
                        print(f"     {direction} {lr['lagger']}: {lr['lagger_return_pct']:+.4f}%")

            # Sleep
            elapsed = time.time() - loop_start
            sleep_time = max(0, interval_seconds - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\n  Live event detection stopped by user.")

    # Save events
    if events:
        with open(EVENTS_PATH, "a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
        print(f"\n  ✅ Saved {len(events)} live events to {EVENTS_PATH}")
    else:
        print(f"\n  ⚠️ No live events detected (market was quiet or window too short)")

    return events


# ── Main ───────────────────────────────────────────────────────────────

def main():
    mode = "backfill"
    if "--live" in sys.argv:
        mode = "live"

    if mode == "live":
        duration = 300
        if "--duration" in sys.argv:
            idx = sys.argv.index("--duration")
            if idx + 1 < len(sys.argv):
                duration = int(sys.argv[idx + 1])

        events = run_live_event_detection(duration_seconds=duration)
        summary = {"total_live_events": len(events)}
    else:
        # Backfill mode
        print("=" * 80)
        print("  LANE 1: LEAD-LAG — Event-Driven Spike Detection (Backfill)")
        print("=" * 80)

        # Load candles
        all_products = LEADERS + LAGGERS
        candles_data = {}
        for pid in all_products:
            candles = load_candles(pid, "ONE_MINUTE", 7, max_age_minutes=7 * 24 * 60)
            if candles:
                candles_data[pid] = candles
                print(f"  {pid}: {len(candles)} candles")

        if len(candles_data) < 2:
            print("ERROR: Not enough candle data.")
            return 1

        # Detect events
        events = detect_spike_events(candles_data)
        summary = analyze_spike_events(events)

        print(f"\n{'='*80}")
        print(f"  RESULTS")
        print(f"{'='*80}")
        print(f"  Total spike events: {summary.get('total_events', 0)}")

        if "pairs" in summary:
            for pair, stats in summary["pairs"].items():
                print(f"\n  {pair}:")
                print(f"    Spikes: {stats['total_spikes']}")
                print(f"    Reacted: {stats['reacted']}/{stats['total_spikes']} ({stats['reaction_rate_pct']}%)")
                if stats["avg_reaction_speed_bar"]:
                    print(f"    Avg reaction speed: {stats['avg_reaction_speed_bar']} bars")
                if stats["avg_reaction_magnitude_pct"]:
                    print(f"    Avg reaction magnitude: {stats['avg_reaction_magnitude_pct']}%")
                if stats["max_reaction_magnitude_pct"]:
                    print(f"    Max reaction magnitude: {stats['max_reaction_magnitude_pct']}%")

    # Save summary
    summary_path = REPORT_DIR / "lead_lag_event_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\n  Summary saved: {summary_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
