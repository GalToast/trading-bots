#!/usr/bin/env python3
"""
Regime Detection Module
========================
Classifies market conditions into PUMP / ACTIVE / DEAD regimes.
Provides deploy percentage and entry/exit recommendations.

Usage:
    from regime_detector import RegimeDetector, load_regime
    
    detector = RegimeDetector(client)
    regime = detector.detect("RAVE-USD")
    print(regime.regime, regime.deploy_pct, regime.recommendation)
    
    # Or load from live monitor JSON:
    regime = load_regime()
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
MONITOR_PATH = ROOT / "reports" / "live_regime_monitor.json"


@dataclass
class RegimeResult:
    regime: str  # "PUMP", "ACTIVE", "DEAD"
    atr_pct: float
    vol_ratio: float
    deploy_pct: float
    rsi_4: float
    recommendation: str
    ts_utc: str
    session_gate_open: bool
    btc_momentum_ok: bool
    btc_price: float
    coins_in_pump: int
    coins_in_dead: int
    per_coin: dict  # coin -> {atr_pct, vol_ratio, rsi_4, regime, deploy_pct, price}


class RegimeDetector:
    """Detects market regime from live data."""
    
    def __init__(self, client, products=None):
        self.client = client
        self.products = products or ["RAVE-USD", "BAL-USD", "BLUR-USD", "IOTX-USD"]
        self._cache = {}
        self._cache_time = 0
    
    def detect(self, primary_coin: str = "RAVE-USD") -> RegimeResult:
        """Detect current regime. Caches for 60 seconds."""
        now = time.time()
        if now - self._cache_time < 60 and primary_coin in self._cache:
            return self._cache[primary_coin]
        
        # Fetch candles for all products
        per_coin = {}
        for pid in self.products:
            try:
                candles = self._fetch_candles(pid, granularity="FIVE_MINUTE", hours=24)
                if len(candles) >= 30:
                    atr_pct = self._compute_atr_pct(candles)
                    vol_ratio = self._compute_vol_ratio(candles)
                    rsi_4 = self._compute_rsi(candles, period=4)
                    
                    regime, deploy_pct = self._classify_regime(atr_pct, vol_ratio)
                    price = float(candles[-1]["close"])
                    
                    per_coin[pid] = {
                        "atr_pct": round(atr_pct, 2),
                        "vol_ratio": round(vol_ratio, 2),
                        "rsi_4": round(rsi_4, 1),
                        "regime": regime,
                        "deploy_pct": deploy_pct,
                        "price": price,
                    }
            except:
                pass
        
        # Primary regime determination
        primary = per_coin.get(primary_coin, {})
        atr_pct = primary.get("atr_pct", 0)
        vol_ratio = primary.get("vol_ratio", 0)
        regime, deploy_pct = self._classify_regime(atr_pct, vol_ratio)
        
        # Session gate
        from datetime import datetime, timezone
        hour = datetime.now(timezone.utc).hour
        session_gate_open = hour not in [0, 6, 12, 19]
        
        # BTC momentum (simplified check)
        btc_momentum_ok = True  # Would need BTC fetch to determine
        
        coins_in_pump = sum(1 for c in per_coin.values() if c["regime"] == "PUMP")
        coins_in_dead = sum(1 for c in per_coin.values() if c["regime"] == "DEAD")
        
        recommendation = self._recommendation(regime, session_gate_open, btc_momentum_ok)
        
        from datetime import datetime, timezone
        ts_utc = datetime.now(timezone.utc).isoformat()
        
        result = RegimeResult(
            regime=regime,
            atr_pct=atr_pct,
            vol_ratio=vol_ratio,
            deploy_pct=deploy_pct,
            rsi_4=primary.get("rsi_4", 0),
            recommendation=recommendation,
            ts_utc=ts_utc,
            session_gate_open=session_gate_open,
            btc_momentum_ok=btc_momentum_ok,
            btc_price=0,
            coins_in_pump=coins_in_pump,
            coins_in_dead=coins_in_dead,
            per_coin=per_coin,
        )
        
        self._cache[primary_coin] = result
        self._cache_time = now
        return result
    
    def _classify_regime(self, atr_pct: float, vol_ratio: float):
        """Classify regime based on ATR% and volume ratio."""
        if atr_pct >= 3.0 and vol_ratio >= 2.0:
            return "PUMP", 0.95
        elif atr_pct >= 1.5 and vol_ratio >= 1.5:
            return "ACTIVE", 0.25
        else:
            return "DEAD", 0.0
    
    def _recommendation(self, regime: str, session_ok: bool, btc_ok: bool) -> str:
        if not session_ok:
            return "⛔ SESSION GATE CLOSED — Death zone hour. Stay in cash."
        if not btc_ok:
            return "⛔ BTC MOMENTUM NEGATIVE — BTC dropping. Stay in cash."
        if regime == "DEAD":
            return "🔴 DEAD REGIME — Vol too low. Edge can't clear fees. Stay in cash."
        if regime == "ACTIVE":
            return "🟡 ACTIVE REGIME — Reduce deploy to 25%. Conservative mode."
        if regime == "PUMP":
            return "🟢 PUMP REGIME — Full deploy (95%). Maximize positions."
        return "UNKNOWN"
    
    def _fetch_candles(self, pid, granularity="FIVE_MINUTE", hours=24):
        now = int(time.time())
        start = now - hours * 3600
        gsec = 300 * 5 if granularity == "FIVE_MINUTE" else 300
        all_c = []
        cs = start
        while cs < now:
            ce = min(cs + 300 * gsec, now)
            try:
                resp = self.client.market_candles(pid, start=cs, end=ce, granularity=granularity)
                cands = resp.get("candles", [])
                all_c.extend(cands)
                cs = ce
                if not cands:
                    break
                time.sleep(0.15)
            except:
                cs = ce
                time.sleep(0.3)
        all_c.sort(key=lambda c: int(c.get("start", c.get("time", 0))))
        return all_c
    
    def _compute_atr_pct(self, candles, period=14):
        if len(candles) < period + 1:
            return 0.0
        true_ranges = []
        for i in range(1, len(candles)):
            h = float(candles[i]["high"])
            l = float(candles[i]["low"])
            pc = float(candles[i-1]["close"])
            tr = max(h - l, abs(h - pc), abs(l - pc))
            true_ranges.append(tr)
        atr = sum(true_ranges[:period]) / period
        close = float(candles[-1]["close"])
        return (atr / close * 100) if close > 0 else 0.0
    
    def _compute_vol_ratio(self, candles, period=20):
        if len(candles) < period:
            return 1.0
        volumes = [float(c["volume"]) for c in candles]
        latest = volumes[-1]
        avg = sum(volumes[-period:]) / period
        return latest / avg if avg > 0 else 1.0
    
    def _compute_rsi(self, candles, period=4):
        closes = [float(c["close"]) for c in candles]
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0 for d in deltas[-period:]]
        losses = [-d if d < 0 else 0 for d in deltas[-period:]]
        avg_g = sum(gains) / period
        avg_l = sum(losses) / period
        if avg_l > 0:
            rs = avg_g / avg_l
            return 100 - 100 / (1 + rs)
        return 100.0


def load_regime() -> Optional[RegimeResult]:
    """Load regime from the live monitor JSON file."""
    if not MONITOR_PATH.exists():
        return None
    try:
        data = json.loads(MONITOR_PATH.read_text(encoding="utf-8"))
        return RegimeResult(
            regime=data.get("primary_regime", "DEAD"),
            atr_pct=data.get("primary_atr_pct", 0),
            vol_ratio=0,
            deploy_pct=data.get("primary_deploy_pct", 0),
            rsi_4=0,
            recommendation=data.get("recommendation", ""),
            ts_utc=data.get("ts_utc", ""),
            session_gate_open=data.get("session_gate_open", True),
            btc_momentum_ok=data.get("btc_momentum_ok", True),
            btc_price=data.get("btc_price", 0),
            coins_in_pump=data.get("regime_counts", {}).get("PUMP", 0),
            coins_in_dead=data.get("regime_counts", {}).get("DEAD", 0),
            per_coin=data.get("products", {}),
        )
    except:
        return None


def is_safe_to_trade() -> bool:
    """Quick check: is it safe to deploy right now?"""
    regime = load_regime()
    if regime is None:
        return False
    return regime.regime in ("PUMP", "ACTIVE") and regime.session_gate_open and regime.btc_momentum_ok


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(line_buffering=True)
    
    regime = load_regime()
    if regime:
        print(f"Regime: {regime.regime}")
        print(f"ATR%: {regime.atr_pct}")
        print(f"Deploy: {regime.deploy_pct*100:.0f}%")
        print(f"Session: {'OPEN' if regime.session_gate_open else 'CLOSED'}")
        print(f"BTC OK: {regime.btc_momentum_ok}")
        print(f"Recommendation: {regime.recommendation}")
        print(f"\nPer coin:")
        for coin, data in regime.per_coin.items():
            print(f"  {coin}: {data['regime']} ATR={data['atr_pct']}% Vol={data['vol_ratio']}x RSI={data['rsi_4']}")
    else:
        print("No regime data available. Start regime_monitor_service.py first.")
    
    print(f"\nSafe to trade: {is_safe_to_trade()}")
