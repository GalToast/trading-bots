#!/usr/bin/env python3
"""
Analyze the 84-record Kraken↔Coinbase sync sample.
Compute lead time distribution, spread stats, and whether Kraken leads Coinbase.
"""
import json
import statistics
from pathlib import Path
from datetime import datetime, timezone

SYNC_PATH = Path(__file__).resolve().parent.parent / "reports" / "kraken_coinbase_sync_raw.jsonl"

def main():
    records = []
    with open(SYNC_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    print(f"Total sync records: {len(records)}")
    if not records:
        return

    # Time range
    first_ts = records[0]["ts"]
    last_ts = records[-1]["ts"]
    duration_sec = last_ts - first_ts
    print(f"Time span: {duration_sec:.0f}s ({duration_sec/60:.1f} min)")
    print(f"  From: {datetime.fromtimestamp(first_ts, tz=timezone.utc).isoformat()}")
    print(f"  To:   {datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat()}")
    print(f"  Sampling rate: {len(records)/max(1, duration_sec):.2f} samples/sec")

    # Price analysis
    kraken_prices = [r["kraken"] for r in records]
    coinbase_prices = [r["coinbase"] for r in records]

    print(f"\nKraken BTC:")
    print(f"  First: ${kraken_prices[0]:.2f}")
    print(f"  Last:  ${kraken_prices[-1]:.2f}")
    print(f"  Min:   ${min(kraken_prices):.2f}")
    print(f"  Max:   ${max(kraken_prices):.2f}")
    print(f"  Range: ${max(kraken_prices) - min(kraken_prices):.2f}")

    print(f"\nCoinbase BTC:")
    print(f"  First: ${coinbase_prices[0]:.2f}")
    print(f"  Last:  ${coinbase_prices[-1]:.2f}")
    print(f"  Min:   ${min(coinbase_prices):.2f}")
    print(f"  Max:   ${max(coinbase_prices):.2f}")
    print(f"  Range: ${max(coinbase_prices) - min(coinbase_prices):.2f}")

    # Spread: Kraken - Coinbase
    spreads = [k - c for k, c in zip(kraken_prices, coinbase_prices)]
    print(f"\nKraken-Coinbase spread:")
    print(f"  Mean:  ${statistics.mean(spreads):.2f}")
    print(f"  Median: ${statistics.median(spreads):.2f}")
    print(f"  Min:   ${min(spreads):.2f}")
    print(f"  Max:   ${max(spreads):.2f}")
    print(f"  Std:   ${statistics.stdev(spreads):.2f}")

    # Who moves first? Compute price changes and cross-correlation
    kr_changes = [kraken_prices[i] - kraken_prices[i-1] for i in range(1, len(kraken_prices))]
    cb_changes = [coinbase_prices[i] - coinbase_prices[i-1] for i in range(1, len(coinbase_prices))]

    # Count same-direction moves (both up or both down)
    same_dir = sum(1 for k, c in zip(kr_changes, cb_changes) if k * c > 0)
    opp_dir = sum(1 for k, c in zip(kr_changes, cb_changes) if k * c < 0)
    no_move = sum(1 for k, c in zip(kr_changes, cb_changes) if k == 0 or c == 0)

    print(f"\nDirectional agreement:")
    print(f"  Same direction: {same_dir}/{len(kr_changes)} ({same_dir/max(1,len(kr_changes))*100:.1f}%)")
    print(f"  Opposite direction: {opp_dir}/{len(kr_changes)} ({opp_dir/max(1,len(kr_changes))*100:.1f}%)")
    print(f"  One side flat: {no_move}/{len(kr_changes)} ({no_move/max(1,len(kr_changes))*100:.1f}%)")

    # Lead-lag: when Kraken moves but Coinbase doesn't, does Coinbase catch up?
    kraken_moves_first = []
    coinbase_moves_first = []
    both_move = []

    for i, (kc, cc) in enumerate(zip(kr_changes, cb_changes)):
        kr_moved = abs(kc) > 0.01  # $0.01 threshold
        cb_moved = abs(cc) > 0.01

        if kr_moved and not cb_moved:
            # Kraken moved, Coinbase didn't — check if Coinbase catches up next sample
            if i + 1 < len(cb_changes):
                catch_up = cb_changes[i + 1]  # Coinbase's next move
                kraken_moves_first.append({
                    "kraken_move": kc,
                    "coinbase_catch_up": catch_up,
                    "caught_up": catch_up * kc > 0  # same direction
                })

        elif cb_moved and not kr_moved:
            if i + 1 < len(kr_changes):
                kr_catch = kr_changes[i + 1]
                coinbase_moves_first.append({
                    "coinbase_move": cc,
                    "kraken_catch_up": kr_catch,
                    "caught_up": kr_catch * cc > 0
                })

        elif kr_moved and cb_moved:
            both_move.append({"kraken": kc, "coinbase": cc})

    print(f"\nLead-lag events:")
    print(f"  Kraken moved first: {len(kraken_moves_first)} events")
    if kraken_moves_first:
        caught = sum(1 for e in kraken_moves_first if e["caught_up"])
        print(f"    Coinbase caught up: {caught}/{len(kraken_moves_first)} ({caught/max(1,len(kraken_moves_first))*100:.1f}%)")
        avg_catch = statistics.mean([e["coinbase_catch_up"] for e in kraken_moves_first])
        print(f"    Avg catch-up move: ${avg_catch:.2f}")

    print(f"  Coinbase moved first: {len(coinbase_moves_first)} events")
    if coinbase_moves_first:
        caught = sum(1 for e in coinbase_moves_first if e["caught_up"])
        print(f"    Kraken caught up: {caught}/{len(coinbase_moves_first)} ({caught/max(1,len(coinbase_moves_first))*100:.1f}%)")

    print(f"  Both moved simultaneously: {len(both_move)} events")

    # Price event detection — find Kraken spikes and check Coinbase reaction
    print(f"\nKraken price spikes (>$0.50 in one sample):")
    for i, kc in enumerate(kr_changes):
        if abs(kc) > 0.50:
            ts = records[i + 1]["ts"]
            kr_price = kraken_prices[i + 1]
            cb_price = coinbase_prices[i + 1]
            cb_next = coinbase_prices[i + 2] if i + 2 < len(coinbase_prices) else None
            print(f"  t={datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%H:%M:%S')} | "
                  f"Kraken: ${kr_price:.2f} (Δ${kc:+.2f}) | Coinbase: ${cb_price:.2f}", end="")
            if cb_next:
                cb_delta = cb_next - cb_price
                print(f" → next: ${cb_next:.2f} (Δ${cb_delta:+.2f})")
            else:
                print()

    # Summary
    print(f"\n{'='*60}")
    print(f"LEAD-LAG ASSESSMENT:")
    print(f"{'='*60}")
    if len(kraken_moves_first) > len(coinbase_moves_first) * 2:
        print(f"  ✅ Kraken leads Coinbase: {len(kraken_moves_first)} vs {len(coinbase_moves_first)} lead events")
    elif len(coinbase_moves_first) > len(kraken_moves_first) * 2:
        print(f"  ⚠️ Coinbase leads Kraken: {len(coinbase_moves_first)} vs {len(kraken_moves_first)} lead events")
    else:
        print(f"  ⚠️ Mixed — both venues lead roughly equally ({len(kraken_moves_first)} vs {len(coinbase_moves_first)})")

    print(f"\n  Sample limitations:")
    print(f"  - Only {len(records)} records over {duration_sec/60:.0f} minutes")
    print(f"  - Only BTC, no altcoins")
    print(f"  - No altcoin price data at all")
    print(f"  - Kraken-Coinbase BTC spread is ~${statistics.mean(spreads):.2f} (structural)")
    print(f"  → Need much larger sample + altcoin data to be conclusive")


if __name__ == "__main__":
    main()
