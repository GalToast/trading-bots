import sys, json; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float, parse_pair
from build_kraken_tiny_live_fire_queue import legal_maker_buy_price_at_offset
from run_kraken_tiny_live_maker_roundtrip_probe import legal_volume, maker_exit_floor_price, exit_floor_above_ask_bps
from crossing_pressure_scanner import compute_spread_bps

c = KrakenSpotClient()
assets = c.asset_pairs()

all_tickers = {}
keys = [k for k in assets if assets[k].get('status') == 'online']
for i in range(0, len(keys), 100):
    try:
        all_tickers.update(c.ticker(keys[i:i+100]))
    except:
        pass

# Manually check CQT, DUCK, BILLY, HONEY
for target in ['CQTUSD', 'DUCKUSD', 'BILLYUSD', 'HONEYUSD']:
    print(f"\n=== {target} ===")
    
    # Find the pair
    rest_pair = target
    payload = assets.get(rest_pair, {})
    if not payload:
        print("  NOT FOUND in assets")
        continue
    
    p = parse_pair(rest_pair, payload)
    print(f"  parsed: base={p.base} quote={p.quote} tick_size={p.tick_size} lot_dec={p.lot_decimals}")
    
    tk = all_tickers.get(rest_pair, {})
    t = tk.get(rest_pair, {})
    bid = to_float((t.get('b') or [None])[0])
    ask = to_float((t.get('a') or [None])[0])
    spread = compute_spread_bps(bid, ask) if bid > 0 and ask > 0 else 0
    print(f"  bid={bid} ask={ask} spread={spread:.0f}bps")
    
    if spread < 100:
        print(f"  FAIL: spread < 100bps")
        continue
    
    # Depth
    d = c.depth(rest_pair, count=10)
    book = d.get(rest_pair, {})
    if not book and len(d) == 1:
        book = list(d.values())[0]
    bids = book.get('bids', [])
    asks = book.get('asks', [])
    bid_d = to_float(bids[0][1]) * to_float(bids[0][0]) if bids else 0
    ask_d = to_float(asks[0][1]) * to_float(asks[0][0]) if asks else 0
    print(f"  bid_depth=${bid_d:.0f} ask_depth=${ask_d:.0f}")
    
    if bid_d < 10 or ask_d < 10:
        print(f"  FAIL: depth < $10")
        continue
    
    # Economics
    entry = legal_maker_buy_price_at_offset(bid, ask, p.tick_size, 0.10)
    print(f"  entry price at 0.10 offset: {entry}")
    
    if entry <= 0:
        print(f"  FAIL: invalid entry price")
        continue
    
    vol = legal_volume(9.0 / entry, p.lot_decimals)
    print(f"  volume: {vol}")
    
    if vol <= 0:
        print(f"  FAIL: invalid volume")
        continue
    
    entry_cost = entry * vol
    entry_fee = entry_cost * 0.0025
    exit_legal, exit_raw = maker_exit_floor_price(
        entry_cost=entry_cost, entry_fee=entry_fee, volume=vol,
        maker_fee_bps=25.0, target_net_pct=0.001, tick_size=p.tick_size
    )
    floor_above_ask = exit_floor_above_ask_bps(exit_legal, ask)
    gross = (exit_legal - entry) / entry * 10000
    net = gross - 50
    
    print(f"  entry_cost=${entry_cost:.4f} entry_fee=${entry_fee:.4f}")
    print(f"  exit_legal={exit_legal} exit_raw={exit_raw}")
    print(f"  floor_above_ask={floor_above_ask:.1f}bps gross={gross:.1f}bps net={net:.1f}bps")
    
    if net > 0:
        print(f"  ✅ PASS")
    else:
        print(f"  FAIL: negative economics")
