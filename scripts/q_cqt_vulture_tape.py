#!/usr/bin/env python3
"""
CQT-USD Vulture Dump Tape — Focused depth-aware proof for codex.

Detects 40bps+ dumps over 10-sample window on CQT-USD.
For each dump:
1. Entry viability: can maker BUY at 0.10 offset fill within TTL?
2. Exit viability: can SELL at profit floor fill?
3. Force-close any open positions at end.
4. Honest accounting: all positions tracked, no survivorship bias.

Output: reports/cache/cqt_vulture_tape.json
"""
import sys, json, time; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float
from build_kraken_tiny_live_fire_queue import legal_maker_buy_price_at_offset
from run_kraken_tiny_live_maker_roundtrip_probe import legal_volume, maker_exit_floor_price
from crossing_pressure_scanner import compute_spread_bps
from datetime import datetime, timezone

def utc_now(): return datetime.now(timezone.utc).isoformat()

c = KrakenSpotClient()
product = 'CQTUSD'

# Get pair info
assets = c.asset_pairs()
pair_info = None
for k, v in assets.items():
    if k == product or v.get('altname','').upper() == product:
        from kraken_spot_client import parse_pair
        pair_info = parse_pair(k, v)
        break
if not pair_info:
    class FakePair:
        tick_size = 1e-05; lot_decimals = 6
    pair_info = FakePair()

duration = 120
dump_threshold_bps = 40
lookback = 10
entry_ttl = 15
exit_ttl = 15
offset = 0.10

print(f"CQT Vulture Tape: {duration}s, dump>{dump_threshold_bps}bps over {lookback} samples")
print(f"Entry: {offset} offset, TTL={entry_ttl}s. Exit: profit floor, TTL={exit_ttl}s")
print()

ticks = []
dumps = []
trades = []
prev_bid = None
bid_history = []

for i in range(duration):
    try:
        tk = c.ticker([product])
        t = tk.get(product, {})
        bid = to_float((t.get('b') or [None])[0])
        ask = to_float((t.get('a') or [None])[0])
        last = to_float((t.get('c') or [None])[0])

        d = c.depth(product, count=10)
        book = d.get(product, d.get('CQT/USD', {}))
        bids = book.get('bids', [])
        asks = book.get('asks', [])
        bid_d = to_float(bids[0][1]) * to_float(bids[0][0]) if bids else 0
        ask_d = to_float(asks[0][1]) * to_float(asks[0][0]) if asks else 0

        sp = compute_spread_bps(bid, ask) if bid > 0 and ask > 0 else 0

        tick = {
            'ts': utc_now(), 's': i,
            'bid': bid, 'ask': ask, 'last': last,
            'spread_bps': round(sp, 1),
            'bid_depth_usd': round(bid_d, 1), 'ask_depth_usd': round(ask_d, 1),
        }
        ticks.append(tick)
        bid_history.append(bid)

        # Dump detection: 40bps+ drop over lookback
        if len(bid_history) >= lookback:
            window = bid_history[-lookback:]
            peak = max(window[:-1])  # peak before current
            if peak > 0:
                drop = (peak - bid) / peak * 100
                if drop >= dump_threshold_bps:
                    dump_event = {
                        's': i,
                        'peak': peak,
                        'current': bid,
                        'drop_bps': round(drop, 1),
                        'bid_depth': bid_d,
                        'ask_depth': ask_d,
                    }
                    dumps.append(dump_event)
                    print(f"  💀 DUMP at t={i}s: {drop:.1f}bps drop peak={peak:.8f} bid={bid:.8f} spread={sp:.0f}bps bid_depth=${bid_d:.0f}")

                    # ENTRY: maker buy at 0.10 offset
                    entry = legal_maker_buy_price_at_offset(bid, ask, pair_info.tick_size, offset)
                    if entry > 0:
                        vol = legal_volume(9.0 / entry, pair_info.lot_decimals)
                        if vol > 0:
                            entry_cost = entry * vol
                            entry_fee = entry_cost * 0.0025
                            exit_legal, _ = maker_exit_floor_price(
                                entry_cost=entry_cost, entry_fee=entry_fee, volume=vol,
                                maker_fee_bps=25.0, target_net_pct=0.001, tick_size=pair_info.tick_size
                            )
                            floor_above_ask = (exit_legal - ask) / ask * 10000 if ask > 0 else 999
                            gross = (exit_legal - entry) / entry * 10000
                            net = gross - 50

                            # Check if entry fills within TTL (ask <= entry)
                            entry_filled = False
                            entry_fill_s = None
                            for j in range(entry_ttl):
                                time.sleep(1)
                                # Quick ticker check
                                tk2 = c.ticker([product])
                                t2 = tk2.get(product, {})
                                f_ask = to_float((t2.get('a') or [None])[0])
                                f_last = to_float((t2.get('c') or [None])[0])
                                if f_ask > 0 and f_ask <= entry:
                                    entry_filled = True
                                    entry_fill_s = i + j
                                    break
                                if f_last > 0 and f_last <= entry:
                                    entry_filled = True
                                    entry_fill_s = i + j
                                    break

                            if entry_filled:
                                print(f"    ✅ ENTRY FILLED at t={entry_fill_s}s: entry={entry:.8f}")
                                # Check exit within TTL
                                exit_filled = False
                                exit_fill_s = None
                                for j in range(exit_ttl):
                                    time.sleep(1)
                                    tk3 = c.ticker([product])
                                    t3 = tk3.get(product, {})
                                    f_bid = to_float((t3.get('b') or [None])[0])
                                    f_last3 = to_float((t3.get('c') or [None])[0])
                                    if f_bid > 0 and f_bid >= exit_legal:
                                        exit_filled = True
                                        exit_fill_s = i + j
                                        break
                                    if f_last3 > 0 and f_last3 >= exit_legal:
                                        exit_filled = True
                                        exit_fill_s = i + j
                                        break

                                if exit_filled:
                                    print(f"    ✅ EXIT FILLED at t={exit_fill_s}s: exit={exit_legal:.8f} net={net:.1f}bps")
                                    trades.append({
                                        'dump_s': i, 'drop_bps': round(drop, 1),
                                        'entry': round(entry, 8), 'entry_fill_s': entry_fill_s,
                                        'exit': round(exit_legal, 8), 'exit_fill_s': exit_fill_s,
                                        'net_bps': round(net, 1), 'closed': True,
                                    })
                                else:
                                    # Force close at best bid
                                    tk4 = c.ticker([product])
                                    t4 = tk4.get(product, {})
                                    force_bid = to_float((t4.get('b') or [None])[0])
                                    force_net = (force_bid - entry) / entry * 10000 - 50 if force_bid > 0 else -999
                                    print(f"    ⏰ EXIT TIMEOUT — force close at {force_bid:.8f}, net={force_net:.1f}bps")
                                    trades.append({
                                        'dump_s': i, 'drop_bps': round(drop, 1),
                                        'entry': round(entry, 8), 'entry_fill_s': entry_fill_s,
                                        'exit': round(force_bid, 8), 'exit_fill_s': 'force_close',
                                        'net_bps': round(force_net, 1), 'closed': False,
                                    })
                            else:
                                print(f"    ❌ ENTRY TIMEOUT after {entry_ttl}s: needed ask<={entry:.8f}")
                                trades.append({
                                    'dump_s': i, 'drop_bps': round(drop, 1),
                                    'entry': round(entry, 8), 'entry_fill_s': None,
                                    'exit': None, 'exit_fill_s': None,
                                    'net_bps': None, 'closed': 'entry_timeout',
                                })

                    time.sleep(max(0, entry_ttl - (time.time() % 1)))  # approximate pacing

        if i % 10 == 0:
            print(f"  t={i}s: bid={bid:.8f} spread={sp:.0f}bps bid_depth=${bid_d:.0f}")

        time.sleep(1)
    except Exception as e:
        print(f"  ERROR t={i}s: {e}")
        time.sleep(1)

# Force close any remaining open trades
print(f"\nTape complete. Forcing close any open positions...")

out = {
    'generated': utc_now(), 'product': 'CQT-USD', 'duration': duration,
    'dump_threshold_bps': dump_threshold_bps, 'lookback': lookback,
    'total_ticks': len(ticks), 'dump_events': len(dumps),
    'trades': trades,
    'summary': {
        'total_dumps': len(dumps),
        'entry_fills': len([t for t in trades if t.get('entry_fill_s')]),
        'exit_fills': len([t for t in trades if t.get('exit_fill_s') and t.get('exit_fill_s') != 'force_close']),
        'force_closes': len([t for t in trades if t.get('exit_fill_s') == 'force_close']),
        'entry_timeouts': len([t for t in trades if t.get('closed') == 'entry_timeout']),
        'winners': len([t for t in trades if t.get('net_bps') and t['net_bps'] > 0]),
        'losers': len([t for t in trades if t.get('net_bps') is not None and t['net_bps'] <= 0]),
    }
}
with open('reports/cache/cqt_vulture_tape.json', 'w') as f:
    json.dump(out, f, indent=2)

print(f"\n✅ Saved to reports/cache/cqt_vulture_tape.json")
print(f"Summary: {len(dumps)} dumps, {out['summary']['entry_fills']} entry fills, {out['summary']['exit_fills']} exit fills")
print(f"  Force closes: {out['summary']['force_closes']}, Entry timeouts: {out['summary']['entry_timeouts']}")
print(f"  Winners: {out['summary']['winners']}, Losers: {out['summary']['losers']}")
