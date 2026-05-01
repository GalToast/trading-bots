import joblib
import os

MODELS_DIR = os.path.join("reports", "models")
MODEL_PATH = os.path.join(MODELS_DIR, "kraken_toxicity_harpoon_v1.joblib")

if os.path.exists(MODEL_PATH):
    payload = joblib.load(MODEL_PATH)
    print(f"Features: {payload['features']}")
    print(f"Model Type: {type(payload['model'])}")
    if hasattr(payload['model'], 'feature_importances_'):
        print(f"Importances: {payload['model'].feature_importances_}")
else:
    print(f"Model not found at {os.path.abspath(MODEL_PATH)}")
