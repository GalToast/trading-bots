""" AGGRESSIVE OANDA BOT - Tight TP/SL, fast cycles """
import requests
import time
from datetime import datetime

from oanda_config import get_oanda_config

OANDA = get_oanda_config()
ACCOUNT_ID = OANDA["account_id"]
BASE_URL = OANDA["api_host"]
HEADERS = {"Authorization": f"Bearer {OANDA['api_token']}", "Content-Type": OANDA["content_type"]}

PAIRS = ["EUR_USD", "GBP_USD", "USD_CAD", "AUD_USD"]

# Competition tracking
VIRTUAL_START = 23.77
virtual_balance = VIRTUAL_START
wins = 0
losses = 0
trades = 0
streak = 0

# Ultra aggressive
TP = 0.003   # 0.3% profit
SL = -0.0015  # 0.15% stop loss
SIZE_PCT = 0.20

def get_account():
    r = requests.get(f"{BASE_URL}/v3/accounts/{ACCOUNT_ID}/summary", headers=HEADERS)
    return r.json().get("account", {}) if r.ok else {}

def get_positions():
    r = requests.get(f"{BASE_URL}/v3/accounts/{ACCOUNT_ID}/positions", headers=HEADERS)
    return r.json().get("positions", []) if r.ok else []

def get_price(pair):
    r = requests.get(f"{BASE_URL}/v3/accounts/{ACCOUNT_ID}/pricing", headers=HEADERS, params={"instruments": pair})
    if r.ok:
        prices = r.json().get("prices", [])
        if prices:
            bid = float(prices[0]["bids"][0]["price"])
            ask = float(prices[0]["asks"][0]["price"])
            return bid, ask, (bid + ask) / 2
    return None, None, None

def buy(pair, units):
    global trades
    body = {"order": {"type": "MARKET", "instrument": pair, "units": str(units), "timeInForce": "FOK"}}
    r = requests.post(f"{BASE_URL}/v3/accounts/{ACCOUNT_ID}/orders", headers=HEADERS, json=body)
    if r.ok:
        trades += 1
        fill = r.json().get("orderFillTransaction", {})
        price = float(fill.get("price", 0))
        return True, price
    return False, 0

def sell(pair, units):
    body = {"order": {"type": "MARKET", "instrument": pair, "units": str(-units), "timeInForce": "FOK", "positionFill": "REDUCE_ONLY"}}
    r = requests.post(f"{BASE_URL}/v3/accounts/{ACCOUNT_ID}/orders", headers=HEADERS, json=body)
    if r.ok:
        fill = r.json().get("orderFillTransaction", {})
        price = float(fill.get("price", 0))
        return True, price
    return False, 0

print("="*60)
print("AGGRESSIVE OANDA BOT")
print(f"Virtual Start: ${VIRTUAL_START} | Target: $237.70 (10x)")
print(f"TP: {TP*100:.1f}% | SL: {SL*100:.1f}% | Size: {SIZE_PCT*100:.0f}%")
print("="*60)

cycle = 0
positions = {}  # {pair: {units, entry}}

while True:
    cycle += 1
    now = datetime.now().strftime("%H:%M:%S")
    
    account = get_account()
    nav = float(account.get("NAV", 0))
    balance = float(account.get("balance", 0))
    
    # Sync real positions
    real_positions = get_positions()
    for rp in real_positions:
        pair = rp["instrument"]
        long_units = int(rp.get("long", {}).get("units", 0))
        if long_units > 0 and pair not in positions:
            avg_price = float(rp.get("long", {}).get("averagePrice", 0))
            positions[pair] = {"units": long_units, "entry": avg_price}
    
    # Remove closed positions
    for pair in list(positions.keys()):
        found = False
        for rp in real_positions:
            if rp["instrument"] == pair:
                units = int(rp.get("long", {}).get("units", 0))
                if units > 0:
                    found = True
                break
        if not found:
            del positions[pair]
    
    # Check existing positions for TP/SL
    closed_any = False
    for pair in list(positions.keys()):
        pos = positions[pair]
        bid, ask, mid = get_price(pair)
        if bid:
            pnl = (bid - pos["entry"]) / pos["entry"]  # Use bid for long exit
            
            print(f"[{now}] #{cycle} | {pair} {pos['units']}u | Entry {pos['entry']:.5f} | Bid {bid:.5f} | P/L {pnl*100:.2f}% | NAV ${nav:.2f}")
            
            if pnl >= TP:
                print(f"\n{'='*40}")
                print(f"[TP HIT] {pair} +{pnl*100:.2f}% - WIN!")
                print(f"{'='*40}")
                ok, exit_price = sell(pair, pos["units"])
                if ok:
                    profit = pos["units"] * (exit_price - pos["entry"]) / pos["entry"] * nav
                    virtual_balance += profit * (VIRTUAL_START / 23.77)
                    wins += 1
                    streak += 1
                    SIZE_PCT = min(0.50, SIZE_PCT + 0.05)
                    del positions[pair]
                    closed_any = True
                    time.sleep(1)
            
            elif pnl <= SL:
                print(f"\n{'='*40}")
                print(f"[SL HIT] {pair} {pnl*100:.2f}% - LOSS")
                print(f"{'='*40}")
                ok, exit_price = sell(pair, pos["units"])
                if ok:
                    loss = pos["units"] * (exit_price - pos["entry"]) / pos["entry"] * nav
                    virtual_balance += loss * (VIRTUAL_START / 23.77)
                    losses += 1
                    streak = 0
                    SIZE_PCT = max(0.10, SIZE_PCT - 0.05)
                    del positions[pair]
                    closed_any = True
                    time.sleep(1)
    
    # Open new position if none
    if len(positions) == 0 and not closed_any:
        # Find pair with best momentum
        best_pair = None
        best_move = 0
        
        for pair in PAIRS:
            bid1, ask1, mid1 = get_price(pair)
            time.sleep(0.3)
            bid2, ask2, mid2 = get_price(pair)
            
            if mid1 and mid2:
                move = abs(mid2 - mid1) / mid1
                if move > best_move:
                    best_move = move
                    best_pair = pair
        
        if best_pair:
            bid, ask, mid = get_price(best_pair)
            if ask:
                units = int((balance * SIZE_PCT) / 10)  # ~$1 per unit
                if units < 1:
                    units = 1
                
                print(f"[BUY] {best_pair} {units}u @ {ask:.5f} ({SIZE_PCT*100:.0f}% size)")
                ok, fill_price = buy(best_pair, units)
                if ok:
                    positions[best_pair] = {"units": units, "entry": fill_price}
                    time.sleep(1)
    
    # Check target
    multiplier = virtual_balance / VIRTUAL_START
    if multiplier >= 10:
        print("\n" + "="*60)
        print(f"10x TARGET REACHED! Virtual: ${virtual_balance:.2f}")
        print("="*60)
        break
    
    time.sleep(5)
