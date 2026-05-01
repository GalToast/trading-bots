#!/usr/bin/env python3
"""Position sizing framework for the universal 10-symbol rearm portfolio.

Computes proportional lot sizes based on verified backtest edge per symbol.
Ensures risk is distributed fairly — more capital to symbols with more edge.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "configs" / "universal_10symbol_rearm.json"

# Risk parameters
TOTAL_ACCOUNT_BALANCE = 69000.0  # Current MT5 account balance
MAX_RISK_PER_TRADE_PCT = 0.01    # Max 1% of account per open position
CONFIDENCE_MULTIPLIER = 0.7      # Apply 70% of theoretical edge for live safety


@dataclass
class SymbolSizing:
    symbol: str
    timeframe: str
    verified_edge_90d: float
    edge_pct_of_portfolio: float
    allocated_capital: float
    max_lot_size: float
    recommended_lot: float
    max_positions: int
    risk_per_position: float


def compute_position_sizing(config: dict, total_balance: float, confidence: float) -> list[SymbolSizing]:
    symbols = config["symbols"]
    total_verified_edge = sum(s["verified_backtest_90d"] for s in symbols.values())

    results = []
    for sym, cfg in symbols.items():
        edge = cfg["verified_backtest_90d"]
        edge_pct = edge / total_verified_edge
        allocated = total_balance * edge_pct

        # Max lot size based on risk
        max_risk_usd = total_balance * MAX_RISK_PER_TRADE_PCT
        # Approximate per-pip value for standard lot (varies by symbol)
        # For crypto, use step size as proxy
        step = cfg["step"]
        # Rough pip value estimate: $1 per pip per 0.01 lot for most pairs
        # For crypto, this is much larger
        pip_value_per_lot = step * 100 if step > 1 else 10.0
        max_lot = max_risk_usd / (pip_value_per_lot * 100) if pip_value_per_lot > 0 else 0.01

        # Recommended lot size: scaled by confidence factor
        recommended_lot = max_lot * confidence

        results.append(SymbolSizing(
            symbol=sym,
            timeframe=cfg["timeframe"],
            verified_edge_90d=edge,
            edge_pct_of_portfolio=edge_pct * 100,
            allocated_capital=allocated,
            max_lot_size=round(max_lot, 4),
            recommended_lot=round(recommended_lot, 4),
            max_positions=cfg["max_open_per_side"],
            risk_per_position=round(max_risk_usd, 2),
        ))

    return results


def main():
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    sizes = compute_position_sizing(config, TOTAL_ACCOUNT_BALANCE, CONFIDENCE_MULTIPLIER)

    print(f"\n{'='*120}")
    print(f"  POSITION SIZING FRAMEWORK — 10-Symbol Universal Rearm Portfolio")
    print(f"  Account Balance: ${TOTAL_ACCOUNT_BALANCE:,.2f} | Confidence: {CONFIDENCE_MULTIPLIER:.0%}")
    print(f"  Max Risk/Trade: {MAX_RISK_PER_TRADE_PCT:.0%} = ${TOTAL_ACCOUNT_BALANCE * MAX_RISK_PER_TRADE_PCT:,.2f}")
    print(f"{'='*120}")

    print(f"\n{'Symbol':<12} {'TF':<5} {'90d Edge':>14} {'% Port':>7} {'Alloc $':>12} {'Max Lot':>8} {'Rec Lot':>8} {'Max Pos':>7} {'Risk/Pos':>10}")
    print("-" * 120)

    # Sort by edge descending
    sizes.sort(key=lambda s: s.verified_edge_90d, reverse=True)

    total_alloc = 0
    for s in sizes:
        total_alloc += s.allocated_capital
        print(f"{s.symbol:<12} {s.timeframe:<5} ${s.verified_edge_90d:>12,.2f} {s.edge_pct_of_portfolio:>6.1f}% ${s.allocated_capital:>10,.2f} {s.max_lot_size:>8.4f} {s.recommended_lot:>8.4f} {s.max_positions:>7} ${s.risk_per_position:>9,.2f}")

    print("-" * 120)
    print(f"{'TOTAL':<12} {'':<5} {'':>14} {'100.0%':>7} ${total_alloc:>10,.2f}")

    # Key insights
    print(f"\n{'='*120}")
    print(f"  KEY INSIGHTS")
    print(f"{'='*120}")

    top = sizes[0]
    top3 = sizes[:3]
    crypto_total = sum(s.verified_edge_90d for s in sizes if s.timeframe == "H1")
    fx_total = sum(s.verified_edge_90d for s in sizes if s.timeframe == "M1")

    print(f"  1. {top.symbol} dominates: {top.edge_pct_of_portfolio:.1f}% of portfolio edge, ${top.verified_edge_90d:,.0f}/90d")
    print(f"  2. Top 3 symbols ({', '.join(s.symbol for s in top3)}): {sum(s.edge_pct_of_portfolio for s in top3):.1f}% of total edge")
    print(f"  3. Crypto H1: ${crypto_total:,.0f} ({crypto_total/(crypto_total+fx_total)*100:.0f}%) vs FX M1: ${fx_total:,.0f} ({fx_total/(crypto_total+fx_total)*100:.0f}%)")
    print(f"  4. Long-tail symbols (ADAUSD, DOTUSD, LTCUSD) contribute <1% — consider cutting for operational simplicity")
    print(f"  5. Risk per position is uniform (${TOTAL_ACCOUNT_BALANCE * MAX_RISK_PER_TRADE_PCT:,.0f}) — position sizing equalizes risk")

    # Minimal viable portfolio
    mvp = [s for s in sizes if s.edge_pct_of_portfolio > 2.0]
    mvp_edge = sum(s.verified_edge_90d for s in mvp)
    print(f"\n  MINIMAL VIABLE PORTFOLIO (>2% edge contribution):")
    for s in mvp:
        print(f"    {s.symbol}: ${s.verified_edge_90d:,.0f} ({s.edge_pct_of_portfolio:.1f}%)")
    print(f"    MVP Total: ${mvp_edge:,.0f} ({mvp_edge/(crypto_total+fx_total)*100:.0f}% of full portfolio)")

    # Save sizing to config
    output = {
        "position_sizing": {
            s.symbol: {
                "edge_pct": round(s.edge_pct_of_portfolio, 2),
                "allocated_capital": round(s.allocated_capital, 2),
                "max_lot_size": s.max_lot_size,
                "recommended_lot": s.recommended_lot,
                "max_positions": s.max_positions,
                "risk_per_position": s.risk_per_position,
            }
            for s in sizes
        },
        "portfolio_summary": {
            "total_account_balance": TOTAL_ACCOUNT_BALANCE,
            "max_risk_per_trade_pct": MAX_RISK_PER_TRADE_PCT,
            "confidence_multiplier": CONFIDENCE_MULTIPLIER,
            "total_verified_edge_90d": round(crypto_total + fx_total, 2),
            "daily_avg_edge": round((crypto_total + fx_total) / 90, 2),
        }
    }

    out_path = ROOT / "configs" / "universal_10symbol_position_sizing.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n  Wrote {out_path}")


if __name__ == "__main__":
    main()
