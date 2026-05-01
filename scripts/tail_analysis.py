#!/usr/bin/env python3
"""Analyze what features distinguish tail moves (>2.5% gross) from the rest."""
import pandas as pd
import numpy as np

df = pd.read_csv('reports/coinbase_spot_fee_survival_training_table.csv')
df['is_tail'] = df['gross_pct'] > 2.5

features = ['ret_1_bps', 'ret_3_bps', 'ret_6_bps', 'ret_12_bps',
            'range_bps', 'body_bps', 'volume_mult_12', 'volatility_12_bps',
            'accel_vs_median_abs_12']

print('=== TAIL vs NON-TAIL Feature Comparison ===')
print(f'{"Feature":<25} {"Non-Tail Mean":>15} {"Tail Mean":>15} {"Ratio":>8}')
print('-' * 65)
for f in features:
    non_tail = df[~df['is_tail']][f].mean()
    tail_val = df[df['is_tail']][f].mean()
    ratio = tail_val / non_tail if non_tail != 0 else float('inf')
    print(f'{f:<25} {non_tail:>15.2f} {tail_val:>15.2f} {ratio:>8.2f}x')

print()
print('Hour distribution for tail:')
hourly_tail = df[df['is_tail']].groupby('hour_utc')['gross_pct'].count()
for h in range(24):
    count = hourly_tail.get(h, 0)
    print(f'  {h:02d}:00 UTC: {count:>5} tail rows')

print()
print('Top 10 product/setup combos in tail:')
tail = df[df['is_tail']]
setup_tail = tail.groupby(['product_id', 'trigger_mode', 'confirmation']).agg(
    count=('gross_pct', 'count'),
    avg_gross=('gross_pct', 'mean'),
).sort_values('avg_gross', ascending=False).head(10)

for (product, trigger, confirm), row in setup_tail.iterrows():
    print(f'  {product:<12} {trigger:<18} {confirm:<18}  {row["count"]:>3} rows  avg {row["avg_gross"]:>6.2f}%')

print()
print('Net % after 2.4% fees for tail rows:')
tail_net = tail['gross_pct'] - 2.4
print(f'  Mean net: {tail_net.mean():.4f}%')
print(f'  Median net: {tail_net.median():.4f}%')
print(f'  % profitable: {(tail_net > 0).mean()*100:.1f}%')
print(f'  Cumulative net: {tail_net.sum():.2f}%')
