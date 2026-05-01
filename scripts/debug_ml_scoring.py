import joblib
import pandas as pd
import json
from pathlib import Path

TAIL_MODEL_PATH = Path("reports/models/coinbase_spot_high_gross_tail_predictor_v2_fixed.joblib")
payload = joblib.load(TAIL_MODEL_PATH)
model = payload["model"]
encoders = payload["encoders"]
feature_cols = list(model.feature_names_in_)

# Dummy row
row = {col: 0.0 for col in feature_cols}
for col in payload.get("categorical", []) + payload.get("categorical_cols", []):
    row[col] = "0"

df = pd.DataFrame([row])
for col in feature_cols:
    if col in encoders:
        le = encoders[col]
        try:
            df[col] = le.transform(df[col].astype(str))
        except:
            df[col] = 0

p = model.predict_proba(df[feature_cols])
print(f"Probabilities: {p}")
print(f"Classes: {model.classes_}")
