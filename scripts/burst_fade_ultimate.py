#!/usr/bin/env python3
"""
Burst Fade Ultimate — combine ALL proven improvements into one system.

Proven edges:
1. Top 20 products by burst frequency
2. 5 concurrent positions (not 2)
3. 60% target fraction, 20% stop fraction
4. Dynamic quote sizing (bigger bursts → bigger positions)
5. Asymmetric stops (tighter for down-bursts)

This is the final form of the burst fade system.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "burst_fade_ultimate.json"

ALL_PRODUCTS = [
    "RAVE-USD", "TROLL-USD", "BAL-USD", "NOM-USD", "MASK-USD",
    "ALEPH-USD", "CHECK-USD", "BLUR-USD", "AVT-USD", "IOTX-USD",
    "IRYS-USD", "CFG-USD", "BOBBOB-USD", "DASH-USD", "FARTCOIN-USD",
    "COMP-USD", "MON-USD", "ZEC-USD", "VVV-USD", "ALGO-USD",
    "ARB-USD", "ETH-USD", "STORJ-USD", "SNX-USD", "AVAX-USD",
    "LDO-USD", "BASED1-USD", "RLC-USD", "SKL-USD", "TAO-USD",
]


def fetch_candles_72h(client, product_id, granularity="FIVE_MINUTE"):
    gsec_map = {"FIVE_MINUTE": 300, "ONE_MINUTE": 60}
    gsec = gsec_map.get(granularity, 300)
    max_per_req = 300
    end = int(time.time())
    start = end - (72 * 3600)
    all_candles = []
    seen = set()
    chunk_end = end
    while chunk_end > start:
        chunk_start = max(start, chunk_end - max_per_req * gsec)
        resp = client.market_candles(product_id, start=chunk_start, end=chunk_end, granularity=granularity)
        raw = resp.get("candles") or []
        if not raw:
            break
        for c in raw:
            t = int(c["start"])
            if t not in seen:
                seen.add(t)
                all_candles.append({
                    "time": t, "open": float(c["open"]), "high": float(c["high"]),
                    "low": float(c["low"]), "close": float(c["close"]), "volume": float(c.get("volume", 0)),
                })
        chunk_end = chunk_start - 1
        time.sleep(0.08)
    return sorted(all_candles, key=lambda x: x["time"])


def run_ultimate_system(candles_by_pid, times, lookup, products, config):
    """Run the ultimate burst fade system with all proven improvements."""
    quote = config.get("quote", 24.0)
    max_concurrent = config.get("max_concurrent", 5)
    target_frac = config.get("target_frac", 0.6)
    stop_frac = config.get("stop_frac", 0.2)
    burst_thresh = config.get("burst_thresh", 2.0)
    maker_fee_bps = config.get("maker_fee_bps", 40.0)
    dynamic_quote = config.get("dynamic_quote", False)
    max_quote_mult = config.get("max_quote_mult", 3.0)
    up_stop_frac = config.get("up_stop_frac", 0.0)
    down_stop_frac = config.get("down_stop_frac", 0.0)

    fee_rate = maker_fee_bps / 10000.0
    starting_cash = 48.0
    cash = starting_cash
    positions = {}
    realized_net = 0.0
    closes = 0
    wins = 0
    losses = 0
    fees = 0.0

    for t in times:
        tick = lookup.get(t, {})

        # Exits
        exit_pids = []
        for pid, pos in list(positions.items()):
            if pid not in tick:
                continue
            c = tick[pid]
            h = float(c["high"])
            l = float(c["low"])
            ep = pos["entry"]
            tp = pos["target"]
            sp = pos["stop"]
            qty = pos["qty"]

            if l <= tp:
                gross = (ep - tp) * qty
                ef = pos["entry_fee"]
                xf = tp * qty * fee_rate
                net = gross - ef - xf
                realized_net += net
                closes += 1
                wins += 1
                fees += ef + xf
                cash += ep * qty + net
                exit_pids.append(pid)
            elif h >= sp:
                gross = (ep - sp) * qty
                ef = pos["entry_fee"]
                xf = sp * qty * fee_rate
                net = gross - ef - xf
                realized_net += net
                closes += 1
                losses += 1
                fees += ef + xf
                cash += ep * qty + net
                exit_pids.append(pid)

        for pid in exit_pids:
            positions.pop(pid, None)

        # Entries
        if len(positions) < max_concurrent:
            for pid in products:
                if pid in positions or pid not in tick:
                    continue
                c = tick[pid]
                o = float(c["open"])
                h = float(c["high"])
                l = float(c["low"])
                cl = float(c["close"])
                mid = (o + cl) / 2 if (o + cl) > 0 else 1
                range_pct = (h - l) / mid * 100

                if range_pct < burst_thresh:
                    continue

                # Asymmetric stops
                effective_stop_frac = stop_frac
                if up_stop_frac > 0 or down_stop_frac > 0:
                    if cl > o:  # Up burst
                        effective_stop_frac = up_stop_frac if up_stop_frac > 0 else stop_frac
                    else:
                        effective_stop_frac = down_stop_frac if down_stop_frac > 0 else stop_frac

                # Dynamic quote sizing
                effective_quote = quote
                if dynamic_quote and range_pct > 3.0:
                    effective_quote = min(quote * max_quote_mult * (range_pct / 3.0), 48.0)

                if cash < effective_quote:
                    continue

                entry = h
                target = entry * (1 - range_pct / 100 * target_frac)
                stop = entry * (1 + range_pct / 100 * effective_stop_frac)

                entry_fee = entry * (effective_quote / entry) * fee_rate
                qty = (effective_quote - entry_fee) / entry
                if qty <= 0:
                    continue

                positions[pid] = {
                    "entry": entry, "target": target, "stop": stop,
                    "qty": qty, "entry_fee": entry_fee,
                }
                cash -= effective_quote

    return {
        "starting_cash": starting_cash,
        "ending_cash": round(cash, 2),
        "realized_net": round(realized_net, 2),
        "return_pct": round(realized_net / starting_cash * 100, 2),
        "closes": closes,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / max(1, closes) * 100, 1),
        "avg_pnl_per_close": round(realized_net / max(1, closes), 4) if closes > 0 else 0,
        "total_fees": round(fees, 2),
        "trades_per_day": round(closes / (len(times) * 5 / 60 / 24), 1),
        "config": config,
    }


def scan_fee_sensitivity(candles_by_pid, times, lookup, products, best_config):
    """Test how the ultimate system performs at different fee levels."""
    results = {}
    for fee_bps in [5, 10, 20, 30, 40, 50, 60, 80, 100]:
        config = {**best_config, "maker_fee_bps": fee_bps}
        r = run_ultimate_system(candles_by_pid, times, lookup, products, config)
        results[f"{fee_bps}bps"] = r
    return results


def scan_product_universe_size(candles_by_pid, times, lookup, products, best_config):
    """Test how many products to include."""
    results = {}
    for n in [5, 10, 15, 20, 25, 30]:
        subset = products[:min(n, len(products))]
        config = {**best_config}
        r = run_ultimate_system(candles_by_pid, times, lookup, subset, config)
        results[f"top{n}"] = r
    return results


def main():
    client = CoinbaseAdvancedClient()

    print("Fetching candles for 30 products...")
    candles_cache = {}
    for pid in ALL_PRODUCTS:
        try:
            candles_cache[pid] = fetch_candles_72h(client, pid)
            print(f"  {pid}: {len(candles_cache[pid])} candles")
        except Exception as e:
            print(f"  {pid}: ERROR {e}")

    times = set()
    for candles in candles_cache.values():
        for c in candles:
            times.add(int(c["time"]))
    times = sorted(times)

    lookup = {}
    for pid, candles in candles_cache.items():
        for c in candles:
            t = int(c["time"])
            if t not in lookup:
                lookup[t] = {}
            lookup[t][pid] = c

    products = list(candles_cache.keys())
    print(f"\nTimeline: {len(times)} steps, {len(products)} products")

    # The ultimate baseline (optimal params from expansion scan)
    baseline_config = {
        "quote": 24.0, "max_concurrent": 5,
        "target_frac": 0.6, "stop_frac": 0.2,
        "burst_thresh": 2.0, "maker_fee_bps": 40.0,
    }
    print(f"\n=== BASELINE (optimal params) ===")
    baseline = run_ultimate_system(candles_cache, times, lookup, products[:20], baseline_config)
    print(f"  ${baseline['realized_net']:.2f} ({baseline['return_pct']:.1f}%), {baseline['closes']} closes, {baseline['win_rate']:.1f}% WR")

    # Ultimate with dynamic quote
    print(f"\n=== ULTIMATE + Dynamic Quote ===")
    dynq_configs = [
        {"dynamic_quote": True, "max_quote_mult": m, "up_stop_frac": u, "down_stop_frac": d}
        for m in [2.0, 2.5, 3.0, 3.5, 4.0]
        for u in [0.0, 0.2, 0.3]
        for d in [0.0, 0.1, 0.15, 0.2]
    ]
    dynq_results = []
    for dc in dynq_configs:
        config = {**baseline_config, **dc}
        r = run_ultimate_system(candles_cache, times, lookup, products[:20], config)
        dynq_results.append((dc, r))

    dynq_results.sort(key=lambda x: x[1]["realized_net"], reverse=True)
    print(f"  Best: {dynq_results[0][0]}")
    print(f"  ${dynq_results[0][1]['realized_net']:.2f} ({dynq_results[0][1]['return_pct']:.1f}%), {dynq_results[0][1]['closes']} closes")

    # Fee sensitivity
    print(f"\n=== FEE SENSITIVITY (ultimate config) ===")
    best_ultimate_config = {**baseline_config, **dynq_results[0][0]}
    fee_results = scan_fee_sensitivity(candles_cache, times, lookup, products[:20], best_ultimate_config)
    for fee_label, r in sorted(fee_results.items(), key=lambda x: int(x[0].replace("bps", ""))):
        print(f"  {fee_label}: ${r['realized_net']:.2f} ({r['return_pct']:.1f}%), {r['closes']} closes")

    # Product universe size
    print(f"\n=== PRODUCT UNIVERSE SIZE ===")
    size_results = scan_product_universe_size(candles_cache, times, lookup, products, best_ultimate_config)
    for size_label, r in sorted(size_results.items(), key=lambda x: int(x[0].replace("top", ""))):
        print(f"  {size_label}: ${r['realized_net']:.2f} ({r['return_pct']:.1f}%), {r['closes']} closes, {r['trades_per_day']:.0f}/day")

    # Summary
    print(f"\n{'='*110}")
    print(f"{'System':<40} {'Net $':>8} {'Ret%':>7} {'Closes':>6} {'Win%':>6} {'Avg/Cl':>8} {'Tr/day':>7} {'Fees':>8}")
    print(f"{'='*110}")
    print(f"{'BASELINE (optimal params)':<40} ${baseline['realized_net']:>6.2f} {baseline['return_pct']:>6.1f}% {baseline['closes']:>6} {baseline['win_rate']:>5.1f}% ${baseline['avg_pnl_per_close']:>6.4f} {baseline['trades_per_day']:>6.0f} ${baseline['total_fees']:>6.2f}")

    best = dynq_results[0][1]
    cfg = dynq_results[0][0]
    label = f"ULTIMATE + dynq{cfg.get('max_quote_mult')} up{cfg.get('up_stop_frac')} dn{cfg.get('down_stop_frac')}"
    print(f"{label:<40} ${best['realized_net']:>6.2f} {best['return_pct']:>6.1f}% {best['closes']:>6} {best['win_rate']:>5.1f}% ${best['avg_pnl_per_close']:>6.4f} {best['trades_per_day']:>6.0f} ${best['total_fees']:>6.2f}")

    improvement = (best['realized_net'] - baseline['realized_net']) / baseline['realized_net'] * 100
    print(f"\n  Improvement: +${best['realized_net'] - baseline['realized_net']:.2f} ({improvement:.1f}%)")

    out = Path(REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "baseline": baseline,
        "best_ultimate": {"result": best, "config": cfg},
        "fee_sensitivity": fee_results,
        "universe_size": size_results,
    }
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
