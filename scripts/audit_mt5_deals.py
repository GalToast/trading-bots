#!/usr/bin/env python3
"""Audit MT5 deal history: separate lattice vs non-lattice activity."""
import MetaTrader5 as mt5
import json
from datetime import datetime, timezone

mt5.initialize()

now = datetime.now(timezone.utc)
# Get deals from today
start = now.replace(hour=0, minute=0, second=0, microsecond=0)

deals = mt5.history_deals_get(start, now)
if deals:
    print(f"\n{'='*70}")
    print(f"  MT5 Deal History Audit — {start.strftime('%Y-%m-%d')}")
    print(f"{'='*70}")
    
    lattice_symbols = {"GBPUSD", "EURUSD", "NZDUSD", "USDJPY", "USDCHF"}
    lattice_total = 0.0
    non_lattice_total = 0.0
    lattice_deals = 0
    non_lattice_deals = 0
    unknown_deals = 0
    unknown_total = 0.0
    
    categories = {
        "lattice_gbpusd": {"count": 0, "pnl": 0.0, "deals": []},
        "lattice_eurusd": {"count": 0, "pnl": 0.0, "deals": []},
        "lattice_usdjpy": {"count": 0, "pnl": 0.0, "deals": []},
        "lattice_nzdusd": {"count": 0, "pnl": 0.0, "deals": []},
        "lattice_usdchf": {"count": 0, "pnl": 0.0, "deals": []},
        "non_lattice": {"count": 0, "pnl": 0.0, "deals": []},
    }
    
    for d in deals:
        symbol = d.symbol
        profit = d.profit + d.swap + d.commission
        time_str = d.time.strftime('%H:%M:%S') if hasattr(d.time, 'strftime') else str(d.time)
        
        if symbol in lattice_symbols:
            key = f"lattice_{symbol.lower()}"
            categories[key]["count"] += 1
            categories[key]["pnl"] += profit
            categories[key]["deals"].append({
                "time": time_str, "symbol": symbol, "type": d.type,
                "profit": profit, "price": d.price, "deal_id": d.ticket,
                "entry": d.entry, "swap": d.swap, "commission": d.commission
            })
        else:
            categories["non_lattice"]["count"] += 1
            categories["non_lattice"]["pnl"] += profit
            categories["non_lattice"]["deals"].append({
                "time": time_str, "symbol": symbol, "type": d.type,
                "profit": profit, "price": d.price, "deal_id": d.ticket,
                "entry": d.entry, "swap": d.swap, "commission": d.commission
            })
    
    print(f"\n{'Symbol':>15} {'Deals':>6} {'Net P&L':>12} {'Profit':>10} {'Swap':>8} {'Comm':>8}")
    print(f"{'-'*15} {'-'*6} {'-'*12} {'-'*10} {'-'*8} {'-'*8}")
    
    total_deals = 0
    total_pnl = 0.0
    
    for key in categories:
        c = categories[key]
        if c["count"] == 0:
            continue
        symbol_name = key.replace("lattice_", "").upper()
        print(f"{symbol_name:>15} {c['count']:>6} ${c['pnl']:>11.2f}")
        total_deals += c["count"]
        total_pnl += c["pnl"]
        
        # Show individual deals for non-lattice
        if "non_lattice" in key and c["count"] > 0:
            for d in c["deals"][:10]:
                print(f"    → {d['time']} {d['symbol']} {d['type']} profit=${d['profit']:.2f} swap=${d['swap']:.2f} comm=${d['commission']:.2f}")
    
    print(f"{'-'*15} {'-'*6} {'-'*12} {'-'*10} {'-'*8} {'-'*8}")
    print(f"{'TOTAL':>15} {total_deals:>6} ${total_pnl:>11.2f}")
    
    # Get account info
    acc = mt5.account_info()
    if acc:
        print(f"\nAccount: {acc.login} | Balance: ${acc.balance:.2f} | Equity: ${acc.equity:.2f} | Profit: ${acc.profit:.2f}")
    
    # Check positions
    positions = mt5.positions_get()
    if positions:
        print(f"\nOpen positions: {len(positions)}")
        for p in positions:
            print(f"  {p.symbol} {p.type} vol={p.volume} entry={p.price_open:.5f} current={p.price_current:.5f} pnl={p.profit:.2f}")
    else:
        print(f"\nNo open positions.")

else:
    print(f"No deals found for {start.strftime('%Y-%m-%d')}")

# Also check yesterday
yesterday = start.replace(day=start.day - 1)
deals_yesterday = mt5.history_deals_get(yesterday, start)
if deals_yesterday:
    print(f"\n{'='*70}")
    print(f"  Yesterday ({yesterday.strftime('%Y-%m-%d')}) — {len(deals_yesterday)} deals")
    print(f"{'='*70}")
    total_yest = sum(d.profit + d.swap + d.commission for d in deals_yesterday)
    print(f"  Total P&L: ${total_yest:.2f}")
    
    for d in deals_yesterday[:20]:
        profit = d.profit + d.swap + d.commission
        print(f"  {d.time.strftime('%H:%M')} {d.symbol} {d.type} profit=${profit:.2f}")
    if len(deals_yesterday) > 20:
        print(f"  ... and {len(deals_yesterday)-20} more")

mt5.shutdown()
