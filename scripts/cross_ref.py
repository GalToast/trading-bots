import pandas as pd

df = pd.read_csv('reports/coinbase_spot_fee_survival_training_table_v2.csv')
print('Products in Coinbase training table:')
print(df['product_id'].value_counts())

kraken_winners = ['CHIP-USD', 'DAI-USD', 'GUN-USD', 'GWEI-USD']
for prod in kraken_winners:
    if prod in df['product_id'].values:
        count = len(df[df['product_id'] == prod])
        avg_gross = df[df['product_id'] == prod]['gross_pct'].mean()
        print(f'\n{prod}: {count} signals, avg gross return = {avg_gross:.2f}%')
    else:
        print(f'\n{prod}: NOT in Coinbase training table')
