#!/usr/bin/env python3
"""
Toxicity Window Correlation — Map microstructure events against V2 trade outcomes.

Hypothesis: Toxic microstructure events (Warp Flushes, iceberg dumps, magnetic wall reversals)
correlate with worse trade outcomes (lower win rate, more timeout exits, larger losses).

Input:
- rave_rsi_mr_live_v2_events.jsonl (trade open/close events)
- predatory_signals.jsonl (microstructure signals with timestamps)
- predatory_shadow_monitor_events.jsonl (aligned signal events with delta_bps)

Output:
- Toxicity correlation report: per-trade toxicity exposure, aggregate stats
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
V2_EVENTS = ROOT / "reports" / "rave_rsi_mr_live_v2_events.jsonl"
PREDATORY_SIGNALS = ROOT / "reports" / "predatory_signals.jsonl"
PREDATORY_EVENTS = ROOT / "reports" / "predatory_shadow_monitor_events.jsonl"

# Toxicity window: how far before/after a trade to look for toxic events
LOOKBACK_MINUTES = 5
HOLD_FORWARD_MINUTES = 10

# Toxic event types and their severity weights
TOXICITY_WEIGHTS = {
    # Kraken warp flushes — arbitrage-driven toxicity
    "kraken_warp_flush": 3.0,
    # Iceberg sell reloads — hidden sell pressure
    "iceberg_sell_reload_detected": 2.0,
    # Iceberg buy reloads — can be positive or neutral
    "iceberg_buy_reload_detected": 0.5,
    # Fake floor pulls — support removal, bearish
    "fake_floor_pull_detected": 2.5,
    # Magnetic wall proximity — price magnetism risk
    "magnetic_wall_proximity": 1.0,
}


def parse_ts(ts_str):
    """Parse ISO timestamp to datetime."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return None


def load_v2_trades():
    """Load V2 trade events and pair opens with closes."""
    events = []
    with open(V2_EVENTS, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    # Pair opens with closes
    trades = []
    open_stack = []
    for evt in events:
        if evt.get("action") == "open":
            open_stack.append(evt)
        elif evt.get("action") == "close" and open_stack:
            open_evt = open_stack.pop(0)
            trade = {**open_evt, **evt}  # merge, close fields override
            trade["open_ts"] = parse_ts(open_evt.get("ts_utc"))
            trade["close_ts"] = parse_ts(evt.get("ts_utc"))
            trade["hold_duration_sec"] = (
                (trade["close_ts"] - trade["open_ts"]).total_seconds()
                if trade["open_ts"] and trade["close_ts"]
                else None
            )
            trades.append(trade)

    return trades


def load_predatory_signals():
    """Load predatory signals (raw stream)."""
    signals = []
    with open(PREDATORY_SIGNALS, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                sig = json.loads(line)
                sig["_ts"] = parse_ts(sig.get("ts_utc"))
                signals.append(sig)
    return signals


def load_predatory_events():
    """Load aligned predatory events (with delta_bps)."""
    events = []
    with open(PREDATORY_EVENTS, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                evt = json.loads(line)
                evt["_ts"] = parse_ts(evt.get("ts_utc"))
                events.append(evt)
    return events


def compute_trade_toxicity(trade, signals, events):
    """
    Compute toxicity exposure for a single trade.

    Returns dict with:
    - toxicity_score: weighted sum of toxic events during trade window
    - toxic_events: list of (timestamp, type, weight) tuples
    - warp_flush_count: count of warp flush events
    - iceberg_sell_count: count of iceberg sell events
    - magnetic_wall_exposure: whether magnetic wall was nearby
    """
    if not trade.get("open_ts") or not trade.get("close_ts"):
        return {"toxicity_score": 0, "toxic_events": [], "reason": "no_timestamps"}

    open_ts = trade["open_ts"]
    close_ts = trade["close_ts"]

    # Window: lookback before entry + trade duration
    window_start = open_ts - timedelta(minutes=LOOKBACK_MINUTES)
    window_end = close_ts + timedelta(minutes=HOLD_FORWARD_MINUTES)

    toxic_events = []
    warp_flush_count = 0
    iceberg_sell_count = 0
    iceberg_buy_count = 0
    fake_floor_count = 0
    magnetic_wall_exposure = False

    # Check raw signals for magnetic wall proximity
    for sig in signals:
        if not sig.get("_ts"):
            continue
        if sig["_ts"] < window_start or sig["_ts"] > window_end:
            continue

        # Check if signal is for RAVE-USD
        if sig.get("product_id") != "RAVE-USD":
            continue

        # Magnetic wall proximity
        if sig.get("magnetic_wall") and sig.get("price"):
            wall = sig["magnetic_wall"]
            price = sig["price"]
            distance_pct = abs(wall - price) / price * 100
            if distance_pct < 2.0:  # Within 2% of magnetic wall
                toxic_events.append((sig["_ts"], "magnetic_wall_proximity",
                                     TOXICITY_WEIGHTS["magnetic_wall_proximity"]))
                magnetic_wall_exposure = True

    # Check aligned events for explicit toxic actions
    for evt in events:
        if not evt.get("_ts"):
            continue
        if evt["_ts"] < window_start or evt["_ts"] > window_end:
            continue

        if evt.get("product_id") != "RAVE-USD":
            continue

        action = evt.get("action", "")
        if action in TOXICITY_WEIGHTS:
            weight = TOXICITY_WEIGHTS[action]
            toxic_events.append((evt["_ts"], action, weight))

            if "warp" in action.lower():
                warp_flush_count += 1
            elif "iceberg_sell" in action.lower():
                iceberg_sell_count += 1
            elif "iceberg_buy" in action.lower():
                iceberg_buy_count += 1
            elif "fake_floor" in action.lower():
                fake_floor_count += 1

    toxicity_score = sum(w for _, _, w in toxic_events)

    return {
        "toxicity_score": round(toxicity_score, 2),
        "toxic_event_count": len(toxic_events),
        "toxic_events": [(str(ts), evt_type, w) for ts, evt_type, w in toxic_events[:10]],  # cap for readability
        "warp_flush_count": warp_flush_count,
        "iceberg_sell_count": iceberg_sell_count,
        "iceberg_buy_count": iceberg_buy_count,
        "fake_floor_count": fake_floor_count,
        "magnetic_wall_exposure": magnetic_wall_exposure,
    }


def main():
    print("Loading V2 trades...", flush=True)
    trades = load_v2_trades()
    print(f"  Found {len(trades)} completed trades", flush=True)

    print("Loading predatory signals...", flush=True)
    signals = load_predatory_signals()
    print(f"  Found {len(signals)} raw signal rows", flush=True)

    print("Loading predatory events...", flush=True)
    events = load_predatory_events()
    print(f"  Found {len(events)} aligned event rows", flush=True)

    # Compute toxicity for each trade
    enriched_trades = []
    for trade in trades:
        tox = compute_trade_toxicity(trade, signals, events)
        enriched = {**trade, **tox}
        enriched_trades.append(enriched)

    # Aggregate analysis
    print("\n" + "=" * 80, flush=True)
    print("TOXICITY WINDOW CORRELATION REPORT", flush=True)
    print("=" * 80, flush=True)

    # Split by outcome
    winning_trades = [t for t in enriched_trades if t.get("net", 0) > 0]
    losing_trades = [t for t in enriched_trades if t.get("net", 0) <= 0]

    print(f"\nTotal trades: {len(enriched_trades)}", flush=True)
    print(f"  Wins: {len(winning_trades)} ({len(winning_trades)/max(1,len(enriched_trades))*100:.1f}%)", flush=True)
    print(f"  Losses: {len(losing_trades)} ({len(losing_trades)/max(1,len(enriched_trades))*100:.1f}%)", flush=True)

    # Toxicity by outcome
    avg_tox_wins = (sum(t["toxicity_score"] for t in winning_trades) / max(1, len(winning_trades)))
    avg_tox_losses = (sum(t["toxicity_score"] for t in losing_trades) / max(1, len(losing_trades)))

    print(f"\nAvg toxicity score — Wins: {avg_tox_wins:.2f}, Losses: {avg_tox_losses:.2f}", flush=True)

    # Warp flush exposure
    trades_with_warp = [t for t in enriched_trades if t.get("warp_flush_count", 0) > 0]
    warp_wins = [t for t in trades_with_warp if t.get("net", 0) > 0]
    warp_losses = [t for t in trades_with_warp if t.get("net", 0) <= 0]

    print(f"\nTrades with Warp Flush exposure: {len(trades_with_warp)}", flush=True)
    if trades_with_warp:
        print(f"  Win rate: {len(warp_wins)/len(trades_with_warp)*100:.1f}%", flush=True)
        print(f"  Avg PnL: ${sum(t.get('net', 0) for t in trades_with_warp)/len(trades_with_warp):.2f}", flush=True)

    # Iceberg sell exposure
    trades_with_iceberg_sell = [t for t in enriched_trades if t.get("iceberg_sell_count", 0) > 0]
    is_wins = [t for t in trades_with_iceberg_sell if t.get("net", 0) > 0]
    is_losses = [t for t in trades_with_iceberg_sell if t.get("net", 0) <= 0]

    print(f"\nTrades with Iceberg Sell exposure: {len(trades_with_iceberg_sell)}", flush=True)
    if trades_with_iceberg_sell:
        print(f"  Win rate: {len(is_wins)/len(trades_with_iceberg_sell)*100:.1f}%", flush=True)
        print(f"  Avg PnL: ${sum(t.get('net', 0) for t in trades_with_iceberg_sell)/len(trades_with_iceberg_sell):.2f}", flush=True)

    # Magnetic wall exposure
    trades_with_mag_wall = [t for t in enriched_trades if t.get("magnetic_wall_exposure")]
    mw_wins = [t for t in trades_with_mag_wall if t.get("net", 0) > 0]
    mw_losses = [t for t in trades_with_mag_wall if t.get("net", 0) <= 0]

    print(f"\nTrades with Magnetic Wall exposure: {len(trades_with_mag_wall)}", flush=True)
    if trades_with_mag_wall:
        print(f"  Win rate: {len(mw_wins)/len(trades_with_mag_wall)*100:.1f}%", flush=True)
        print(f"  Avg PnL: ${sum(t.get('net', 0) for t in trades_with_mag_wall)/len(trades_with_mag_wall):.2f}", flush=True)

    # Toxicity quartile analysis
    enriched_sorted = sorted(enriched_trades, key=lambda t: t["toxicity_score"])
    q_size = max(1, len(enriched_sorted) // 4)
    quartiles = [
        enriched_sorted[i*q_size:(i+1)*q_size]
        for i in range(4)
    ]

    print(f"\nToxicity Quartile Analysis:", flush=True)
    for i, q in enumerate(quartiles):
        if not q:
            continue
        q_wins = [t for t in q if t.get("net", 0) > 0]
        avg_pnl = sum(t.get("net", 0) for t in q) / len(q)
        tox_range = f"{q[0]['toxicity_score']:.1f}-{q[-1]['toxicity_score']:.1f}"
        print(f"  Q{i+1} (tox {tox_range}): {len(q)} trades, "
              f"WR={len(q_wins)/len(q)*100:.1f}%, avg PnL=${avg_pnl:.2f}", flush=True)

    # Exit reason vs toxicity
    timeout_trades = [t for t in enriched_trades if t.get("reason") == "timeout"]
    tp_trades = [t for t in enriched_trades if t.get("reason") == "tp"]

    if timeout_trades:
        avg_tox_timeout = sum(t["toxicity_score"] for t in timeout_trades) / len(timeout_trades)
        print(f"\nTimeout exits: {len(timeout_trades)}, avg toxicity: {avg_tox_timeout:.2f}", flush=True)
    if tp_trades:
        avg_tox_tp = sum(t["toxicity_score"] for t in tp_trades) / len(tp_trades)
        print(f"TP exits: {len(tp_trades)}, avg toxicity: {avg_tox_tp:.2f}", flush=True)

    # Detailed trade log
    print(f"\n{'='*80}", flush=True)
    print("DETAILED TRADE LOG WITH TOXICITY", flush=True)
    print(f"{'='*80}", flush=True)
    print(f"{'#':>3} | {'Net':>8} | {'Tox':>6} | {'Warp':>4} | {'IceS':>4} | {'MagW':>4} | {'Reason':>7} | {'Hold':>6}", flush=True)
    print(f"{'-'*3}-+-{'-'*8}-+-{'-'*6}-+-{'-'*4}-+-{'-'*4}-+-{'-'*4}-+-{'-'*7}-+-{'-'*6}", flush=True)

    for i, t in enumerate(enriched_trades, 1):
        print(f"{i:>3} | ${t.get('net', 0):>7.2f} | {t['toxicity_score']:>6.1f} | "
              f"{t.get('warp_flush_count', 0):>4} | {t.get('iceberg_sell_count', 0):>4} | "
              f"{'Y' if t.get('magnetic_wall_exposure') else 'N':>4} | "
              f"{t.get('reason', '?'):>7} | {t.get('hold_bars', '?'):>5}b", flush=True)

    print(f"\n{'='*80}", flush=True)
    print("CRITICAL CAVEAT: TEMPORAL MISMATCH", flush=True)
    print(f"{'='*80}", flush=True)
    print(f"All {len(enriched_trades)} V2 trades are from the 72h startup backfill.", flush=True)
    print(f"Backfill events are compressed to a single timestamp (~13:27 UTC).", flush=True)
    print(f"Predatory signal logger started at ~13:52 UTC — 25 min AFTER backfill.", flush=True)
    print(f"Result: Zero toxicity scores are expected — there is NO temporal overlap.", flush=True)
    print(f"", flush=True)
    print(f"What WOULD be needed for honest toxicity correlation:", flush=True)
    print(f"1. Wait for V2 forward live trades with proper bar_start timestamps", flush=True)
    print(f"2. Or replay the 72h backfill WITH the signal logger data aligned by candle time", flush=True)
    print(f"3. The predatory_shadow_monitor_events.jsonl has 2792 events from 13:52-15:35 UTC", flush=True)
    print(f"   — These cover the LIVE forward period, not the backfill", flush=True)

    # Save report
    report = {
        "caveat": "All V2 trades are startup backfill (compressed timestamps). Predatory signal logger started 25 min after backfill completed. Zero toxicity scores are expected — there is NO temporal overlap. Forward live trades with proper bar_start timestamps are needed for honest correlation.",
        "summary": {
            "total_trades": len(enriched_trades),
            "wins": len(winning_trades),
            "losses": len(losing_trades),
            "win_rate_pct": round(len(winning_trades) / max(1, len(enriched_trades)) * 100, 1),
            "avg_toxicity_wins": round(avg_tox_wins, 2),
            "avg_toxicity_losses": round(avg_tox_losses, 2),
            "trades_with_warp": len(trades_with_warp),
            "trades_with_iceberg_sell": len(trades_with_iceberg_sell),
            "trades_with_magnetic_wall": len(trades_with_mag_wall),
        },
        "quartiles": [
            {
                "quartile": i+1,
                "tox_range": f"{q[0]['toxicity_score']:.1f}-{q[-1]['toxicity_score']:.1f}" if q else "N/A",
                "trades": len(q),
                "win_rate_pct": round(len([t for t in q if t.get("net", 0) > 0]) / max(1, len(q)) * 100, 1),
                "avg_pnl": round(sum(t.get("net", 0) for t in q) / max(1, len(q)), 2),
            }
            for i, q in enumerate(quartiles) if q
        ],
        "trades": [
            {
                "trade_num": i+1,
                "net": round(t.get("net", 0), 2),
                "toxicity_score": t["toxicity_score"],
                "warp_flush_count": t.get("warp_flush_count", 0),
                "iceberg_sell_count": t.get("iceberg_sell_count", 0),
                "iceberg_buy_count": t.get("iceberg_buy_count", 0),
                "fake_floor_count": t.get("fake_floor_count", 0),
                "magnetic_wall_exposure": t.get("magnetic_wall_exposure", False),
                "exit_reason": t.get("reason"),
                "hold_bars": t.get("hold_bars"),
                "rsi_at_entry": t.get("rsi_at_entry"),
                "regime_score": t.get("regime_score"),
            }
            for i, t in enumerate(enriched_trades)
        ],
    }

    report_path = ROOT / "reports" / "toxicity_correlation_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {report_path}", flush=True)
    print("\nDone. 🎯", flush=True)


if __name__ == "__main__":
    main()
