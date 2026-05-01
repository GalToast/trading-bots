#!/usr/bin/env python3
"""
Kraken Harpoon Executor
Bridges Coinbase micro-anomalies with Kraken execution using an ML Toxicity Model.
Enforces "Toxic Pocket" filtering using Kraken Live Foundry features.
"""

import os
import json
import time
import joblib
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

# Add scripts to path for imports
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "scripts"))

from coinbase_advanced_client import CoinbaseAdvancedClient
from kraken_spot_client import KrakenSpotClient
from predatory_shadow_monitor import PredatoryShadowMonitor, fetch_kraken_btc
from mfe_capture_tracker import MFETracker

# Paths
MODELS_DIR = ROOT / "reports" / "models"
MODEL_PATH = MODELS_DIR / "kraken_toxicity_harpoon_v1.joblib"
KRAKEN_FEATURES_PATH = ROOT / "reports" / "kraken_spot_live_foundry_features.json"
HARPOON_LOG_PATH = ROOT / "reports" / "kraken_harpoon_live_log.jsonl"

# Config
PRODUCTS = ["RAVE-USD", "KAT-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
PROB_THRESHOLD = 0.70  # Higher confidence for live
MIN_TAIL_PROB = 0.60   # Foundry filter
MAX_SPREAD_BPS = 30    # Spread filter

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path, payload):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")

class KrakenHarpoonExecutor:
    def __init__(self, model_payload, products):
        self.model = model_payload["model"]
        self.features = model_payload["features"]
        self.encoder = model_payload["encoder"]
        self.products = products
        
        self.cb_client = CoinbaseAdvancedClient()
        self.kr_client = KrakenSpotClient()
        self.monitor = PredatoryShadowMonitor(self.products)
        self.tracker = MFETracker(default_fee_bps=40.0) # Kraken Taker Fee
        
        self.active_trades = {} # product_id -> trade_id
        
    def load_foundry_features(self):
        if not KRAKEN_FEATURES_PATH.exists():
            return {}
        try:
            with open(KRAKEN_FEATURES_PATH, "r") as f:
                return json.load(f)
        except:
            return {}

    def extract_features(self, event, current_book, previous_book):
        action = event.get("action", "")
        price = float(event.get("price", 0.0))
        reload_multiple = float(event.get("reload_multiple", 0.0))
        vol_24h = float(event.get("vol_24h", current_book.get("vol_24h", 0.0)))
        mag_level = float(event.get("mag_level", 0.0))
        
        current_bid_size = current_book.get("bid_size", 0.0)
        previous_bid_size = previous_book.get("bid_size", 0.0)
        current_ask_size = current_book.get("ask_size", 0.0)
        previous_ask_size = previous_book.get("ask_size", 0.0)
        
        size_delta_pct = 0.0
        if previous_bid_size > 0:
            size_delta_pct = (current_bid_size - previous_bid_size) / previous_bid_size
        elif previous_ask_size > 0:
            size_delta_pct = (current_ask_size - previous_ask_size) / previous_ask_size

        row = {
            "price": price,
            "reload_multiple": reload_multiple,
            "vol_24h": vol_24h,
            "size_delta_pct": size_delta_pct,
            "mag_level": mag_level
        }
        
        df_action = pd.DataFrame([[action]], columns=["action"])
        try:
            encoded = self.encoder.transform(df_action)
            action_cols = self.encoder.get_feature_names_out(["action"])
            for col, val in zip(action_cols, encoded[0]):
                row[col] = val
        except:
            for col in self.encoder.get_feature_names_out(["action"]):
                row[col] = 0.0

        feature_vector = [row.get(f, 0.0) for f in self.features]
        return pd.DataFrame([feature_vector], columns=self.features)

    def run_loop(self):
        print("="*80)
        print("KRAKEN HARPOON EXECUTOR ONLINE")
        print(f"Monitoring: {self.products}")
        print("="*80)
        
        while True:
            # 1. Heartbeat - Update MFE and check exits
            foundry_features = self.load_foundry_features()
            
            for product in list(self.active_trades.keys()):
                trade_id = self.active_trades[product]
                # In a real HF script, we'd use WebSockets. Here we poll.
                try:
                    # Quick Kraken price check
                    ticker = self.kr_client.ticker([product.replace("-", "")])
                    # Kraken returns data indexed by pair name (e.g. RAVEUSD)
                    pair_data = list(ticker.values())[0]
                    curr_price = float(pair_data["c"][0])
                    
                    self.tracker.on_heartbeat(trade_id, curr_price)
                    
                    # Logic: 30s timeout or TP/SL
                    record = self.tracker.trades[trade_id]
                    elapsed = time.time() - record.entry_time
                    
                    pnl_pct = (curr_price / record.entry_price) - 1.0
                    
                    exit_reason = None
                    if pnl_pct >= 0.015: exit_reason = "tp_150bps"
                    elif pnl_pct <= -0.03: exit_reason = "sl_300bps"
                    elif elapsed >= 60: exit_reason = "timeout_60s"
                    
                    if exit_reason:
                        print(f" >>> EXITING {product} | {exit_reason} | PnL: {pnl_pct:.2%}")
                        self.tracker.on_exit(trade_id, curr_price)
                        del self.active_trades[product]
                        
                        # Log it
                        append_jsonl(HARPOON_LOG_PATH, {
                            "ts_utc": utc_now_iso(),
                            "action": "EXIT",
                            "product_id": product,
                            "reason": exit_reason,
                            "pnl_pct": round(pnl_pct, 4)
                        })
                except Exception as e:
                    print(f"Error checking exit for {product}: {e}")

            # 2. Scanning - Detect new Harpoon Triggers
            for product in self.products:
                if product in self.active_trades: continue
                
                try:
                    # Get Coinbase Snapshot (The Radar)
                    ticker = self.cb_client.get_product(product)
                    cb_price = float(ticker.get("price") or 0.0)
                    vol_24h = float(ticker.get("volume_24h") or 0.0)
                    
                    resp = self.cb_client.best_bid_ask([product])
                    book = resp.get("pricebooks")[0]
                    current_book = {
                        "price": cb_price,
                        "bid": float(book["bids"][0]["price"]),
                        "ask": float(book["asks"][0]["price"]),
                        "bid_size": float(book["bids"][0]["size"]),
                        "ask_size": float(book["asks"][0]["size"]),
                        "vol_24h": vol_24h
                    }
                    
                    previous_book = self.monitor.last_book.get(product, {})
                    events = self.monitor.process_snapshot(product, current_book, ts_utc=utc_now_iso())
                    
                    for event in events:
                        action = event.get("action")
                        if action in ["fake_floor_pull_detected", "iceberg_buy_reload_detected", 
                                      "iceberg_sell_reload_detected", "magnetic_wall_touch_detected"]:
                            
                            X = self.extract_features(event, current_book, previous_book)
                            prob = self.model.predict_proba(X)[0][1]
                            
                            # FOUNDRY FILTER
                            pf = foundry_features.get(product, {})
                            tail_prob = pf.get("tail_prob", 0.0)
                            
                            print(f"[{utc_now_iso()}] {product} | {action} | Warp Prob: {prob:.4f} | Tail: {tail_prob:.4f}")
                            
                            if prob >= PROB_THRESHOLD and tail_prob >= MIN_TAIL_PROB:
                                # FIRE HARPOON
                                trade_id = f"harpoon_{int(time.time())}_{product}"
                                print(f" 🎯 FIRING HARPOON on KRAKEN: {product} (Prob: {prob:.4f}, Tail: {tail_prob:.4f})")
                                
                                # Real Kraken Price check for MFE entry
                                ticker = self.kr_client.ticker([product.replace("-", "")])
                                pair_data = list(ticker.values())[0]
                                kr_entry_price = float(pair_data["c"][0])
                                
                                self.tracker.on_entry(trade_id, product, kr_entry_price, predicted_mfe_pct=0.01)
                                self.active_trades[product] = trade_id
                                
                                append_jsonl(HARPOON_LOG_PATH, {
                                    "ts_utc": utc_now_iso(),
                                    "action": "ENTRY",
                                    "product_id": product,
                                    "trigger": action,
                                    "warp_prob": round(prob, 4),
                                    "tail_prob": round(tail_prob, 4),
                                    "kr_price": kr_entry_price
                                })
                except Exception as e:
                    # print(f"Error scanning {product}: {e}")
                    pass
                    
            time.sleep(2.0)

if __name__ == "__main__":
    if not MODEL_PATH.exists():
        print(f"Model not found: {MODEL_PATH}")
    else:
        model_payload = joblib.load(MODEL_PATH)
        executor = KrakenHarpoonExecutor(model_payload, PRODUCTS)
        executor.run_loop()
