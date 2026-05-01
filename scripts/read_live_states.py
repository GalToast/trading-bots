#!/usr/bin/env python3
"""Read and summarize live lane states."""
import json

def summarize(path, symbol_key):
    try:
        d = json.load(open(path))
    except Exception as e:
        print(f"  ERROR: {e}")
        return
    sym = d.get('symbols', {}).get(symbol_key, {})
    rc = sym.get('realized_closes', '?')
    rnet = sym.get('realized_net_usd', '?')
    opens = len(sym.get('open_tickets', []))
    rearm = sym.get('rearm_opens', '?')
    resets = sym.get('anchor_resets', '?')
    anchor = sym.get('anchor', '?')
    runner = d.get('runner', {})
    started = runner.get('started_at', '?')
    heartbeat = runner.get('heartbeat_at', '?')
    print(f"  symbol={symbol_key} anchor={anchor} closes={rc} net=${rnet} open={opens} rearm={rearm} resets={resets}")
    print(f"  started={started} heartbeat={heartbeat}")

print("BTC M5 Warp (live 941780):")
summarize('reports/penetration_lattice_live_btcusd_m5_warp_state.json', 'BTCUSD')

print("\nLive FX Rearm (941777):")
try:
    d = json.load(open('reports/penetration_lattice_live_source_state.json'))
    runner = d.get('runner', {})
    started = runner.get('started_at', '?')
    heartbeat = runner.get('heartbeat_at', '?')
    for sym_key in ['EURUSD', 'GBPUSD']:
        sym = d.get('symbols', {}).get(sym_key, {})
        rc = sym.get('realized_closes', '?')
        rnet = sym.get('realized_net_usd', '?')
        opens = len(sym.get('open_tickets', []))
        rearm = sym.get('rearm_opens', '?')
        anchor = sym.get('anchor', '?')
        print(f"  {sym_key}: anchor={anchor} closes={rc} net=${rnet} open={opens} rearm={rearm}")
    print(f"  started={started} heartbeat={heartbeat}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\nBTC M15 Warp (shadow):")
summarize('reports/penetration_lattice_shadow_btcusd_m15_warp_state.json', 'BTCUSD')

print("\nBTC M15 Warp on20 (shadow):")
summarize('reports/penetration_lattice_shadow_btcusd_m15_warp_on20_state.json', 'BTCUSD')

print("\nGBPUSD Tick-Forward:")
try:
    d = json.load(open('reports/shadow_gbpusd_tick_forward_state.json'))
    runner = d.get('runner', {})
    durable = d.get('durable_proof', {})
    dc = durable.get('close_count', '?')
    dnet = durable.get('close_net_usd', '?')
    opens = len(d.get('symbols', {}).get('GBPUSD', {}).get('open_tickets', []))
    print(f"  durable_closes={dc} durable_net=${dnet} open={opens}")
    print(f"  heartbeat={runner.get('heartbeat_at','?')}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\nKelly Shadow:")
try:
    d = json.load(open('reports/kelly_shadow_state.json'))
    print(f"  cycle={d.get('cycle','?')} equity=${d.get('equity',0):.2f}")
    print(f"  per_coin={d.get('per_coin', {})}")
    print(f"  heartbeat={d.get('runner', {}).get('heartbeat_at', '?')}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\nCFG/ETH Ratio:")
try:
    d = json.load(open('reports/cfg_eth_synthetic_sleeve_shadow_state.json'))
    rc = d.get('realized_closes', '?')
    rnet = d.get('realized_pnl_usd', '?')
    opens = d.get('open_count', '?')
    print(f"  closes={rc} pnl=${rnet} open={opens}")
    print(f"  heartbeat={d.get('heartbeat', 'n/a')}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\nCFG/BTC Ratio:")
try:
    d = json.load(open('reports/cfg_btc_synthetic_sleeve_shadow_state.json'))
    rc = d.get('realized_closes', '?')
    rnet = d.get('realized_pnl_usd', '?')
    opens = d.get('open_count', '?')
    print(f"  closes={rc} pnl=${rnet} open={opens}")
except Exception as e:
    print(f"  ERROR: {e}")
