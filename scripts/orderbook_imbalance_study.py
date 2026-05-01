import json
import time
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "reports" / "orderbook_imbalance_study.jsonl"

PRODUCT = "RAVE-USD"

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def compute_rsi(closes, period=4):
    if len(closes) < period + 1: return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
    return 100.0

def main():
    client = CoinbaseAdvancedClient()
    print(f"Starting Order Book Imbalance Study for {PRODUCT}...")
    
    price_history = []
    
    # Initialize price history with recent candles
    try:
        resp = client.market_candles(PRODUCT, granularity="FIVE_MINUTE")
        price_history = [float(c["close"]) for c in resp.get("candles", [])][-50:]
    except:
        pass

    last_ratio = 1.0
    try:
        while True:
            # 1. Fetch Order Book
            try:
                resp = client.best_bid_ask([PRODUCT])
                book = resp["pricebooks"][0]
                bid_size = sum(float(b["size"]) for b in book["bids"])
                ask_size = sum(float(a["size"]) for a in book["asks"])
                imbalance = (bid_size - ask_size) / (bid_size + ask_size) if (bid_size + ask_size) > 0 else 0
                bid_ask_ratio = bid_size / ask_size if ask_size > 0 else 999.0
                
                ratio_velocity = bid_ask_ratio - last_ratio
                last_ratio = bid_ask_ratio
            except Exception as e:
                print(f"Book error: {e}")
                time.sleep(10)
                continue

            # 2. Fetch Latest RSI (Approximated from current price)
            try:
                # Get current price
                ticker = client.get_product(PRODUCT)
                current_price = float(ticker.get("price", 0))
                
                temp_history = price_history + [current_price]
                rsi = compute_rsi(temp_history, 4)
            except:
                rsi = 50.0

            # 3. Log State
            log_entry = {
                "ts": utc_now_iso(),
                "product": PRODUCT,
                "price": current_price,
                "rsi": round(rsi, 2),
                "bid_size": round(bid_size, 2),
                "ask_size": round(ask_size, 2),
                "imbalance": round(imbalance, 4),
                "ratio": round(bid_ask_ratio, 2),
                "velocity": round(ratio_velocity, 2)
            }
            
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry) + "\n")
            
            # Print if signal is approaching
            if rsi < 50:
                status = "SIGNAL" if (rsi < 45 and bid_ask_ratio > 3.0 and ratio_velocity > 0) else "WATCH"
                print(f"[{log_entry['ts']}] {status} | RSI={rsi:.1f} | Ratio={bid_ask_ratio:.1f} | Vel={ratio_velocity:.2f}")

            time.sleep(10) # High-frequency sampling
            
    except KeyboardInterrupt:
        print("Study stopped.")

if __name__ == "__main__":
    main()
