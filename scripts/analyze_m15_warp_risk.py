import json

with open('reports/penetration_lattice_shadow_btcusd_m15_warp_state.json') as f:
    state = json.load(f)

btc = state['symbols']['BTCUSD']
realized = btc['realized_net_usd']
closes = btc['realized_closes']
open_tickets = btc['open_tickets']
next_buy = btc['next_buy_level']
next_sell = btc['next_sell_level']
current_price = (next_buy + next_sell) / 2
volume = 0.01

buy_pos = [t for t in open_tickets if t['direction'] == 'BUY']
sell_pos = [t for t in open_tickets if t['direction'] == 'SELL']

buy_float = sum((current_price - t['fill_price']) * volume for t in buy_pos)
sell_float = sum((t['fill_price'] - current_price) * volume for t in sell_pos)
total_float = buy_float + sell_float

lines = [
    f'Current price: {current_price:.2f}',
    f'Open: {len(open_tickets)} ({len(buy_pos)}B/{len(sell_pos)}S)',
    f'BUY float: {buy_float:+.2f}',
    f'SELL float: {sell_float:+.2f}',
    f'Total float: {total_float:+.2f}',
    f'Realized: +{realized:.2f} ({closes} closes)',
    f'Net: {realized + total_float:+.2f}',
    f'Floating/Realized ratio: {abs(total_float)/realized*100:.1f}%',
    f'Runtime: ~3.3h',
    f'Realized/hour: {realized/3.3:.2f}',
]

# Stress test
for delta in [-2000, -1000, -500, 500, 1000, 2000]:
    stress_price = current_price + delta
    b_f = sum((stress_price - t['fill_price']) * volume for t in buy_pos)
    s_f = sum((t['fill_price'] - stress_price) * volume for t in sell_pos)
    net = realized + b_f + s_f
    lines.append(f'BTC {delta:+d} to {stress_price:.0f}: float={b_f+s_f:+.2f}, net={net:+.2f}')

with open('reports/m15_warp_risk_analysis.txt', 'w') as f:
    f.write('\n'.join(lines) + '\n')

print('\n'.join(lines))
