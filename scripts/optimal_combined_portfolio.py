#!/usr/bin/env python3
"""
Optimal Combined Portfolio Backtest
=====================================
Runs the BEST strategy per coin simultaneously, with shared $48 bankroll and compounding.

Strategies per coin (based on 30d verified results):
- NOM: range_breakout (lb=10, TP=10%, SL=1%, MH=24)
- RAVE: momentum (lb=15, TP=10%, SL=0%, MH=36)
- GHST: momentum (lb=20, TP=15%, SL=3%, MH=24)
- TRU: momentum (lb=10, TP=10%, SL=3%, MH=24)
- SUP: range_breakout (lb=8, TP=8%, SL=1%, MH=24)

Shared bankroll: $48.00
Fees: 40bps per side
Entry slip: 8bps, Exit slip: 0bps
"""
import sys; sys.path.insert(0, 'scripts')
import json
import random
from datetime import datetime, timezone

STARTING_CASH = 48.0
FEE_RATE = 0.004
ENTRY_SLIP = 0.0008
EXIT_SLIP = 0.0
MIN_CASH = 10.0  # Minimum cash to open a position

# Coin configurations: (coin_name, strategy_fn, params)
from strategy_library import _momentum_entry, _range_breakout_entry

COIN_CONFIGS = [
    ("NOM-USD", _range_breakout_entry, {"range_lookback": 10, "tp_pct": 10.0, "sl_pct": 1.0, "max_hold": 24}),
    ("RAVE-USD", _momentum_entry, {"lookback": 15, "tp_pct": 10.0, "sl_pct": 0.0, "max_hold": 36}),
    ("GHST-USD", _momentum_entry, {"lookback": 20, "tp_pct": 15.0, "sl_pct": 3.0, "max_hold": 24}),
    ("TRU-USD", _momentum_entry, {"lookback": 10, "tp_pct": 10.0, "sl_pct": 3.0, "max_hold": 24}),
    ("SUP-USD", _range_breakout_entry, {"range_lookback": 8, "tp_pct": 8.0, "sl_pct": 1.0, "max_hold": 24}),
]

class CoinLane:
    def __init__(self, coin, entry_fn, params):
        self.coin = coin
        self.entry_fn = entry_fn
        self.params = params
        self.candles = []
        self.position = None
        self.signals = 0
        self.closes = 0
        self.wins = 0
        self.losses = 0
    
    def process_bar(self, candle, cash):
        """Process a single candle bar. Returns (events, new_cash)."""
        events = []
        self.candles.append(candle)
        closes = [float(c['close']) for c in self.candles]
        
        # EXIT existing position
        if self.position:
            self.position['hold'] += 1
            high = float(candle['high'])
            low = float(candle['low'])
            close = float(candle['close'])
            
            exit_price = None
            reason = None
            if high >= self.position['tp']:
                exit_price = self.position['tp']
                reason = 'tp'
            elif self.position['sl'] > 0 and low <= self.position['sl']:
                exit_price = self.position['sl']
                reason = 'sl'
            elif self.position['hold'] >= self.position['max_hold']:
                exit_price = close
                reason = 'timeout'
            
            if exit_price is not None:
                actual_exit = exit_price * (1 - EXIT_SLIP)
                units = self.position['units']
                gross = (actual_exit - self.position['entry']) * units
                exit_fee = actual_exit * units * FEE_RATE
                net = gross - self.position['entry_fee'] - exit_fee
                
                cash += self.position['deploy'] + net
                self.closes += 1
                if net > 0:
                    self.wins += 1
                else:
                    self.losses += 1
                
                events.append({
                    'action': 'close', 'coin': self.coin,
                    'entry_price': self.position['entry'],
                    'exit_price': actual_exit, 'net': round(net, 4),
                    'reason': reason, 'hold_bars': self.position['hold'],
                    'fees': round(self.position['entry_fee'] + exit_fee, 4),
                })
                self.position = None
        
        # ENTRY (if no position and enough cash)
        if self.position is None and cash >= MIN_CASH:
            # Need enough history for the strategy
            lookback = self.params.get('lookback', self.params.get('range_lookback', 10))
            if len(self.candles) > lookback + 1:
                signal = self.entry_fn(self.candles, closes, candle, self.params)
                if signal:
                    self.signals += 1
                    entry_price = float(candle['close']) * (1 + ENTRY_SLIP)
                    if entry_price <= 0:
                        return events, cash  # Skip zero-price candles
                    
                    deploy = cash
                    entry_fee = deploy * FEE_RATE
                    units = (deploy - entry_fee) / entry_price
                    
                    tp_pct = self.params.get('tp_pct', 10)
                    sl_pct = self.params.get('sl_pct', 0)
                    max_hold = self.params.get('max_hold', 24)
                    
                    tp = entry_price * (1 + tp_pct / 100)
                    sl = entry_price * (1 - sl_pct / 100) if sl_pct > 0 else 0
                    
                    self.position = {
                        'entry': entry_price, 'tp': tp, 'sl': sl,
                        'max_hold': max_hold, 'hold': 0,
                        'units': units, 'deploy': deploy,
                        'entry_fee': entry_fee,
                    }
                    cash -= deploy
                    
                    events.append({
                        'action': 'open', 'coin': self.coin,
                        'entry_price': round(entry_price, 8),
                        'tp': round(tp, 8), 'sl': round(sl, 8),
                        'deploy': round(deploy, 4),
                    })
        
        return events, cash


def main():
    print("=" * 80)
    print("  OPTIMAL COMBINED PORTFOLIO BACKTEST")
    print("=" * 80)
    print(f"  Coins: {len(COIN_CONFIGS)}")
    print(f"  Starting cash: ${STARTING_CASH}")
    print(f"  Min cash per position: ${MIN_CASH}")
    print()
    
    # Load all candles
    coin_lanes = {}
    all_timestamps = set()
    
    for coin_name, entry_fn, params in COIN_CONFIGS:
        cache_file = f"reports/candle_cache/{coin_name.replace('-', '_')}_FIVE_MINUTE_30d.json"
        try:
            data = json.loads(open(cache_file).read())
            candles = data['candles']
            coin_lanes[coin_name] = CoinLane(coin_name, entry_fn, params)
            coin_lanes[coin_name].candles_raw = candles
            # Collect timestamps for time-sync
            for c in candles:
                ts = c.get('start', c.get('time', 0))
                all_timestamps.add(int(ts))
        except Exception as e:
            print(f"  ERROR loading {coin_name}: {e}")
    
    print(f"  Loaded {len(coin_lanes)} coins")
    print(f"  Time-synced bars: {len(all_timestamps)}")
    
    # Sort timestamps
    sorted_ts = sorted(all_timestamps)
    
    # Build lookup: coin -> {timestamp: candle}
    for lane in coin_lanes.values():
        lane.lookup = {}
        for c in lane.candles_raw:
            ts = int(c.get('start', c.get('time', 0)))
            lane.lookup[ts] = c
    
    # Process bars in time order
    cash = STARTING_CASH
    peak = cash
    max_dd = 0.0
    all_events = []
    
    print(f"\n  Processing {len(sorted_ts)} time-synced bars...")
    
    for i, ts in enumerate(sorted_ts):
        for lane in coin_lanes.values():
            if ts in lane.lookup:
                events, cash = lane.process_bar(lane.lookup[ts], cash)
                all_events.extend(events)
        
        # Track drawdown
        if cash > peak:
            peak = cash
        dd = (peak - cash) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
        
        # Progress
        if (i + 1) % 1000 == 0:
            print(f"    Bar {i+1}/{len(sorted_ts)}: cash=${cash:.2f}, DD={max_dd*100:.1f}%")
    
    # Print results
    print(f"\n{'='*80}")
    print(f"  RESULTS")
    print(f"{'='*80}")
    
    total_net = cash - STARTING_CASH
    total_trades = sum(l.closes for l in coin_lanes.values())
    total_wins = sum(l.wins for l in coin_lanes.values())
    wr = total_wins / total_trades * 100 if total_trades > 0 else 0
    
    print(f"\n  Final cash: ${cash:.2f}")
    print(f"  Net PnL: ${total_net:+.2f}")
    print(f"  Return: {total_net/STARTING_CASH*100:.1f}%")
    print(f"  Max DD: {max_dd*100:.1f}%")
    print(f"  Total trades: {total_trades}")
    print(f"  Wins: {total_wins}, Losses: {total_trades - total_wins}")
    print(f"  Win rate: {wr:.1f}%")
    print(f"  Total signals: {sum(l.signals for l in coin_lanes.values())}")
    print(f"  Total events: {len(all_events)}")
    
    print(f"\n  Per-coin breakdown:")
    print(f"  {'Coin':<12} {'Signals':>8} {'Closes':>7} {'Wins':>5} {'Losses':>7} {'WR%':>5} {'Net/100':>9}")
    print(f"  {'-'*12} {'-'*8} {'-'*7} {'-'*5} {'-'*7} {'-'*5} {'-'*9}")
    
    for name in ["NOM-USD", "RAVE-USD", "GHST-USD", "TRU-USD", "SUP-USD"]:
        if name in coin_lanes:
            l = coin_lanes[name]
            # Estimate net per $100 by scaling
            net_per_100 = l.wins * 0  # Can't easily compute per-coin net in shared bankroll
            print(f"  {name:<12} {l.signals:>8} {l.closes:>7} {l.wins:>5} {l.losses:>7} "
                  f"{l.wins/max(l.closes,1)*100:>5.1f}% N/A (shared)")
    
    # Save report
    report = {
        'final_cash': round(cash, 4),
        'net_pnl': round(total_net, 4),
        'return_pct': round(total_net/STARTING_CASH*100, 1),
        'max_dd': round(max_dd*100, 1),
        'total_trades': total_trades,
        'wins': total_wins,
        'losses': total_trades - total_wins,
        'win_rate': round(wr, 1),
        'total_signals': sum(l.signals for l in coin_lanes.values()),
        'coins': {name: {
            'signals': coin_lanes[name].signals,
            'closes': coin_lanes[name].closes,
            'wins': coin_lanes[name].wins,
            'losses': coin_lanes[name].losses,
        } for name in coin_lanes},
        'events_sample': all_events[:20],
    }
    
    output_path = "reports/optimal_combined_portfolio.json"
    with open(output_path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f"\n  Report saved: {output_path}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
