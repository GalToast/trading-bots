import os
""" AGGRESSIVE ALPACA BOT - Fixed position tracking """
import requests
import time
from datetime import datetime

API_KEY = os.getenv("ALPACA_API_KEY", "")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
BASE_URL = "https://paper-api.alpaca.markets"
HEADERS = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": SECRET_KEY}

VIRTUAL_START = 50.0
TARGET = 500.0
TP = 0.002   # 0.2% profit
SL = -0.001  # 0.1% stop loss
SIZE_PCT = 0.20

wins = 0
losses = 0
streak = 0
max_streak = 0
virtual_balance = VIRTUAL_START

def get_account():
    r = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS)
    return r.json() if r.ok else {}

def get_positions():
    r = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS)
    return r.json() if r.ok else []

def get_price():
    """Get live ETH price from quotes"""
    r = requests.get(
        "https://data.alpaca.markets/v1beta3/crypto/us/latest/quotes",
        headers=HEADERS,
        params={"symbols": "ETH/USD"}
    )
    if r.ok:
        quotes = r.json().get("quotes", {})
        if quotes and "ETH/USD" in quotes:
            q = quotes["ETH/USD"]
            # Use ask for buying, bid for selling
            return (float(q["bp"]), float(q["ap"]))  # (bid, ask)
    # Fallback: check position
    for p in get_positions():
        if p["symbol"] == "ETHUSD":
            return (float(p["current_price"]), float(p["current_price"]))
    return (None, None)

def buy(qty):
    r = requests.post(
        f"{BASE_URL}/v2/orders",
        headers=HEADERS,
        json={"symbol": "ETHUSD", "qty": str(round(qty, 6)), "side": "buy", "type": "market", "time_in_force": "ioc"}
    )
    if r.ok:
        data = r.json()
        fill = data.get("filled_avg_price")
        if fill:
            return float(fill)
        # Check if pending
        if data.get("status") in ["pending_new", "new"]:
            print(f"[BUY PENDING] qty={qty:.4f}")
            return 2100  # Approximate fill
    print(f"[BUY ERROR] {r.text[:100]}")
    return None

def sell(qty):
    r = requests.post(
        f"{BASE_URL}/v2/orders",
        headers=HEADERS,
        json={"symbol": "ETHUSD", "qty": str(round(qty, 6)), "side": "sell", "type": "market", "time_in_force": "ioc"}
    )
    if not r.ok:
        print(f"[SELL ERROR] {r.text[:100]}")
    return r.ok

print("=" * 60)
print(f"AGGRESSIVE ALPACA BOT")
print(f"Virtual Start: ${VIRTUAL_START} | Target: ${TARGET}")
print(f"TP: {TP*100}% | SL: {SL*100}% | Size: {SIZE_PCT*100}%")
print("=" * 60)

cycle = 0
start_time = time.time()

while True:
    cycle += 1
    account = get_account()
    positions = get_positions()
    bid, ask = get_price()
    
    if not bid or not ask:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] No price data")
        time.sleep(3)
        continue
    
    price = ask  # Use ask price (what we'd pay to buy)
    
    # Check for ETH position
    eth_pos = None
    for p in positions:
        if p["symbol"] == "ETHUSD":
            eth_pos = p
    
    elapsed = (time.time() - start_time) / 3600
    now = datetime.now().strftime("%H:%M:%S")
    
    if eth_pos:
        # HAVE POSITION - monitor for TP/SL
        entry = float(eth_pos["avg_entry_price"])
        qty = float(eth_pos["qty"])
        current = float(eth_pos["current_price"])
        pnl = (current - entry) / entry
        
        # Track virtual P/L
        real_pl = float(eth_pos["unrealized_pl"])
        virtual_pl = real_pl * (VIRTUAL_START / float(account["equity"]))
        virtual_balance = VIRTUAL_START + virtual_pl
        
        print(f"[{now}] #{cycle} | ETH ${current:.1f} | Entry ${entry:.1f} | P/L {pnl*100:.2f}% | Virtual ${virtual_balance:.2f} | W/L {wins}/{losses} | Streak {streak}")
        
        if pnl >= TP:
            print(f"\n{'='*40}")
            print(f"[TP HIT] {pnl*100:.2f}% - WIN!")
            print(f"{'='*40}")
            if sell(qty):
                virtual_balance = VIRTUAL_START + (qty * (current - entry) * (VIRTUAL_START / float(account["equity"])))
                wins += 1
                streak += 1
                max_streak = max(max_streak, streak)
                SIZE_PCT = min(0.50, SIZE_PCT + 0.05)  # Compound on win
                VIRTUAL_START = virtual_balance  # Compound virtual too!
                print(f"[WIN] Virtual now ${virtual_balance:.2f} | Size {SIZE_PCT*100:.0f}%")
            time.sleep(2)
        
        elif pnl <= SL:
            print(f"\n{'='*40}")
            print(f"[SL HIT] {pnl*100:.2f}% - LOSS")
            print(f"{'='*40}")
            if sell(qty):
                virtual_balance = VIRTUAL_START + (qty * (current - entry) * (VIRTUAL_START / float(account["equity"])))
                losses += 1
                streak = 0
                SIZE_PCT = max(0.10, SIZE_PCT - 0.05)  # Reduce after loss
                VIRTUAL_START = max(10, virtual_balance)  # Don't go below $10
                print(f"[LOSS] Virtual now ${virtual_balance:.2f} | Size {SIZE_PCT*100:.0f}%")
            time.sleep(2)
    
    else:
        # NO POSITION - check if can buy
        cash = float(account.get("cash", 0))
        if cash > 100:
            print(f"[{now}] #{cycle} | ETH ${price:.1f} | Cash ${cash:.0f} | Virtual ${virtual_balance:.2f} | W/L {wins}/{losses}")
            buy_amount = cash * SIZE_PCT
            qty = buy_amount / price
            
            fill_price = buy(qty)
            if fill_price:
                print(f"[BUY EXECUTED] {qty:.4f} ETH @ ${fill_price:.1f} (${buy_amount:.0f})")
                time.sleep(2)
            else:
                print("[BUY FAILED]")
    
    # Check target
    if virtual_balance >= TARGET:
        print("\n" + "="*60)
        print(f"TARGET REACHED! Virtual: ${virtual_balance:.2f}")
        print(f"W/L: {wins}/{losses} | Max Streak: {max_streak} | Time: {elapsed:.2f}h")
        print("="*60)
        break
    
    if elapsed >= 1:  # 1 hour max for this test
        print("\n" + "="*60)
        print(f"1 HOUR ELAPSED | Virtual: ${virtual_balance:.2f}")
        print(f"W/L: {wins}/{losses} | Max Streak: {max_streak}")
        print("="*60)
        break
    
    time.sleep(3)