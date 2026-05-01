"""
Session Gate Effectiveness Analysis

Tests whether SESSION_DEAD_HOURS = {0, 6, 12, 19} is filtering profitable signals
or just wasting trading time.

Output:
- Hour-by-hour signal quality analysis
- Session gate ON vs OFF comparison per coin
- Revenue impact estimate
- Coin-specific optimal session windows

Usage:
    python scripts/session_gate_analysis.py
"""

import json
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Dict

ROOT = Path(__file__).resolve().parent.parent

# Current session dead hours (UTC)
SESSION_DEAD_HOURS = {0, 6, 12, 19}

# Coin configs for analysis
COINS_TO_TEST = [
    {"coin": "RAVE-USD", "strategy": "supertrend", "lookback": 200},
    {"coin": "NOM-USD", "strategy": "fibonacci", "fib_lookback": 20},
    {"coin": "GHST-USD", "strategy": "fibonacci", "fib_lookback": 10},
    {"coin": "TRU-USD", "strategy": "supertrend", "lookback": 200},
    {"coin": "A8-USD", "strategy": "momentum", "lookback": 10},
    {"coin": "CFG-USD", "strategy": "momentum", "lookback": 50},
]

FEE_RATE = 0.0040


@dataclass
class HourlyStats:
    """Stats for signals fired in a specific hour."""
    hour: int
    signals: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    total_fees: float = 0.0

    @property
    def win_rate(self) -> float:
        trades = self.wins + self.losses
        return self.wins / trades * 100 if trades > 0 else 0.0

    @property
    def avg_pnl(self) -> float:
        trades = self.wins + self.losses
        return self.total_pnl / trades if trades > 0 else 0.0


@dataclass
class SessionResult:
    """Result of session gate ON vs OFF comparison."""
    coin: str
    strategy: str
    
    # Session gate OFF (24/7 trading)
    off_signals: int = 0
    off_wins: int = 0
    off_losses: int = 0
    off_pnl: float = 0.0
    off_fees: float = 0.0
    
    # Session gate ON (dead hours filtered)
    on_signals: int = 0
    on_wins: int = 0
    on_losses: int = 0
    on_pnl: float = 0.0
    on_fees: float = 0.0
    
    # Impact
    filtered_signals: int = 0
    filtered_pnl: float = 0.0
    revenue_impact: str = ""


def analyze_hourly_quality(all_trades: List[dict]) -> Dict[int, HourlyStats]:
    """Analyze signal quality by hour of day (UTC)."""
    hourly = {h: HourlyStats(hour=h) for h in range(24)}
    
    for trade in all_trades:
        # Extract hour from trade timestamp
        ts = trade.get("ts_utc", "")
        if not ts:
            continue
        
        # Parse hour from ISO timestamp
        try:
            hour = int(ts[11:13])  # Extract HH from "2026-04-13T02:22:00+00:00"
        except (ValueError, IndexError):
            continue
        
        stats = hourly[hour]
        stats.signals += 1
        
        if trade.get("action") == "close":
            net = trade.get("net", 0.0)
            fees = trade.get("fees", 0.0)
            stats.total_pnl += net
            stats.total_fees += fees
            
            if net > 0:
                stats.wins += 1
            else:
                stats.losses += 1
    
    return hourly


def compare_session_gate(all_trades: List[dict], coin: str, strategy: str) -> SessionResult:
    """Compare session gate ON vs OFF for a specific coin."""
    result = SessionResult(coin=coin, strategy=strategy)
    
    coin_trades = [t for t in all_trades if t.get("coin") == coin]
    
    # Filter to closes only
    closes = [t for t in coin_trades if t.get("action") == "close"]
    
    for trade in closes:
        ts = trade.get("ts_utc", "")
        try:
            hour = int(ts[11:13])
        except (ValueError, IndexError):
            continue
        
        net = trade.get("net", 0.0)
        fees = trade.get("fees", 0.0)
        
        # Session gate OFF: all trades count
        result.off_signals += 1
        result.off_pnl += net
        result.off_fees += fees
        if net > 0:
            result.off_wins += 1
        else:
            result.off_losses += 1
        
        # Session gate ON: only count trades during active hours
        if hour not in SESSION_DEAD_HOURS:
            result.on_signals += 1
            result.on_pnl += net
            result.on_fees += fees
            if net > 0:
                result.on_wins += 1
            else:
                result.on_losses += 1
        else:
            # This trade was filtered by session gate
            result.filtered_signals += 1
            result.filtered_pnl += net
    
    # Calculate revenue impact
    if result.filtered_pnl > 0:
        result.revenue_impact = f"LOSING ${result.filtered_pnl:.2f} by filtering"
    elif result.filtered_pnl < 0:
        result.revenue_impact = f"SAVED ${abs(result.filtered_pnl):.2f} by filtering"
    else:
        result.revenue_impact = "Neutral"
    
    return result


def load_events() -> List[dict]:
    """Load events from the events JSONL file."""
    events = []
    
    # Try multiple event file locations
    event_paths = [
        ROOT / "reports" / "multi_coin_momentum_events.jsonl",
        ROOT / "reports" / "multi_coin_isolated_events.jsonl",
        ROOT / "reports" / "isolated_runner_events.jsonl",
    ]
    
    for path in event_paths:
        if path.exists():
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            print(f"  Loaded {len(events)} events from {path.name}")
            return events
    
    print(f"  ⚠️  No events file found. Looking for alternative data sources...")
    return events


def generate_mock_data() -> List[dict]:
    """Generate realistic mock trade data for demonstration."""
    import random
    from datetime import datetime, timezone, timedelta
    
    mock_trades = []
    base_time = datetime(2026, 4, 12, 0, 0, 0, tzinfo=timezone.utc)
    
    coin_params = {
        "RAVE-USD": {"strategy": "supertrend", "wr": 0.52, "avg_win": 0.40, "avg_loss": -0.15, "trades_per_day": 2},
        "NOM-USD": {"strategy": "fibonacci", "wr": 0.49, "avg_win": 0.70, "avg_loss": -0.20, "trades_per_day": 3},
        "GHST-USD": {"strategy": "fibonacci", "wr": 0.46, "avg_win": 0.20, "avg_loss": -0.15, "trades_per_day": 2},
        "TRU-USD": {"strategy": "supertrend", "wr": 0.50, "avg_win": 0.35, "avg_loss": -0.15, "trades_per_day": 1},
        "A8-USD": {"strategy": "momentum", "wr": 0.53, "avg_win": 0.15, "avg_loss": -0.10, "trades_per_day": 2},
        "CFG-USD": {"strategy": "momentum", "wr": 0.41, "avg_win": 0.15, "avg_loss": -0.08, "trades_per_day": 2},
    }
    
    for coin, params in coin_params.items():
        for day in range(30):
            for _ in range(params["trades_per_day"]):
                # Random hour (with some patterns)
                # Supertrend tends to fire during high-vol hours (8-16 UTC)
                # Fibonacci more evenly distributed
                if params["strategy"] == "supertrend":
                    hour = random.choices(range(24), weights=[
                        1, 1, 1, 1, 1, 2, 2, 3, 4, 5, 5, 4,
                        3, 3, 4, 5, 5, 4, 3, 2, 2, 1, 1, 1
                    ])[0]
                else:
                    hour = random.randint(0, 23)
                
                minute = random.randint(0, 59)
                ts = base_time + timedelta(days=day, hours=hour, minutes=minute)
                
                # Generate PnL
                is_win = random.random() < params["wr"]
                if is_win:
                    net = abs(random.gauss(params["avg_win"], params["avg_win"] * 0.3))
                else:
                    net = -abs(random.gauss(params["avg_loss"], params["avg_loss"] * 0.3))
                
                fees = abs(net) * 0.08  # ~8% fee drag
                
                mock_trades.append({
                    "ts_utc": ts.isoformat(),
                    "coin": coin,
                    "action": "close",
                    "net": round(net, 4),
                    "fees": round(fees, 4),
                    "strategy": params["strategy"],
                })
    
    print(f"  Generated {len(mock_trades)} mock trades for demonstration")
    return mock_trades


def print_hourly_heatmap(hourly: Dict[int, HourlyStats], coin: str):
    """Print a visual heatmap of signal quality by hour."""
    print(f"\n  {'Hour':<6} {'Signals':>9} {'WR':>8} {'Avg PnL':>10} {'Net PnL':>10} {'Status':<10}")
    print(f"  {'─' * 60}")
    
    for h in range(24):
        stats = hourly[h]
        is_dead = h in SESSION_DEAD_HOURS
        status = "DEAD" if is_dead else "ACTIVE"
        wr_str = f"{stats.win_rate:.1f}%" if stats.signals > 0 else "N/A"
        avg_pnl_str = f"${stats.avg_pnl:.2f}" if stats.signals > 0 else "N/A"
        net_pnl_str = f"${stats.total_pnl:.2f}"
        
        marker = "🔴" if is_dead else "🟢"
        print(f"  {marker} {h:02d}:00  {stats.signals:>9} {wr_str:>8} {avg_pnl_str:>10} {net_pnl_str:>10} {status:<10}")


def main():
    print("=" * 80)
    print("SESSION GATE EFFECTIVENESS ANALYSIS")
    print("=" * 80)
    
    # Load events
    print("\nLoading trade events...")
    events = load_events()
    
    if not events or len(events) < 20:
        print(f"\nOnly {len(events)} live events found. Generating mock data for demonstration...")
        events = generate_mock_data()
    
    # 1. Hourly quality analysis
    print(f"\n{'=' * 80}")
    print("HOURLY SIGNAL QUALITY ANALYSIS")
    print(f"{'=' * 80}")
    
    hourly = analyze_hourly_quality(events)
    
    print(f"\n  {'Hour':<6} {'Signals':>9} {'WR':>8} {'Avg PnL':>10} {'Net PnL':>10}")
    print(f"  {'─' * 50}")
    
    for h in range(24):
        stats = hourly[h]
        is_dead = h in SESSION_DEAD_HOURS
        wr_str = f"{stats.win_rate:.1f}%" if (stats.wins + stats.losses) > 0 else "N/A"
        avg_pnl_str = f"${stats.avg_pnl:.2f}" if (stats.wins + stats.losses) > 0 else "N/A"
        net_pnl_str = f"${stats.total_pnl:.2f}"
        
        marker = "[DEAD] " if is_dead else "[OK]   "
        print(f"  {marker}{h:02d}:00  {stats.signals:>9} {wr_str:>8} {avg_pnl_str:>10} {net_pnl_str:>10}")
    
    # 2. Session gate comparison per coin
    print(f"\n{'=' * 80}")
    print("SESSION GATE: ON vs OFF COMPARISON")
    print(f"{'=' * 80}")
    
    print(f"\n{'Coin':<15} {'Strategy':<15} {'Gate':<6} {'Signals':>8} {'WR':>8} {'Net PnL':>10} {'Fees':>10}")
    print(f"{'─' * 80}")
    
    for coin_cfg in COINS_TO_TEST:
        coin = coin_cfg["coin"]
        strategy = coin_cfg["strategy"]
        
        result = compare_session_gate(events, coin, strategy)
        
        # Session OFF
        off_wr = result.off_wins / max(1, result.off_wins + result.off_losses) * 100
        print(f"{coin:<15} {strategy:<15} {'OFF':<6} {result.off_signals:>8} {off_wr:>7.1f}% ${result.off_pnl:>9.2f} ${result.off_fees:>9.2f}")
        
        # Session ON
        on_wr = result.on_wins / max(1, result.on_wins + result.on_losses) * 100
        print(f"{coin:<15} {strategy:<15} {'ON':<6} {result.on_signals:>8} {on_wr:>7.1f}% ${result.on_pnl:>9.2f} ${result.on_fees:>9.2f}")
        
        # Impact
        print(f"{'':<15} {'':<15} {'Impact':<6} {result.filtered_signals:>8} trades filtered → {result.revenue_impact}")
        print()
    
    # 3. Summary
    print(f"{'=' * 80}")
    print("SUMMARY & RECOMMENDATIONS")
    print(f"{'=' * 80}")
    
    total_filtered_pnl = 0
    total_filtered_signals = 0
    
    for coin_cfg in COINS_TO_TEST:
        coin = coin_cfg["coin"]
        strategy = coin_cfg["strategy"]
        result = compare_session_gate(events, coin, strategy)
        total_filtered_pnl += result.filtered_pnl
        total_filtered_signals += result.filtered_signals
    
    print(f"\nTotal signals filtered by session gate: {total_filtered_signals}")
    print(f"Net PnL of filtered signals: ${total_filtered_pnl:.2f}")
    
    if total_filtered_pnl > 0:
        print(f"\n⚠️  Session gate is FILTERING PROFITABLE trades (${total_filtered_pnl:.2f} lost)")
        print(f"   → Consider removing or adjusting SESSION_DEAD_HOURS")
    elif total_filtered_pnl < -5.0:
        print(f"\n✅ Session gate is SAVING us from losses (${abs(total_filtered_pnl):.2f} saved)")
        print(f"   → Keep session gate, it's working")
    else:
        print(f"\n🟡 Session gate is roughly neutral (${abs(total_filtered_pnl):.2f} impact)")
        print(f"   → Marginal benefit, consider removing for simplicity")
    
    print(f"\n{'=' * 80}")
    print("NOTE: This analysis uses mock data if no live events are available.")
    print("For production decisions, run with 30+ days of live trade data.")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
