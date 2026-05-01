#!/usr/bin/env python3
"""Time distribution analysis for combined scorer signals"""
import pandas as pd
import numpy as np

df = pd.read_csv('reports/coinbase_spot_fee_survival_training_table_v2.csv')
df['net_pct'] = df['gross_pct'] - 2.4
df['timestamp'] = pd.to_datetime(df['time'], unit='s')

# Chronological split
split_at = int(len(df) * 0.75)
test_df = df.iloc[split_at:].copy()

print('Test set time range:')
print(f'  From: {test_df["timestamp"].min()}')
print(f'  To: {test_df["timestamp"].max()}')
print(f'  Duration: {(test_df["timestamp"].max() - test_df["timestamp"].min()).days} days')

# Group by hour to see signal frequency
test_df['hour'] = test_df['timestamp'].dt.floor('h')
signals_per_hour = test_df.groupby('hour').size()

print(f'\nSignals per hour:')
print(f'  Mean: {signals_per_hour.mean():.1f}')
print(f'  Max: {signals_per_hour.max()}')
print(f'  Hours with signals: {len(signals_per_hour)}')

# Group by product + hour to find unique execution cycles
cycles = test_df.groupby(['product_id', 'hour']).agg({
    'net_pct': ['count', 'max', 'mean']
}).reset_index()
cycles.columns = ['product', 'hour', 'signal_count', 'max_net', 'avg_net']

print(f'\nUnique execution cycles (product + hour): {len(cycles)}')
print(f'Per product:')
for prod in cycles['product'].unique():
    prod_cycles = cycles[cycles['product'] == prod]
    print(f'  {prod}: {len(prod_cycles)} cycles, avg max_net={prod_cycles["max_net"].mean():.2f}%, survival={(prod_cycles["max_net"] > 2.4).mean():.1%}')

# How many cycles per day?
cycles['date'] = pd.to_datetime(cycles['hour']).dt.date
cycles_per_day = cycles.groupby('date').size()
print(f'\nCycles per day:')
print(cycles_per_day)
print(f'\nSummary:')
print(f'  Mean cycles/day: {cycles_per_day.mean():.1f}')
print(f'  Max cycles/day: {cycles_per_day.max()}')
