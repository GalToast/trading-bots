#!/usr/bin/env python3
"""Analyze time distribution in test set to understand compression audit."""
import pandas as pd
import numpy as np
from datetime import datetime

df = pd.read_csv('reports/coinbase_spot_fee_survival_training_table.csv')

split_at = int(len(df) * 0.75)
test_df = df.iloc[split_at:]

print('Test set time analysis:')
print(f'  Total rows: {len(test_df):,}')
print(f'  Unique timestamps: {test_df["time"].nunique():,}')
print(f'  Rows per timestamp (avg): {len(test_df)/test_df["time"].nunique():.1f}')

tail = test_df[test_df['gross_pct'] > 2.5]
print(f'\nTail rows (gross > 2.5%):')
print(f'  Total tail rows: {len(tail):,}')
print(f'  Unique timestamps with tail: {tail["time"].nunique():,}')
print(f'  Tail rows per timestamp (avg): {len(tail)/tail["time"].nunique():.1f}')

times = sorted(test_df['time'].unique())
print(f'\nTime range:')
print(f'  First: {datetime.utcfromtimestamp(times[0])}')
print(f'  Last:  {datetime.utcfromtimestamp(times[-1])}')
span = (datetime.utcfromtimestamp(times[-1]) - datetime.utcfromtimestamp(times[0])).total_seconds() / 3600
print(f'  Span:  {span:.1f} hours ({span/24:.1f} days)')
print(f'  Gaps:  {len(times)} timestamps over {span:.0f}h = {span/len(times):.1f}h avg gap')

days = {}
for t in times:
    day = datetime.utcfromtimestamp(t).strftime('%Y-%m-%d')
    days[day] = days.get(day, 0) + 1

print(f'\nTimestamps per day:')
for day, count in sorted(days.items()):
    print(f'  {day}: {count} timestamps')

# Products per timestamp
print(f'\nProducts per timestamp (sample):')
for t in times[:10]:
    prods = test_df[test_df["time"] == t]["product_id"].nunique()
    print(f'  {datetime.utcfromtimestamp(t)}: {prods} products')
