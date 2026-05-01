#!/usr/bin/env python3
"""Build label encoders for V2 model categorical columns."""
from __future__ import annotations

import joblib
import pandas as pd
from sklearn.preprocessing import LabelEncoder

# Load training data to get string-to-numeric mapping
df = pd.read_csv('reports/coinbase_spot_high_gross_v2_fixed_training_table.csv')

# Categorical columns
cat_cols = ['archetype', 'trigger', 'confirmation', 'exit', 'sizing', 'trigger_mode']

# Load V2 model payload
payload = joblib.load('reports/models/coinbase_spot_high_gross_tail_predictor_v2_fixed.joblib')

# Build encoders
encoders = {}
for col in cat_cols:
    le = LabelEncoder()
    # Fit on unique string values that ml_feature_row() would return
    # For now, fit on the unique values from training data (converted to string)
    unique_strs = [str(x) for x in df[col].unique()]
    le.fit(unique_strs)
    encoders[col] = le
    print(f'{col}: {dict(zip(le.classes_, le.transform(le.classes_)))}')

# Save encoders to payload
payload['encoders'] = encoders
joblib.dump(payload, 'reports/models/coinbase_spot_high_gross_tail_predictor_v2_fixed.joblib')
print('\nSaved encoders to V2 model payload!')
