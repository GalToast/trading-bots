#!/usr/bin/env python3
"""Maker-fee RSI shadow runner for execution-reality survivors.

Based on the proven MOG-USD RSI(4) geometry:
- RSI(4) on 5m candles
- Oversold < 30 → BUY
- Profit target: 5%
- Stop loss: 5% (emergency circuit breaker, primary exit is ATR leash)
- Max hold: 24 bars (2 hours)
- Dynamic ATR Trail: 1.5x ATR leash to handle volatile microcaps.
- Fleet-Wide Death Spiral Protection: Shared loss tracking with 1hr hard block.
"""
import argparse
import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = ROOT / "reports" / "maker_fee_rsi_shadow_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "maker_fee_rsi_shadow_events.jsonl"
MAKER_FEE_ANALYSIS = ROOT / "reports" / "maker_fee_unlock_analysis.json"
MAKER_REALITY_BOARD = ROOT / "reports" / "coinbase_spot_maker_execution_reality_board.json"
LOSS_TRACKER_BASE_PATH = ROOT / "reports"

from coinbase_advanced_client import CoinbaseAdvancedClient
from coinbase_rate_limit import safe_market_candles_limit
from death_spiral_prevention import LossTracker


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path, record):
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def load_maker_reality_products(source: str = "current"):
    """Load products from the maker execution reality board."""
    if not MAKER_REALITY_BOARD.exists():
        print(f"WARNING: {MAKER_REALITY_BOARD} not found. Run build_coinbase_spot_maker_execution_reality_board.py first.")
        return []
    with open(MAKER_REALITY_BOARD, encoding="utf-8") as f:
        data = json.load(f)
    rows = data.get("rows", [])
    if source == "current":
        verdicts = {"maker_taker_shadow_probe", "maker_maker_only_needs_exit_fill_proof"}
        products = [row for row in rows if row.get("current_verdict") in verdicts]
    elif source == "current-maker-taker":
        products = [row for row in rows if row.get("current_verdict") == "maker_taker_shadow_probe"]
    elif source == "zero":
        verdicts = {"maker_taker_shadow_probe", "maker_maker_only_needs_exit_fill_proof"}
        products = [row for row in rows if row.get("zero_maker_verdict") in verdicts]
    else:
        products = []
    print(f"Loaded {len(products)} maker-reality products from {source} verdicts")
    return products

def load_products(product_source: str):
    if product_source == "maker-unlock":
        return load_maker_fee_products()
    if product_source in {"current", "current-maker-taker", "zero"}:
        return load_maker_reality_products(product_source)
    raise ValueError(f"Unknown product source: {product_source}")

def fetch_recent_candles(client, product_id, granularity="FIVE_MINUTE", count=50):
    """Fetch recent candles for bootstrapping ATR/RSI."""
    resp = safe_market_candles_limit(
        client,
        product_id,
        granularity=granularity,
        limit=count,
        retries=3
    )
    if not resp:
        return []
    raw = resp.get("candles") or []
    candles = []
    for c in sorted(raw, key=lambda x: int(x["start"])):
        candles.append({
            "time": int(c["start"]),
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
        })
    return candles

def compute_rsi(prices, period=4):
    """Compute RSI for the given price series."""
    if len(prices) < period + 1:
        return 50.0  # Neutral

    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    recent = deltas[-period:]

    gains = [d for d in recent if d > 0]
    losses = [-d for d in recent if d < 0]

    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def compute_atr(candles, period=12):
    """Compute ATR for the latest candle in a series."""
    if len(candles) < period + 1:
        return 0.0

    tr_list = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i-1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_list.append(tr)
    
    if not tr_list:
        return 0.0
        
    atr = sum(tr_list[-period:]) / period
    return atr


@dataclass
class RsiPosition:
    product_id: str
    entry_price: float
    quantity: float
    cost_usd: float
    entry_fee: float
    opened_at: str
    entry_bar: int
    highest_price: float
    lowest_price: float
    entry_rsi: float
    target_pct: float
    stop_pct: float
    max_hold_bars: int
    trail_pct: float = 1.5


class MakerFeeRsiEngine:
    def __init__(
        self,
        *,
        starting_cash_usd: float = 100.0,
        deploy_pct: float = 0.8,
        maker_fee_bps: float = 60.0,
        taker_fee_bps: float = 120.0,
        exit_mode: str = "maker_taker",
        rsi_period: int = 4,
        oversold_threshold: float = 30.0,
        overbought_threshold: float = 70.0,
        profit_target_pct: float = 5.0,
        stop_loss_pct: float = 5.0,
        max_hold_bars: int = 24,
        price_history_len: int = 50,
    ):
        self.starting_cash_usd = starting_cash_usd
        self.cash_usd = starting_cash_usd
        self.deploy_pct = deploy_pct
        self.maker_fee_bps = maker_fee_bps
        self.taker_fee_bps = taker_fee_bps
        self.exit_mode = exit_mode
        self.rsi_period = rsi_period
        self.oversold_threshold = oversold_threshold
        self.overbought_threshold = overbought_threshold
        self.profit_target_pct = profit_target_pct
        self.stop_loss_pct = stop_loss_pct
        self.max_hold_bars = max_hold_bars
        self.price_history_len = price_history_len

        self.positions: dict[str, RsiPosition] = {}
        self.price_history: dict[str, list[float]] = {}
        self.candle_history: dict[str, list[dict]] = {}
        self.bar_count: dict[str, int] = {}
        self.signal_count: dict[str, int] = {}

        self.realized_net_usd = 0.0
        self.realized_closes = 0
        self.total_fees = 0.0
        self.wins = 0
        self.losses = 0
        
        # Trackers
        self.trackers: dict[str, LossTracker] = {}

    def get_tracker(self, product_id: str) -> LossTracker:
        if product_id not in self.trackers:
            tracker_path = LOSS_TRACKER_BASE_PATH / f"strict_maker_loss_tracker_{product_id.lower()}.json"
            self.trackers[product_id] = LossTracker(
                max_consecutive_losses=3,
                cooldown_seconds=3600,
                state_path=tracker_path
            )
        return self.trackers[product_id]

    def fee_rate(self) -> float:
        return self.maker_fee_bps / 10000.0

    def exit_fee_rate(self) -> float:
        if self.exit_mode == "maker_maker":
            return self.maker_fee_bps / 10000.0
        return self.taker_fee_bps / 10000.0

    def update_price(self, product_id: str, price: float, candle: dict = None):
        if product_id not in self.price_history:
            self.price_history[product_id] = []
        self.price_history[product_id].append(price)
        if len(self.price_history[product_id]) > self.price_history_len:
            self.price_history[product_id] = self.price_history[product_id][-self.price_history_len:]

        if candle:
            if product_id not in self.candle_history:
                self.candle_history[product_id] = []
            self.candle_history[product_id].append(candle)
            if len(self.candle_history[product_id]) > self.price_history_len:
                self.candle_history[product_id] = self.candle_history[product_id][-self.price_history_len:]

        if product_id not in self.bar_count:
            self.bar_count[product_id] = 0
        self.bar_count[product_id] += 1
        
        # Tick the tracker
        self.get_tracker(product_id).tick()

    def check_signals(self, product_id: str, price: float, event_path: Path) -> bool:
        """Check for RSI oversold signal. Returns True if signal fired."""
        # Death Spiral Check
        if self.get_tracker(product_id).is_blocked(product_id):
            return False

        history = self.price_history.get(product_id, [])
        if len(history) < self.rsi_period + 1:
            return False

        rsi = compute_rsi(history, self.rsi_period)

        if rsi < self.oversold_threshold:
            self.signal_count[product_id] = self.signal_count.get(product_id, 0) + 1
            append_jsonl(event_path, {
                "ts_utc": utc_now_iso(),
                "event": "rsi_oversold",
                "product_id": product_id,
                "price": price,
                "rsi": round(rsi, 2),
                "history_len": len(history),
                "execution_note": "signal_only_post_only_fill_not_guaranteed",
            })
            return True

        return False

    def open_position(self, product_id: str, price: float, event_path: Path) -> bool:
        if product_id in self.positions:
            return False

        quote_usd = min(self.cash_usd * self.deploy_pct, self.cash_usd)
        entry_fee = quote_usd * self.fee_rate()
        quantity = (quote_usd - entry_fee) / price if price > 0 else 0

        if quantity <= 0:
            return False

        # Calculate ATR for adaptive trailing (leash)
        candles = self.candle_history.get(product_id, [])
        atr = compute_atr(candles, period=12)
        atr_pct = (atr / price) * 100.0 if price > 0 else 0.0
        # Match Kraken logic: max(1.5, atr_pct * 1.5)
        trail_pct = max(1.5, atr_pct * 1.5)

        self.cash_usd -= quote_usd
        self.total_fees += entry_fee

        current_bar = self.bar_count.get(product_id, 0)
        rsi = compute_rsi(self.price_history.get(product_id, []), self.rsi_period)

        self.positions[product_id] = RsiPosition(
            product_id=product_id,
            entry_price=price,
            quantity=quantity,
            cost_usd=quote_usd,
            entry_fee=entry_fee,
            opened_at=utc_now_iso(),
            entry_bar=current_bar,
            highest_price=price,
            lowest_price=price,
            entry_rsi=rsi,
            target_pct=self.profit_target_pct,
            stop_pct=self.stop_loss_pct,
            max_hold_bars=self.max_hold_bars,
            trail_pct=trail_pct,
        )

        append_jsonl(event_path, {
            "ts_utc": utc_now_iso(),
            "event": "entry",
            "product_id": product_id,
            "price": price,
            "rsi": round(rsi, 2),
            "atr_pct": round(atr_pct, 4),
            "trail_pct": round(trail_pct, 2),
            "cost_usd": round(quote_usd, 2),
            "quantity": round(quantity, 6),
            "entry_fee_bps": self.maker_fee_bps,
            "exit_mode": self.exit_mode,
            "execution_note": "shadow assumes post_only_entry_fill_at_bid; validate with live order-book fill telemetry before live use",
        })

        print(f"  ENTRY: {product_id} @ {price:.8f} (RSI={rsi:.1f}, cost=${quote_usd:.2f}, trail={trail_pct:.2f}%)")
        return True

    def check_exit(self, product_id: str, price: float, event_path: Path) -> bool:
        pos = self.positions.get(product_id)
        if not pos:
            return False

        pos.highest_price = max(pos.highest_price, price)
        pos.lowest_price = min(pos.lowest_price, price)

        current_bar = self.bar_count.get(product_id, 0)
        bars_held = current_bar - pos.entry_bar

        gross_move_pct = (price - pos.entry_price) / pos.entry_price * 100
        target_hit = gross_move_pct >= pos.target_pct
        stop_hit = gross_move_pct <= -pos.stop_pct
        
        # ATR Leash (Trailing Stop)
        trail_stop_price = pos.highest_price * (1 - (pos.trail_pct / 100.0))
        trail_hit = price <= trail_stop_price

        timeout = bars_held >= pos.max_hold_bars

        exit_reason = None
        if target_hit:
            exit_reason = "target_hit"
        elif trail_hit:
            exit_reason = "trail_hit"
        elif stop_hit:
            exit_reason = "stop_hit"
        elif timeout:
            exit_reason = "timeout"

        if exit_reason:
            proceeds = pos.quantity * price
            exit_fee = proceeds * self.exit_fee_rate()
            net_pnl = proceeds - exit_fee - pos.cost_usd
            net_pct = (net_pnl / pos.cost_usd) * 100

            self.cash_usd += proceeds - exit_fee
            self.total_fees += exit_fee
            self.realized_net_usd += net_pnl
            self.realized_closes += 1

            if net_pnl > 0:
                self.wins += 1
            else:
                self.losses += 1
                
            # Update Loss Tracker
            res = self.get_tracker(product_id).record_close(product_id, won=(net_pnl > 0))
            if res["action"] == "blocked":
                print(f"  🚨 FLEET-WIDE DEATH SPIRAL BLOCK: {product_id} blocked for {res['cooldown_seconds']}s after {res['consecutive_losses']} losses.")
            self.get_tracker(product_id).save()

            append_jsonl(event_path, {
                "ts_utc": utc_now_iso(),
                "event": "exit",
                "product_id": product_id,
                "price": price,
                "entry_price": pos.entry_price,
                "exit_reason": exit_reason,
                "bars_held": bars_held,
                "gross_move_pct": round(gross_move_pct, 4),
                "trail_pct": round(pos.trail_pct, 2),
                "proceeds": round(proceeds, 6),
                "entry_fee": round(pos.entry_fee, 6),
                "exit_fee": round(exit_fee, 6),
                "entry_fee_bps": self.maker_fee_bps,
                "exit_fee_bps": self.maker_fee_bps if self.exit_mode == "maker_maker" else self.taker_fee_bps,
                "exit_mode": self.exit_mode,
                "net_pnl": round(net_pnl, 6),
                "net_pct": round(net_pct, 4),
                "realized_net_usd": round(self.realized_net_usd, 6),
                "cash_after": round(self.cash_usd, 6),
                "entry_rsi": round(pos.entry_rsi, 2),
            })

            result = "WIN" if net_pnl > 0 else "LOSS"
            print(f"  EXIT {result}: {product_id} {exit_reason} @ {price:.8f} net={net_pct:+.2f}% (${net_pnl:+.4f}) bars={bars_held}")

            del self.positions[product_id]
            return True

        return False

    def state(self) -> dict:
        return {
            "mode": "maker_fee_rsi_shadow",
            "starting_cash_usd": self.starting_cash_usd,
            "cash_usd": round(self.cash_usd, 6),
            "realized_net_usd": round(self.realized_net_usd, 6),
            "realized_closes": self.realized_closes,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": self.wins / max(1, self.wins + self.losses) * 100,
            "total_fees": round(self.total_fees, 6),
            "maker_fee_bps": self.maker_fee_bps,
            "taker_fee_bps": self.taker_fee_bps,
            "exit_mode": self.exit_mode,
            "rsi_period": self.rsi_period,
            "oversold_threshold": self.oversold_threshold,
            "profit_target_pct": self.profit_target_pct,
            "stop_loss_pct": self.stop_loss_pct,
            "max_hold_bars": self.max_hold_bars,
            "open_positions": {pid: asdict(pos) for pid, pos in self.positions.items()},
            "signal_counts": dict(self.signal_count),
            "price_history_len": {pid: len(h) for pid, h in self.price_history.items()},
        }

    def save_state(self, path: Path):
        with open(path, "w") as f:
            json.dump(self.state(), f, indent=2)

    def load_state(self, path: Path):
        if not path.exists():
            return
        with open(path) as f:
            data = json.load(f)
        state = data.get("state", data)
        self.cash_usd = float(state.get("cash_usd", self.starting_cash_usd))
        self.realized_net_usd = float(state.get("realized_net_usd", 0.0))
        self.realized_closes = int(state.get("realized_closes", 0))
        self.wins = int(state.get("wins", 0))
        self.losses = int(state.get("losses", 0))
        self.total_fees = float(state.get("total_fees", 0.0))
        
        open_pos = state.get("open_positions", {})
        for pid, pos_data in open_pos.items():
            self.positions[pid] = RsiPosition(**pos_data)


def main():
    parser = argparse.ArgumentParser(description="Maker-Fee RSI Shadow Runner")
    parser.add_argument("--starting-cash", type=float, default=100.0)
    parser.add_argument("--deploy-pct", type=float, default=0.8)
    parser.add_argument("--maker-fee-bps", type=float, default=60.0)
    parser.add_argument("--taker-fee-bps", type=float, default=120.0)
    parser.add_argument(
        "--exit-mode",
        choices=["maker_taker", "maker_maker"],
        default="maker_taker",
        help="maker_taker exits long inventory at bid with taker fee; maker_maker exits at ask and must be treated as extra optimistic until fill proof exists.",
    )
    parser.add_argument(
        "--product-source",
        choices=["current", "current-maker-taker", "zero", "maker-unlock"],
        default="current",
        help="current uses maker execution reality current verdict survivors; zero is hypothetical fee-tier research; maker-unlock is broad fee-math-only.",
    )
    parser.add_argument("--rsi-period", type=int, default=4)
    parser.add_argument("--oversold", type=float, default=30.0)
    parser.add_argument("--profit-target", type=float, default=5.0)
    parser.add_argument("--stop-loss", type=float, default=5.0)
    parser.add_argument("--max-hold", type=int, default=24)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    args = parser.parse_args()

    print("=" * 80)
    print("MAKER-FEE RSI SHADOW RUNNER")
    print("=" * 80)
    print(f"Starting cash: ${args.starting_cash:.2f}")
    print(f"Maker fee: {args.maker_fee_bps}bps")
    print(f"Taker fee: {args.taker_fee_bps}bps")
    print(f"Product source: {args.product_source}")
    print(f"Exit mode: {args.exit_mode}")
    print(f"RSI({args.rsi_period}), oversold<{args.oversold}")
    print(f"Target: {args.profit_target}%, Stop: {args.stop_loss}%, Max hold: {args.max_hold} bars")

    engine = MakerFeeRsiEngine(
        starting_cash_usd=args.starting_cash,
        deploy_pct=args.deploy_pct,
        maker_fee_bps=args.maker_fee_bps,
        taker_fee_bps=args.taker_fee_bps,
        exit_mode=args.exit_mode,
        rsi_period=args.rsi_period,
        oversold_threshold=args.oversold,
        profit_target_pct=args.profit_target,
        stop_loss_pct=args.stop_loss,
        max_hold_bars=args.max_hold,
    )

    state_path = Path(args.state_path)
    event_path = Path(args.event_path)

    if state_path.exists() and not args.fresh_start:
        engine.load_state(state_path)
        print(f"Loaded state: cash=${engine.cash_usd:.2f}, closes={engine.realized_closes}")

    products = load_products(args.product_source)
    if not products:
        print("No maker-fee products available. Exiting.")
        return

    product_ids = [p["product_id"] for p in products]
    print(f"Monitoring {len(product_ids)} products: {', '.join(product_ids[:10])}...")

    client = CoinbaseAdvancedClient()
    
    # Bootstrap candles for ATR/RSI
    print(f"Bootstrapping {len(product_ids)} products with historical 5m candles...")
    for pid in product_ids:
        candles = fetch_recent_candles(client, pid)
        for c in candles:
            engine.update_price(pid, c["close"], candle=c)
    print("Bootstrap complete.")

    poll_count = 0

    try:
        while True:
            poll_count += 1

            # Fetch current prices
            try:
                payload = client.best_bid_ask(product_ids)
                ticks = {}
                for book in payload.get("pricebooks") or []:
                    pid = str(book.get("product_id") or "")
                    bids = book.get("bids") or []
                    asks = book.get("asks") or []
                    if bids and asks:
                        bid = float(bids[0]["price"])
                        ask = float(asks[0]["price"])
                        mid = (bid + ask) / 2
                        ticks[pid] = {"bid": bid, "ask": ask, "mid": mid}
            except Exception as e:
                print(f"  Poll {poll_count}: Fetch error: {e}")
                time.sleep(args.poll_seconds)
                continue

            if not ticks:
                print(f"  Poll {poll_count}: No ticks received")
                time.sleep(args.poll_seconds)
                continue

            # Update prices and check for signals/exits
            for pid, tick in ticks.items():
                bid = tick["bid"]
                ask = tick["ask"]
                mid = tick["mid"]
                
                # Synthetic candle for ATR update (mid as surrogate for close/high/low)
                synthetic_candle = {
                    "time": int(time.time()),
                    "open": mid,
                    "high": max(mid, ask),
                    "low": min(mid, bid),
                    "close": mid
                }
                engine.update_price(pid, mid, candle=synthetic_candle)

                # Check exits first
                exit_price = ask if args.exit_mode == "maker_maker" else bid
                engine.check_exit(pid, exit_price, event_path)

                # Then check for new signals
                if pid not in engine.positions:
                    if engine.check_signals(pid, mid, event_path):
                        engine.open_position(pid, bid, event_path)

            # Status
            state = engine.state()
            open_count = len(state["open_positions"])
            print(f"  Poll {poll_count}: Cash=${state['cash_usd']:.2f} Net=${state['realized_net_usd']:+.2f} "
                  f"Closes={state['realized_closes']} W/L={state['wins']}/{state['losses']} Open={open_count}")

            engine.save_state(state_path)

            if args.once:
                break

            time.sleep(args.poll_seconds)

    except KeyboardInterrupt:
        print("\nStopping maker-fee RSI shadow runner...")
    finally:
        engine.save_state(state_path)
        state = engine.state()
        print(f"\nFinal: Cash=${state['cash_usd']:.2f} Net=${state['realized_net_usd']:+.2f} "
              f"Closes={state['realized_closes']} W/L={state['wins']}/{state['losses']} "
              f"WinRate={state['win_rate']:.1f}%")


if __name__ == "__main__":
    main()
