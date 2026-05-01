import json
import joblib
import warnings
import numpy as np
import pandas as pd
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
TICKS = ROOT / "reports" / "cache" / "kraken_spot_live_radar_ticks.json"
TAIL_MODEL = ROOT / "reports" / "models" / "coinbase_spot_tail_predictor.joblib"
FG_MODEL = ROOT / "reports" / "models" / "coinbase_spot_fast_green_model.joblib"

def main():
    data = json.load(open(TICKS))["samples"]
    tail = joblib.load(TAIL_MODEL)
    fg = joblib.load(FG_MODEL)
    
    max_t, max_f = 0, 0
    
    for p, ticks in data.items():
        if len(ticks) < 15: continue
        df = pd.DataFrame(ticks)
        df["mid"] = (df["bid"] + df["ask"]) / 2
        df["time"] = (df["ts"] // 300) * 300
        candles = df.groupby("time")["mid"].agg(["first", "max", "min", "last"]).rename(columns={"first":"open","max":"high","min":"low","last":"close"})
        if len(candles) < 13: continue
        
        # Simple feature calc
        c = candles["close"].values
        ret12 = (c[12:] / c[:-12] - 1) * 10000
        
        # Mock other features to 0 for a quick prob check
        feat_df = pd.DataFrame({"ret_12_bps": ret12})
        for f in tail["numeric"]: 
            if f not in feat_df: feat_df[f] = 0
        for f in tail["categorical"]: feat_df[f] = "unknown"
        
        t_p = tail["model"].predict_proba(feat_df[tail["categorical"] + tail["numeric"]])[:, 1]
        max_t = max(max_t, np.max(t_p))
        
    print(f"Max Tail Prob Found: {max_t:.4f}")

if __name__ == "__main__":
    main()
