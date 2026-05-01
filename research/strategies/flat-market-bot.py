import os
""" FLAT MARKET BOT - Mean Reversion Strategy
Optimized for low volatility hours (evening/night)

Strategy:
1. Track price range over last N cycles
2. Buy when price near bottom of range (oversold)
3. Sell when price near top of range (overbought)
4. Avoid trading during breakouts (volatility spikes)
"""

import requests
import time
from datetime import datetime
from collections import deque

# === ALPACA CONFIG ===
API_KEY = os.getenv("ALPACA_API_KEY", "")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
BASE_URL = "https://paper-api.alpaca.markets"
HEADERS = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": SECRET_KEY}

# === STRATEGY CONFIG ===
RANGE_PERIOD = 50          # Look back 50 cycles for range
ENTRY_THRESHOLD = 0.15     # Enter when price is 15% into range from edge
TP_THRESHOLD = 0.60        # Take profit at 60% into range
STOP_THRESHOLD = 0.05      # Stop if price breaks out (5% beyond range)
POSITION_SIZE = 0.25       # 25% of cash per trade
CYCLE_SEC = 3              # Check every 3 seconds

# Trackers
price_history = deque(maxlen=RANGE_PERIOD)
virtual_balance = 50.0
virtual_start = 50.0
wins = 0
losses = 0
trades = 0

def get_account():
    r = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS)
    return r.json() if r.ok else {}

def get_positions():
    r = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS)
    return r.json() if r.ok else []

def get_price():
    """Get live ETH bid/ask"""
    r = requests.get(
        "https://data.alpaca.markets/v1beta3/crypto/us/latest/quotes",
        headers=HEADERS,
        params={"symbols": "ETH/USD"}
    )
    if r.ok:
        quotes = r.json().get("quotes", {})
        if quotes and "ETH/USD" in quotes:
            q = quotes["ETH/USD"]
            return float(q["bp"]), float(q["ap"])
    # Fallback to position price
    for p in get_positions():
        if p["symbol"] == "ETHUSD":
            price = float(p["current_price"])
            return price, price
    return None, None

def buy(qty):
    r = requests.post(
        f"{BASE_URL}/v2/orders",
        headers=HEADERS,
        json={"symbol": "ETHUSD", "qty": str(round(qty, 6)), "side": "buy", "type": "market", "time_in_force": "gtc"}
    )
    if r.ok:
        return float(r.json().get("filled_avg_price", 0) or 0)
    print(f"[BUY ERROR] {r.text[:100]}")
    return None

def sell(qty):
    r = requests.post(
        f"{BASE_URL}/v2/orders",
        headers=HEADERS,
        json={"symbol": "ETHUSD", "qty": str(round(qty, 6)), "side": "sell", "type": "market", "time_in_force": "gtc"}
    )
    if r.ok:
        return True
    print(f"[SELL ERROR] {r.text[:100]}")
    return False

def close_position():
    """Close any open ETH position"""
    positions = get_positions()
    for p in positions:
        if p["symbol"] == "ETHUSD":
            qty = float(p["qty"])
            if sell(qty):
                print(f"[CLOSED] {qty:.4f} ETH")
                return True
    return False

def calculate_range_position(price):
    """Calculate where price is in its recent range (0=bottom, 1=top)"""
    if len(price_history) < 10:
        return 0.5, price, price  # Not enough data
    
    prices = list(price_history)
    range_low = min(prices)
    range_high = max(prices)
    range_size = range_high - range_low
    
    if range_size < 0.01:  # Range too small
        return 0.5, range_low, range_high
    
    position = (price - range_low) / range_size
    return position, range_low, range_high

print("=" * 60)
print("FLAT MARKET BOT - Mean Reversion")
print(f"Range: {RANGE_PERIOD} cycles | Entry: {ENTRY_THRESHOLD*100:.0f}% from edge")
print(f"TP: {TP_THRESHOLD*100:.0f}% into range | Size: {POSITION_SIZE*100:.0f}%")
print("=" * 60)

# Close any existing position first
close_position()
time.sleep(2)

entry_price = None
qty = 0

cycle = 0
while True:
    cycle += 1
    
    now = datetime.now().strftime("%H:%M:%S")
    account = get_account()
    cash = float(account.get("cash", 0))
    
    bid, ask = get_price()
    if not bid or not ask:
        print(f"[{now}] No price data")
        time.sleep(CYCLE_SEC)
        continue
    
    mid = (bid + ask) / 2
    price_history.append(mid)
    
    range_pos, range_low, range_high = calculate_range_position(mid)
    range_size_pct = (range_high - range_low) / range_low * 100 if range_low > 0 else 0
    
    # Check for existing position
    positions = get_positions()
    eth_pos = None
    for p in positions:
        if p["symbol"] == "ETHUSD":
            eth_pos = p
    
    if eth_pos:
        # Have position - check exit
        entry_price = float(eth_pos["avg_entry_price"])
        qty = float(eth_pos["qty"])
        pnl = (mid - entry_price) / entry_price
        
        print(f"[{now}] #{cycle} | ETH ${mid:.1f} | Entry ${entry_price:.1f} | P/L {pnl*100:.2f}% | Range [{range_low:.1f}-{range_high:.1f}] | Pos {range_pos*100:.0f}%")
        
        # Exit conditions
        should_sell = False
        reason = ""
        
        # TP: Price moved into upper part of range
        if range_pos >= TP_THRESHOLD:
            should_sell = True
            reason = f"TP (range {range_pos*100:.0f}% >= {TP_THRESHOLD*100:.0f}%)"
        
        # SL: Price broke out below range
        if range_pos < STOP_THRESHOLD:
            should_sell = True
            reason = f"SL (range {range_pos*100:.0f}% < {STOP_THRESHOLD*100:.0f}%)"
        
        if should_sell and sell(qty):
            pnl_amt = (mid - entry_price) * qty
            virtual_balance += pnl_amt * (virtual_start / 99705)
            if pnl > 0:
                wins += 1
                print(f"[WIN] {reason} | +${abs(pnl_amt):.2f} | Virtual ${virtual_balance:.2f}")
            else:
                losses += 1
                print(f"[LOSS] {reason} | -${abs(pnl_amt):.2f} | Virtual ${virtual_balance:.2f}")
            trades += 1
            entry_price = None
            qty = 0
            time.sleep(2)
    
    else:
        # No position - check entry
        print(f"[{now}] #{cycle} | ETH ${mid:.1f} | Range [{range_low:.1f}-{range_high:.1f}] ({range_size_pct:.2f}%) | Pos {range_pos*100:.0f}% | Virtual ${virtual_balance:.2f} | W/L {wins}/{losses}")
        
        # Entry condition: Price near bottom of range (oversold)
        if range_pos <= ENTRY_THRESHOLD and len(price_history) >= 10:
            print(f"[ENTRY SIGNAL] Price at {range_pos*100:.0f}% of range (low)")
            
            if cash > 100:
                buy_amount = cash * POSITION_SIZE
                buy_qty = buy_amount / ask  # Buy at ask
                print(f"[BUYING] {buy_qty:.4f} ETH @ ${ask:.1f} = ${buy_amount:.0f}")
                fill = buy(buy_qty)
                if fill:
                    entry_price = fill
                    qty = buy_qty
                    print(f"[BOUGHT] {buy_qty:.4f} ETH @ ${fill:.1f}")
                    time.sleep(2)
        
        # Short entry: Price near top of range (overbought) - skip for now, crypto only long
    
    # Check target
    if virtual_balance >= 500:
        print("\n" + "=" * 60)
        print(f"10x TARGET! Virtual: ${virtual_balance:.2f}")
        print(f"Trades: {trades} | W/L: {wins}/{losses}")
        print("=" * 60)
        break
    
    time.sleep(CYCLE_SEC)