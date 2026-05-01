#!/usr/bin/env python3
"""
Dual-Asset Grid Trading Backtest for Kraken Spot.

Tracks BOTH USD and token growth. Grid trading naturally accumulates tokens:
1. Buy when price drops (acquires more tokens per USD)
2. Sell when price bounces (releases tokens for more USD)
3. Net result: MORE tokens over time AND potential USD profit

The edge is measuring total account value in BOTH assets.
A strategy that grows tokens is valid even if USD is flat — tokens appreciate.

Usage:
    python scripts/build_kraken_dual_grid_backtest.py
    python scripts/build_kraken_dual_grid_backtest.py --products BILLY-USD,DUCK-USD --grid-spacing-bps 100
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
CACHE = ROOT / "reports" / "cache"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

DEFAULT_CACHE_PATH = CACHE / "kraken_ohlc_collector.json"
DEFAULT_PRODUCTS = "SHAPE-USD,SWEAT-USD,HONEY-USD,CQT-USD,BILLY-USD,DUCK-USD,CLOUD-USD"
DEFAULT_GRID_SPACING_BPS = 100.0  # 1% between levels (tighter on 1m data)
DEFAULT_NUM_LEVELS = 15
DEFAULT_INITIAL_USD = 100.0
DEFAULT_INITIAL_TOKENS = 0.0  # Start with only USD, tokens accumulate from grid
DEFAULT_MAKER_FEE_BPS = 16.0
DEFAULT_TAKER_FEE_BPS = 120.0


@dataclass
class GridState:
    """Tracks both USD and token balance."""
    usd: float
    tokens: float
    total_buys: int = 0
    total_sells: int = 0
    total_usd_spent: float = 0.0
    total_usd_received: float = 0.0
    total_tokens_bought: float = 0.0
    total_tokens_sold: float = 0.0
    total_fees_usd: float = 0.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_candles(cache_path: Path) -> dict[str, dict[str, list]]:
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    products = cache.get("products", {})
    result = {}
    for product, prod_data in products.items():
        grains = {}
        for grain_str, grain_data in prod_data.get("granularities", {}).items():
            grains[int(grain_str)] = grain_data.get("candles", [])
        if grains:
            result[product] = grains
    return result


def simulate_dual_grid(
    candles: list[dict],
    grid_spacing_bps: float,
    num_levels: int,
    initial_usd: float,
    initial_tokens: float,
    maker_fee_bps: float,
    taker_fee_bps: float,
) -> dict[str, Any]:
    """
    Simulate grid trading tracking BOTH USD and token balances.

    The grid is set around the first candle's close. Each level has a buy order
    (USD → tokens) and a sell order (tokens → USD) one level above.

    Token accumulation is the key metric — if we end with more tokens than we started,
    we've grown the account even if USD is flat.
    """
    if len(candles) < 10:
        return {"error": "Not enough candles"}

    first_price = candles[0]["c"]
    token_price_usd = first_price  # Value of 1 token in USD

    # Build grid levels from first_price downward
    spacing = grid_spacing_bps / 10000.0
    # Level 0 is at first_price, levels go downward
    grid_levels = [first_price * (1 - i * spacing) for i in range(num_levels)]

    state = GridState(
        usd=initial_usd,
        tokens=initial_tokens,
    )

    # Track which grid levels have pending buy/sell orders
    # For each level: buy_order = limit order to buy tokens at this price
    #                sell_order = limit order to sell tokens at level above
    filled_buys = {}  # level_idx → (tokens_bought, buy_fee_usd, buy_price)

    # Allocate USD per level for buying
    usd_per_level = initial_usd / num_levels

    for idx, candle in enumerate(candles):
        high = candle["h"]
        low = candle["l"]
        close = candle["c"]

        # Check buy fills (low touches grid level)
        for level_idx, level_price in enumerate(grid_levels):
            if low <= level_price:
                # Check if this level already has a filled buy
                if level_idx not in filled_buys:
                    # Buy fills at grid level (maker order)
                    buy_fee = usd_per_level * maker_fee_bps / 10000.0
                    usd_to_spend = usd_per_level - buy_fee
                    tokens_bought = usd_to_spend / level_price

                    state.usd -= usd_per_level  # Full allocation spent + fee
                    state.tokens += tokens_bought
                    state.total_fees_usd += buy_fee
                    state.total_buys += 1
                    state.total_usd_spent += usd_per_level
                    state.total_tokens_bought += tokens_bought

                    filled_buys[level_idx] = (tokens_bought, buy_fee, level_price)

        # Check sell fills (high touches sell level = one level above)
        levels_to_remove = []
        for level_idx in list(filled_buys.keys()):
            sell_level_idx = level_idx - 1
            sell_level_price = grid_levels[sell_level_idx] if sell_level_idx >= 0 else grid_levels[0] * (1 + spacing)

            if high >= sell_level_price:
                # Sell the tokens we bought at this level
                tokens_sold, buy_fee, buy_price = filled_buys[level_idx]

                # Sell at sell level price (maker order ideally, but use taker for conservative estimate)
                sell_fee = (tokens_sold * sell_level_price) * taker_fee_bps / 10000.0
                usd_received = tokens_sold * sell_level_price - sell_fee

                state.tokens -= tokens_sold
                state.usd += usd_received
                state.total_fees_usd += sell_fee
                state.total_sells += 1
                state.total_usd_received += usd_received
                state.total_tokens_sold += tokens_sold

                levels_to_remove.append(level_idx)

        for level_idx in levels_to_remove:
            del filled_buys[level_idx]

    # Final valuation
    last_price = candles[-1]["c"]
    final_usd_value = state.usd + state.tokens * last_price  # Token holdings valued at last price
    initial_usd_value = initial_usd + initial_tokens * first_price

    usd_return_pct = (state.usd - initial_usd) / initial_usd * 100 if initial_usd > 0 else 0
    token_return_pct = (state.tokens - initial_tokens) / initial_tokens * 100 if initial_tokens > 0 else float('inf') if state.tokens > initial_tokens else 0
    total_return_pct = (final_usd_value - initial_usd_value) / initial_usd_value * 100 if initial_usd_value > 0 else 0

    # Per-trade stats
    trades = state.total_sells  # Each sell completes a round-trip trade
    avg_profit_per_trade_usd = (state.usd - initial_usd + state.total_fees_usd) / trades if trades > 0 else 0

    return {
        "grid_spacing_bps": grid_spacing_bps,
        "num_levels": num_levels,
        "initial_usd": initial_usd,
        "initial_tokens": initial_tokens,
        "initial_price": first_price,
        "final_price": last_price,
        "final_usd": round(state.usd, 4),
        "final_tokens": round(state.tokens, 6),
        "final_usd_value": round(final_usd_value, 4),
        "usd_return_pct": round(usd_return_pct, 4),
        "token_growth_pct": round(((state.tokens - initial_tokens) / max(initial_tokens, 0.0001)) * 100, 4) if initial_tokens == 0 else round((state.tokens / max(initial_tokens, 1e-10) - 1) * 100, 4),
        "total_return_pct": round(total_return_pct, 4),
        "total_buys": state.total_buys,
        "total_sells": state.total_sells,
        "trades_completed": trades,
        "total_fees_usd": round(state.total_fees_usd, 4),
        "open_buy_positions": len(filled_buys),
        "avg_profit_per_trade_usd": round(avg_profit_per_trade_usd, 6),
        "span_minutes": round((candles[-1]["t"] - candles[0]["t"]) / 60, 1),
        "candles_processed": len(candles),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dual-asset grid backtest tracking USD + token growth")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--products", default=DEFAULT_PRODUCTS)
    parser.add_argument("--grid-spacing-bps", type=float, default=DEFAULT_GRID_SPACING_BPS)
    parser.add_argument("--num-levels", type=int, default=DEFAULT_NUM_LEVELS)
    parser.add_argument("--initial-usd", type=float, default=DEFAULT_INITIAL_USD)
    parser.add_argument("--initial-tokens", type=float, default=DEFAULT_INITIAL_TOKENS)
    parser.add_argument("--maker-fee-bps", type=float, default=DEFAULT_MAKER_FEE_BPS)
    parser.add_argument("--taker-fee-bps", type=float, default=DEFAULT_TAKER_FEE_BPS)
    parser.add_argument("--json-path", default=str(REPORTS / "kraken_dual_grid_backtest.json"))
    parser.add_argument("--md-path", default=str(REPORTS / "kraken_dual_grid_backtest.md"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    products = [p.strip() for p in args.products.split(",") if p.strip()]

    print(f"🎯 Dual-Asset Grid Trading Backtest")
    print(f"   Products: {products}")
    print(f"   Grid spacing: {args.grid_spacing_bps}bps")
    print(f"   Levels: {args.num_levels}")
    print(f"   Initial: ${args.initial_usd} USD, {args.initial_tokens} tokens")
    print(f"   Fees: maker {args.maker_fee_bps}bps (buy), taker {args.taker_fee_bps}bps (sell)")
    print()

    candles = load_candles(Path(args.cache_path))
    all_results = {}

    for product in products:
        prod_candles = candles.get(product, {})
        if not prod_candles:
            print(f"  ❌ {product}: no candles found")
            continue

        # Use 1m candles for maximum resolution
        if 1 in prod_candles:
            grain = 1
            grain_name = "1m"
        elif 5 in prod_candles:
            grain = 5
            grain_name = "5m"
        else:
            grain = list(prod_candles.keys())[0]
            grain_name = f"{grain}m"

        c = prod_candles[grain]
        print(f"  Testing {product} ({grain_name}, {len(c)} candles)...", end=" ", flush=True)

        result = simulate_dual_grid(
            candles=c,
            grid_spacing_bps=args.grid_spacing_bps,
            num_levels=args.num_levels,
            initial_usd=args.initial_usd,
            initial_tokens=args.initial_tokens,
            maker_fee_bps=args.maker_fee_bps,
            taker_fee_bps=args.taker_fee_bps,
        )

        all_results[product] = result

        if "error" in result:
            print(f"❌ {result['error']}")
        else:
            print(f"USD ${result['final_usd']:+.2f} ({result['usd_return_pct']:+.2f}%), "
                  f"Tokens {result['final_tokens']:+.2f} ({result['token_growth_pct']:+.2f}%), "
                  f"Total ${result['final_usd_value']:+.2f} ({result['total_return_pct']:+.2f}%), "
                  f"{result['trades_completed']} trades")

    # Save JSON
    payload = {
        "generated_at": utc_now_iso(),
        "parameters": {
            "products": products,
            "grid_spacing_bps": args.grid_spacing_bps,
            "num_levels": args.num_levels,
            "initial_usd": args.initial_usd,
            "initial_tokens": args.initial_tokens,
            "maker_fee_bps": args.maker_fee_bps,
            "taker_fee_bps": args.taker_fee_bps,
        },
        "results": all_results,
    }
    Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    # Write MD report
    md_lines = [
        "# Dual-Asset Grid Trading Backtest",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Grid spacing: {args.grid_spacing_bps}bps between levels",
        f"- Levels: {args.num_levels}",
        f"- Initial: ${args.initial_usd} USD, {args.initial_tokens} tokens",
        f"- Fees: {args.maker_fee_bps}bps maker (buy), {args.taker_fee_bps}bps taker (sell)",
        "",
        "## Results: BOTH USD and Token Growth Matter",
        "",
        "| Product | Final USD | USD Return % | Final Tokens | Token Growth % | Total Value ($) | Total Return % | Trades | Open Buys |",
        "|---------|----------:|-------------:|-------------:|---------------:|----------------:|---------------:|-------:|----------:|",
    ]

    for product in products:
        r = all_results.get(product, {})
        if r and "error" not in r:
            md_lines.append(
                f"| {product} | ${r['final_usd']:.2f} | {r['usd_return_pct']:+.2f}% | {r['final_tokens']:.2f} | {r['token_growth_pct']:+.2f}% | ${r['final_usd_value']:.2f} | {r['total_return_pct']:+.2f}% | {r['trades_completed']} | {r['open_buy_positions']} |"
            )

    md_lines.extend([
        "",
        "## How to Read This",
        "",
        "- **USD Return %**: How much cash we made/lost from grid trading",
        "- **Token Growth %**: How many MORE tokens we accumulated vs started",
        "- **Total Return %**: Combined value of USD + tokens at final price",
        "- **A strategy that grows tokens is winning** — even if USD is flat, tokens appreciate",
        "- **Open Buys**: Grid levels where we bought but haven't sold yet (unrealized token accumulation)",
        "",
        "## Key Insight: Token Accumulation IS Profit",
        "",
        "In crypto spot trading, the goal is to grow the account — not just USD.",
        "If you end with 5% more tokens and the same USD, you're 5% richer.",
        "Tokens that appreciate = compound growth. This is the real edge.",
        "",
    ])

    # Find winners (by total return)
    winners = [(p, r) for p, r in all_results.items() if isinstance(r, dict) and r.get("total_return_pct", 0) > 0]
    losers = [(p, r) for p, r in all_results.items() if isinstance(r, dict) and r.get("total_return_pct", 0) <= 0]
    token_winners = [(p, r) for p, r in all_results.items() if isinstance(r, dict) and r.get("token_growth_pct", 0) > 0]

    if winners:
        md_lines.append(f"**{len(winners)} winners by total return:**\n")
        for p, r in sorted(winners, key=lambda x: x[1]["total_return_pct"], reverse=True):
            md_lines.append(f"- {p}: Total {r['total_return_pct']:+.2f}% (USD {r['usd_return_pct']:+.2f}%, Tokens {r['token_growth_pct']:+.2f}%)")
        md_lines.append("")

    if token_winners:
        md_lines.append(f"**{len(token_winners)} products with token growth:**\n")
        for p, r in sorted(token_winners, key=lambda x: x[1]["token_growth_pct"], reverse=True):
            md_lines.append(f"- {p}: Tokens {r['token_growth_pct']:+.2f}% (USD {r['usd_return_pct']:+.2f}%)")
        md_lines.append("")

    if losers:
        md_lines.append(f"**{len(losers)} products losing on total value:**\n")
        for p, r in sorted(losers, key=lambda x: x[1]["total_return_pct"]):
            md_lines.append(f"- {p}: Total {r['total_return_pct']:+.2f}% (USD {r['usd_return_pct']:+.2f}%, Tokens {r['token_growth_pct']:+.2f}%)")
        md_lines.append("")

    Path(args.md_path).write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"\n📁 JSON: {args.json_path}")
    print(f"📁 MD: {args.md_path}")


if __name__ == "__main__":
    main()
