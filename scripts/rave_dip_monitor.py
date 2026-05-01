#!/usr/bin/env python3
"""
Live RAVE Dip Monitor — Real-time alerts for profitable dip opportunities.

Monitors RAVE-USD for:
- RSI(3) < 15 → Extreme oversold (95.7% bounce rate at 2%, 66% at 10%)
- Dip Score >= 50 → Multi-signal capitulation detection
- Price near magnetic wall ($2.81) → Mean reversion likely

Runs continuously, prints alerts when conditions are met.
Lightweight, no trading, just detection.

Usage:
    python scripts/rave_dip_monitor.py
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
LOG_PATH = ROOT / "reports" / "rave_dip_monitor_log.jsonl"
STATE_PATH = ROOT / "reports" / "rave_dip_monitor_state.json"

PRODUCT = "RAVE-USD"
MAGNETIC_WALL = 2.8143  # From microstructure analysis

# Alert thresholds
RSI_EXTREME = 15     # 95.7% hit rate at 2% bounce
RSI_OVERSOLD = 20    # 80.9% hit rate at 5% bounce
DIP_SCORE_HIGH = 60  # Strong multi-signal dip
DIP_SCORE_MED = 50   # Moderate dip


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def log_event(path, event):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def fetch_recent_candles(client, pid, minutes=120):
    """Fetch recent M5 candles."""
    now = int(time.time())
    start = now - minutes * 60
    resp = client.market_candles(pid, start=start, end=now, granularity="FIVE_MINUTE")
    candles = resp.get("candles", [])
    candles.sort(key=lambda c: int(c["start"]))
    return candles


def compute_rsi(closes, period):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l > 0:
        rs = avg_g / avg_l
        return 100 - 100 / (1 + rs)
    return 100.0


def compute_bollinger(closes, period=20, num_std=2):
    if len(closes) < period:
        return None
    recent = closes[-period:]
    sma = statistics.mean(recent)
    std = statistics.stdev(recent) if len(recent) > 1 else 0
    upper = sma + num_std * std
    lower = sma - num_std * std
    width = (upper - lower) / sma * 100 if sma > 0 else 0
    return {"sma": sma, "upper": upper, "lower": lower, "width": width}


def compute_dip_score(candles):
    """Simplified dip score using only candle data (no order book)."""
    if len(candles) < 50:
        return {"score": 0, "reason": "insufficient_data"}

    closes = [float(c["close"]) for c in candles]
    volumes = [float(c["volume"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    opens = [float(c["open"]) for c in candles]

    current_price = closes[-1]
    score = 0
    components = {}

    # 1. RSI extreme oversold (0-30 points)
    rsi_3 = compute_rsi(closes, 3)
    rsi_6 = compute_rsi(closes, 6)
    if rsi_3 < 10:
        score += 30
    elif rsi_3 < 15:
        score += 25
    elif rsi_3 < 20:
        score += 18
    elif rsi_3 < 25:
        score += 10
    elif rsi_6 < 25:
        score += 6
    components["rsi_3"] = round(rsi_3, 1)
    components["rsi_6"] = round(rsi_6, 1)

    # 2. Volume climax (0-25 points)
    avg_vol = statistics.mean(volumes[-30:-5]) if len(volumes) > 35 else statistics.mean(volumes[-20:])
    last_vol = volumes[-1]
    vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1.0
    price_drop_5 = (closes[-1] - closes[-6]) / closes[-6] * 100 if closes[-6] > 0 else 0

    if vol_ratio >= 5.0 and price_drop_5 < -3:
        score += 25
    elif vol_ratio >= 3.0 and price_drop_5 < -1:
        score += 18
    elif vol_ratio >= 2.0:
        score += 10
    else:
        score += 4
    components["vol_ratio"] = round(vol_ratio, 2)
    components["price_drop_5bar"] = round(price_drop_5, 2)

    # 3. Long lower wick / hammer (0-25 points)
    last_open, last_close = opens[-1], closes[-1]
    last_low, last_high = lows[-1], highs[-1]
    body_size = abs(last_close - last_open)
    lower_wick = min(last_open, last_close) - last_low
    total_range = last_high - last_low

    if total_range > 0:
        wick_ratio = lower_wick / total_range
        if wick_ratio >= 0.6 and body_size / total_range <= 0.3:
            score += 25  # Perfect hammer
        elif wick_ratio >= 0.4:
            score += 15
        elif wick_ratio >= 0.3:
            score += 8
        else:
            score += 4
        components["wick_ratio"] = round(wick_ratio, 2)
        components["is_hammer"] = wick_ratio >= 0.6
    else:
        components["wick_ratio"] = 0
        components["is_hammer"] = False

    # 4. Mean reversion distance (0-20 points)
    bb = compute_bollinger(closes[-20:])
    if bb:
        distance_pct = (current_price - bb["lower"]) / bb["sma"] * 100 if bb["sma"] > 0 else 0
        if distance_pct < -3:
            score += 20
        elif distance_pct < -1:
            score += 14
        elif distance_pct < 0:
            score += 8
        else:
            score += 4
        components["distance_from_bb_lower"] = round(distance_pct, 2)
    else:
        score += 4
        components["distance_from_bb_lower"] = None

    # 5. Magnetic wall proximity (0-15 points)
    wall_distance = abs(current_price - MAGNETIC_WALL) / MAGNETIC_WALL * 100
    if wall_distance < 1.0:
        score += 15
    elif wall_distance < 2.0:
        score += 10
    elif wall_distance < 5.0:
        score += 6
    else:
        score += 2
    components["wall_distance_pct"] = round(wall_distance, 2)

    return {
        "score": score,
        "current_price": round(current_price, 6),
        "components": components,
    }


def print_banner():
    print("=" * 70, flush=True)
    print("  RAVE DIP MONITOR — Live", flush=True)
    print(f"  Magnetic Wall: ${MAGNETIC_WALL}", flush=True)
    print(f"  Thresholds: RSI<{RSI_EXTREME}=EXTREME, RSI<{RSI_OVERSOLD}=OVERSOLD, Dip>={DIP_SCORE_MED}=BUY", flush=True)
    print("=" * 70, flush=True)
    print(f"\nMonitoring started at {utc_now_iso()}", flush=True)


def print_state(rsi_3, rsi_6, dip_score, price, vol_ratio, wick_ratio, bb_dist, wall_dist):
    """Print current state in a compact format."""
    # Status indicators
    rsi_status = "🔴 EXTREME" if rsi_3 < RSI_EXTREME else "🟡 OVERSOLD" if rsi_3 < RSI_OVERSOLD else "🟢 Normal"
    dip_status = "🔴 STRONG BUY" if dip_score >= DIP_SCORE_HIGH else "🟡 BUY" if dip_score >= DIP_SCORE_MED else "🟢 Hold/Wait"
    wall_status = "🔴 NEAR WALL" if wall_dist < 2 else "🟡 Close" if wall_dist < 5 else "🟢 Far"

    print(f"\n{'─' * 70}", flush=True)
    print(f"  Price: ${price:.6f}  |  RSI(3): {rsi_3:.1f} {rsi_status}  |  RSI(6): {rsi_6:.1f}", flush=True)
    print(f"  Dip Score: {dip_score}/115 {dip_status}", flush=True)
    print(f"  Vol Ratio: {vol_ratio:.1f}x  |  Wick: {wick_ratio:.2f}  |  BB dist: {bb_dist:.1f}%  |  Wall: {wall_dist:.1f}%", flush=True)
    print(f"  {'⚠️  DIP OPPORTUNITY DETECTED' if dip_score >= DIP_SCORE_MED else '  No actionable signal right now'}", flush=True)


def main():
    client = CoinbaseAdvancedClient()
    print_banner()

    last_alert_time = 0
    alert_cooldown = 300  # 5 min between alerts

    cycle = 0
    while True:
        cycle += 1
        try:
            candles = fetch_recent_candles(client, PRODUCT, minutes=120)

            if len(candles) < 50:
                print(f"\n[{utc_now_iso()}] Waiting for data... ({len(candles)} candles)", flush=True)
                time.sleep(30)
                continue

            closes = [float(c["close"]) for c in candles]
            rsi_3 = compute_rsi(closes, 3)
            rsi_6 = compute_rsi(closes, 6)

            dip = compute_dip_score(candles)
            dip_score = dip["score"]
            price = dip["current_price"]
            comps = dip.get("components", {})

            vol_ratio = comps.get("vol_ratio", 0)
            wick_ratio = comps.get("wick_ratio", 0)
            bb_dist = comps.get("distance_from_bb_lower", 0) or 0
            wall_dist = comps.get("wall_distance_pct", 0)

            # Print state every cycle
            print_state(rsi_3, rsi_6, dip_score, price, vol_ratio, wick_ratio, bb_dist, wall_dist)

            # Check for alert conditions
            alert_triggered = False
            alert_reason = []

            if rsi_3 < RSI_EXTREME:
                alert_triggered = True
                alert_reason.append(f"RSI(3)={rsi_3:.1f} EXTREME OVERSOLD")

            if dip_score >= DIP_SCORE_HIGH:
                alert_triggered = True
                alert_reason.append(f"Dip Score={dip_score} STRONG BUY")
            elif dip_score >= DIP_SCORE_MED:
                alert_triggered = True
                alert_reason.append(f"Dip Score={dip_score} BUY")

            if alert_triggered and (time.time() - last_alert_time) > alert_cooldown:
                last_alert_time = time.time()
                print(f"\n{'='*70}", flush=True)
                print(f"  🚨 DIP ALERT TRIGGERED", flush=True)
                for reason in alert_reason:
                    print(f"  → {reason}", flush=True)
                print(f"  Price: ${price:.6f}", flush=True)
                print(f"  Expected bounce: 10-23% (66%+ hit rate)", flush=True)
                print(f"  Suggested TP: 10% (${price*1.10:.4f}) - 20% (${price*1.20:.4f})", flush=True)
                print(f"{'='*70}", flush=True)

                # Log alert
                log_event(LOG_PATH, {
                    "ts_utc": utc_now_iso(),
                    "type": "dip_alert",
                    "rsi_3": round(rsi_3, 1),
                    "rsi_6": round(rsi_6, 1),
                    "dip_score": dip_score,
                    "price": price,
                    "vol_ratio": vol_ratio,
                    "wick_ratio": wick_ratio,
                    "bb_dist": bb_dist,
                    "wall_dist": wall_dist,
                    "reasons": alert_reason,
                    "suggested_tp_10": round(price * 1.10, 6),
                    "suggested_tp_20": round(price * 1.20, 6),
                })

            # Save state
            state = {
                "updated_at": utc_now_iso(),
                "cycle": cycle,
                "price": price,
                "rsi_3": round(rsi_3, 1),
                "rsi_6": round(rsi_6, 1),
                "dip_score": dip_score,
                "last_alert": utc_now_iso() if alert_triggered else None,
            }
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")

        except KeyboardInterrupt:
            print(f"\n\nMonitor stopped at {utc_now_iso()}", flush=True)
            return
        except Exception as e:
            print(f"\n  ERROR: {e}", flush=True)
            import traceback
            traceback.print_exc()

        # Wait for next cycle (check every 60 seconds)
        print(f"\n  Next check in 60s...", end="", flush=True)
        time.sleep(60)


if __name__ == "__main__":
    main()
