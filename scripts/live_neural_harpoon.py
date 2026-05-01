#!/usr/bin/env python3
"""
Live Neural Harpoon Engine
Bridges Coinbase micro-anomalies with Kraken execution using an ML Toxicity Model.
Runs in SHADOW MODE to validate edge before live capital deployment.
"""

import os
import json
import time
import joblib
import pandas as pd
from datetime import datetime, timezone

from coinbase_advanced_client import CoinbaseAdvancedClient
from predatory_shadow_monitor import PredatoryShadowMonitor, detect_predatory_events, fetch_kraken_btc

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
MODELS_DIR = os.path.join(PROJECT_ROOT, "reports", "models")
MODEL_PATH = os.path.join(MODELS_DIR, "kraken_toxicity_harpoon_v2.joblib")
SHADOW_LOG_PATH = os.path.join(PROJECT_ROOT, "reports", "neural_harpoon_shadow_log.jsonl")
VETO_PATH = os.path.join(PROJECT_ROOT, "reports", "kraken_toxic_veto.json")
MER_PATH = os.path.join(PROJECT_ROOT, "reports", "kraken_maker_opportunity_board.json")

PRODUCTS = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD", "HONEY-USD", "XCN-USD", "NCT-USD", "KAT-USD"]

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path, payload):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")

class NeuralHarpoonRunner:
    def __init__(self, model_payload, products):
        self.model = model_payload["model"]
        self.features = model_payload["features"]
        self.encoder = model_payload["encoder"]
        self.products = products
        self.monitor = PredatoryShadowMonitor(self.products)
        self.client = CoinbaseAdvancedClient()
        self.prob_threshold = 0.50 # Confidence Threshold
        self.mer_map = self.load_mer_data()

    def load_mer_data(self):
        mer_map = {}
        if os.path.exists(MER_PATH):
            try:
                with open(MER_PATH, "r") as f:
                    mer_data = json.load(f)
                    for row in mer_data.get("rows", []):
                        mer_map[row["product_id"]] = {
                            "mer": row.get("mer", 0.0),
                            "spread_bps": row.get("spread_bps", 0.0)
                        }
            except Exception:
                pass
        return mer_map

    def extract_features(self, event, current_book, previous_book, ts_utc):
        # Default values
        action = event.get("action", "")
        price = float(event.get("price", 0.0))
        reload_multiple = float(event.get("reload_multiple", 0.0))
        vol_24h = float(event.get("vol_24h", current_book.get("vol_24h", 0.0)))
        mag_level = float(event.get("mag_level", 0.0))
        product_id = event.get("product_id")
        
        # Calculate size delta pct
        current_bid_size = current_book.get("bid_size", 0.0)
        previous_bid_size = previous_book.get("bid_size", 0.0)
        current_ask_size = current_book.get("ask_size", 0.0)
        previous_ask_size = previous_book.get("ask_size", 0.0)
        
        size_delta_pct = 0.0
        if previous_bid_size > 0:
            size_delta_pct = (current_bid_size - previous_bid_size) / previous_bid_size
        elif previous_ask_size > 0:
            size_delta_pct = (current_ask_size - previous_ask_size) / previous_ask_size

        mdata = self.mer_map.get(product_id, {"mer": 0.0, "spread_bps": 0.0})
        ts = datetime.fromisoformat(ts_utc)

        row = {
            "price": price,
            "reload_multiple": reload_multiple,
            "vol_24h": vol_24h,
            "size_delta_pct": size_delta_pct,
            "mag_level": mag_level,
            "mer": mdata["mer"],
            "spread_bps": mdata["spread_bps"],
            "hour_of_day": ts.hour
        }
        
        # Encode action
        df_action = pd.DataFrame([[action]], columns=["action"])
        try:
            encoded = self.encoder.transform(df_action)
            action_cols = self.encoder.get_feature_names_out(["action"])
            for col, val in zip(action_cols, encoded[0]):
                row[col] = val
        except Exception:
            for col in self.encoder.get_feature_names_out(["action"]):
                row[col] = 0.0

        # Construct feature vector in correct order
        feature_vector = [row.get(f, 0.0) for f in self.features]
        return pd.DataFrame([feature_vector], columns=self.features)

    def write_veto(self, product_id, expected_dir):
        vetoes = {}
        if os.path.exists(VETO_PATH):
            try:
                with open(VETO_PATH, "r") as f:
                    vetoes = json.load(f)
            except Exception:
                pass
        
        now = time.time()
        vetoes = {p: v for p, v in vetoes.items() if v["expiry"] > now}
        
        vetoes[product_id] = {
            "dir": expected_dir,
            "expiry": now + 600, # Increased to 10 min for V2
            "detected_at": utc_now_iso(),
            "reason": "neural_harpoon_v2_toxicity"
        }
        
        with open(VETO_PATH, "w") as f:
            json.dump(vetoes, f, indent=2)

    def run_shadow_loop(self):
        print("Neural Harpoon V2 Shadow Engine Online.")
        print(f"Monitoring: {self.products}")
        
        while True:
            kr_price = fetch_kraken_btc()
            self.monitor.note_kraken_btc(kr_price)
            ts_now = utc_now_iso()
            
            for product in self.products:
                try:
                    ticker = self.client.get_product(product)
                    current_price = float(ticker.get("price") or 0.0)
                    vol_24h = float(ticker.get("volume_24h") or 0.0)
                    
                    resp = self.client.best_bid_ask([product])
                    pricebooks = resp.get("pricebooks") or []
                    if not pricebooks: continue
                    
                    book = pricebooks[0]
                    current_book = {
                        "price": current_price,
                        "bid": float(book["bids"][0]["price"]),
                        "ask": float(book["asks"][0]["price"]),
                        "bid_size": float(book["bids"][0]["size"]),
                        "ask_size": float(book["asks"][0]["size"]),
                        "vol_24h": vol_24h
                    }
                    
                    previous_book = self.monitor.last_book.get(product, {})
                    events = self.monitor.process_snapshot(product, current_book, ts_utc=ts_now)
                    
                    for event in events:
                        action = event.get("action")
                        if action in ["fake_floor_pull_detected", "iceberg_buy_reload_detected", 
                                      "iceberg_sell_reload_detected", "magnetic_wall_touch_detected"]:
                            
                            X = self.extract_features(event, current_book, previous_book, ts_now)
                            try:
                                prob = self.model.predict_proba(X)[0][1]
                            except Exception:
                                prob = 0.0
                                
                            print(f"[{ts_now}] {product} | {action} | Warp V2 Prob: {prob:.4f}")
                            
                            if prob >= self.prob_threshold:
                                expected_dir = "SHORT" if "sell" in action or "pull" in action else "LONG"
                                print(f" >>> FIRING SHADOW HARPOON V2: {expected_dir} {product}")
                                self.write_veto(product, expected_dir)
                                
                                log_entry = {
                                    "ts_utc": ts_now,
                                    "product_id": product,
                                    "harpoon_action": f"SHADOW_{expected_dir}",
                                    "trigger_event": action,
                                    "warp_probability": float(round(prob, 4)),
                                    "model_version": "2.0",
                                    "coinbase_entry_price": float(current_price)
                                }
                                append_jsonl(SHADOW_LOG_PATH, log_entry)

                except Exception as e:
                    print(f"Error processing {product}: {e}")
                    
            time.sleep(2.0)

def main():
    if not os.path.exists(MODEL_PATH):
        print(f"Model not found at {MODEL_PATH}. Run Phase 1 first.")
        return
        
    print(f"Loading model from {MODEL_PATH}")
    model_payload = joblib.load(MODEL_PATH)
    
    runner = NeuralHarpoonRunner(model_payload, PRODUCTS)
    runner.run_shadow_loop()

if __name__ == "__main__":
    main()
