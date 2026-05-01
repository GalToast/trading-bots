import json

events = []
with open('reports/coinbase_rsi_shadow_mogusd_events.jsonl') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            pass

closes = [e for e in events if e.get('action') == 'close_trade']
wins = [c for c in closes if c.get('net_pnl', 0) > 0]
losses = [c for c in closes if c.get('net_pnl', 0) <= 0]

print(f"Total closes: {len(closes)}")
print(f"Wins: {len(wins)} | Losses: {len(losses)}")
if closes:
    print(f"Win rate: {len(wins)/len(closes)*100:.1f}%")

total_net = sum(c.get('net_pnl', 0) for c in closes)
total_gross = sum(c.get('gross_pnl', 0) for c in closes)
total_fee = sum(c.get('fee', 0) for c in closes)
print(f"Total net PnL: ${total_net:.4f}")
print(f"Total gross PnL: ${total_gross:.4f}")
print(f"Total fees paid: ${total_fee:.4f}")
if total_gross > 0:
    print(f"Fee drag: {total_fee/total_gross*100:.1f}%")

print()
print("--- WIN ANALYSIS ---")
for w in wins:
    ep = w.get('entry_price', 0)
    xp = w.get('exit_price', 0)
    move_pct = (xp - ep) / ep * 100 if ep else 0
    fee_model = w.get('fee_model', 'legacy')
    fee_bps = w.get('fee_bps_per_side', '?')
    print(f"  entry={ep:.2e} exit={xp:.2e} move={move_pct:.1f}% net=${w['net_pnl']:.4f} "
          f"fee_bps={fee_bps} exit_rsi={w.get('exit_rsi','?')} reason={w.get('exit_reason')}")

print()
print("--- LOSS ANALYSIS ---")
for l in losses:
    ep = l.get('entry_price', 0)
    xp = l.get('exit_price', 0)
    move_pct = (xp - ep) / ep * 100 if ep else 0
    fee_bps = l.get('fee_bps_per_side', '?')
    print(f"  entry={ep:.2e} exit={xp:.2e} move={move_pct:.1f}% net=${l['net_pnl']:.4f} "
          f"fee_bps={fee_bps} exit_rsi={l.get('exit_rsi','?')} reason={l.get('exit_reason')}")

print()
print("--- Fee model evolution ---")
for c in closes:
    print(f"  ts={c.get('ts_utc','?')[:10]} fee_model={c.get('fee_model','legacy')} fee_bps={c.get('fee_bps_per_side','?')} net=${c.get('net_pnl',0):.3f}")

# What tick-size jump cleared the fee?
print()
print("--- Minimum price move needed to clear fees (entry * fee_bps/10000 * 2) ---")
for c in closes:
    ep = c.get('entry_price', 0)
    fee_bps = c.get('fee_bps_per_side', 60)
    if ep and isinstance(fee_bps, (int, float)):
        min_move_pct = fee_bps / 10000 * 2 * 100
        actual_move = (c.get('exit_price', ep) - ep) / ep * 100 if ep else 0
        cleared = "YES" if actual_move > min_move_pct else "NO"
        print(f"  {c.get('ts_utc','?')[:10]} min_move_needed={min_move_pct:.2f}% actual={actual_move:.2f}% cleared={cleared} net=${c.get('net_pnl',0):.3f}")
