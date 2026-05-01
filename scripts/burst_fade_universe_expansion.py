#!/usr/bin/env python3
"""
Burst Fade Universe Expansion — scan ALL Coinbase USD pairs for burst frequency,
then test the top N in a multi-coin rotation with optimized parameters.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "burst_fade_universe_expansion.json"

# Massive product universe — all known USD pairs
ALL_PRODUCTS = [
    "BAL-USD", "CHECK-USD", "ALEPH-USD", "BLUR-USD", "BOBBOB-USD", "CFG-USD", "COMP-USD", "DASH-USD",
    "ARB-USD", "SOL-USD", "WIF-USD", "BTC-USD", "ETH-USD", "DOGE-USD", "XRP-USD", "ADA-USD",
    "AVAX-USD", "SUI-USD", "LINK-USD", "DOT-USD", "UNI-USD", "ATOM-USD", "LTC-USD", "BCH-USD",
    "NEAR-USD", "FIL-USD", "APT-USD", "OP-USD", "INJ-USD", "TIA-USD", "SEI-USD", "STX-USD",
    "PEPE-USD", "BONK-USD", "FLOKI-USD", "SHIB-USD", "AAVE-USD", "ALGO-USD", "GRT-USD",
    "IMX-USD", "SNX-USD", "CRV-USD", "SAND-USD", "MANA-USD", "AXS-USD", "RENDER-USD",
    "FET-USD", "ICP-USD", "HBAR-USD", "VET-USD", "XLM-USD", "ETC-USD", "XTZ-USD",
    # Additional volatile alts
    "TROLL-USD", "FARTCOIN-USD", "RAVE-USD", "NOM-USD", "IRYS-USD", "TAO-USD",
    "ZEC-USD", "MON-USD", "VVV-USD", "BASED1-USD", "AVT-USD", "MKR-USD",
    "PEOPLE-USD", "LDO-USD", "ENS-USD", "SXP-USD", "RLC-USD", "STORJ-USD",
    "ANKR-USD", "CRO-USD", "MASK-USD", "GALA-USD", "CHZ-USD", "SKL-USD",
    "BAT-USD", "ZRX-USD", "OMG-USD", "CELR-USD", "IOTX-USD", "CELO-USD",
]


def fetch_candles_72h(client: CoinbaseAdvancedClient, product_id: str, granularity: str = "FIVE_MINUTE") -> list[dict]:
    gsec_map = {"FIVE_MINUTE": 300, "ONE_MINUTE": 60, "FIFTEEN_MINUTE": 900}
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
        time.sleep(0.1)
    return sorted(all_candles, key=lambda x: x["time"])


def scan_burst_frequency(candles: list[dict], burst_thresh_pct: float = 2.0) -> dict:
    """Count how many candles exceed the burst threshold."""
    if len(candles) < 10:
        return {"error": "not enough candles"}

    bursts = []
    for c in candles:
        o = float(c["open"])
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        mid = (o + cl) / 2 if (o + cl) > 0 else 1
        range_pct = (h - l) / mid * 100
        if range_pct >= burst_thresh_pct:
            bursts.append({
                "time": c["time"],
                "range_pct": round(range_pct, 4),
                "high": h,
                "low": l,
                "open": o,
                "close": cl,
                "direction": "up" if cl > o else "down",
            })

    return {
        "total_candles": len(candles),
        "burst_count": len(bursts),
        "burst_rate": round(len(bursts) / len(candles) * 100, 2),
        "avg_burst_range": round(sum(b["range_pct"] for b in bursts) / len(bursts), 2) if bursts else 0,
        "max_burst_range": round(max(b["range_pct"] for b in bursts), 2) if bursts else 0,
        "bursts_per_hour": round(len(bursts) / (len(candles) * 5 / 60), 2),
        "current_price": candles[-1]["close"],
    }


def run_burst_fade_backtest(
    candles_by_pid: dict[str, list[dict]],
    *,
    products: list[str],
    starting_cash: float = 48.0,
    quote: float = 24.0,
    burst_thresh: float = 2.0,
    target_frac: float = 0.5,
    stop_frac: float = 0.3,
    max_concurrent: int = 2,
    maker_fee_bps: float = 40.0,
) -> dict:
    """Run the burst fade rotation backtest."""
    fee_rate = maker_fee_bps / 10000.0

    # Build timeline
    all_times = set()
    time_lookup = {}
    for pid, candles in candles_by_pid.items():
        for c in candles:
            t = int(c["time"])
            all_times.add(t)
            if t not in time_lookup:
                time_lookup[t] = {}
            time_lookup[t][pid] = c

    all_times = sorted(all_times)

    cash = starting_cash
    positions = {}
    realized_net = 0.0
    closes = 0
    wins = 0
    losses = 0
    fees = 0.0

    for t in all_times:
        tick = time_lookup.get(t, {})

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
            units = quote / ep

            if l <= tp:
                gross = (ep - tp) * units
                ef = ep * units * fee_rate
                xf = tp * units * fee_rate
                net = gross - ef - xf
                realized_net += net
                closes += 1
                wins += 1
                fees += ef + xf
                cash += quote + net
                exit_pids.append(pid)
            elif h >= sp:
                gross = (ep - sp) * units
                ef = ep * units * fee_rate
                xf = sp * units * fee_rate
                net = gross - ef - xf
                realized_net += net
                closes += 1
                losses += 1
                fees += ef + xf
                cash += quote + net
                exit_pids.append(pid)

        for pid in exit_pids:
            positions.pop(pid, None)

        # Entries
        if cash >= quote and len(positions) < max_concurrent:
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
                if range_pct >= burst_thresh:
                    entry = h
                    target = entry * (1 - range_pct / 100 * target_frac)
                    stop = entry * (1 + range_pct / 100 * stop_frac)
                    positions[pid] = {"entry": entry, "target": target, "stop": stop}
                    cash -= quote

    return {
        "starting_cash": starting_cash,
        "ending_cash": round(cash, 2),
        "realized_net": round(realized_net, 2),
        "return_pct": round(realized_net / starting_cash * 100, 2),
        "closes": closes,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / max(1, closes) * 100, 1),
        "avg_pnl_per_close": round(realized_net / max(1, closes), 4),
        "total_fees": round(fees, 2),
        "profit_factor": round(sum(1 for _ in range(wins)) / max(1, losses), 2) if losses > 0 else float("inf"),
        "trades_per_day": round(closes / (len(all_times) * 5 / 60 / 24), 1),
        "max_concurrent_used": max_concurrent,
    }


def main() -> None:
    client = CoinbaseAdvancedClient()

    # Step 1: Scan ALL products for burst frequency
    print("=== STEP 1: Scanning burst frequency across all products ===")
    burst_stats = {}
    for pid in ALL_PRODUCTS:
        try:
            candles = fetch_candles_72h(client, pid)
            if len(candles) < 50:
                continue
            stats = scan_burst_frequency(candles, burst_thresh_pct=2.0)
            if "error" not in stats:
                burst_stats[pid] = stats
                print(f"  {pid:20s}: {stats['burst_count']:3d} bursts ({stats['burst_rate']:.1f}%), {stats['bursts_per_hour']:.1f}/hr, max {stats['max_burst_range']:.1f}%")
            time.sleep(0.05)
        except Exception as e:
            print(f"  {pid:20s}: ERROR {e}")

    # Sort by bursts per hour
    ranked = sorted(burst_stats.items(), key=lambda x: x[1]["bursts_per_hour"], reverse=True)
    print(f"\nTop 20 by burst frequency:")
    for i, (pid, stats) in enumerate(ranked[:20]):
        print(f"  {i+1:>2}. {pid:20s}: {stats['bursts_per_hour']:.1f}/hr, {stats['burst_count']} bursts, avg {stats['avg_burst_range']:.1f}%")

    # Step 2: Test multi-coin rotation with different product counts
    print("\n=== STEP 2: Testing multi-coin rotation ===")

    # Fetch candles for top 30 products
    top_30 = [pid for pid, _ in ranked[:30]]
    candles_cache = {}
    for pid in top_30:
        try:
            candles_cache[pid] = fetch_candles_72h(client, pid)
            print(f"  Cached {pid}: {len(candles_cache[pid])} candles")
        except Exception as e:
            print(f"  {pid}: ERROR {e}")

    # Test different configurations
    configs = []
    for max_conc in [2, 3, 4, 5]:
        for top_n in [5, 10, 15, 20]:
            products = [pid for pid, _ in ranked[:top_n]]
            for burst_t in [1.5, 2.0, 2.5, 3.0]:
                for target_f in [0.4, 0.5, 0.6]:
                    for stop_f in [0.2, 0.3, 0.4]:
                        result = run_burst_fade_backtest(
                            candles_cache,
                            products=products,
                            starting_cash=48.0,
                            quote=24.0,
                            burst_thresh=burst_t,
                            target_frac=target_f,
                            stop_frac=stop_f,
                            max_concurrent=max_conc,
                            maker_fee_bps=40.0,
                        )
                        result["config"] = f"top{top_n}_conc{max_conc}_bt{burst_t}_tf{target_f}_sf{stop_f}"
                        configs.append(result)

    # Sort by realized net
    configs.sort(key=lambda x: x["realized_net"], reverse=True)

    print(f"\n{'='*130}")
    print(f"{'Rank':>4} {'Config':<45} {'Net $':>8} {'Ret%':>7} {'Closes':>6} {'Win%':>6} {'Avg/Cl':>8} {'Fees':>8} {'Tr/day':>7}")
    print(f"{'='*130}")
    for i, r in enumerate(configs[:20]):
        print(f"{i+1:>4} {r['config']:<45} ${r['realized_net']:>6.2f} {r['return_pct']:>6.1f}% {r['closes']:>6} {r['win_rate']:>5.1f}% ${r['avg_pnl_per_close']:>6.4f} ${r['total_fees']:>6.2f} {r['trades_per_day']:>6.1f}")

    # Also test with different fee assumptions
    best_config = configs[0]
    print(f"\n=== Fee sensitivity for best config: {best_config['config']} ===")
    for fee_bps in [5, 10, 20, 40, 60]:
        products = [pid for pid, _ in ranked[:10]]
        result = run_burst_fade_backtest(
            candles_cache, products=products, starting_cash=48.0, quote=24.0,
            burst_thresh=2.0, target_frac=0.5, stop_frac=0.3, max_concurrent=2,
            maker_fee_bps=fee_bps,
        )
        print(f"  {fee_bps}bps: net=${result['realized_net']:.2f} ({result['return_pct']:.1f}%), fees=${result['total_fees']:.2f}, {result['closes']} closes")

    # Write report
    out = Path(REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "burst_ranking": {pid: stats for pid, stats in ranked[:30]},
        "top_20_configs": configs[:20],
        "total_configs_tested": len(configs),
    }
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
