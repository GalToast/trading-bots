#!/usr/bin/env python3
"""
Beyond Burst Fade — structural market edges that exist OUTSIDE the burst fade paradigm.

These are completely different strategy families, not variations of burst fade:
1. Cross-product mean reversion (stat arb between correlated microcaps)
2. Volume-spike leading (volume precedes price — catch the move before it happens)
3. Burst cascade prediction (after N bursts, next one reverses)
4. Quiet-to-explosive regime switch (detect the calm, position before the storm)
5. Leader-follower network (which product moves first → trade the laggards)
6. Time-of-day regime switching (different parameters per UTC hour)
7. RSI + Burst confluence (RSI oversold AND burst = higher win rate)
8. Opening range breakout (first burst after quiet sets multi-hour trend)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "beyond_burst_fade.json"

TOP_PRODUCTS = [
    "RAVE-USD", "TROLL-USD", "BAL-USD", "NOM-USD", "MASK-USD",
    "ALEPH-USD", "CHECK-USD", "BLUR-USD", "AVT-USD", "IOTX-USD",
    "IRYS-USD", "CFG-USD", "BOBBOB-USD", "DASH-USD", "FARTCOIN-USD",
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


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    result = [50.0] * period
    if avg_l > 0:
        result.append(100 - 100 / (1 + avg_g / avg_l))
    else:
        result.append(100.0)
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period-1) + gains[i]) / period
        avg_l = (avg_l * (period-1) + losses[i]) / period
        if avg_l > 0:
            result.append(100 - 100 / (1 + avg_g / avg_l))
        else:
            result.append(100.0)
    return result


def build_timeline(candles_by_pid):
    all_times = set()
    for candles in candles_by_pid.values():
        for c in candles:
            all_times.add(int(c["time"]))
    return sorted(all_times)


def build_lookup(candles_by_pid, times):
    lookup = {}
    for pid, candles in candles_by_pid.items():
        for c in candles:
            t = int(c["time"])
            if t not in lookup:
                lookup[t] = {}
            lookup[t][pid] = c
    return lookup


# ============================================================
# EDGE 1: Cross-product mean reversion (stat arb)
# When correlated products diverge, bet on convergence
# ============================================================
def test_cross_product_mean_reversion(candles_by_pid, times, lookup, products):
    """
    Find pairs of products that move together. When they diverge
    (one up, one down), bet on the lagging one to catch up.
    """
    # Compute returns for each product
    returns_by_pid = {}
    for pid in products:
        candles = candles_by_pid.get(pid, [])
        if len(candles) < 2:
            continue
        returns = []
        for i in range(1, len(candles)):
            prev = candles[i-1]["close"]
            curr = candles[i]["close"]
            ret = (curr - prev) / prev if prev > 0 else 0
            returns.append({"time": candles[i]["time"], "return": ret})
        returns_by_pid[pid] = returns

    # Find pairs with high correlation over 72h
    # Then test: when one moves +X% and the other -Y%, does the laggard revert?
    signals = []
    for i, pid_a in enumerate(products):
        for pid_b in products[i+1:]:
            rets_a = returns_by_pid.get(pid_a, [])
            rets_b = returns_by_pid.get(pid_b, [])
            if len(rets_a) < 10 or len(rets_b) < 10:
                continue

            # Align by time
            time_a = {r["time"]: r["return"] for r in rets_a}
            time_b = {r["time"]: r["return"] for r in rets_b}
            common_times = sorted(set(time_a.keys()) & set(time_b.keys()))

            if len(common_times) < 50:
                continue

            # Compute correlation
            ra = [time_a[t] for t in common_times]
            rb = [time_b[t] for t in common_times]
            mean_a = sum(ra) / len(ra)
            mean_b = sum(rb) / len(rb)
            cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(ra, rb)) / len(ra)
            std_a = (sum((a - mean_a)**2 for a in ra) / len(ra)) ** 0.5
            std_b = (sum((b - mean_b)**2 for b in rb) / len(rb)) ** 0.5
            corr = cov / (std_a * std_b) if std_a > 0 and std_b > 0 else 0

            # If correlation > 0.3, test divergence trades
            if abs(corr) > 0.3:
                divergence_signals = 0
                divergence_wins = 0
                total_pnl = 0.0

                for j in range(1, len(common_times)):
                    r_a = time_a[common_times[j]]
                    r_b = time_b[common_times[j]]

                    # Divergence: A up >1%, B down >0.5% (or vice versa)
                    if r_a > 0.01 and r_b < -0.005:
                        # Bet on B to recover (buy B)
                        divergence_signals += 1
                        # Check next 3 bars for B recovery
                        if j + 3 < len(common_times):
                            future_b = sum(time_b[common_times[j+k]] for k in range(1, 4))
                            if future_b > 0:
                                divergence_wins += 1
                            total_pnl += future_b
                    elif r_b > 0.01 and r_a < -0.005:
                        # Bet on A to recover
                        divergence_signals += 1
                        if j + 3 < len(common_times):
                            future_a = sum(time_a[common_times[j+k]] for k in range(1, 4))
                            if future_a > 0:
                                divergence_wins += 1
                            total_pnl += future_a

                if divergence_signals > 5:
                    signals.append({
                        "pair": f"{pid_a}-{pid_b}",
                        "correlation": round(corr, 3),
                        "divergence_signals": divergence_signals,
                        "wins": divergence_wins,
                        "win_rate": round(divergence_wins / divergence_signals, 3) if divergence_signals > 0 else 0,
                        "total_pnl_pct": round(total_pnl * 100, 4),
                        "avg_pnl_per_signal_pct": round(total_pnl / divergence_signals * 100, 4) if divergence_signals > 0 else 0,
                    })

    signals.sort(key=lambda x: x["total_pnl_pct"], reverse=True)
    return {"strategy": "cross_product_mean_reversion", "signals": signals[:20]}


# ============================================================
# EDGE 2: Volume-spike leading (volume precedes price)
# ============================================================
def test_volume_spike_leading(candles_by_pid, times, lookup, products):
    """
    When volume spikes 2-3x average, does price follow in the next 1-3 bars?
    """
    results = {}
    for pid in products:
        candles = candles_by_pid.get(pid, [])
        if len(candles) < 30:
            continue

        volumes = [c["volume"] for c in candles]
        closes = [c["close"] for c in candles]

        signals = []
        for i in range(20, len(candles) - 3):
            # Volume spike: current volume > 2x 20-bar average
            avg_vol = sum(volumes[i-20:i]) / 20
            if avg_vol > 0 and volumes[i] > avg_vol * 2.0:
                # Check price move in next 3 bars
                entry = closes[i]
                exit_price = closes[min(i+3, len(candles)-1)]
                pnl_pct = (exit_price - entry) / entry * 100

                # Check max excursion
                max_price = max(closes[i:min(i+4, len(candles))])
                min_price = min(closes[i:min(i+4, len(candles))])
                max_up = (max_price - entry) / entry * 100
                max_down = (min_price - entry) / entry * 100

                signals.append({
                    "bar": i,
                    "vol_spike_mult": round(volumes[i] / avg_vol, 2),
                    "pnl_3bar_pct": round(pnl_pct, 4),
                    "max_up_pct": round(max_up, 4),
                    "max_down_pct": round(max_down, 4),
                    "win": pnl_pct > 0,
                })

        if signals:
            wins = sum(1 for s in signals if s["win"])
            results[pid] = {
                "total_signals": len(signals),
                "wins": wins,
                "win_rate": round(wins / len(signals), 3),
                "avg_pnl_3bar_pct": round(sum(s["pnl_3bar_pct"] for s in signals) / len(signals), 4),
                "avg_max_up_pct": round(sum(s["max_up_pct"] for s in signals) / len(signals), 4),
                "avg_max_down_pct": round(sum(s["max_down_pct"] for s in signals) / len(signals), 4),
                "avg_vol_spike_mult": round(sum(s["vol_spike_mult"] for s in signals) / len(signals), 2),
            }

    return {"strategy": "volume_spike_leading", "results": results}


# ============================================================
# EDGE 3: Burst cascade prediction
# After N bursts in M bars, next burst reverses
# ============================================================
def test_burst_cascade(candles_by_pid, times, lookup, products):
    """
    When a product has 3+ bursts within 6 bars, the next burst
    is more likely to reverse (exhaustion signal).
    """
    results = {}
    for pid in products:
        candles = candles_by_pid.get(pid, [])
        if len(candles) < 20:
            continue

        bursts = []
        for i in range(1, len(candles)):
            o = candles[i-1]["open"]
            h = candles[i-1]["high"]
            l = candles[i-1]["low"]
            cl = candles[i-1]["close"]
            mid = (o + cl) / 2 if (o + cl) > 0 else 1
            range_pct = (h - l) / mid * 100
            if range_pct >= 2.0:
                bursts.append(i)

        # Find clusters of 3+ bursts within 6 bars
        cascade_signals = 0
        cascade_wins = 0
        total_pnl = 0.0

        for i in range(len(bursts) - 2):
            if bursts[i+2] - bursts[i] <= 6:  # 3 bursts in 6 bars
                # This is a cascade
                cascade_signals += 1
                # Next bar after the 3rd burst → bet on reversal
                entry_bar = bursts[i+2]
                if entry_bar < len(candles) - 2:
                    entry = candles[entry_bar]["close"]
                    exit_price = candles[min(entry_bar + 2, len(candles)-1)]["close"]
                    # Fade the last burst direction
                    last_burst_up = candles[entry_bar]["close"] >= candles[entry_bar]["open"]
                    if last_burst_up:
                        pnl_pct = (entry - exit_price) / entry * 100  # Short the top
                    else:
                        pnl_pct = (exit_price - entry) / entry * 100  # Long the bottom

                    if pnl_pct > 0:
                        cascade_wins += 1
                    total_pnl += pnl_pct

        if cascade_signals > 3:
            results[pid] = {
                "cascade_signals": cascade_signals,
                "wins": cascade_wins,
                "win_rate": round(cascade_wins / cascade_signals, 3),
                "total_pnl_pct": round(total_pnl, 4),
                "avg_pnl_per_signal_pct": round(total_pnl / cascade_signals, 4),
            }

    return {"strategy": "burst_cascade", "results": results}


# ============================================================
# EDGE 4: Quiet-to-explosive regime switch
# Detect calm periods, position for the explosion
# ============================================================
def test_quiet_to_explosive(candles_by_pid, times, lookup, products):
    """
    When volatility compresses to bottom 10% for 10+ bars,
    the next 10 bars average X% move. Direction? Use the first
    break direction.
    """
    results = {}
    for pid in products:
        candles = candles_by_pid.get(pid, [])
        if len(candles) < 30:
            continue

        closes = [c["close"] for c in candles]
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]

        # Rolling 10-bar volatility
        vol_window = 10
        vols = []
        for i in range(vol_window, len(returns)):
            window = returns[i-vol_window:i]
            mean_r = sum(window) / len(window)
            std = (sum((r - mean_r)**2 for r in window) / len(window)) ** 0.5
            vols.append(std)

        if not vols:
            continue

        # Find bottom 10% threshold
        sorted_vols = sorted(vols)
        threshold = sorted_vols[len(sorted_vols) // 10]

        signals = []
        in_squeeze = False
        squeeze_start = 0
        for i in range(len(vols)):
            if vols[i] < threshold:
                if not in_squeeze:
                    in_squeeze = True
                    squeeze_start = i
            else:
                if in_squeeze and (i - squeeze_start) >= 5:
                    # Squeeze ended after 5+ bars → breakout
                    entry_bar = i + vol_window
                    if entry_bar < len(candles) - 5:
                        entry = candles[entry_bar]["close"]
                        # Direction: use first move after squeeze
                        first_move = (candles[entry_bar+1]["close"] - entry) / entry
                        exit_bar = min(entry_bar + 5, len(candles) - 1)
                        exit_price = candles[exit_bar]["close"]

                        if first_move > 0:
                            pnl_pct = (exit_price - entry) / entry * 100
                        else:
                            pnl_pct = (entry - exit_price) / entry * 100

                        signals.append({
                            "squeeze_length": i - squeeze_start,
                            "pnl_pct": round(pnl_pct, 4),
                            "win": pnl_pct > 0,
                        })
                in_squeeze = False

        if signals:
            wins = sum(1 for s in signals if s["win"])
            results[pid] = {
                "signals": len(signals),
                "wins": wins,
                "win_rate": round(wins / len(signals), 3),
                "avg_pnl_pct": round(sum(s["pnl_pct"] for s in signals) / len(signals), 4),
                "avg_squeeze_length": round(sum(s["squeeze_length"] for s in signals) / len(signals), 1),
            }

    return {"strategy": "quiet_to_explosive", "results": results}


# ============================================================
# EDGE 5: Leader-follower network
# ============================================================
def test_leader_follower(candles_by_pid, times, lookup, products):
    """
    Which product moves first? When the leader moves >2%,
    do the followers move in the same direction within 1-2 bars?
    """
    # Compute average return per bar for each product
    avg_returns = {}
    for pid in products:
        candles = candles_by_pid.get(pid, [])
        if len(candles) < 10:
            continue
        rets = [(candles[i]["close"] - candles[i-1]["close"]) / candles[i-1]["close"] for i in range(1, len(candles))]
        avg_returns[pid] = sum(abs(r) for r in rets) / len(rets) if rets else 0

    # Leader = highest average absolute return
    sorted_products = sorted(avg_returns.items(), key=lambda x: x[1], reverse=True)
    leader = sorted_products[0][0] if sorted_products else None

    results = {}
    if leader:
        leader_candles = candles_by_pid.get(leader, [])
        if len(leader_candles) > 1:
            leader_returns = [(leader_candles[i]["close"] - leader_candles[i-1]["close"]) / leader_candles[i-1]["close"] for i in range(1, len(leader_candles))]

            for follower in products:
                if follower == leader:
                    continue
                follower_candles = candles_by_pid.get(follower, [])
                if len(follower_candles) < 3:
                    continue

                # Align by time
                leader_times = {c["time"]: i for i, c in enumerate(leader_candles)}
                follower_times = {c["time"]: i for i, c in enumerate(follower_candles)}

                signals = 0
                wins = 0
                total_pnl = 0.0

                for t in leader_times:
                    if t not in follower_times:
                        continue
                    li = leader_times[t]
                    fi = follower_times[t]

                    # Leader moved >1.5%
                    if li > 0 and li < len(leader_returns) and abs(leader_returns[li]) > 0.015:
                        leader_dir = 1 if leader_returns[li] > 0 else -1
                        # Follower move in next 2 bars
                        if fi + 2 < len(follower_candles):
                            f_move = (follower_candles[fi+2]["close"] - follower_candles[fi]["close"]) / follower_candles[fi]["close"]
                            f_dir = 1 if f_move > 0 else -1

                            signals += 1
                            if f_dir == leader_dir:
                                wins += 1
                            total_pnl += abs(f_move)

                if signals > 10:
                    results[follower] = {
                        "leader": leader,
                        "leader_move_threshold": "1.5%",
                        "signals": signals,
                        "same_direction_pct": round(wins / signals * 100, 1),
                        "avg_follower_move_pct": round(total_pnl / signals * 100, 4),
                    }

    return {"strategy": "leader_follower", "leader": leader, "results": results}


# ============================================================
# EDGE 6: RSI + Burst confluence
# ============================================================
def test_rsi_burst_confluence(candles_by_pid, times, lookup, products):
    """
    When RSI(7) < 30 AND a burst candle forms → higher win rate fade.
    RSI confirms oversold, burst provides the entry signal.
    """
    results = {}
    for pid in products:
        candles = candles_by_pid.get(pid, [])
        if len(candles) < 20:
            continue

        closes = [c["close"] for c in candles]
        rsi_7 = rsi(closes, 7)

        burst_signals = []
        rsi_burst_signals = []

        for i in range(10, len(candles) - 2):
            o = candles[i]["open"]
            h = candles[i]["high"]
            l = candles[i]["low"]
            cl = candles[i]["close"]
            mid = (o + cl) / 2 if (o + cl) > 0 else 1
            range_pct = (h - l) / mid * 100

            if range_pct >= 2.0:
                entry = h
                # Check pullback in next 2 bars
                min_low = min(candles[min(i+j, len(candles)-1)]["low"] for j in range(1, 3))
                pnl_pct = (entry - min_low) / entry * 100

                burst_signals.append(pnl_pct)

                if rsi_7[i] < 30:
                    rsi_burst_signals.append(pnl_pct)

        results[pid] = {
            "burst_only": {
                "signals": len(burst_signals),
                "wins": sum(1 for p in burst_signals if p > 0),
                "win_rate": round(sum(1 for p in burst_signals if p > 0) / len(burst_signals), 3) if burst_signals else 0,
                "avg_pnl_pct": round(sum(burst_signals) / len(burst_signals), 4) if burst_signals else 0,
            },
            "rsi_burst_confluence": {
                "signals": len(rsi_burst_signals),
                "wins": sum(1 for p in rsi_burst_signals if p > 0),
                "win_rate": round(sum(1 for p in rsi_burst_signals if p > 0) / len(rsi_burst_signals), 3) if rsi_burst_signals else 0,
                "avg_pnl_pct": round(sum(rsi_burst_signals) / len(rsi_burst_signals), 4) if rsi_burst_signals else 0,
                "improvement_vs_burst_only": "N/A",
            },
        }
        if burst_signals and rsi_burst_signals:
            burst_wr = sum(1 for p in burst_signals if p > 0) / len(burst_signals)
            confluence_wr = sum(1 for p in rsi_burst_signals if p > 0) / len(rsi_burst_signals)
            results[pid]["rsi_burst_confluence"]["improvement_vs_burst_only"] = round((confluence_wr - burst_wr) * 100, 1)

    return {"strategy": "rsi_burst_confluence", "results": results}


def main():
    client = CoinbaseAdvancedClient()

    print("Fetching candles...")
    candles_cache = {}
    for pid in TOP_PRODUCTS:
        try:
            candles_cache[pid] = fetch_candles_72h(client, pid)
            print(f"  {pid}: {len(candles_cache[pid])} candles")
        except Exception as e:
            print(f"  {pid}: ERROR {e}")

    times = build_timeline(candles_cache)
    lookup = build_lookup(candles_cache, times)
    print(f"\nTimeline: {len(times)} steps, {len(candles_cache)} products")

    all_results = {}

    # Edge 1: Cross-product mean reversion
    print("\n=== EDGE 1: Cross-Product Mean Reversion ===")
    e1 = test_cross_product_mean_reversion(candles_cache, times, lookup, TOP_PRODUCTS)
    all_results["cross_product_mean_reversion"] = e1
    top_pairs = e1.get("signals", [])[:10]
    for p in top_pairs:
        print(f"  {p['pair']}: corr={p['correlation']}, {p['divergence_signals']} signals, {p['win_rate']:.1%} WR, {p['total_pnl_pct']:+.4f}%")

    # Edge 2: Volume spike leading
    print("\n=== EDGE 2: Volume-Spike Leading ===")
    e2 = test_volume_spike_leading(candles_cache, times, lookup, TOP_PRODUCTS)
    all_results["volume_spike_leading"] = e2
    for pid, r in sorted(e2.get("results", {}).items(), key=lambda x: x[1].get("avg_pnl_3bar_pct", 0), reverse=True)[:10]:
        print(f"  {pid}: {r['total_signals']} signals, {r['win_rate']:.1%} WR, {r['avg_pnl_3bar_pct']:+.4f}% (max up {r['avg_max_up_pct']:+.4f}%, max down {r['avg_max_down_pct']:+.4f}%)")

    # Edge 3: Burst cascade
    print("\n=== EDGE 3: Burst Cascade ===")
    e3 = test_burst_cascade(candles_cache, times, lookup, TOP_PRODUCTS)
    all_results["burst_cascade"] = e3
    for pid, r in sorted(e3.get("results", {}).items(), key=lambda x: x[1].get("total_pnl_pct", 0), reverse=True)[:10]:
        print(f"  {pid}: {r['cascade_signals']} cascades, {r['win_rate']:.1%} WR, {r['total_pnl_pct']:+.4f}%, avg {r['avg_pnl_per_signal_pct']:+.4f}%")

    # Edge 4: Quiet-to-explosive
    print("\n=== EDGE 4: Quiet-to-Explosive ===")
    e4 = test_quiet_to_explosive(candles_cache, times, lookup, TOP_PRODUCTS)
    all_results["quiet_to_explosive"] = e4
    for pid, r in sorted(e4.get("results", {}).items(), key=lambda x: x[1].get("avg_pnl_pct", 0), reverse=True)[:10]:
        print(f"  {pid}: {r['signals']} squeezes, {r['win_rate']:.1%} WR, {r['avg_pnl_pct']:+.4f}%, avg squeeze {r['avg_squeeze_length']:.1f} bars")

    # Edge 5: Leader-follower
    print("\n=== EDGE 5: Leader-Follower Network ===")
    e5 = test_leader_follower(candles_cache, times, lookup, TOP_PRODUCTS)
    all_results["leader_follower"] = e5
    print(f"  Leader: {e5.get('leader', 'N/A')}")
    for pid, r in sorted(e5.get("results", {}).items(), key=lambda x: x[1].get("same_direction_pct", 0), reverse=True)[:10]:
        print(f"  {pid} follows {r['leader']}: {r['same_direction_pct']:.1f}% same direction, {r['signals']} signals, avg move {r['avg_follower_move_pct']:+.4f}%")

    # Edge 6: RSI + Burst confluence
    print("\n=== EDGE 6: RSI + Burst Confluence ===")
    e6 = test_rsi_burst_confluence(candles_cache, times, lookup, TOP_PRODUCTS)
    all_results["rsi_burst_confluence"] = e6
    for pid, r in sorted(e6.get("results", {}).items(), key=lambda x: x[1].get("rsi_burst_confluence", {}).get("win_rate", 0), reverse=True)[:10]:
        bf = r["burst_only"]
        rc = r["rsi_burst_confluence"]
        imp = rc.get("improvement_vs_burst_only", "N/A")
        print(f"  {pid}: Burst {bf['win_rate']:.1%} ({bf['signals']} sigs) → RSI+Confluence {rc['win_rate']:.1%} ({rc['signals']} sigs) [{imp:+.1f}%] improvement" if isinstance(imp, (int, float)) else f"  {pid}: Burst {bf['win_rate']:.1%} ({bf['signals']} sigs) → RSI+Confluence {rc['win_rate']:.1%} ({rc['signals']} sigs) [{imp}]")

    out = Path(REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "edges": all_results,
    }, indent=2), encoding="utf-8")
    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
