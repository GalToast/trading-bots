#!/usr/bin/env python3
import json
import pandas as pd
import joblib
import warnings
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
FOUNDRY_PATH = REPORTS / "kraken_spot_live_foundry_features.json"
RADAR_PATH = REPORTS / "kraken_spot_live_radar.json"
PULSE_PATH = REPORTS / "kraken_spot_pulse_board.json"
OUTPUT_PATH = REPORTS / "kraken_maker_opportunity_board.json"
MD_PATH = REPORTS / "kraken_maker_opportunity_board.md"
TAIL_MODEL_PATH = REPORTS / "models" / "coinbase_spot_high_gross_tail_predictor_v2_fixed.joblib"
FAST_GREEN_MODEL_PATH = REPORTS / "models" / "coinbase_spot_fast_green_model.joblib"
TEMPORAL_FEATURES_PATH = REPORTS / "coinbase_spot_temporal_features.json"

def ml_feature_row(pid, opp_dict, feat, temporal_all):
    temporal = temporal_all.get(pid, {})
    return {
        "product_id": "BTC-USD",
        "archetype": "fee_hurdle_breakout",
        "trigger": "five_min_ignition",
        "confirmation": "two_poll_hold",
        "exit": "tight_fee_paid_trail",
        "sizing": "standard_50",
        "trigger_mode": "impulse",
        "hour_utc": datetime.now(timezone.utc).hour,
        "lookback": 1,
        "trigger_bps": 25.0,
        "target_pct": 2.0,
        "stop_pct": 1.0,
        "hold_bars": 12,
        "spread_bps_proxy": float(opp_dict["spread_bps"]),
        "fee_bps_round_trip": 50.0 + float(opp_dict["spread_bps"]),
        "ret_1_bps": float(feat.get("ret_15m_bps", 0)) / 15.0,
        "ret_3_bps": float(feat.get("ret_15m_bps", 0)) / 5.0,
        "ret_6_bps": float(feat.get("ret_15m_bps", 0)) / 2.5,
        "ret_12_bps": float(feat.get("ret_15m_bps", 0)),
        "range_bps": float(feat.get("atr_12_bps", 0)),
        "body_bps": float(feat.get("ret_15m_bps", 0)),
        "close_location": 0.85 if float(feat.get("ret_15m_bps", 0)) > 0 else 0.15,
        "volume_mult_12": 1.0,
        "volatility_12_bps": float(feat.get("atr_12_bps", 0)),
        "accel_vs_median_abs_12": 1.0,
        "dist_from_12_high_bps": 0.0,
        "dist_from_12_low_bps": float(feat.get("atr_12_bps", 0)),
        "position_in_12_range": 0.85 if float(feat.get("ret_15m_bps", 0)) > 0 else 0.15,
        "tail_hit_rate_5": float(temporal.get("tail_hit_rate_5", 0.0)),
        "time_since_tail": float(temporal.get("time_since_tail", 99.0)),
        "prev_ret_1_bps": float(temporal.get("prev_ret_1_bps", 0.0)),
        "trend_3": float(temporal.get("trend_3", 0.0)),
        "trend_6": float(temporal.get("trend_6", 0.0)),
        "non_tail_streak": float(temporal.get("non_tail_streak", 0.0)),
    }

def preprocess_for_model(frame, model_payload):
    model = model_payload["model"]
    encoders = model_payload.get("encoders", {})
    if hasattr(model, "feature_names_in_"):
        feature_cols = list(model.feature_names_in_)
    else:
        feature_cols = list(model_payload.get("categorical", []) + model_payload.get("numeric", []))
    
    df = frame.copy()
    for col in feature_cols:
        if col in encoders:
            le = encoders[col]
            try:
                df[col] = le.transform(df[col].astype(str).fillna("unknown"))
            except:
                df[col] = 0
        elif col in model_payload.get("categorical", []) or col in model_payload.get("categorical_cols", []):
            df[col] = df[col].astype(str).fillna("")
        else:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
            else:
                df[col] = 0.0
    return df[feature_cols]

def main():
    print("KRAKEN MAKER OPPORTUNITY SCANNER (ML UPGRADED V2)")
    
    if not all(p.exists() for p in [FOUNDRY_PATH, RADAR_PATH, PULSE_PATH, TAIL_MODEL_PATH, FAST_GREEN_MODEL_PATH, TEMPORAL_FEATURES_PATH]):
        print("Required files missing.")
        return
        
    with open(FOUNDRY_PATH, "r") as f: features = json.load(f)
    with open(RADAR_PATH, "r") as f: radar = json.load(f)
    with open(PULSE_PATH, "r") as f: pulse = json.load(f)
    with open(TEMPORAL_FEATURES_PATH, "r") as f: temporal_all = json.load(f)
    
    radar_rows = {r["product_id"]: r for r in radar.get("rows", [])}
    pulse_rows = {r["product_id"]: r for r in pulse.get("rows", [])}
    
    print("Loading ML Models...")
    tail_payload = joblib.load(TAIL_MODEL_PATH)
    fg_payload = joblib.load(FAST_GREEN_MODEL_PATH)
    
    opportunities = []
    # Combine radar and foundry. Use radar as the primary source for product existence.
    for pid, r in radar_rows.items():
        feat = features.get(pid, {})
        p = pulse_rows.get(pid, {})
        
        spread_bps = float(r.get("spread_bps", 0))
        # Fallback ATR: Use 50bps if missing
        atr_bps = float(feat.get("atr_12_pct", 0.50)) * 100
        vol_usd = float(r.get("volume_24h_base", 0)) * (float(r.get("bid", 0)) + float(r.get("ask", 0))) / 2.0
        
        if vol_usd < 1000 or spread_bps < 100: continue

        opp = {
            "product_id": pid,
            "playbook": "maker_harvest",
            "signal_state": "live_hot",
            "mer": round(spread_bps / (atr_bps + 1e-9), 4),
            "spread_bps": round(spread_bps, 2),
            "atr_12_bps": round(atr_bps, 2),
            "ret_15m_bps": round(float(feat.get("ret_15m_bps", 0)), 2),
            "vol_24h_usd": round(vol_usd, 0),
            "pulse_score": float(p.get("pulse_score", 0))
        }
        opp["machinegun_score"] = round(opp["mer"] * 10.0, 2)
        
        try:
            f_row = ml_feature_row(pid, opp, feat, temporal_all)
            frame = pd.DataFrame([f_row])
            
            # Tail Score
            t_input = preprocess_for_model(frame, tail_payload)
            tail_p = tail_payload["model"].predict_proba(t_input)[:, 1][0]
            opp["tail_prob"] = round(float(tail_p), 6)
            
            # FG Score
            fg_input = preprocess_for_model(frame, fg_payload)
            fg_p = fg_payload["model"].predict_proba(fg_input)[:, 1][0]
            opp["fast_green_prob"] = round(float(fg_p), 6)
            
            opp["combined_ml_score"] = round(opp["tail_prob"] * opp["fast_green_prob"], 6)
            print(f"  {pid}: Tail={opp['tail_prob']:.4f} FG={opp['fast_green_prob']:.4f}")
        except Exception as e:
            import traceback
            print(f"ML Error for {pid}: {e}")
            traceback.print_exc()
            opp["tail_prob"] = 0.0
            opp["fast_green_prob"] = 0.0
            opp["combined_ml_score"] = 0.0

        opportunities.append(opp)

    opportunities.sort(key=lambda x: x.get("combined_ml_score", 0), reverse=True)
    
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": opportunities
    }
    with open(OUTPUT_PATH, "w") as f: json.dump(payload, f, indent=2)
    
    # MD Output
    lines = ["# Kraken Maker Opportunity Board", "", "| Product | Combined | Tail | FG | MER | Spread | Vol |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for o in opportunities[:30]:
        lines.append(f"| {o['product_id']} | {o.get('combined_ml_score', 0):.4f} | {o.get('tail_prob', 0):.2f} | {o.get('fast_green_prob', 0):.2f} | {o['mer']:.2f} | {o['spread_bps']:.1f} | ${o['vol_24h_usd']:.0f} |")
    
    with open(MD_PATH, "w") as f: f.write("\n".join(lines))
    print(f"DONE! Saved {len(opportunities)} rows.")

if __name__ == "__main__":
    main()
