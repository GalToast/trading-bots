#!/usr/bin/env python3
"""Retrain V2 model with OneHotEncoder to handle string inputs."""
from __future__ import annotations

import joblib
import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import StandardScaler, OneHotEncoder
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, average_precision_score
from sklearn.impute import SimpleImputer

# Load training data
df = pd.read_csv('reports/coinbase_spot_high_gross_v2_fixed_training_table.csv')

# Define features
categorical_cols = ['archetype', 'trigger', 'confirmation', 'exit', 'sizing', 'trigger_mode']
numeric_cols = ['hour_utc', 'lookback', 'trigger_bps', 'target_pct', 'stop_pct', 'hold_bars', 
                'spread_bps_proxy', 'fee_bps_round_trip', 'ret_1_bps', 'ret_3_bps', 'ret_6_bps', 
                'ret_12_bps', 'range_bps', 'body_bps', 'close_location', 'volume_mult_12', 
                'volatility_12_bps', 'accel_vs_median_abs_12', 'dist_from_12_high_bps', 
                'dist_from_12_low_bps', 'position_in_12_range', 'tail_hit_rate_5', 
                'time_since_tail', 'prev_ret_1_bps', 'trend_3', 'trend_6', 'non_tail_streak']

# Target
target_col = 'target'  # high_gross target
y = df[target_col]

# Prepare feature columns - convert categoricals to strings for OneHotEncoder
X = df[categorical_cols + numeric_cols].copy()
for col in categorical_cols:
    X[col] = X[col].astype(str)

print(f'Training data shape: {X.shape}')
print(f'Target distribution: {y.value_counts().to_dict()}')

# Split
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

# Build pipeline with OneHotEncoder for categoricals and SimpleImputer for NaN
preprocessor = ColumnTransformer(
    transformers=[
        ('cat', Pipeline([
            ('imputer', SimpleImputer(strategy='constant', fill_value='missing')),
            ('onehot', OneHotEncoder(handle_unknown='ignore', sparse_output=False))
        ]), categorical_cols),
        ('num', Pipeline([
            ('imputer', SimpleImputer(strategy='mean')),
            ('scaler', StandardScaler())
        ]), numeric_cols)
    ])

pipeline = Pipeline([
    ('preprocessor', preprocessor),
    ('classifier', LogisticRegression(C=1.0, max_iter=1000, class_weight='balanced'))
])

# Train
print('Training V2 with OneHotEncoder...')
pipeline.fit(X_train, y_train)

# Evaluate
y_pred_proba = pipeline.predict_proba(X_test)[:, 1]
test_auc = roc_auc_score(y_test, y_pred_proba)
test_ap = average_precision_score(y_test, y_pred_proba)

print(f'Test AUC: {test_auc:.4f}')
print(f'Test AP: {test_ap:.4f}')

# Save model
payload = {
    'model': pipeline,
    'categorical_cols': categorical_cols,
    'numeric_cols': numeric_cols,
    'test_auc': test_auc,
    'test_ap': test_ap,
    'version': 'v2_onehot_fixed'
}

joblib.dump(payload, 'reports/models/coinbase_spot_high_gross_tail_predictor_v2_onehot.joblib')
print('\nSaved V2 OneHot model to: reports/models/coinbase_spot_high_gross_tail_predictor_v2_onehot.joblib')
print('This model handles string inputs directly from ml_feature_row()!')
