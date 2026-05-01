""" FAST CYCLE OANDA BOT - Tighter thresholds for rapid testing """
import requests
import time
from datetime import datetime

from oanda_config import get_oanda_config

OANDA = get_oanda_config()
ACCOUNT_ID = OANDA["account_id"]
BASE = OANDA["api_host"]

HEADERS = {
    "Authorization": f"Bearer {OANDA['api_token']}",
    "Content-Type": OANDA["content_type"]
}

PAIRS = ["EUR_USD", "GBP_USD", "AUD_USD", "USD_CAD"]

# TIGHTER thresholds
TP = 0.003  # 0.3% profit (was 0.5%)
SL = -0.002  # -0.2% stop (was -0.25%)
POSITION_SIZE = 0.20  # 20% of NAV

wins = 0
losses = 0
trades = 0

def get_account():
    r = requests.get(f"{BASE}/v3/accounts/{ACCOUNT_ID}/summary", headers=HEADERS)
    return r.json()["account"] if r.ok else {}

def get_positions():
    r = requests.get(f"{BASE}/v3/accounts/{ACCOUNT_ID}/openPositions", headers=HEADERS)
    return r.json().get("positions", []) if r.ok else []

def get_price(pair):
    r = requests.get(f"{BASE}/v3/accounts/{ACCOUNT_ID}/pricing?instruments={pair}", headers=HEADERS)
    if r.ok:
        data = r.json()
        if data.get("prices"):
            bid = float(data["prices"][0]["bids"][0]["price"])
            ask = float(data["prices"][0]["asks"][0]["price"])
            return (bid, ask, (bid + ask) / 2)
    return (0, 0, 0)

def close_position(pair, units):
    body = {
        "order": {
            "type": "MARKET",
            "instrument": pair,
            "units": str(-units),
            "timeInForce": "FOK",
            "positionFill": "REDUCE_ONLY"
        }
    }
    r = requests.post(f"{BASE}/v3/accounts/{ACCOUNT_ID}/orders", headers=HEADERS, json=body)
    if r.ok:
        print(f"[EXECUTED] CLOSE {pair} ({units} units)")
        return True
    print(f"[ERROR] Close failed: {r.text[:100]}")
    return False

def buy(pair, units):
    global trades
    body = {
        "order": {
            "type": "MARKET",
            "instrument": pair,
            "units": str(units),
            "timeInForce": "FOK"
        }
    }
    r = requests.post(f"{BASE}/v3/accounts/{ACCOUNT_ID}/orders", headers=HEADERS, json=body)
    if r.ok:
        trades += 1
        fill = r.json().get("orderFillTransaction", {})
        price = fill.get("price", "unknown")
        print(f"[EXECUTED] BUY {units} {pair} @ {price}")
        return True
    print(f"[ERROR] Buy failed: {r.text[:100]}")
    return False

def get_momentum(pair):
    """Simple momentum from last 2 prices"""
    bid1, ask1, mid1 = get_price(pair)
    time.sleep(0.5)
    bid2, ask2, mid2 = get_price(pair)
    if mid1 and mid2:
        return (mid2 - mid1) / mid1
    return 0

print("=" * 60)
print("FAST CYCLE OANDA BOT")
print(f"TP: {TP*100:.1f}% | SL: {SL*100:.1f}% | Position: {POSITION_SIZE*100:.0f}%")
print("=" * 60)

acc = get_account()
start_nav = float(acc["NAV"])
print(f"[START] NAV: ${start_nav:.2f}")

cycle = 0

while True:
    cycle += 1
    acc = get_account()
    nav = float(acc["NAV"])
    positions = get_positions()
    
    now = datetime.now().strftime("%H:%M:%S")
    
    # Check existing positions
    if positions:
        for pos in positions:
            pair = pos["instrument"]
            units = int(pos.get("long", {}).get("units", 0))
            if units == 0:
                continue
            
            entry = float(pos["long"]["averagePrice"])
            bid, ask, mid = get_price(pair)
            
            # For LONG, use bid to close
            pnl = (bid - entry) / entry
            
            print(f"[{now}] CYCLE {cycle} | {pair} {units}u | Entry {entry:.5f} | Bid {bid:.5f} | P/L {pnl*100:.2f}% | NAV ${nav:.2f} | W/L {wins}/{losses}")
            
            # Check TP/SL
            if pnl >= TP:
                print(f"\n{'='*40}")
                print(f"[TP HIT] {pair} {pnl*100:.2f}% - PROFIT!")
                print(f"{'='*40}")
                if close_position(pair, units):
                    wins += 1
                    trades += 1
                    time.sleep(2)
            
            elif pnl <= SL:
                print(f"\n{'='*40}")
                print(f"[SL HIT] {pair} {pnl*100:.2f}% - STOP!")
                print(f"{'='*40}")
                if close_position(pair, units):
                    losses += 1
                    trades += 1
                    time.sleep(2)
    
    else:
        # No position - pick pair with best momentum
        print(f"[{now}] CYCLE {cycle} | NAV ${nav:.2f} | W/L {wins}/{losses} | Trades {trades}")
        
        best_pair = None
        best_mom = 0
        
        for pair in PAIRS:
            mom = get_momentum(pair)
            print(f"  {pair}: momentum {mom*100:.3f}%")
            if mom > best_mom:
                best_mom = mom
                best_pair = pair
        
        if best_pair:
            bid, ask, mid = get_price(best_pair)
            # Position size in units (for forex, units = base currency)
            units = int(nav * POSITION_SIZE / mid)
            
            print(f"[ENTRY] {best_pair} momentum {best_mom*100:.3f}%")
            print(f"[BUY] {units} units = ${nav * POSITION_SIZE:.2f}")
            
            if buy(best_pair, units):
                time.sleep(2)
    
    # Check multiplier
    multiplier = nav / start_nav
    if multiplier >= 10:
        print("\n" + "=" * 60)
        print(f"10x TARGET! NAV: ${nav:.2f}")
        print(f"W/L: {wins}/{losses} | Trades: {trades}")
        print("=" * 60)
        break
    
    time.sleep(5)  # 5 second cycles
