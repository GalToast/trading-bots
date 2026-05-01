import pandas as pd

df = pd.read_csv('reports/coinbase_spot_fee_survival_training_table_v2.csv')
df['timestamp'] = pd.to_datetime(df['time'], unit='s')

print('Training table time range:')
print(f'  From: {df["timestamp"].min()}')
print(f'  To: {df["timestamp"].max()}')
print(f'  That is {(df["timestamp"].max() - df["timestamp"].min()).days} days of data')
print(f'  Today is: {pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC")}')
print(f'  Age of newest data: {(pd.Timestamp.now(tz="UTC") - df["timestamp"].max()).days} days old')

rave = df[df['product_id'] == 'RAVE-USD']
if len(rave) > 0:
    print(f'\nRAVE-USD signals:')
    print(f'  First: {rave["timestamp"].min()}')
    print(f'  Last: {rave["timestamp"].max()}')
    print(f'  Avg gross return: {rave["gross_pct"].mean():.2f}%')
