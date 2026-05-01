""" FLAT MARKET OANDA BOT - Mean Reversion for Forex
Optimized for low-volatility hours:
- Bids at resistance, sells at support
- Uses Bollinger Band-like bands
- Tiny TP/SL for quick scalps
"""
import requests
import time
from datetime import datetime

from oanda_config import get_oanda_config

OANDA = get_oanda_config()
ACCOUNT_ID = OANDA["account_id"]
BASE = OANDA["api_base_v3"]
HEADERS = {"Authorization": f"Bearer {OANDA['api_token']}", "Content-Type": OANDA["content_type"]}

# Pairs to trade
PAIRS = ["EUR_USD", "GBP_USD", "AUD_USD", "USD_CAD"]

# Parameters for flat markets
BAND_PCT = 0.0005      # 0.05% bands around baseline
TP_PCT = 0.0003        # 0.03% profit target
SL_PCT = 0.0002        # 0.02% stop loss
SIZE_PCT = 0.20        # 20% of NAV per trade
CYCLE_SEC = 5          # Check every 5 seconds
BASELINE_PERIOD = 60   # 60 second baseline

# Tracking
prices = {p: [] for p in PAIRS}
wins = 0
losses = 0
total_trades = 0

def get_account():
    r = requests.get(f"{BASE}/accounts/{ACCOUNT_ID}/summary", headers=HEADERS)
    return r.json().get("account", {}) if r.ok else {}

def get_price(pair):
    r = requests.get(f"{BASE}/instruments/{pair}/candles?count=1&price=BAM&granularity=S5", headers=HEADERS)
    if r.ok:
        candles = r.json().get("candles", [])
        if candles:
            c = candles[0]
            return float(c["bid"]["c"]), float(c["ask"]["c"])  # bid, ask
    return None, None

def get_positions():
    r = requests.get(f"{BASE}/accounts/{ACCOUNT_ID}/positions", headers=HEADERS)
    if r.ok:
        return r.json().get("positions", [])
    return []

def close_position(pair):
    pos = get_positions()
    units = 0
    for p in pos:
        if p["instrument"] == pair:
            units = int(p.get("long", {}).get("units", 0))
            break
    if units == 0:
        return False
    body = {"order": {"type": "MARKET", "instrument": pair, "units": str(-units), "timeInForce": "FOK", "positionFill": "REDUCE_ONLY"}}
    r = requests.post(f"{BASE}/accounts/{ACCOUNT_ID}/orders", headers=HEADERS, json=body)
    if r.ok:
        print(f"[CLOSED] {pair} ({units} units)")
        return True
    return False

def buy(pair, units):
    body = {"order": {"type": "MARKET", "instrument": pair, "units": str(units), "timeInForce": "FOK"}}
    r = requests.post(f"{BASE}/accounts/{ACCOUNT_ID}/orders", headers=HEADERS, json=body)
    if r.ok:
        fill = r.json().get("orderFillTransaction", {})
        print(f"[BUY] {pair} {units}u @ {fill.get('price', '?')}")
        return True
    print(f"[BUY FAIL] {r.text[:100]}")
    return False

def get_baseline(pair):
    """Get average price over baseline period"""
    if len(prices[pair]) < 10:
        return None
    recent = prices[pair][-BASELINE_PERIOD:] if len(prices[pair]) >= BASELINE_PERIOD else prices[pair]
    return sum(recent) / len(recent)

print("=" * 60)
print("FLAT MARKET OANDA BOT - Mean Reversion")
print(f"Bands: {BAND_PCT*100:.2f}% | TP: {TP_PCT*100:.2f}% | SL: {SL_PCT*100:.2f}%")
print("=" * 60)

start_nav = float(get_account().get("NAV", 23.77))
print(f"[START] NAV: ${start_nav:.2f}")

cycle = 0
while True:
    cycle += 1
    now = datetime.now().strftime("%H:%M:%S")
    
    # Get current NAV
    acc = get_account()
    nav = float(acc.get("NAV", 0))
    
    # Check open positions
    positions = get_positions()
    open_pairs = []
    for p in positions:
        units = int(p.get("long", {}).get("units", 0))
        if units > 0:
            pair = p["instrument"]
            open_pairs.append(pair)
            # Check TP/SL
            bid, _ = get_price(pair)
            if bid:
                entry = float(p.get("long", {}).get("averagePrice", bid))
                pnl = (bid - entry) / entry
                if pnl >= TP_PCT:
                    print(f"\n{'='*40}")
                    print(f"[TP HIT] {pair} +{pnl*100:.2f}%")
                    if close_position(pair):
                        wins += 1
                        total_trades += 1
                    print("="*40)
                elif pnl <= -SL_PCT:
                    print(f"\n{'='*40}")
                    print(f"[SL HIT] {pair} {pnl*100:.2f}%")
                    if close_position(pair):
                        losses += 1
                        total_trades += 1
                    print("="*40)
    
    # Scan for entries
    if len(open_pairs) == 0:  # Only one position at a time
        for pair in PAIRS:
            bid, ask = get_price(pair)
            if not bid:
                continue
            
            mid = (bid + ask) / 2
            prices[pair].append(mid)
            if len(prices[pair]) > 200:
                prices[pair] = prices[pair][-200:]
            
            baseline = get_baseline(pair)
            if not baseline:
                continue
            
            # Mean reversion: buy if below baseline
            deviation = (mid - baseline) / baseline
            
            if deviation <= -BAND_PCT:  # Price below band - BUY
                print(f"\n[{now}] #{cycle}")
                print(f"[SIGNAL] {pair} deviation {deviation*100:.3f}% < -{BAND_PCT*100:.2f}%")
                size_units = int((nav * SIZE_PCT) / bid)
                if size_units > 0 and buy(pair, size_units):
                    break
    
    # Status
    if cycle % 20 == 0:
        print(f"[{now}] #{cycle} | NAV ${nav:.2f} | W/L {wins}/{losses} | Trades {total_trades}")
    
    # Check 10x target
    if nav >= start_nav * 10:
        print("\n" + "="*60)
        print(f"10x TARGET! NAV: ${nav:.2f}")
        print("="*60)
        break
    
    # Max 1 hour
    if cycle * CYCLE_SEC >= 3600:
        print("\n" + "="*60)
        print(f"1 HOUR DONE | NAV: ${nav:.2f} | W/L: {wins}/{losses}")
        print("="*60)
        break
    
    time.sleep(CYCLE_SEC)
