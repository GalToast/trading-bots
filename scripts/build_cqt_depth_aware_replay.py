#!/usr/bin/env python3
"""
Depth-Aware Causal Replay for CQT/USD.

Builds on the vulture replay concept but adds:
1. LIVE L2 order book depth capture (not just bid/ask)
2. VWAP-based slippage modeling for $10 notional orders
3. TRUE taker fees (120bps) on entry AND exit
4. Force-close at horizon with taker exit pricing
5. Fillability modeling based on actual book depth

This is the VERIFICATION GATE before any live probe on CQT/USD.

Usage:
    python scripts/build_cqt_depth_aware_replay.py --samples 200 --interval 2 --horizons 30,60,300
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kraken_spot_client import KrakenSpotClient, parse_pair, to_float, product_id_for_pair
from build_kraken_vulture_reversal_replay import normalize_product  # noqa: E402

DEFAULT_PRODUCT = "CQT-USD"
DEFAULT_SAMPLES = 200
DEFAULT_INTERVAL = 2
DEFAULT_HORIZONS = "30,60,300,900"
DEFAULT_TAURER_FEE_BPS = 120.0  # Kraken taker fee for tier 0
DEFAULT_NOTIONAL_USD = 10.0
DEFAULT_MIN_DUMP_BPS = 200.0  # Lower threshold for illiquid products


@dataclass(frozen=True)
class BookSnapshot:
    ts: float
    bid: float
    ask: float
    bids: list[tuple[float, float]]  # (price, size)
    asks: list[tuple[float, float]]
    spread_bps: float
    bid_depth_usd: float
    ask_depth_usd: float


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def kraken_name(product: str) -> str:
    return product.replace("-", "").upper()


def compute_spread_bps(bid: float, ask: float) -> float:
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return 0.0
    return (ask - bid) / mid * 10000.0


def compute_vwap_fill_price(levels: list[tuple[float, float]], target_usd: float, reverse: bool = False) -> tuple[float, float, bool]:
    """
    Compute VWAP fill price for a taker order of target_usd.
    
    Args:
        levels: (price, size) tuples, sorted best-first
        target_usd: USD notional to fill
        reverse: if True, levels are sorted worst-first (shouldn't happen)
    
    Returns:
        (vwap_price, filled_usd, fully_filled)
    """
    filled_usd = 0.0
    total_cost = 0.0
    
    for price, size in levels:
        level_usd = price * size
        take_usd = min(level_usd, target_usd - filled_usd)
        take_qty = take_usd / price if price > 0 else 0
        total_cost += take_qty * price
        filled_usd += take_usd
        
        if filled_usd >= target_usd - 0.01:  # Within 1 cent
            break
    
    if filled_usd <= 0:
        return 0.0, 0.0, False
    
    vwap = total_cost / (filled_usd / levels[0][0]) if filled_usd > 0 else 0  # Approximate
    # Better: vwap = total_qty / total_cost inverse
    total_qty = 0
    for price, size in levels:
        level_usd = price * size
        take_usd = min(level_usd, target_usd - (total_qty * (price if total_qty > 0 else 1)))
        # Simpler approach:
        take_qty = min(size, (target_usd - total_qty * price) / price) if price > 0 else 0
        if take_qty <= 0:
            break
        total_qty += take_qty
        total_cost += take_qty * price
        if total_cost >= target_usd:
            break
    
    vwap = total_cost / total_qty if total_qty > 0 else 0
    fully_filled = filled_usd >= target_usd * 0.95  # 95% fill threshold
    
    return vwap, filled_usd, fully_filled


def compute_vwap_fill_price_v2(levels: list[tuple[float, float]], target_usd: float) -> tuple[float, float, bool]:
    """
    Compute VWAP fill price for a taker order consuming from the book.
    levels should be sorted best-first (lowest ask for buy, highest bid for sell).
    
    Returns: (vwap_price, filled_usd, fully_filled)
    """
    remaining_usd = target_usd
    total_qty = 0.0
    total_cost = 0.0
    
    for price, size in levels:
        if remaining_usd <= 0:
            break
        level_usd = price * size
        take_usd = min(level_usd, remaining_usd)
        take_qty = take_usd / price
        total_qty += take_qty
        total_cost += take_usd  # cost = qty * price = take_usd
        remaining_usd -= take_usd
    
    if total_qty <= 0:
        return 0.0, 0.0, False
    
    vwap = total_cost / total_qty
    filled_usd = total_cost
    fully_filled = remaining_usd <= target_usd * 0.05  # 95% filled
    
    return vwap, filled_usd, fully_filled


def taker_buy_net_bps(entry_vwap: float, exit_vwap: float, taker_fee_bps: float) -> float:
    """Net bps after round-trip taker fees."""
    if entry_vwap <= 0 or exit_vwap <= 0:
        return -9999.0
    gross_bps = ((exit_vwap / entry_vwap) - 1.0) * 10000.0
    fee_cost_bps = 2.0 * float(taker_fee_bps)  # Entry + exit
    return gross_bps - fee_cost_bps


def capture_book(client: KrakenSpotClient, product: str) -> BookSnapshot | None:
    """Capture a single L2 book snapshot."""
    try:
        kraken = kraken_name(product)
        book = client.depth(kraken, count=10)
        if not book:
            return None
        
        # Kraken depth response: {'bids': [[price, size, ts], ...], 'asks': [...]}
        bids_raw = book.get('bids', [])
        asks_raw = book.get('asks', [])
        
        bids = [(to_float(r[0]), to_float(r[1])) for r in bids_raw if len(r) >= 2]
        asks = [(to_float(r[0]), to_float(r[1])) for r in asks_raw if len(r) >= 2]
        
        if not bids or not asks:
            return None
        
        # Sort: bids descending (best first), asks ascending (best first)
        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])
        
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        spread = compute_spread_bps(best_bid, best_ask)
        
        bid_depth_usd = sum(p * s for p, s in bids[:10])
        ask_depth_usd = sum(p * s for p, s in asks[:10])
        
        return BookSnapshot(
            ts=time.time(),
            bid=best_bid,
            ask=best_ask,
            bids=bids,
            asks=asks,
            spread_bps=round(spread, 2),
            bid_depth_usd=round(bid_depth_usd, 2),
            ask_depth_usd=round(ask_depth_usd, 2),
        )
    except Exception as e:
        print(f"  Error capturing {product}: {e}")
        return None


def find_dump_events(snapshots: list[BookSnapshot], min_dump_bps: float, lookback: int = 10) -> list[dict]:
    """Find dump->recovery events causally from the captured tape."""
    events = []
    
    if len(snapshots) < lookback + 2:
        return events
    
    for i in range(lookback, len(snapshots) - 1):
        # Look back to find prior high
        prior_window = snapshots[max(0, i - lookback):i]
        prior_high_bid = max(s.bid for s in prior_window)
        
        current = snapshots[i]
        if prior_high_bid <= 0:
            continue
        
        # Check for dump
        dump_bps = ((current.bid / prior_high_bid) - 1.0) * 10000.0
        if dump_bps > -abs(min_dump_bps):
            continue  # Not a big enough dump
        
        # Entry: next snapshot's ask (taker buy)
        entry_idx = i + 1
        if entry_idx >= len(snapshots):
            continue
        
        entry = snapshots[entry_idx]
        if not entry.asks or entry.asks[0][0] <= 0:
            continue
        
        events.append({
            'signal_idx': i,
            'entry_idx': entry_idx,
            'prior_high_bid': prior_high_bid,
            'dump_bps': round(dump_bps, 2),
            'signal_bid': current.bid,
            'signal_ask': current.ask,
            'signal_spread_bps': current.spread_bps,
        })
    
    return events


def replay_event(
    event: dict,
    snapshots: list[BookSnapshot],
    horizons: list[float],
    notional_usd: float,
    taker_fee_bps: float,
) -> list[dict]:
    """Replay a single dump event with depth-aware modeling."""
    results = []
    entry_idx = event['entry_idx']
    entry_snapshot = snapshots[entry_idx]
    
    # Entry: taker buy at ask VWAP
    entry_vwap, entry_filled_usd, entry_full = compute_vwap_fill_price_v2(
        entry_snapshot.asks, notional_usd
    )
    
    if entry_vwap <= 0:
        return [{
            **event,
            'horizon_seconds': 0,
            'entry_vwap': 0,
            'exit_vwap': 0,
            'net_bps': -9999,
            'entry_slippage_bps': 0,
            'exit_slippage_bps': 0,
            'entry_fully_filled': False,
            'exit_fully_filled': False,
            'blocker': 'entry_fill_failed',
        }]
    
    # Entry slippage vs best ask
    best_ask = entry_snapshot.asks[0][0]
    entry_slippage_bps = ((entry_vwap / best_ask) - 1.0) * 10000.0 if best_ask > 0 else 0
    
    for horizon in horizons:
        # Find exit snapshot at horizon
        target_ts = entry_snapshot.ts + horizon
        exit_idx = None
        best_delta = float('inf')
        
        for j in range(entry_idx + 1, len(snapshots)):
            delta = abs(snapshots[j].ts - target_ts)
            if delta < best_delta:
                best_delta = delta
                exit_idx = j
            if snapshots[j].ts >= target_ts:
                break
        
        if exit_idx is None or exit_idx >= len(snapshots):
            continue
        
        exit_snapshot = snapshots[exit_idx]
        
        # Exit: taker sell at bid VWAP
        exit_vwap, exit_filled_usd, exit_full = compute_vwap_fill_price_v2(
            sorted(exit_snapshot.bids, key=lambda x: x[0], reverse=True),  # Best bids first
            notional_usd
        )
        
        # Exit slippage vs best bid
        best_bid = exit_snapshot.bids[0][0]
        exit_slippage_bps = ((best_bid / exit_vwap) - 1.0) * 10000.0 if exit_vwap > 0 else 0
        
        # Net bps
        net_bps = taker_buy_net_bps(entry_vwap, exit_vwap, taker_fee_bps)
        
        # MFE/MAE
        path = snapshots[entry_idx:exit_idx + 1]
        mfe_bps = max(
            taker_buy_net_bps(entry_vwap, s.bids[0][0], taker_fee_bps)
            for s in path if s.bids
        ) if path else -9999
        mae_bps = min(
            taker_buy_net_bps(entry_vwap, s.bids[0][0], taker_fee_bps)
            for s in path if s.bids
        ) if path else -9999
        
        # Blockers
        blockers = []
        if not entry_full:
            blockers.append(f'entry_partial_fill({entry_filled_usd:.2f}/{notional_usd:.2f})')
        if not exit_full:
            blockers.append(f'exit_partial_fill({exit_filled_usd:.2f}/{notional_usd:.2f})')
        if entry_slippage_bps > 50:
            blockers.append(f'high_entry_slippage({entry_slippage_bps:.1f}bps)')
        if net_bps < 0:
            blockers.append('net_negative')
        
        results.append({
            **event,
            'horizon_seconds': horizon,
            'entry_ts': round(entry_snapshot.ts, 3),
            'exit_ts': round(exit_snapshot.ts, 3),
            'elapsed_seconds': round(exit_snapshot.ts - entry_snapshot.ts, 3),
            'entry_vwap': round(entry_vwap, 8),
            'exit_vwap': round(exit_vwap, 8),
            'entry_best_ask': round(best_ask, 8),
            'exit_best_bid': round(best_bid, 8),
            'entry_slippage_bps': round(entry_slippage_bps, 2),
            'exit_slippage_bps': round(exit_slippage_bps, 2),
            'entry_filled_usd': round(entry_filled_usd, 2),
            'exit_filled_usd': round(exit_filled_usd, 2),
            'entry_fully_filled': entry_full,
            'exit_fully_filled': exit_full,
            'net_bps': round(net_bps, 2),
            'mfe_bps': round(mfe_bps, 2),
            'mae_bps': round(mae_bps, 2),
            'executable_positive': net_bps > 0 and not blockers,
            'blocker': ', '.join(blockers) if blockers else 'none',
        })
    
    return results


def main():
    parser = argparse.ArgumentParser(description="Depth-aware causal replay for CQT/USD")
    parser.add_argument("--product", default=DEFAULT_PRODUCT)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("--horizons", default=DEFAULT_HORIZONS)
    parser.add_argument("--taker-fee-bps", type=float, default=DEFAULT_TAURER_FEE_BPS)
    parser.add_argument("--notional-usd", type=float, default=DEFAULT_NOTIONAL_USD)
    parser.add_argument("--min-dump-bps", type=float, default=DEFAULT_MIN_DUMP_BPS)
    parser.add_argument("--json-path", default=str(REPORTS / "cqt_depth_aware_replay.json"))
    parser.add_argument("--md-path", default=str(REPORTS / "cqt_depth_aware_replay.md"))
    args = parser.parse_args()
    
    horizons = [float(x) for x in args.horizons.split(",")]
    client = KrakenSpotClient()
    
    print(f"🔬 Capturing {args.samples} L2 snapshots of {args.product} at {args.interval}s intervals...")
    print(f"   Notional: ${args.notional_usd}, Taker fee: {args.taker_fee_bps}bps, Min dump: {args.min_dump_bps}bps")
    print()
    
    # Capture snapshots
    snapshots: list[BookSnapshot] = []
    for i in range(args.samples):
        snap = capture_book(client, args.product)
        if snap:
            snapshots.append(snap)
            if (i + 1) % 20 == 0:
                print(f"  Captured {i+1}/{args.samples} snapshots (spread: {snap.spread_bps}bps, depth: ${snap.bid_depth_usd:.0f}/${snap.ask_depth_usd:.0f})")
        else:
            print(f"  Failed to capture snapshot {i+1}")
        if i < args.samples - 1:
            time.sleep(args.interval)
    
    if len(snapshots) < 20:
        print(f"\n❌ Only captured {len(snapshots)} snapshots — not enough for replay")
        return
    
    print(f"\n✅ Captured {len(snapshots)} snapshots over {snapshots[-1].ts - snapshots[0].ts:.0f}s")
    
    # Find dump events
    events = find_dump_events(snapshots, args.min_dump_bps)
    print(f"📊 Found {len(events)} dump events (≥{args.min_dump_bps}bps)")
    
    if not events:
        print("\n❌ No dump events found. Either the market was too stable or threshold is too high.")
        # Save empty result
        payload = {
            "generated_at": utc_now_iso(),
            "product": args.product,
            "snapshots_captured": len(snapshots),
            "duration_seconds": round(snapshots[-1].ts - snapshots[0].ts, 2) if len(snapshots) > 1 else 0,
            "parameters": {
                "samples": args.samples,
                "interval": args.interval,
                "horizons": horizons,
                "taker_fee_bps": args.taker_fee_bps,
                "notional_usd": args.notional_usd,
                "min_dump_bps": args.min_dump_bps,
            },
            "dump_events": 0,
            "summary": {"events_scored": 0, "net_positive": 0, "executable_positive": 0},
            "rows": [],
        }
        Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_path).write_text(json.dumps(payload, indent=2, sort_keys=True))
        return
    
    # Replay events
    all_rows = []
    for event in events:
        rows = replay_event(event, snapshots, horizons, args.notional_usd, args.taker_fee_bps)
        all_rows.extend(rows)
    
    # Sort by net_bps descending
    all_rows.sort(key=lambda r: r.get('net_bps', -9999), reverse=True)
    
    # Summary
    net_positive = [r for r in all_rows if r['net_bps'] > 0]
    executable_positive = [r for r in all_rows if r.get('executable_positive')]
    avg_slippage_entry = sum(r['entry_slippage_bps'] for r in all_rows) / len(all_rows) if all_rows else 0
    avg_slippage_exit = sum(r['exit_slippage_bps'] for r in all_rows) / len(all_rows) if all_rows else 0
    
    payload = {
        "generated_at": utc_now_iso(),
        "product": args.product,
        "mode": "depth_aware_causal_replay",
        "shadow_only": True,
        "places_orders": False,
        "snapshots_captured": len(snapshots),
        "duration_seconds": round(snapshots[-1].ts - snapshots[0].ts, 2) if len(snapshots) > 1 else 0,
        "parameters": {
            "samples": args.samples,
            "interval": args.interval,
            "horizons": horizons,
            "taker_fee_bps": args.taker_fee_bps,
            "notional_usd": args.notional_usd,
            "min_dump_bps": args.min_dump_bps,
        },
        "summary": {
            "dump_events_found": len(events),
            "events_scored": len(all_rows),
            "net_positive": len(net_positive),
            "executable_positive": len(executable_positive),
            "best_net_bps": max((r['net_bps'] for r in all_rows), default=-9999),
            "avg_entry_slippage_bps": round(avg_slippage_entry, 2),
            "avg_exit_slippage_bps": round(avg_slippage_exit, 2),
        },
        "rows": all_rows,
    }
    
    # Write JSON
    Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_path).write_text(json.dumps(payload, indent=2, sort_keys=True))
    
    # Write MD report
    md_lines = [
        "# CQT/USD Depth-Aware Causal Replay",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Product: `{args.product}`",
        f"- Snapshots: `{len(snapshots)}` over `{payload['duration_seconds']:.0f}s`",
        f"- Dump events: `{len(events)}`",
        f"- Events scored: `{len(all_rows)}`",
        f"- Net-positive: `{len(net_positive)}`",
        f"- Executable positive: `{len(executable_positive)}`",
        f"- Best net: `{max((r['net_bps'] for r in all_rows), default=-9999):.2f}bps`",
        f"- Avg entry slippage: `{avg_slippage_entry:.2f}bps`",
        f"- Avg exit slippage: `{avg_slippage_exit:.2f}bps`",
        "",
        "## Summary Table",
        "",
        "| # | Horizon | Dump bps | Entry VWAP | Exit VWAP | Entry Slip | Exit Slip | Net bps | MFE | MAE | Fillable | Blockers |",
        "|---|--------:|---------:|-----------:|----------:|-----------:|----------:|--------:|----:|----:|---------:|----------|",
    ]
    
    for i, row in enumerate(all_rows[:50], 1):
        md_lines.append(
            f"| {i} | {row['horizon_seconds']:.0f}s | {row['dump_bps']:.1f} | {row['entry_vwap']:.6f} | {row['exit_vwap']:.6f} | {row['entry_slippage_bps']:.1f}bps | {row['exit_slippage_bps']:.1f}bps | {row['net_bps']:+.1f}bps | {row['mfe_bps']:+.1f}bps | {row['mae_bps']:+.1f}bps | {'✅' if row['entry_fully_filled'] and row['exit_fully_filled'] else '❌'} | {row['blocker']} |"
        )
    
    md_lines.extend([
        "",
        "## Interpretation",
        "",
        "- **Executable positive** = net positive AND fully filled on both sides AND no high slippage",
        "- **Entry slippage** = VWAP vs best ask at entry (lower = better book depth)",
        "- **Exit slippage** = best bid vs VWAP at exit (lower = better book depth)",
        "- **Net bps** = gross return - 2× taker fees (entry + exit)",
        "",
        "## Verdict",
        "",
    ])
    
    if executable_positive:
        md_lines.append(f"✅ **PASS** — {len(executable_positive)} executable positive events. CQT/USD edge survives taker fees + slippage.")
    elif net_positive:
        md_lines.append(f"🟡 **CONDITIONAL** — {len(net_positive)} net-positive events but slippage/fill issues. Need more depth or smaller notional.")
    else:
        md_lines.append("❌ **FAIL** — Zero positive events after taker fees + slippage. CQT/USD edge does not survive realistic execution.")
    
    Path(args.md_path).write_text("\n".join(md_lines) + "\n")
    
    # Print summary
    print(f"\n{'='*60}")
    print(f"📊 RESULTS:")
    print(f"   Dump events: {len(events)}")
    print(f"   Events scored: {len(all_rows)}")
    print(f"   Net-positive: {len(net_positive)}")
    print(f"   Executable positive: {len(executable_positive)}")
    print(f"   Best net: {max((r['net_bps'] for r in all_rows), default=-9999):.2f}bps")
    print(f"   Avg entry slippage: {avg_slippage_entry:.2f}bps")
    print(f"   Avg exit slippage: {avg_slippage_exit:.2f}bps")
    print(f"\n📁 Reports: {args.json_path}, {args.md_path}")
    
    if executable_positive:
        print(f"\n✅ CQT/USD PASSES depth-aware replay — edge survives taker fees + slippage!")
    elif net_positive:
        print(f"\n🟡 CONDITIONAL — some net-positive events but execution friction kills them")
    else:
        print(f"\n❌ CQT/USD FAILS — edge erased by taker fees + slippage")


if __name__ == "__main__":
    main()
