import json
import time
import sys
import os
import math
import urllib.request
from datetime import datetime, timezone

class PredatoryLogicEngine:
    """
    The supreme structural logic engine for Coinbase microcap scalping.
    Fuses real-time order book physics with global market pulse.
    """
    def __init__(self, product_id):
        self.pid = product_id
        self.last_bid_size = 0.0
        self.last_ask_size = 0.0
        self.last_ratio = 1.0
        self.last_kraken_btc = None
        
        # Iceberg Overrun State
        self.active_iceberg_price = 0.0
        self.iceberg_gulp_volume = 0.0
        self.iceberg_side = None

    def detect_iceberg_overrun(self, current_price, current_ask_size, tick_volume):
        """
        Returns True if a massive Sell Iceberg was just exhausted.
        """
        is_overrun = False
        
        # Detect NEW Iceberg
        if self.last_ask_size > 0 and current_ask_size > self.last_ask_size * 5:
            self.active_iceberg_price = current_price
            self.iceberg_gulp_volume = 0.0
            self.iceberg_side = "SELL"
            
        # Track GULP
        if self.iceberg_side == "SELL" and current_price == self.active_iceberg_price:
            self.iceberg_gulp_volume += tick_volume
            
            # Check for Exhaustion
            if current_ask_size < 100 and self.iceberg_gulp_volume > 5000:
                is_overrun = True
                self.iceberg_side = None # Reset
                
        return is_overrun

    def get_kraken_btc(self):
        try:
            url = "https://api.kraken.com/0/public/Ticker?pair=XXBTZUSD"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                return float(data["result"]["XXBTZUSD"]["c"][0])
        except: return None

    def check_magnetic_proximity(self, price, step=0.05, proximity=0.0025):
        """Returns True if price is within 0.25% of a round number wall."""
        magnetic_level = round(price / step) * step
        if abs(price - magnetic_level) / magnetic_level <= proximity:
            return True, magnetic_level
        return False, None

    def check_gulp_active(self, current_bid_size, current_ask_size, threshold=1000):
        """Returns True if a massive wall has been 'gulped' without price movement."""
        # This requires historical tracking
        bid_gulp = False; ask_gulp = False
        if self.last_bid_size > 0:
            if self.last_bid_size - current_bid_size > threshold: bid_gulp = True
            if self.last_ask_size - current_ask_size > threshold: ask_gulp = True
        
        self.last_bid_size = current_bid_size
        self.last_ask_size = current_ask_size
        return bid_gulp, ask_gulp

    def check_kraken_lag_safety(self, cb_btc_price, drop_threshold=5.0):
        """Returns False if Kraken BTC has dropped > threshold before Coinbase."""
        kr_price = self.get_kraken_btc()
        if kr_price and self.last_kraken_btc:
            kr_move = kr_price - self.last_kraken_btc
            self.last_kraken_btc = kr_price
            if kr_move < -drop_threshold:
                return False # UNSAFE
        if kr_price: self.last_kraken_btc = kr_price
        return True # SAFE

    def check_regime_suitability(self, atr_pct, max_hourly_swing):
        """
        Returns True if the coin is in a profitable regime.
        Criteria: ATR% > 1.5% OR Max Hourly Swing > 4%.
        """
        if atr_pct > 0.015: return True
        if max_hourly_swing > 0.04: return True
        return False

    def evaluate_entry_quality(self, rsi, price, bid_size, ask_size, cb_btc, atr_pct=0.0, max_swing=0.0, daily_poc=0.0):
        """
        Final confluence score (0-100).
        Entry only if Score > 80.
        """
        # 0. Session Gate (Hard Gate)
        # Block entries during Death Zones (12:00, 19:00, 06:00, 00:00 UTC)
        hour_now = datetime.now(timezone.utc).hour
        if hour_now in [12, 19, 6, 0]:
            return 0

        # 0.5 Regime Filter (Hard Gate)
        if atr_pct > 0 and not self.check_regime_suitability(atr_pct, max_swing):
            return 0

        score = 0
        
        # 1. RSI Base (40 points)
        if rsi < 30: score += 40
        elif rsi < 45: score += 20
        
        # 2. Magnetic & Pool Bonus (20 points max)
        is_mag, level = self.check_magnetic_proximity(price)
        is_pool = False
        if daily_poc > 0:
            if abs(price - daily_poc) / daily_poc <= 0.0025:
                is_pool = True
        
        if is_mag or is_pool: score += 20
        
        # 3. Book Imbalance (20 points)
        ratio = bid_size / ask_size if ask_size > 0 else 999.0
        if ratio > 2.0: score += 20
        
        # 4. Kraken Safety (CRITICAL - Multiplier)
        is_safe = self.check_kraken_lag_safety(cb_btc)
        if not is_safe: score = 0
        
        # 5. Gulp Check (CRITICAL)
        bid_gulp, ask_gulp = self.check_gulp_active(bid_size, ask_size)
        if ask_gulp: score = 0 # Don't buy into a hidden seller gulping bids
        
        return score

# Implementation Example
if __name__ == "__main__":
    engine = PredatoryLogicEngine("RAVE-USD")
    print("Predatory Logic Engine Initialized.")
