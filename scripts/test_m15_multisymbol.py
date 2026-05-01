from m1_step_sweep import load_m15_bars, simulate_m1_engine
import MetaTrader5 as mt5
mt5.initialize()

symbols = ['ETHUSD', 'SOLUSD', 'XRPUSD']

print(f"\n{'='*100}")
print(f"  M15 $20 VALIDATION — Multi-Symbol (mom=ON vs mom=OFF)")
print(f"{'='*100}\n")

for sym in symbols:
    info = mt5.symbol_info(sym)
    if info is None:
        print(f'{sym}: NOT AVAILABLE')
        continue
    bars = load_m15_bars(sym, 90)
    if not bars:
        print(f'{sym}: NO M15 BARS')
        continue
    
    print(f'\n{sym}: {len(bars)} bars')
    for mom in [True, False]:
        r = simulate_m1_engine(sym, bars, info, step=20.0, max_open=40, alpha=1.0, gap=1, momentum_gate=mom)
        print(f'  mom={str(mom):<5}: ${r["combined_net_usd"]:>12,.2f}, {r["realized_closes"]:>5} closes, {r["rearm_opens"]:>4} rearm, max_seen={r["max_open_total"]}')

mt5.shutdown()
