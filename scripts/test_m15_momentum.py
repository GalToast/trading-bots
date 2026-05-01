from m1_step_sweep import load_m15_bars, simulate_m1_engine
import MetaTrader5 as mt5
mt5.initialize()
info = mt5.symbol_info('BTCUSD')
bars = load_m15_bars('BTCUSD', 90)
print(f'Loaded {len(bars)} M15 bars')
print()

# Test M15 $20 mom=ON vs mom=OFF
for mom in [True, False]:
    r = simulate_m1_engine('BTCUSD', bars, info, step=20.0, max_open=40, alpha=1.0, gap=1, momentum_gate=mom)
    print(f'M15 $20, mom={mom}: ${r["combined_net_usd"]:,.2f}, {r["realized_closes"]} closes, {r["rearm_opens"]} rearm, max_seen={r["max_open_total"]}')

mt5.shutdown()
