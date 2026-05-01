#!/usr/bin/env python3
"""
Shared Regime Monitor + Order Book Logger
==========================================
A live service that ALL bots on the switchboard can use.

Outputs:
1. Current regime (Pump/Active/Dead) with ATR% and Volume readings
2. Order Book Imbalance (bid/ask size ratio)
3. BTC momentum state
4. Session gate state
5. Writes to shared JSON file for other bots to read
6. Logs to switchboard every 5 minutes

This is the SHARED BRAIN for the entire trading team.
"""
from __future__ import annotations

import json
import time
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
MONITOR_PATH = ROOT / "reports" / "live_regime_monitor.json"
OB_LOG_PATH = ROOT / "reports" / "ob_imbalance_log.jsonl"
REGIME_LOG_PATH = ROOT / "reports" / "regime_history.jsonl"

PRODUCTS = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
BTC = "BTC-USD"


def compute_atr_pct(candles, period=14):
    """Compute latest ATR% from candle list."""
    if len(candles) < period + 2:
        return 0.0
    
    true_ranges = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        pc = float(candles[i-1]["close"])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        true_ranges.append(tr)
    
    if len(true_ranges) < period:
        return 0.0
    
    atr = sum(true_ranges[-period:]) / period
    close = float(candles[-1]["close"])
    return (atr / close * 100) if close > 0 else 0.0


def get_latest_volume(candles, period=20):
    """Get latest volume and moving average."""
    if len(candles) < period:
        return 0.0, 0.0
    
    volumes = [float(c["volume"]) for c in candles]
    latest = volumes[-1]
    avg = sum(volumes[-period:]) / period
    return latest, avg


def determine_regime(atr_pct, vol_ratio):
    """Determine current regime based on ATR% and volume."""
    if atr_pct >= 3.0 and vol_ratio >= 2.0:
        return "PUMP", 0.95
    elif atr_pct >= 1.5 and vol_ratio >= 1.5:
        return "ACTIVE", 0.25
    else:
        return "DEAD", 0.0


def get_session_gate():
    """Check if current hour is in death zones."""
    hour = datetime.now(timezone.utc).hour
    death_zones = [0, 6, 12, 19]
    return hour not in death_zones, hour


def get_rsi(closes, period=4):
    """Quick RSI computation."""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
    return 100.0


class RegimeMonitor:
    def __init__(self):
        self.client = CoinbaseAdvancedClient()
        self.candle_cache = {}  # product_id -> list of candles
        self.last_fetch = {}
        self.ob_readings = []
        self.regime_readings = []
    
    def fetch_candles(self, product_id, granularity="FIVE_MINUTE", hours=24):
        """Fetch recent candles with caching."""
        now = int(time.time())
        if product_id in self.last_fetch and (now - self.last_fetch[product_id]) < 25:
            return self.candle_cache.get(product_id, [])
        
        start = now - hours * 3600
        all_c = []
        chunk_sec = 300 * 5 * 60 if granularity == "FIVE_MINUTE" else 300 * 60
        cs = start
        
        while cs < now:
            ce = min(cs + chunk_sec, now)
            try:
                resp = self.client.market_candles(product_id, start=cs, end=ce, granularity=granularity)
                cands = resp.get("candles", [])
                all_c.extend(cands)
                cs = ce
                if not cands:
                    break
                time.sleep(0.15)
            except:
                cs = ce
                time.sleep(0.3)
        
        all_c.sort(key=lambda c: int(c["start"]))
        self.candle_cache[product_id] = all_c
        self.last_fetch[product_id] = now
        return all_c
    
    def sample_order_book(self, product_id):
        """Sample live bid/ask from best_bid_ask API."""
        try:
            resp = self.client.best_bid_ask([product_id])
            products = resp.get("products", [])
            if products:
                p = products[0]
                bids = p.get("bids", [])
                asks = p.get("asks", [])
                
                if bids and asks:
                    best_bid = float(bids[0]["price"])
                    best_ask = float(asks[0]["price"])
                    bid_size = float(bids[0].get("size", 0))
                    ask_size = float(asks[0].get("size", 0))
                    
                    spread = best_ask - best_bid
                    spread_pct = spread / best_bid * 100 if best_bid > 0 else 0
                    imbalance = bid_size / ask_size if ask_size > 0 else 9999
                    
                    return {
                        "product": product_id,
                        "ts": time.time(),
                        "bid": best_bid,
                        "ask": best_ask,
                        "bid_size": bid_size,
                        "ask_size": ask_size,
                        "spread_pct": round(spread_pct, 4),
                        "imbalance": round(imbalance, 2) if imbalance != 9999 else 9999,
                    }
        except Exception as e:
            return {"product": product_id, "error": str(e), "ts": time.time()}
        return None
    
    def run_cycle(self):
        """Run one full monitoring cycle."""
        ts = time.time()
        now_utc = datetime.now(timezone.utc)
        session_ok, current_hour = get_session_gate()
        
        # Fetch candles for all products
        product_states = {}
        for pid in PRODUCTS:
            candles = self.fetch_candles(pid, "FIVE_MINUTE", 24)
            if len(candles) >= 30:
                closes = [float(c["close"]) for c in candles]
                atr = compute_atr_pct(candles, 14)
                vol_latest, vol_avg = get_latest_volume(candles, 20)
                vol_ratio = vol_latest / vol_avg if vol_avg > 0 else 1.0
                rsi = get_rsi(closes, 4)
                regime, deploy = determine_regime(atr, vol_ratio)
                
                product_states[pid] = {
                    "atr_pct": round(atr, 2),
                    "vol_ratio": round(vol_ratio, 2),
                    "rsi_4": round(rsi, 1),
                    "regime": regime,
                    "deploy_pct": deploy,
                    "price": closes[-1] if closes else 0,
                }
            time.sleep(0.1)
        
        # BTC momentum
        btc_candles = self.fetch_candles(BTC, "ONE_MINUTE", 1)
        btc_mom_ok = True
        btc_price = 0
        if len(btc_candles) >= 3:
            closes = [float(c["close"]) for c in btc_candles]
            btc_price = closes[-1]
            mom = (closes[-1] - closes[-3]) / closes[-3] if closes[-3] > 0 else 0
            btc_mom_ok = mom >= -0.001
        
        # Sample order books
        ob_samples = []
        for pid in ["RAVE-USD", "BAL-USD"]:  # Focus on highest-edge coins
            ob = self.sample_order_book(pid)
            if ob:
                ob_samples.append(ob)
                self.ob_readings.append(ob)
                if len(self.ob_readings) > 1000:
                    self.ob_readings = self.ob_readings[-500:]
                time.sleep(0.5)
        
        # Aggregate regime (weighted by RAVE as primary)
        rave_state = product_states.get("RAVE-USD", {})
        primary_regime = rave_state.get("regime", "DEAD")
        primary_atr = rave_state.get("atr_pct", 0)
        primary_deploy = rave_state.get("deploy_pct", 0.0)
        
        # Count regimes across all coins
        regime_counts = {"PUMP": 0, "ACTIVE": 0, "DEAD": 0}
        for ps in product_states.values():
            regime_counts[ps["regime"]] = regime_counts.get(ps["regime"], 0) + 1
        
        # Build monitor state
        monitor = {
            "ts": ts,
            "ts_utc": now_utc.isoformat(),
            "hour_utc": current_hour,
            "session_gate_open": session_ok,
            "btc_momentum_ok": btc_mom_ok,
            "btc_price": btc_price,
            "primary_regime": primary_regime,
            "primary_atr_pct": primary_atr,
            "primary_deploy_pct": primary_deploy,
            "regime_counts": regime_counts,
            "coins_in_pump": regime_counts.get("PUMP", 0),
            "coins_in_dead": regime_counts.get("DEAD", 0),
            "products": product_states,
            "order_book": ob_samples,
            "avg_ob_imbalance": round(
                sum(s.get("imbalance", 0) for s in ob_samples) / max(1, len(ob_samples)), 2
            ) if ob_samples else 0,
            "recommendation": self.get_recommendation(
                primary_regime, session_ok, btc_mom_ok, product_states
            ),
        }
        
        # Save to shared file
        with open(MONITOR_PATH, "w", encoding="utf-8") as f:
            json.dump(monitor, f, indent=2)
        
        # Log regime reading
        self.regime_readings.append({
            "ts": ts,
            "regime": primary_regime,
            "atr": primary_atr,
            "deploy": primary_deploy,
            "session_ok": session_ok,
            "btc_ok": btc_mom_ok,
        })
        if len(self.regime_readings) > 1000:
            self.regime_readings = self.regime_readings[-500:]
        
        # Log OB samples
        if ob_samples:
            OB_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(OB_LOG_PATH, "a", encoding="utf-8") as f:
                for s in ob_samples:
                    f.write(json.dumps(s) + "\n")
        
        return monitor
    
    def get_recommendation(self, regime, session_ok, btc_ok, product_states):
        """Generate human-readable recommendation."""
        if not session_ok:
            return "⛔ SESSION GATE CLOSED — Death zone hour. Stay in cash."
        if not btc_ok:
            return "⛔ BTC MOMENTUM NEGATIVE — BTC dropping. Stay in cash."
        if regime == "DEAD":
            return "🔴 DEAD REGIME — Vol too low. Edge can't clear fees. Stay in cash."
        if regime == "ACTIVE":
            return "🟡 ACTIVE REGIME — Reduce deploy to 25%. Conservative mode."
        if regime == "PUMP":
            pump_coins = [pid for pid, ps in product_states.items() if ps["regime"] == "PUMP"]
            return f"🟢 PUMP REGIME — Full deploy (95%). Best coins: {', '.join(pump_coins)}"
        return "UNKNOWN"


def main():
    monitor = RegimeMonitor()
    
    print("=" * 80)
    print("  SHARED REGIME MONITOR + ORDER BOOK LOGGER")
    print("=" * 80)
    print(f"  Writing to: {MONITOR_PATH}")
    print(f"  OB log: {OB_LOG_PATH}")
    print(f"  Cycle: every 60 seconds")
    print(f"  Switchboard update: every 5 minutes")
    print()
    
    cycle = 0
    try:
        while True:
            cycle += 1
            start_cycle = time.time()
            
            m = monitor.run_cycle()
            elapsed = time.time() - start_cycle
            
            # Print status
            status_icon = {
                "PUMP": "🟢",
                "ACTIVE": "🟡",
                "DEAD": "🔴",
            }.get(m["primary_regime"], "⚪")
            
            session_icon = "✅" if m["session_gate_open"] else "❌"
            btc_icon = "✅" if m["btc_momentum_ok"] else "❌"
            
            print(f"  [{m['ts_utc'][:19]}] {status_icon} {m['primary_regime']:<8} "
                  f"ATR={m['primary_atr_pct']:.1f}% "
                  f"Deploy={m['primary_deploy_pct']*100:.0f}% "
                  f"S:{session_icon} BTC:{btc_icon} "
                  f"Pumps:{m['coins_in_pump']}/5 "
                  f"OB:{m['avg_ob_imbalance']:.1f}x "
                  f"({elapsed:.1f}s)")
            print(f"    {m['recommendation']}")
            print()
            
            # Post to switchboard every 5 cycles
            if cycle % 5 == 0:
                try:
                    # Build concise status message
                    coins_summary = []
                    for pid, ps in m["products"].items():
                        coins_summary.append(f"{pid}: {ps['regime']} ATR{ps['atr_pct']:.1f}% RSI{ps['rsi_4']:.0f}")
                    
                    ob_summary = ""
                    if m["order_book"]:
                        for ob in m["order_book"]:
                            ob_summary += f" {ob['product']}: Imbalance={ob['imbalance']:.1f}x Spread={ob['spread_pct']:.3f}%"
                    
                    print(f"  [SWITCHBOARD] Regime: {m['primary_regime']} | ATR: {m['primary_atr_pct']:.1f}% | "
                          f"Deploy: {m['primary_deploy_pct']*100:.0f}% | "
                          f"Session: {'OPEN' if m['session_gate_open'] else 'CLOSED'} | "
                          f"BTC: {'OK' if m['btc_momentum_ok'] else 'NEGATIVE'} | "
                          f"Pump coins: {m['coins_in_pump']}/5{ob_summary}")
                    print(f"  [SWITCHBOARD] {m['recommendation']}")
                except Exception as e:
                    print(f"  [SWITCHBOARD ERROR] {e}")
            
            # Sleep to next cycle
            sleep_time = max(1, 60 - elapsed)
            time.sleep(sleep_time)
    
    except KeyboardInterrupt:
        print("\n  Monitor stopped. Final state saved.")
        return 0


if __name__ == "__main__":
    main()
