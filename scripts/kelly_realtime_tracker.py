#!/usr/bin/env python3
"""Kelly Shadow Real-Time PnL Tracker.

Monitors the Kelly shadow runner's active positions, calculates unrealized PnL,
tracks closes, and reports progress vs the $269/mo decorrelated projection.

Usage:
    python scripts/kelly_realtime_tracker.py              # Poll every 60s
    python scripts/kelly_realtime_tracker.py --once        # Single snapshot
    python scripts/kelly_realtime_tracker.py --post        # Post to switchboard on close
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "reports" / "kelly_shadow_state.json"
EVENTS_FILE = ROOT / "reports" / "kelly_shadow_events.jsonl"
TRACKER_OUTPUT = ROOT / "reports" / "kelly_realtime_tracker.json"
TRACKER_REPORT = ROOT / "reports" / "kelly_realtime_tracker.txt"

# Projections
PROJECTION_MONTHLY = 269.0
STARTING_CAPITAL = 48.0


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_state():
    if not STATE_FILE.exists():
        return None
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def load_events(last_n=50):
    events = []
    if not EVENTS_FILE.exists():
        return events
    try:
        with open(EVENTS_FILE, encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[-last_n:]:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return events


def get_current_prices():
    """Fetch current prices for all Kelly coins from Coinbase API."""
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from coinbase_advanced_client import CoinbaseAdvancedClient
        client = CoinbaseAdvancedClient()
        
        coins = ["GHST-USD", "CFG-USD", "NOM-USD", "A8-USD", "SUP-USD"]
        prices = {}
        for coin in coins:
            try:
                ticker = client.public_exchange_ticker(coin)
                price = ticker.price  # CoinbasePublicTicker has .price attribute
                prices[coin] = price
            except Exception as e:
                print(f"  [WARN] Failed to fetch {coin} price: {e}", flush=True)
                prices[coin] = None
        return prices
    except Exception as e:
        print(f"  [WARN] Price fetch failed: {e}", flush=True)
        return {}


def calculate_unrealized_pnl(state, prices):
    """Calculate unrealized PnL for all active positions."""
    if not state:
        return {}
    
    ledgers = state.get("ledgers", {})
    results = {}
    
    for coin, ledger in ledgers.items():
        if ledger.get("position") != "active":
            continue
        
        entry_price = ledger.get("position_entry", 0)
        tp = ledger.get("position_tp", 0)
        sl = ledger.get("position_sl", 0)
        deploy = ledger.get("position_deploy", 0)
        units = ledger.get("position_units", 0)
        hold = ledger.get("position_hold", 0)
        max_hold = ledger.get("position_max_hold", 0)
        
        current_price = prices.get(coin)
        if current_price is None:
            results[coin] = {
                "status": "active",
                "entry": entry_price,
                "tp": tp,
                "sl": sl,
                "deploy": deploy,
                "units": units,
                "hold_bars": hold,
                "max_hold": max_hold,
                "current_price": None,
                "unrealized_pnl": None,
                "distance_to_tp_pct": None,
                "distance_to_sl_pct": None,
                "hold_pct": hold / max_hold * 100 if max_hold > 0 else None,
            }
            continue
        
        # Calculate unrealized PnL
        # For long positions: pnl = (current - entry) * units
        price_diff = current_price - entry_price
        unrealized_pnl = price_diff * units
        
        # Distance to TP and SL
        dist_to_tp = (tp - current_price) / current_price * 100 if current_price > 0 else None
        dist_to_sl = (current_price - sl) / current_price * 100 if current_price > 0 and sl > 0 else None
        hold_pct = hold / max_hold * 100 if max_hold > 0 else None
        
        results[coin] = {
            "status": "active",
            "entry": entry_price,
            "tp": tp,
            "sl": sl,
            "deploy": deploy,
            "units": units,
            "hold_bars": hold,
            "max_hold": max_hold,
            "current_price": current_price,
            "unrealized_pnl": round(unrealized_pnl, 4),
            "distance_to_tp_pct": round(dist_to_tp, 2) if dist_to_tp is not None else None,
            "distance_to_sl_pct": round(dist_to_sl, 2) if dist_to_sl is not None else None,
            "hold_pct": round(hold_pct, 1) if hold_pct is not None else None,
        }
    
    return results


def format_report(state, prices, unrealized):
    """Format human-readable report."""
    lines = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines.append("=" * 72)
    lines.append("  KELLY SHADOW REAL-TIME TRACKER")
    lines.append(f"  {now}")
    lines.append("=" * 72)
    
    if not state:
        lines.append("\n  STATE: NOT FOUND")
        lines.append("  The runner may not be running.")
        return "\n".join(lines)
    
    cycle = state.get("cycle", 0)
    equity = state.get("total_equity", STARTING_CAPITAL)
    pnl = state.get("total_pnl", 0)
    return_pct = state.get("return_pct", 0)
    ledgers = state.get("ledgers", {})
    
    # Count active positions and signals
    active_positions = sum(1 for l in ledgers.values() if l.get("position") == "active")
    total_signals = sum(l.get("signals", 0) for l in ledgers.values())
    total_closes = sum(l.get("closes", 0) for l in ledgers.values())
    
    lines.append(f"\n  Cycle: {cycle} | Equity: ${equity:.2f} | PnL: ${pnl:+.2f} | Return: {return_pct:+.2f}%")
    lines.append(f"  Active positions: {active_positions} | Signals: {total_signals} | Closes: {total_closes}")
    
    # Active positions detail
    if unrealized:
        lines.append("\n  ACTIVE POSITIONS:")
        lines.append(f"  {'Coin':<12} {'Entry':>8} {'Current':>8} {'TP':>8} {'SL':>8} {'PnL':>8} {'TP Dist':>8} {'Hold':>6}")
        lines.append(f"  {'─' * 12} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 6}")
        
        for coin, data in unrealized.items():
            current_str = f"${data['current_price']:.4f}" if data['current_price'] else "N/A"
            pnl_str = f"${data['unrealized_pnl']:+.2f}" if data['unrealized_pnl'] is not None else "N/A"
            tp_dist_str = f"{data['distance_to_tp_pct']:+.1f}%" if data['distance_to_tp_pct'] is not None else "N/A"
            hold_str = f"{data['hold_pct']:.0f}%" if data['hold_pct'] is not None else "N/A"
            
            lines.append(
                f"  {coin:<12} ${data['entry']:.4f} {current_str:>8} "
                f"${data['tp']:.4f} ${data['sl']:.4f} {pnl_str:>8} {tp_dist_str:>8} {hold_str:>6}"
            )
    
    # Projection comparison
    lines.append("\n  PROJECTION:")
    lines.append(f"  Monthly target: ${PROJECTION_MONTHLY:.0f}")
    lines.append(f"  Current cycle: {cycle}")
    lines.append(f"  Closes completed: {total_closes}")
    lines.append(f"  Needs ~30 closes for statistical significance")
    
    return "\n".join(lines)


def run_once():
    """Execute one tracking cycle."""
    state = load_state()
    events = load_events(50)
    prices = get_current_prices()
    unrealized = calculate_unrealized_pnl(state, prices)
    
    report = format_report(state, prices, unrealized)
    print(report, flush=True)
    
    # Save tracker output
    tracker_data = {
        "timestamp": utc_now_iso(),
        "cycle": state.get("cycle", 0) if state else 0,
        "equity": state.get("total_equity", STARTING_CAPITAL) if state else STARTING_CAPITAL,
        "total_signals": sum(l.get("signals", 0) for l in state.get("ledgers", {}).values()) if state else 0,
        "total_closes": sum(l.get("closes", 0) for l in state.get("ledgers", {}).values()) if state else 0,
        "active_positions": sum(1 for l in state.get("ledgers", {}).values() if l.get("position") == "active") if state else 0,
        "unrealized": unrealized,
        "prices": prices,
    }
    
    TRACKER_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(TRACKER_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(tracker_data, f, indent=2, sort_keys=True)
    
    # Save report
    TRACKER_REPORT.parent.mkdir(parents=True, exist_ok=True)
    with open(TRACKER_REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    
    return tracker_data


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Kelly Shadow Real-Time PnL Tracker")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between checks (default 60)")
    parser.add_argument("--once", action="store_true", help="Single snapshot then exit")
    args = parser.parse_args()
    
    print(f"Kelly Real-Time Tracker starting (interval={args.interval}s)", flush=True)
    print(f"  State:  {STATE_FILE}", flush=True)
    print(f"  Events: {EVENTS_FILE}", flush=True)
    print(f"  Output: {TRACKER_OUTPUT}", flush=True)
    print()
    
    while True:
        run_once()
        
        if args.once:
            break
        
        print(f"\n--- Next check in {args.interval}s ---\n", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
