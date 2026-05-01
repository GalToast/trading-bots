#!/usr/bin/env python3
"""
Live Kraken Grid Trading Bot.

Places limit orders at grid levels and waits for fills.
Tracks both USD and token growth.

Parameters (backtest-validated on 15m DUCK/USD 7.5-day data):
- 500bps spacing, 3 levels, maker exit → +92.28% in 7.5 days, 59 trades

Usage:
    python scripts/live_kraken_grid_bot.py --product DUCK-USD --spacing-bps 500 --levels 3 --usd 10 --validate-only
    python scripts/live_kraken_grid_bot.py --product DUCK-USD --spacing-bps 500 --levels 3 --usd 10  # LIVE
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kraken_spot_client import KrakenSpotClient, to_float, parse_pair, product_id_for_pair  # noqa: E402

DEFAULT_PRODUCT = "DUCK-USD"
DEFAULT_SPACING_BPS = 500.0
DEFAULT_LEVELS = 3
DEFAULT_USD = 10.0
DEFAULT_POLL_SECONDS = 30
DEFAULT_STATE_PATH = REPORTS / "grid_bot_state.json"
DEFAULT_LOG_PATH = REPORTS / "grid_bot_log.jsonl"


@dataclass
class GridLevel:
    level_idx: int
    buy_price: float  # Limit buy price
    sell_price: float  # Limit sell price (one level above)
    order_id: str | None = None
    filled: bool = False
    tokens_bought: float = 0.0
    buy_time: str | None = None


@dataclass
class GridBotState:
    product: str
    rest_pair: str
    spacing_bps: float
    num_levels: int
    initial_usd: float
    current_usd: float
    current_tokens: float
    levels: list[GridLevel] = field(default_factory=list)
    completed_trades: int = 0
    total_fees_usd: float = 0.0
    started_at: str = ""
    last_update: str = ""
    running: bool = True


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(log_path: Path, event: dict) -> None:
    """Append event to JSONL log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    event["ts"] = utc_now_iso()
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def save_state(state: GridBotState, state_path: Path) -> None:
    """Save bot state atomically."""
    state.last_update = utc_now_iso()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(state), indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(state_path)


def load_state(state_path: Path) -> GridBotState | None:
    """Load bot state if exists."""
    if state_path.exists():
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return GridBotState(**data)
    return None


def get_current_price(client: KrakenSpotClient, rest_pair: str) -> float:
    """Get current mid price from ticker."""
    ticker = client.ticker([rest_pair])
    if not ticker:
        return 0.0
    key = list(ticker.keys())[0]
    t = ticker[key]
    bid = to_float((t.get("b") or [None])[0])
    ask = to_float((t.get("a") or [None])[0])
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    return 0.0


def get_balances(client: KrakenSpotClient) -> dict[str, float]:
    """Get account balances."""
    try:
        balances = client.balance()
        return {k.lower(): to_float(v) for k, v in balances.items()}
    except Exception:
        return {}


def build_grid_levels(current_price: float, spacing_bps: float, num_levels: int) -> list[GridLevel]:
    """Build grid levels around current price."""
    spacing = spacing_bps / 10000.0
    levels = []
    for i in range(num_levels):
        buy_price = current_price * (1 - i * spacing)
        sell_price = current_price * (1 - (i - 1) * spacing) if i > 0 else current_price * (1 + spacing)
        levels.append(GridLevel(
            level_idx=i,
            buy_price=round(buy_price, 12),
            sell_price=round(sell_price, 12),
        ))
    return levels


def place_buy_order(
    client: KrakenSpotClient,
    rest_pair: str,
    price: float,
    volume: float,
    validate_only: bool = False,
) -> str | None:
    """Place a post-only buy limit order."""
    try:
        result = client.add_order(
            rest_pair=rest_pair,
            side="buy",
            order_type="limit",
            volume=volume,
            price=price,
            post_only=True,
            validate=validate_only,
        )
        # Kraken returns txid on success
        txids = result.get("txid", [])
        if txids:
            return txids[0]
        return None
    except Exception as e:
        print(f"  Buy order error: {e}")
        return None


def place_sell_order(
    client: KrakenSpotClient,
    rest_pair: str,
    price: float,
    volume: float,
    validate_only: bool = False,
) -> str | None:
    """Place a post-only sell limit order."""
    try:
        result = client.add_order(
            rest_pair=rest_pair,
            side="sell",
            order_type="limit",
            volume=volume,
            price=price,
            post_only=True,
            validate=validate_only,
        )
        txids = result.get("txid", [])
        if txids:
            return txids[0]
        return None
    except Exception as e:
        print(f"  Sell order error: {e}")
        return None


def check_order_filled(client: KrakenSpotClient, order_id: str) -> bool:
    """Check if an order has been filled."""
    try:
        result = client.query_orders([order_id])
        if result and order_id in result:
            order = result[order_id]
            status = order.get("status", "")
            return status.lower() in ("closed", "filled", "expired")
        return False
    except Exception:
        return False


def cancel_order(client: KrakenSpotClient, order_id: str) -> bool:
    """Cancel an open order."""
    try:
        result = client.cancel_order(order_id)
        return result.get("count", 0) > 0
    except Exception:
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live Kraken Grid Trading Bot")
    parser.add_argument("--product", default=DEFAULT_PRODUCT)
    parser.add_argument("--spacing-bps", type=float, default=DEFAULT_SPACING_BPS)
    parser.add_argument("--levels", type=int, default=DEFAULT_LEVELS)
    parser.add_argument("--usd", type=float, default=DEFAULT_USD)
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--log-path", default=str(DEFAULT_LOG_PATH))
    parser.add_argument("--validate-only", action="store_true", help="Validate orders without placing them")
    parser.add_argument("--resume", action="store_true", help="Resume from saved state")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    product = args.product
    rest_pair = product.replace("-", "")
    spacing_bps = args.spacing_bps
    num_levels = args.num_levels
    initial_usd = args.usd
    state_path = Path(args.state_path)
    log_path = Path(args.log_path)

    print(f"🤖 Kraken Grid Trading Bot")
    print(f"   Product: {product} ({rest_pair})")
    print(f"   Grid: {spacing_bps}bps spacing, {num_levels} levels")
    print(f"   Capital: ${initial_usd} USD")
    print(f"   Poll: {args.poll_seconds}s")
    print(f"   Mode: {'VALIDATE ONLY' if args.validate_only else 'LIVE'}")
    print()

    client = KrakenSpotClient()

    # Resume or start fresh
    if args.resume:
        state = load_state(state_path)
        if state:
            print(f"📂 Resumed from saved state ({state.completed_trades} completed trades)")
        else:
            state = None
    else:
        state = None

    if state is None:
        # Get current price for grid setup
        current_price = get_current_price(client, rest_pair)
        if current_price <= 0:
            print("❌ Could not get current price")
            return

        print(f"💰 Current {product} price: ${current_price:.8f}")

        # Build grid levels
        levels = build_grid_levels(current_price, spacing_bps, num_levels)
        usd_per_level = initial_usd / num_levels

        print(f"📊 Grid levels (usd_per_level=${usd_per_level:.2f}):")
        for level in levels:
            tokens_at_level = (usd_per_level * 0.9984) / level.buy_price  # minus maker fee
            print(f"  Level {level.level_idx}: Buy @ ${level.buy_price:.8f}, Sell @ ${level.sell_price:.8f} ({tokens_at_level:.2f} tokens)")

        state = GridBotState(
            product=product,
            rest_pair=rest_pair,
            spacing_bps=spacing_bps,
            num_levels=num_levels,
            initial_usd=initial_usd,
            current_usd=initial_usd,
            current_tokens=0.0,
            levels=levels,
            started_at=utc_now_iso(),
        )

        log_event(log_path, {"event": "grid_init", "price": current_price, "levels": [asdict(l) for l in levels]})

    save_state(state, state_path)

    # Main loop
    print(f"\n🔄 Starting grid loop (poll every {args.poll_seconds}s)...")
    usd_per_level = initial_usd / num_levels

    try:
        while state.running:
            current_price = get_current_price(client, rest_pair)
            if current_price <= 0:
                time.sleep(args.poll_seconds)
                continue

            # Check each level for buy opportunities
            for level in state.levels:
                if level.filled:
                    # Level has tokens - check if we should sell
                    # If price has risen above sell level, place sell order
                    if current_price >= level.sell_price and level.tokens_bought > 0:
                        if not level.order_id:
                            print(f"  📈 Level {level.level_idx}: Placing SELL @ ${level.sell_price:.8f} ({level.tokens_bought:.6f} tokens)")
                            order_id = place_sell_order(
                                client, rest_pair, level.sell_price, level.tokens_bought,
                                validate_only=args.validate_only,
                            )
                            if order_id:
                                level.order_id = order_id
                                log_event(log_path, {
                                    "event": "sell_order_placed",
                                    "level": level.level_idx,
                                    "price": level.sell_price,
                                    "tokens": level.tokens_bought,
                                    "order_id": order_id,
                                })
                        else:
                            # Check if sell order filled
                            if check_order_filled(client, level.order_id):
                                sell_usd = level.tokens_bought * level.sell_price
                                fee = sell_usd * 0.00016  # maker fee
                                net_usd = sell_usd - fee
                                state.current_usd += net_usd
                                state.current_tokens -= level.tokens_bought
                                state.completed_trades += 1
                                state.total_fees_usd += fee

                                profit_bps = ((level.sell_price / level.buy_price) - 1) * 10000
                                print(f"  ✅ Level {level.level_idx}: SOLD @ ${level.sell_price:.8f} → +${net_usd:.4f} ({profit_bps:+.1f}bps)")

                                log_event(log_path, {
                                    "event": "sell_filled",
                                    "level": level.level_idx,
                                    "sell_price": level.sell_price,
                                    "buy_price": level.buy_price,
                                    "tokens": level.tokens_bought,
                                    "net_usd": net_usd,
                                    "profit_bps": profit_bps,
                                })

                                # Reset level
                                level.filled = False
                                level.tokens_bought = 0.0
                                level.order_id = None
                                level.buy_time = None
                else:
                    # Level is empty - check if we should buy
                    if current_price <= level.buy_price:
                        if not level.order_id:
                            tokens_to_buy = (usd_per_level * 0.9984) / level.buy_price  # minus maker fee
                            print(f"  📉 Level {level.level_idx}: Placing BUY @ ${level.buy_price:.8f} ({tokens_to_buy:.6f} tokens)")
                            order_id = place_buy_order(
                                client, rest_pair, level.buy_price, tokens_to_buy,
                                validate_only=args.validate_only,
                            )
                            if order_id:
                                level.order_id = order_id
                                log_event(log_path, {
                                    "event": "buy_order_placed",
                                    "level": level.level_idx,
                                    "price": level.buy_price,
                                    "tokens": tokens_to_buy,
                                    "order_id": order_id,
                                })
                        else:
                            # Check if buy order filled
                            if check_order_filled(client, level.order_id):
                                tokens_received = (usd_per_level * 0.9984) / level.buy_price
                                state.current_usd -= usd_per_level
                                state.current_tokens += tokens_received
                                level.filled = True
                                level.tokens_bought = tokens_received
                                level.buy_time = utc_now_iso()
                                level.order_id = None  # Clear order ID, waiting for sell now

                                print(f"  ✅ Level {level.level_idx}: BOUGHT @ ${level.buy_price:.8f} ({tokens_received:.6f} tokens)")

                                log_event(log_path, {
                                    "event": "buy_filled",
                                    "level": level.level_idx,
                                    "price": level.buy_price,
                                    "tokens": tokens_received,
                                })

            # Save state
            save_state(state, state_path)

            # Status line
            total_value = state.current_usd + state.current_tokens * current_price
            ret_pct = ((total_value / state.initial_usd) - 1) * 100
            print(f"\r  📊 Price: ${current_price:.8f} | USD: ${state.current_usd:.2f} | Tokens: {state.current_tokens:.2f} | Value: ${total_value:.2f} ({ret_pct:+.2f}%) | Trades: {state.completed_trades} | Fees: ${state.total_fees_usd:.4f}", end="", flush=True)

            time.sleep(args.poll_seconds)

    except KeyboardInterrupt:
        print("\n\n🛑 Bot stopped by user")
        state.running = False
        save_state(state, state_path)

        # Summary
        total_value = state.current_usd + state.current_tokens * current_price
        print(f"\n📊 Final Summary:")
        print(f"  Started: {state.started_at}")
        print(f"  Completed trades: {state.completed_trades}")
        print(f"  Final USD: ${state.current_usd:.4f}")
        print(f"  Final tokens: {state.current_tokens:.6f}")
        print(f"  Total value: ${total_value:.4f}")
        print(f"  Return: {((total_value / state.initial_usd) - 1) * 100:+.2f}%")
        print(f"  Total fees: ${state.total_fees_usd:.4f}")


if __name__ == "__main__":
    main()
