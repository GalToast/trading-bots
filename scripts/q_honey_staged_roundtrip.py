#!/usr/bin/env python3
"""
HONEY Staged Long-Only Roundtrip Proof at 0.10 offset.

1. Wait for trigger (spread>=150, depth>=$15, ask_down>=20 OR bid_up>=20)
2. Entry fill proxy: BUY at 0.10 offset, TTL 10s
3. If entry fills: wait for exit signal, SELL at profit floor, TTL 10s
4. Track: entry_fill, exit_fill, net_bps

Usage:
    python scripts/q_honey_staged_roundtrip.py --cycles 5 --entry-ttl 10 --exit-ttl 10
"""
import sys, json, time; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float
from build_kraken_tiny_live_fire_queue import legal_maker_buy_price_at_offset
from run_kraken_tiny_live_maker_roundtrip_probe import legal_volume, maker_exit_floor_price
from crossing_pressure_scanner import compute_spread_bps

c = KrakenSpotClient()

# Get pair info
assets = c.asset_pairs()
pair_info = None
for k, v in assets.items():
    if v.get('altname','').upper() == 'HONEYUSD':
        from kraken_spot_client import parse_pair
        pair_info = parse_pair(k, v)
        break
if not pair_info:
    class FakePair:
        tick_size = 1e-05; lot_decimals = 5
    pair_info = FakePair()

cycles = 5
entry_ttl = 10
exit_ttl = 10
offset = 0.10

print(f"HONEY staged roundtrip: {cycles} cycles, 0.10 offset, entry TTL={entry_ttl}s, exit TTL={exit_ttl}s")
print()

entry_fills = 0
exit_fills = 0
roundtrips = 0
net_bps_list = []

for cycle in range(cycles):
    print(f"Cycle {cycle+1}: waiting for trigger...")

    # Poll for trigger
    triggered = False
    trig_tick = None
    for poll in range(60):  # max 60s wait
        try:
            tk = c.ticker(['HONEYUSD'])
            t = tk.get('HONEYUSD', {})
            bid = to_float((t.get('b') or [None])[0])
            ask = to_float((t.get('a') or [None])[0])
            last = to_float((t.get('c') or [None])[0])

            d = c.depth('HONEYUSD', count=10)
            book = d.get('HONEYUSD', d.get('HONEY/USD', {}))
            bids = book.get('bids', [])
            asks = book.get('asks', [])
            bid_d = to_float(bids[0][1]) * to_float(bids[0][0]) if bids else 0
            ask_d = to_float(asks[0][1]) * to_float(asks[0][0]) if asks else 0

            sp = compute_spread_bps(bid, ask) if bid > 0 and ask > 0 else 0

            # Ask-down detection
            if hasattr(c, '_prev_ask') and c._prev_ask > 0:
                ask_down = max(0, (c._prev_ask - ask) / c._prev_ask * 10000)
            else:
                ask_down = 0
            c._prev_ask = ask

            # Bid-up detection
            if hasattr(c, '_prev_bid') and c._prev_bid > 0:
                bid_up = max(0, (bid - c._prev_bid) / c._prev_bid * 10000)
            else:
                bid_up = 0
            c._prev_bid = bid

            if sp >= 150 and bid_d >= 15 and ask_d >= 15 and (ask_down >= 20 or bid_up >= 20):
                triggered = True
                trig_tick = {'bid': bid, 'ask': ask, 'last': last, 'spread': sp, 'bid_depth': bid_d, 'ask_depth': ask_d, 'ask_down': ask_down, 'bid_up': bid_up}
                print(f"  TRIGGER at poll {poll}s: spread={sp:.0f}bps bid_depth=${bid_d:.0f} ask_depth=${ask_d:.0f} ask_down={ask_down:.1f}bps bid_up={bid_up:.1f}bps")
                break

            if poll % 10 == 0:
                print(f"  poll {poll}s: spread={sp:.0f}bps ask_down={ask_down:.1f}bps bid_up={bid_up:.1f}bps")
            time.sleep(1)
        except Exception as e:
            print(f"  poll error: {e}")
            time.sleep(1)

    if not triggered:
        print(f"  TIMEOUT after 60s — no trigger")
        continue

    # Entry fill proxy
    bid = trig_tick['bid']
    ask = trig_tick['ask']
    entry_price = legal_maker_buy_price_at_offset(bid, ask, pair_info.tick_size, offset)
    if entry_price <= 0:
        print(f"  INVALID entry price at 0.10 offset")
        continue

    entry_filled = False
    for i in range(entry_ttl):
        try:
            tk = c.ticker(['HONEYUSD'])
            t = tk.get('HONEYUSD', {})
            f_ask = to_float((t.get('a') or [None])[0])
            f_last = to_float((t.get('c') or [None])[0])
            if f_ask > 0 and f_ask <= entry_price:
                entry_filled = True
                print(f"  ENTRY FILLED at t+{i}s: entry={entry_price:.8f} ask={f_ask:.8f}")
                break
            if f_last > 0 and f_last <= entry_price:
                entry_filled = True
                print(f"  ENTRY FILLED (trade) at t+{i}s: entry={entry_price:.8f} last={f_last:.8f}")
                break
            time.sleep(1)
        except:
            time.sleep(1)

    if not entry_filled:
        print(f"  ENTRY TIMEOUT after {entry_ttl}s: entry={entry_price:.8f}")
        continue

    entry_fills += 1

    # Exit: compute profit floor
    vol = legal_volume(9.0 / entry_price, pair_info.lot_decimals)
    entry_cost = entry_price * vol
    entry_fee = entry_cost * 0.0025
    exit_legal, _ = maker_exit_floor_price(
        entry_cost=entry_cost, entry_fee=entry_fee, volume=vol,
        maker_fee_bps=25.0, target_net_pct=0.001, tick_size=pair_info.tick_size
    )

    # Exit fill proxy: wait for bid >= exit_legal (taker buys at or above our sell price)
    exit_filled = False
    for i in range(exit_ttl):
        try:
            tk = c.ticker(['HONEYUSD'])
            t = tk.get('HONEYUSD', {})
            f_bid = to_float((t.get('b') or [None])[0])
            f_last = to_float((t.get('c') or [None])[0])
            if f_bid > 0 and f_bid >= exit_legal:
                exit_filled = True
                print(f"  EXIT FILLED at t+{i}s: exit={exit_legal:.8f} bid={f_bid:.8f}")
                break
            if f_last > 0 and f_last >= exit_legal:
                exit_filled = True
                print(f"  EXIT FILLED (trade) at t+{i}s: exit={exit_legal:.8f} last={f_last:.8f}")
                break
            time.sleep(1)
        except:
            time.sleep(1)

    if not exit_filled:
        print(f"  EXIT TIMEOUT after {exit_ttl}s: exit={exit_legal:.8f}")
        continue

    exit_fills += 1
    roundtrips += 1

    # Net bps
    gross = (exit_legal - entry_price) / entry_price * 10000
    net = gross - 50
    net_bps_list.append(net)
    print(f"  ROUNDTRIP COMPLETE: entry={entry_price:.8f} exit={exit_legal:.8f} gross={gross:.1f}bps net={net:.1f}bps")

print()
print(f"RESULTS: {cycles} cycles, {entry_fills} entry fills, {exit_fills} exit fills, {roundtrips} roundtrips")
if net_bps_list:
    print(f"Net bps: {[round(x,1) for x in net_bps_list]}")
    print(f"Avg net: {sum(net_bps_list)/len(net_bps_list):.1f}bps")
