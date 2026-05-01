#!/usr/bin/env python3
"""
Super System — Combine ALL 4 independent edges into one:
1. Burst Fade (the proven core)
2. Volume-Spike Leading (pre-burst signal on RAVE)
3. RSI + Burst Confluence (100% WR filter)
4. Leader-Follower (stat arb when RAVE moves)

This tests whether stacking edges compounds or cancels.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "super_system.json"

ALL_PRODUCTS = [
    "RAVE-USD", "TROLL-USD", "BAL-USD", "NOM-USD", "MASK-USD",
    "ALEPH-USD", "CHECK-USD", "BLUR-USD", "AVT-USD", "IOTX-USD",
    "IRYS-USD", "CFG-USD", "FARTCOIN-USD", "DASH-USD", "BOBBOB-USD",
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
        time.sleep(0.06)
    return sorted(all_candles, key=lambda x: x["time"])


def rsi(closes, period=7):
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


def run_super_system(candles_by_pid, products, times, lookup, config):
    """
    Combined system with 4 edge types:
    - Edge A: Burst fade (core)
    - Edge B: Volume spike leading (RAVE-USD)
    - Edge C: RSI + burst confluence filter
    - Edge D: Leader-follower (RAVE → followers)
    """
    starting_cash = config.get("starting_cash", 48.0)
    maker_fee_bps = config.get("maker_fee_bps", 40.0)
    fee_rate = maker_fee_bps / 10000.0

    # Burst fade params
    bf_max_conc = config.get("bf_max_concurrent", 5)
    bf_target_frac = config.get("bf_target_frac", 0.6)
    bf_stop_frac = config.get("bf_stop_frac", 0.2)
    bf_up_stop_frac = config.get("bf_up_stop_frac", 0.0)
    bf_down_stop_frac = config.get("bf_down_stop_frac", 0.1)
    bf_burst_thresh = config.get("bf_burst_thresh", 2.0)
    bf_dynamic_quote = config.get("bf_dynamic_quote", True)
    bf_max_quote_mult = config.get("bf_max_quote_mult", 2.0)
    bf_base_quote = config.get("bf_base_quote", 24.0)

    # Volume spike params
    vs_enabled = config.get("vs_enabled", True)
    vs_vol_mult = config.get("vs_vol_mult", 2.0)
    vs_max_conc = config.get("vs_max_concurrent", 2)
    vs_target_pct = config.get("vs_target_pct", 0.015)
    vs_stop_pct = config.get("vs_stop_pct", 0.01)
    vs_quote = config.get("vs_quote", 12.0)
    vs_products = config.get("vs_products", ["RAVE-USD", "BAL-USD"])

    # RSI confluence filter
    rsi_filter_enabled = config.get("rsi_filter_enabled", False)
    rsi_period = config.get("rsi_period", 7)
    rsi_oversold = config.get("rsi_oversold", 30)

    # Leader-follower params
    lf_enabled = config.get("lf_enabled", True)
    lf_leader = config.get("lf_leader", "RAVE-USD")
    lf_move_thresh = config.get("lf_move_thresh", 0.015)
    lf_max_conc = config.get("lf_max_concurrent", 2)
    lf_target_pct = config.get("lf_target_pct", 0.01)
    lf_stop_pct = config.get("lf_stop_pct", 0.005)
    lf_quote = config.get("lf_quote", 12.0)
    lf_followers = config.get("lf_followers", ["MASK-USD", "FARTCOIN-USD", "DASH-USD", "ALEPH-USD", "IRYS-USD"])

    cash = starting_cash
    positions = {}  # pid -> {entry, target, stop, qty, entry_fee, edge_type}
    realized_net = 0.0
    closes = 0
    wins = 0
    losses = 0
    fees = 0.0
    edge_trades = {"burst_fade": 0, "volume_spike": 0, "leader_follower": 0}
    edge_wins = {"burst_fade": 0, "volume_spike": 0, "leader_follower": 0}

    # Pre-compute RSI for all products
    rsi_by_pid = {}
    for pid in products:
        candles = candles_by_pid.get(pid, [])
        if candles:
            closes_list = [c["close"] for c in candles]
            rsi_by_pid[pid] = rsi(closes_list, rsi_period)

    # Pre-compute volume averages for volume spike products
    vol_avg_by_pid = {}
    for pid in vs_products:
        candles = candles_by_pid.get(pid, [])
        if candles:
            volumes = [c["volume"] for c in candles]
            vol_avg = {}
            for i in range(20, len(candles)):
                avg = sum(volumes[i-20:i]) / 20
                vol_avg[int(candles[i]["time"])] = avg
            vol_avg_by_pid[pid] = vol_avg

    # Pre-compute leader returns
    leader_returns = {}
    leader_candles = candles_by_pid.get(lf_leader, [])
    if leader_candles:
        for i in range(1, len(leader_candles)):
            prev = leader_candles[i-1]["close"]
            curr = leader_candles[i]["close"]
            ret = (curr - prev) / prev if prev > 0 else 0
            leader_returns[int(leader_candles[i]["time"])] = ret

    for t in times:
        tick = lookup.get(t, {})

        # === EXITS ===
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
            edge = pos.get("edge_type", "unknown")

            if l <= tp:
                gross = (ep - tp) * qty
                ef = pos["entry_fee"]
                xf = tp * qty * fee_rate
                net = gross - ef - xf
                realized_net += net
                closes += 1
                wins += 1
                fees += ef + xf
                edge_wins[edge] = edge_wins.get(edge, 0) + 1
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

        # Count current positions by edge type
        bf_conc = sum(1 for p in positions.values() if p.get("edge_type") == "burst_fade")
        vs_conc = sum(1 for p in positions.values() if p.get("edge_type") == "volume_spike")
        lf_conc = sum(1 for p in positions.values() if p.get("edge_type") == "leader_follower")

        # === EDGE A: Burst Fade ===
        if bf_conc < bf_max_conc:
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

                if range_pct < bf_burst_thresh:
                    continue

                # RSI confluence filter
                if rsi_filter_enabled and pid in rsi_by_pid:
                    rsi_vals = rsi_by_pid[pid]
                    # Find RSI at current bar
                    candle_idx = None
                    for i, candle in enumerate(candles_by_pid.get(pid, [])):
                        if int(candle["time"]) == t:
                            candle_idx = i
                            break
                    if candle_idx is not None and candle_idx < len(rsi_vals):
                        if rsi_vals[candle_idx] > rsi_oversold:
                            continue  # Skip if not oversold

                # Asymmetric stops
                effective_stop_frac = bf_stop_frac
                if bf_up_stop_frac > 0 or bf_down_stop_frac > 0:
                    if cl > o:
                        effective_stop_frac = bf_up_stop_frac if bf_up_stop_frac > 0 else bf_stop_frac
                    else:
                        effective_stop_frac = bf_down_stop_frac if bf_down_stop_frac > 0 else bf_stop_frac

                # Dynamic quote sizing
                effective_quote = bf_base_quote
                if bf_dynamic_quote and range_pct > 3.0:
                    effective_quote = min(bf_base_quote * bf_max_quote_mult * (range_pct / 3.0), 48.0)

                if cash < effective_quote:
                    continue

                entry = h
                target = entry * (1 - range_pct / 100 * bf_target_frac)
                stop = entry * (1 + range_pct / 100 * effective_stop_frac)

                entry_fee = entry * (effective_quote / entry) * fee_rate
                qty = (effective_quote - entry_fee) / entry
                if qty <= 0:
                    continue

                positions[pid] = {
                    "entry": entry, "target": target, "stop": stop,
                    "qty": qty, "entry_fee": entry_fee, "edge_type": "burst_fade",
                }
                cash -= effective_quote
                edge_trades["burst_fade"] += 1

        # === EDGE B: Volume Spike Leading ===
        if vs_enabled and vs_conc < vs_max_conc:
            for pid in vs_products:
                if pid in positions or pid not in tick:
                    continue
                c = tick[pid]
                vol = float(c["volume"])

                avg_vol = vol_avg_by_pid.get(pid, {}).get(t, 0)
                if avg_vol > 0 and vol >= avg_vol * vs_vol_mult:
                    entry = float(c["close"])
                    target = entry * (1 + vs_target_pct)
                    stop = entry * (1 - vs_stop_pct)

                    if cash < vs_quote:
                        continue

                    entry_fee = entry * (vs_quote / entry) * fee_rate
                    qty = (vs_quote - entry_fee) / entry
                    if qty <= 0:
                        continue

                    positions[pid] = {
                        "entry": entry, "target": target, "stop": stop,
                        "qty": qty, "entry_fee": entry_fee, "edge_type": "volume_spike",
                    }
                    cash -= vs_quote
                    edge_trades["volume_spike"] += 1

        # === EDGE D: Leader-Follower ===
        if lf_enabled and lf_conc < lf_max_conc:
            leader_ret = leader_returns.get(t, 0)
            if abs(leader_ret) > lf_move_thresh:
                leader_dir = 1 if leader_ret > 0 else -1
                for pid in lf_followers:
                    if pid in positions or pid not in tick:
                        continue
                    c = tick[pid]
                    entry = float(c["close"])

                    # Trade in leader's direction
                    if leader_dir > 0:
                        target = entry * (1 + lf_target_pct)
                        stop = entry * (1 - lf_stop_pct)
                    else:
                        # Can't short on spot, skip
                        continue

                    if cash < lf_quote:
                        continue

                    entry_fee = entry * (lf_quote / entry) * fee_rate
                    qty = (lf_quote - entry_fee) / entry
                    if qty <= 0:
                        continue

                    positions[pid] = {
                        "entry": entry, "target": target, "stop": stop,
                        "qty": qty, "entry_fee": entry_fee, "edge_type": "leader_follower",
                    }
                    cash -= lf_quote
                    edge_trades["leader_follower"] += 1

    # Per-edge breakdown
    edge_results = {}
    for edge in ["burst_fade", "volume_spike", "leader_follower"]:
        edge_results[edge] = {
            "trades": edge_trades.get(edge, 0),
            "wins": edge_wins.get(edge, 0),
        }

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
        "edge_results": edge_results,
    }


def main():
    client = CoinbaseAdvancedClient()

    print("Fetching candles for 15 products...")
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

    # Test different configurations
    configs = []

    # Config 1: Burst fade only (baseline)
    configs.append({
        "name": "burst_fade_only",
        "vs_enabled": False,
        "lf_enabled": False,
        "rsi_filter_enabled": False,
    })

    # Config 2: Burst fade + RSI filter
    configs.append({
        "name": "burst_fade_rsi_filter",
        "vs_enabled": False,
        "lf_enabled": False,
        "rsi_filter_enabled": True,
    })

    # Config 3: Burst fade + volume spike
    configs.append({
        "name": "burst_fade_vol_spike",
        "vs_enabled": True,
        "lf_enabled": False,
        "rsi_filter_enabled": False,
    })

    # Config 4: Burst fade + leader-follower
    configs.append({
        "name": "burst_fade_leader_follower",
        "vs_enabled": False,
        "lf_enabled": True,
        "rsi_filter_enabled": False,
    })

    # Config 5: ALL edges combined
    configs.append({
        "name": "ALL_edges_combined",
        "vs_enabled": True,
        "lf_enabled": True,
        "rsi_filter_enabled": False,
    })

    # Config 6: All edges + RSI filter
    configs.append({
        "name": "ALL_edges_RSI_filter",
        "vs_enabled": True,
        "lf_enabled": True,
        "rsi_filter_enabled": True,
    })

    # Core burst fade params (from ultimate system)
    base_config = {
        "bf_max_concurrent": 5,
        "bf_target_frac": 0.6,
        "bf_stop_frac": 0.2,
        "bf_up_stop_frac": 0.0,
        "bf_down_stop_frac": 0.1,
        "bf_burst_thresh": 2.0,
        "bf_dynamic_quote": True,
        "bf_max_quote_mult": 2.0,
        "bf_base_quote": 24.0,
        "maker_fee_bps": 40.0,
        "vs_vol_mult": 2.0,
        "vs_max_concurrent": 2,
        "vs_target_pct": 0.015,
        "vs_stop_pct": 0.01,
        "vs_quote": 12.0,
        "vs_products": ["RAVE-USD", "BAL-USD"],
        "lf_leader": "RAVE-USD",
        "lf_move_thresh": 0.015,
        "lf_max_concurrent": 2,
        "lf_target_pct": 0.01,
        "lf_stop_pct": 0.005,
        "lf_quote": 12.0,
        "lf_followers": ["MASK-USD", "FARTCOIN-USD", "DASH-USD", "ALEPH-USD", "IRYS-USD"],
    }

    results = {}
    for cfg in configs:
        full_config = {**base_config, **cfg}
        r = run_super_system(candles_cache, products, times, lookup, full_config)
        results[cfg["name"]] = r
        print(f"\n{cfg['name']}:")
        print(f"  Net: ${r['realized_net']:.2f} ({r['return_pct']:.1f}%), {r['closes']} closes, {r['win_rate']:.1f}% WR")
        print(f"  Trades/day: {r['trades_per_day']:.0f}, Fees: ${r['total_fees']:.2f}")
        for edge, er in r["edge_results"].items():
            wr = round(er["wins"] / max(1, er["trades"]) * 100, 1) if er["trades"] > 0 else 0
            print(f"    {edge}: {er['trades']} trades, {er['wins']} wins ({wr:.1f}%)")

    # Fee sensitivity for best config
    best_name = max(results, key=lambda k: results[k]["realized_net"])
    print(f"\n=== Fee sensitivity for best: {best_name} ===")
    best_base = {**base_config, **next(c for c in configs if c["name"] == best_name)}
    for fee_bps in [5, 10, 20, 40, 60]:
        test_config = {**best_base, "maker_fee_bps": fee_bps}
        r = run_super_system(candles_cache, products, times, lookup, test_config)
        print(f"  {fee_bps}bps: ${r['realized_net']:.2f} ({r['return_pct']:.1f}%), {r['closes']} closes")

    out = Path(REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": results,
    }, indent=2), encoding="utf-8")
    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
