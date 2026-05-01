import os
""" FAST CYCLE ALPACA BOT - Tighter thresholds for rapid testing """
import requests
import time
from datetime import datetime

API_KEY = os.getenv("ALPACA_API_KEY", "")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
BASE_URL = "https://paper-api.alpaca.markets"

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": SECRET_KEY
}

# TIGHTER thresholds for testing
TP = 0.005  # 0.5% profit target (was 3%)
SL = -0.003  # -0.3% stop loss (was -1.5%)
POSITION_SIZE = 0.05  # 5% of equity

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
    """Get live ETH price"""
    r = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS)
    if r.ok:
        for p in r.json():
            if p["symbol"] == "ETHUSD":
                return float(p["current_price"])
    # Fallback to snapshot
    r = requests.get(
        "https://data.alpaca.markets/v1beta3/crypto/us/latest/trades",
        headers=HEADERS,
        params={"symbols": "ETH/USD"}
    )
    if r.ok and r.json().get("trades"):
        t = r.json()["trades"].get("ETH/USD", [])
        if isinstance(t, dict):
            # trades is a dict with keys
            return float(t.get("p", 2100))
        elif isinstance(t, list) and len(t) > 0:
            return float(t[-1]["p"])
    return 2100

def buy(qty):
    global trades
    r = requests.post(
        f"{BASE_URL}/v2/orders",
        headers=HEADERS,
        json={
            "symbol": "ETHUSD",
            "qty": str(qty),
            "side": "buy",
            "type": "market",
            "time_in_force": "ioc"
        }
    )
    if r.ok:
        trades += 1
        print(f"[EXECUTED] BUY {qty:.6f} ETH")
        return True
    print(f"[ERROR] Buy failed: {r.text}")
    return False

def sell(qty):
    r = requests.post(
        f"{BASE_URL}/v2/orders",
        headers=HEADERS,
        json={
            "symbol": "ETHUSD",
            "qty": str(qty),
            "side": "sell",
            "type": "market",
            "time_in_force": "ioc"
        }
    )
    if r.ok:
        print(f"[EXECUTED] SELL {qty:.6f} ETH")
        return True
    print(f"[ERROR] Sell failed: {r.text}")
    return False

print("=" * 60)
print("FAST CYCLE ALPACA BOT")
print(f"TP: {TP*100:.1f}% | SL: {SL*100:.1f}% | Position: {POSITION_SIZE*100:.0f}%")
print("=" * 60)

start_equity = float(get_account()["equity"])
print(f"[START] Equity: ${start_equity:,.2f}")

cycle = 0

while True:
    cycle += 1
    account = get_account()
    equity = float(account["equity"])
    positions = get_positions()
    price = get_price()
    
    now = datetime.now().strftime("%H:%M:%S")
    
    # Find ETH position
    eth_pos = None
    for p in positions:
        if p["symbol"] == "ETHUSD":
            eth_pos = p
    
    if eth_pos:
        entry = float(eth_pos["avg_entry_price"])
        qty = float(eth_pos["qty"])
        pnl = (price - entry) / entry
        
        print(f"[{now}] CYCLE {cycle} | ETH ${price:.2f} | Entry ${entry:.2f} | P/L {pnl*100:.2f}% | Equity ${equity:,.2f} | W/L {wins}/{losses} | Trades {trades}")
        
        # Check TP/SL
        if pnl >= TP:
            print(f"\n{'='*40}")
            print(f"[TP HIT] {pnl*100:.2f}% - CLOSING FOR PROFIT!")
            print(f"{'='*40}")
            if sell(qty):
                wins += 1
                trades += 1
                time.sleep(2)
        
        elif pnl <= SL:
            print(f"\n{'='*40}")
            print(f"[SL HIT] {pnl*100:.2f}% - STOPPING LOSS!")
            print(f"{'='*40}")
            if sell(qty):
                losses += 1
                trades += 1
                time.sleep(2)
    
    else:
        # No position - buy immediately
        print(f"[{now}] CYCLE {cycle} | ETH ${price:.2f} | Cash ${float(account['cash']):,.2f} | Equity ${equity:,.2f} | W/L {wins}/{losses}")
        
        if float(account["cash"]) > 100:
            buy_amount = equity * POSITION_SIZE
            qty = buy_amount / price
            print(f"[BUY SIGNAL] {qty:.6f} ETH @ ${price:.2f} (${buy_amount:,.2f})")
            if buy(qty):
                time.sleep(2)
    
    # Check multiplier
    multiplier = equity / start_equity
    if multiplier >= 10:
        print("\n" + "=" * 60)
        print(f"10x TARGET REACHED! Equity: ${equity:,.2f}")
        print(f"W/L: {wins}/{losses} | Trades: {trades}")
        print("=" * 60)
        break
    
    time.sleep(5)  # 5 second cycles (faster)