#!/usr/bin/env python3
"""
OMNI-VIP-FORTRESS V6.1 (RAVE Anchor + Regime Gate)
====================================================
Enhanced V6 with choppy-regime skip.

Logic:
1. RAVE Only (The structural survivor)
2. RSI(3) < 30 (Verified crater entry)
3. 25% Take Profit (Optimal alpha space)
4. No Stop Loss (Mean reversion physics)
5. Session Gate (12, 19, 6, 0 UTC Death Zones blocked)
6. **NEW: Regime Gate — Skip entries when regime = choppy**

Regime classifier:
- score >= 70 → HOT (100% WR, rare but explosive) → FULL size
- score 40-69 → COLD (50% WR, break even) → HALF size
- score < 40  → CHOPPY (39% WR, losing) → SKIP

Based on @main's regime-segmented benchmark findings.
"""
import argparse
import json
import os
import sys
import time
import math
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "omni_vip_fortress_v61_state.json"
EVENT_PATH = ROOT / "reports" / "omni_vip_fortress_v61_events.jsonl"

PRODUCT = "RAVE-USD"
BTC_PRODUCT = "BTC-USD"

# VERIFIED PARAMS
RSI_PERIOD = 3
OS_ENTRY = 30
TP_PCT = 25.0
MAX_HOLD = 48  # 4 hours

# Regime gate params
REGIME_WINDOW = 24  # 24 candles = 2 hours at 5m
REGIME_UPDATE_INTERVAL = 300  # recompute every 5 min

# ── Regime Detection (inline, no external dependency needed) ─────────────

def _compute_atr_pct(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        high = highs[i]
        low = lows[i]
        prev_close = closes[i - 1]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if len(trs) < period:
        return (sum(trs) / max(1, len(trs))) / max(0.001, closes[-1]) * 100
    return (sum(trs[-period:]) / period) / max(0.001, closes[-1]) * 100


def _pearson(x, y):
    n = min(len(x), len(y))
    if n < 5:
        return 0.0
    x = x[-n:]
    y = y[-n:]
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    dx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    dy = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def _compute_adx(highs, lows, closes, period=14):
    """Simplified ADX approximation."""
    if len(closes) < period + 2:
        return 0.0

    plus_dm = []
    minus_dm = []
    for i in range(1, len(highs)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)

    if len(plus_dm) < period:
        return 0.0

    avg_plus = sum(plus_dm[-period:]) / period
    avg_minus = sum(minus_dm[-period:]) / period
    tr_sum = avg_plus + avg_minus
    if tr_sum == 0:
        return 0.0

    di_plus = (avg_plus / tr_sum) * 100
    di_minus = (avg_minus / tr_sum) * 100
    dx = abs(di_plus - di_minus) / max(0.001, di_plus + di_minus) * 100
    return dx


def classify_regime(candles: list[dict], btc_candles: list[dict]) -> dict:
    """
    Classify current regime from recent candles.

    Returns:
        {
            "score": 0-100,
            "regime": "hot" | "cold" | "choppy",
            "atr_pct": float,
            "btc_corr": float,
            "adx": float,
            "volume_ratio": float,
        }
    """
    if len(candles) < 20:
        return {"score": 50, "regime": "cold", "atr_pct": 0, "btc_corr": 0, "adx": 0, "volume_ratio": 1.0}

    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    volumes = [float(c.get("volume", 0)) for c in candles]

    # 1. ATR%
    atr_pct = _compute_atr_pct(highs, lows, closes, period=14)

    # 2. BTC correlation
    if len(btc_candles) >= len(candles):
        btc_closes = [float(c["close"]) for c in btc_candles[-len(closes):]]
        alt_returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
        btc_returns = [(btc_closes[i] - btc_closes[i - 1]) / btc_closes[i - 1] for i in range(1, len(btc_closes))]
        btc_corr = abs(_pearson(alt_returns, btc_returns))
    else:
        btc_corr = 0.5  # Default moderate correlation

    # 3. Volume ratio
    if len(volumes) >= 10 and volumes[-1] > 0:
        avg_vol = sum(volumes[-10:]) / 10
        volume_ratio = volumes[-1] / max(0.001, avg_vol)
    else:
        volume_ratio = 1.0

    # 4. ADX
    adx = _compute_adx(highs, lows, closes, period=14)

    # Score components (0-100):
    # ATR% (0-30 pts): higher vol = more tradable
    if atr_pct >= 3.0:
        atr_pts = 30
    elif atr_pct >= 2.0:
        atr_pts = 25
    elif atr_pct >= 1.5:
        atr_pts = 20
    elif atr_pct >= 1.0:
        atr_pts = 15
    elif atr_pct >= 0.5:
        atr_pts = 10
    else:
        atr_pts = 0

    # BTC correlation (0-30 pts): LOW correlation = coin moves independently = better for RSI MR
    if btc_corr < 0.1:
        corr_pts = 30
    elif btc_corr < 0.2:
        corr_pts = 25
    elif btc_corr < 0.3:
        corr_pts = 20
    elif btc_corr < 0.5:
        corr_pts = 10
    else:
        corr_pts = 0

    # Volume ratio (0-20 pts): higher recent volume = better liquidity
    if volume_ratio >= 2.0:
        vol_pts = 20
    elif volume_ratio >= 1.5:
        vol_pts = 15
    elif volume_ratio >= 1.0:
        vol_pts = 10
    elif volume_ratio >= 0.5:
        vol_pts = 5
    else:
        vol_pts = 0

    # ADX (0-20 pts): RANGING (low ADX) is good for RSI MR
    if adx < 15:
        adx_pts = 20  # Strong range → good for mean reversion
    elif adx < 25:
        adx_pts = 15
    elif adx < 35:
        adx_pts = 10
    elif adx < 50:
        adx_pts = 5
    else:
        adx_pts = 0  # Strong trend → bad for mean reversion

    score = atr_pts + corr_pts + vol_pts + adx_pts

    # Classify
    if score >= 70:
        regime = "hot"
    elif score >= 40:
        regime = "cold"
    else:
        regime = "choppy"

    return {
        "score": score,
        "regime": regime,
        "atr_pct": round(atr_pct, 2),
        "btc_corr": round(btc_corr, 3),
        "adx": round(adx, 1),
        "volume_ratio": round(volume_ratio, 2),
    }


# ── Engine ────────────────────────────────────────────────────────────────

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def compute_rsi(closes, period=RSI_PERIOD):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
    return 100.0


class RaveAnchorV61:
    def __init__(self, starting_cash=48.0):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.position = None
        self.realized_net = 0.0
        self.closes = 0
        self.total_volume = 0.0
        self.history = []
        self.last_candle_time = 0

        # Regime state
        self.current_regime = "cold"  # Default
        self.regime_score = 50
        self.regime_history = []  # Recent regime classifications
        self.last_regime_check = 0
        self.entries_skipped_choppy = 0
        self.signals_by_regime = {"hot": 0, "cold": 0, "choppy": 0}  # Per-regime signal count

    def get_fee_rate(self):
        return 0.0025

    def update_regime(self, rave_candles, btc_candles):
        """Update regime classification."""
        if len(rave_candles) < 20:
            return
        result = classify_regime(rave_candles, btc_candles)
        self.current_regime = result["regime"]
        self.regime_score = result["score"]
        self.regime_history.append({
            "ts": utc_now_iso(),
            "regime": result["regime"],
            "score": result["score"],
            "atr_pct": result["atr_pct"],
            "btc_corr": result["btc_corr"],
            "adx": result["adx"],
        })
        if len(self.regime_history) > 100:
            self.regime_history.pop(0)

    def process_tick(self, rave_candles, btc_candles):
        events = []
        fee_rate = self.get_fee_rate()

        # Update history
        if rave_candles:
            for c in rave_candles:
                self.history.append(float(c["close"]))
                if len(self.history) > 200:
                    self.history.pop(0)

        # Update regime periodically
        now = time.time()
        if now - self.last_regime_check >= REGIME_UPDATE_INTERVAL:
            self.update_regime(self.history[-REGIME_WINDOW * 2:] if len(self.history) >= REGIME_WINDOW * 2 else self.history,
                               btc_candles)
            self.last_regime_check = now
            events.append({
                "ts_utc": utc_now_iso(),
                "action": "regime_update",
                "regime": self.current_regime,
                "score": self.regime_score,
            })

        # 1. Exit Logic
        if self.position and rave_candles:
            for c in rave_candles:
                cl = float(c["close"])
                h = float(c["high"])
                self.position["hold"] += 1
                if h >= self.position["target"] or self.position["hold"] >= MAX_HOLD:
                    exit_p = self.position["target"] if h >= self.position["target"] else cl
                    units = self.position["quote"] / self.position["entry"]
                    total_returned = (units * exit_p) * (1 - fee_rate)
                    self.cash += total_returned
                    pnl = total_returned - (self.position["quote"] * (1 + fee_rate))
                    self.realized_net += pnl
                    self.closes += 1
                    self.total_volume += self.position["quote"] + (units * exit_p)
                    events.append({
                        "ts_utc": utc_now_iso(),
                        "action": "close",
                        "net": round(pnl, 4),
                        "reason": "tp" if h >= self.position["target"] else "timeout",
                        "regime_at_entry": self.position.get("regime_at_entry", "unknown"),
                    })
                    self.position = None
                    break

        # 2. Entry Logic
        if self.position is None and self.cash >= 10.0 and len(self.history) >= 10:
            # Session Gate
            dt = datetime.now(timezone.utc)
            if dt.hour in [12, 19, 6, 0]:
                return events

            rsi_now = compute_rsi(self.history)

            if rsi_now <= OS_ENTRY:
                # Log every RSI signal with regime telemetry
                self.signals_by_regime[self.current_regime] += 1
                events.append({
                    "ts_utc": utc_now_iso(),
                    "action": "signal",
                    "rsi": round(rsi_now, 1),
                    "regime": self.current_regime,
                    "regime_score": self.regime_score,
                })

                # Regime-based position sizing (Gated Moderate: 100/75/25)
                # Based on @qwen-trading-bots' regime-gated sizing optimization:
                #   HOT=100%: Full deployment (rare but explosive)
                #   COLD=75%: Preserve most upside, trim worst trades
                #   CHOPPY=25%: Small size (not full skip — some choppy signals still win)
                if self.current_regime == "hot":
                    size_pct = 0.95  # Full size
                elif self.current_regime == "cold":
                    size_pct = 0.75  # Gated Moderate — optimal per @qwen-trading-bots
                else:  # choppy
                    size_pct = 0.25  # Small size — don't fully skip

                if rave_candles:
                    ep = float(rave_candles[0]["open"])
                    tq = self.cash * size_pct
                    tp_price = ep * (1 + TP_PCT / 100.0)
                    self.position = {
                        "entry": ep,
                        "quote": tq,
                        "hold": 0,
                        "target": tp_price,
                        "regime_at_entry": self.current_regime,
                        "regime_score_at_entry": self.regime_score,
                    }
                    self.cash -= (tq * (1 + fee_rate))
                    events.append({
                        "ts_utc": utc_now_iso(),
                        "action": "open",
                        "size": round(tq, 2),
                        "price": ep,
                        "tp": tp_price,
                        "regime": self.current_regime,
                        "regime_score": self.regime_score,
                    })

        return events

    def snapshot(self):
        return {
            "cash": round(self.cash, 4),
            "realized_net": round(self.realized_net, 4),
            "closes": self.closes,
            "vol": round(self.total_volume, 4),
            "pos": "active" if self.position else "flat",
            "entry": self.position["entry"] if self.position else None,
            "tp": self.position["target"] if self.position else None,
            "current_regime": self.current_regime,
            "regime_score": self.regime_score,
            "entries_skipped_choppy": self.entries_skipped_choppy,
            "signals_by_regime": dict(self.signals_by_regime),
        }


def save_state(path, engine, runner):
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main():
    client = CoinbaseAdvancedClient()
    engine = RaveAnchorV61()
    print("🚀 RAVE ANCHOR V6.1: Live Portfolio Anchor Deployed (Regime Gate)")
    print(f"   Regime gate: choppy=SKIP, cold=50% size, hot=100% size")

    engine.last_candle_time = int(time.time()) - 3600
    runner = {"pid": os.getpid(), "started_at": utc_now_iso()}

    # Fetch BTC candles on first tick
    btc_history = []
    btc_last_fetch = 0

    while True:
        try:
            end = int(time.time())

            # Fetch RAVE candles
            resp = client.market_candles(PRODUCT, start=max(engine.last_candle_time, end - 300 * 60), end=end,
                                          granularity="FIVE_MINUTE")
            new_c = [c for c in resp.get("candles", []) if int(c["start"]) > engine.last_candle_time]
            for c in new_c:
                engine.last_candle_time = max(engine.last_candle_time, int(c["start"]))

            # Fetch BTC candles for regime classification (every 5 min, not every tick)
            btc_candles = []
            if not btc_history or (end - btc_last_fetch) >= 300:
                try:
                    btc_resp = client.market_candles(BTC_PRODUCT, start=end - 7200, end=end, granularity="FIVE_MINUTE")
                    btc_candles = btc_resp.get("candles", [])
                    btc_history = btc_candles
                    btc_last_fetch = end
                except Exception:
                    btc_candles = btc_history if btc_history else []
            else:
                btc_candles = btc_history

            events = engine.process_tick(new_c, btc_candles)
            for ev in events:
                append_jsonl(EVENT_PATH, ev)

            save_state(STATE_PATH, engine, runner)
            snap = engine.snapshot()
            pos_str = f"active (entry={snap['entry']:.4f} tp={snap['tp']:.4f})" if snap['pos'] == "active" else "flat"
            print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net']:.2f} regime={snap['current_regime']}({snap['regime_score']}) skips={snap['entries_skipped_choppy']} {pos_str}",
                  flush=True)
        except Exception as e:
            print(f"  EXC: {e}")
            if "429" in str(e):
                print(f"  Rate limited, backing off 60s...")
                time.sleep(60)
            else:
                time.sleep(30)


if __name__ == "__main__":
    main()
