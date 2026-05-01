#!/usr/bin/env python3
"""
Live Coinbase RSI mean reversion shadow runner.

Runs RSI(7) mean reversion strategy on real-time Coinbase spot data,
validating the benchmark edge discovered in historical backtesting.

Strategy:
- RSI period: 7
- Oversold: 30, Overbought: 70
- Profit target: 2%, Stop loss: 0.3%
- 5-min candles
- No volume filter needed
- Dynamic ATR Trail: 1.5x ATR leash to handle volatile microcaps.
- Fleet-Wide Death Spiral Protection: Shared loss tracking with 1hr hard block.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from coinbase_advanced_client import CoinbaseAdvancedClient
from coinbase_fee_model import CoinbaseSpotFeeTier, resolve_spot_fee_tier
from coinbase_rate_limit import safe_market_candles_limit
from live_penetration_lattice_shadow import append_jsonl, log_runner_exception, utc_now_iso
from death_spiral_prevention import LossTracker
from volatility_targets import AdaptiveTargetCalculator

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = ROOT / "reports" / "coinbase_rsi_shadow_arbusd_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "coinbase_rsi_shadow_arbusd_events.jsonl"


@dataclass
class RSITrade:
    entry_time: int
    entry_price: float
    direction: str
    quantity: float
    entry_rsi: float = 0.0
    entry_bar: int = 0
    entry_fee: float = 0.0
    exit_time: int = 0
    exit_price: float = 0.0
    exit_reason: str = ""
    exit_rsi: float = 0.0
    gross_pnl: float = 0.0
    fee: float = 0.0
    net_pnl: float = 0.0
    hold_bars: int = 0
    highest_price: float = 0.0
    trail_pct: float = 0.0
    target_pct: float = 0.0
    stop_pct: float = 0.0


def compute_rsi(closes: list[float], period: int = 7) -> float:
    """Compute RSI for the latest price in a series."""
    if len(closes) < period + 1:
        return 50.0

    deltas = [closes[-(i+1)] - closes[-(i+2)] for i in range(period)]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss > 0:
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
    return 100.0


class CoinbaseRSIShadowEngine:
    def __init__(
        self,
        *,
        product_id: str,
        starting_cash_usd: float,
        rsi_period: int,
        oversold_threshold: float,
        overbought_threshold: float,
        profit_target_pct: float,
        stop_loss_pct: float,
        max_hold_bars: int,
        maker_fee_bps: float,
        deploy_pct: float,
        candle_granularity: str = "FIVE_MINUTE",
    ) -> None:
        self.product_id = str(product_id).upper()
        self.starting_cash_usd = float(starting_cash_usd)
        self.rsi_period = int(rsi_period)
        self.oversold_threshold = float(oversold_threshold)
        self.overbought_threshold = float(overbought_threshold)
        self.profit_target_pct = float(profit_target_pct)
        self.stop_loss_pct = float(stop_loss_pct)
        self.max_hold_bars = int(max_hold_bars)
        self.maker_fee_bps = float(maker_fee_bps)
        self.deploy_pct = float(deploy_pct)
        self.candle_granularity = str(candle_granularity)
        self.fee_rate = self.maker_fee_bps / 10000.0
        self.fee_model = "coinbase_spot_shadow_fee_bps_per_side"
        self.fee_source = "configured"
        self.fee_tier = ""
        self.fill_model = "candle_close_proxy"

        # State
        self.cash_usd = float(starting_cash_usd)
        self.realized_net_usd = 0.0
        self.realized_closes = 0
        self.in_position = False
        self.current_trade: RSITrade | None = None
        self.price_history: list[float] = []
        self.candle_history: list[dict[str, float]] = []
        self.current_bar = 0
        self.last_candle_time = 0
        self.total_fees = 0.0
        self.signals_generated = 0
        
        # Loss Tracker
        tracker_path = ROOT / "reports" / f"coinbase_rsi_loss_tracker_{self.product_id.lower()}.json"
        self.tracker = LossTracker(
            max_consecutive_losses=3,
            cooldown_seconds=3600,
            state_path=tracker_path
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "mode": "coinbase_rsi_shadow",
            "cash_usd": round(self.cash_usd, 2),
            "realized_net_usd": round(self.realized_net_usd, 4),
            "realized_closes": self.realized_closes,
            "in_position": self.in_position,
            "current_trade": asdict(self.current_trade) if self.current_trade else None,
            "price_history_len": len(self.price_history),
            "current_bar": self.current_bar,
            "last_candle_time": self.last_candle_time,
            "total_fees": round(self.total_fees, 4),
            "signals_generated": self.signals_generated,
            "config": {
                "rsi_period": self.rsi_period,
                "oversold_threshold": self.oversold_threshold,
                "overbought_threshold": self.overbought_threshold,
                "profit_target_pct": self.profit_target_pct,
                "stop_loss_pct": self.stop_loss_pct,
                "max_hold_bars": self.max_hold_bars,
                "maker_fee_bps": self.maker_fee_bps,
                "fee_bps_per_side": self.maker_fee_bps,
                "fee_model": self.fee_model,
                "fee_source": self.fee_source,
                "fee_tier": self.fee_tier,
                "fill_model": self.fill_model,
                "deploy_pct": self.deploy_pct,
                "candle_granularity": self.candle_granularity,
            },
        }

    def apply_fee_tier(self, fee_tier: CoinbaseSpotFeeTier) -> None:
        self.maker_fee_bps = float(fee_tier.taker_bps)
        self.fee_rate = self.maker_fee_bps / 10000.0
        self.fee_model = "coinbase_spot_account_taker_fee_tier"
        self.fee_source = fee_tier.source
        self.fee_tier = fee_tier.pricing_tier

    def process_candle(self, candle: dict[str, Any], *, event_path: Path | None = None) -> None:
        """Process a new 5-min candle through the RSI system."""
        cl = float(candle["close"])
        h = float(candle["high"])
        l = float(candle["low"])
        ts = int(candle["time"])

        self.price_history.append(cl)
        self.candle_history.append(candle)
        if len(self.price_history) > self.rsi_period + 50:
            self.price_history = self.price_history[-(self.rsi_period + 50):]
            self.candle_history = self.candle_history[-(self.rsi_period + 50):]
            
        # Tick the tracker
        self.tracker.tick()

        # 1. Compute 12-bar ATR for dynamic trailing
        atr_12_pct = 0.0
        if len(self.candle_history) >= 13:
            tr_list = []
            for j in range(len(self.candle_history) - 12, len(self.candle_history)):
                curr = self.candle_history[j]
                prev = self.candle_history[j-1]
                tr = max(float(curr["high"]) - float(curr["low"]), 
                         abs(float(curr["high"]) - float(prev["close"])), 
                         abs(float(curr["low"]) - float(prev["close"])))
                tr_list.append(tr)
            atr_12 = sum(tr_list) / len(tr_list)
            atr_12_pct = (atr_12 / cl) * 100.0 if cl > 0 else 0.0

        # Check exit conditions for open position
        if self.in_position and self.current_trade is not None:
            # Update high for trailing
            self.current_trade.highest_price = max(self.current_trade.highest_price, cl)
            
            # Dynamic Trail: 1.5x ATR, min 1.5% for noise buffer
            if atr_12_pct > 0:
                self.current_trade.trail_pct = max(1.5, atr_12_pct * 1.5)
            else:
                self.current_trade.trail_pct = 2.5 # Default fallback

            tp_price = self.current_trade.entry_price * (1 + self.profit_target_pct)
            # Original hard stop
            sl_price = self.current_trade.entry_price * (1 - self.stop_loss_pct)
            
            # Trailing stop price
            trail_stop_price = self.current_trade.highest_price * (1 - (self.current_trade.trail_pct / 100.0))
            
            current_rsi = compute_rsi(self.price_history, self.rsi_period)

            exit_reason = ""
            exit_price = cl

            if h >= tp_price:
                exit_reason = "tp"
                exit_price = tp_price
            elif l <= sl_price:
                exit_reason = "sl"
                exit_price = sl_price
            elif cl <= trail_stop_price:
                # Use trail for dynamic protection based on ATR noise
                exit_reason = "trail_hit"
                exit_price = cl
            elif current_rsi >= self.overbought_threshold:
                exit_reason = "rsi_exit"
                exit_price = cl
            elif (self.current_bar - self.current_trade.entry_bar) >= self.max_hold_bars:
                exit_reason = "timeout"
                exit_price = cl

            if exit_reason:
                qty = self.current_trade.quantity
                gross = (exit_price - self.current_trade.entry_price) * qty
                exit_fee = exit_price * qty * self.fee_rate
                total_fee = self.current_trade.entry_fee + exit_fee
                net = gross - total_fee

                self.current_trade.exit_price = exit_price
                self.current_trade.exit_reason = exit_reason
                self.current_trade.exit_rsi = round(current_rsi, 2)
                self.current_trade.gross_pnl = round(gross, 4)
                self.current_trade.fee = round(total_fee, 4)
                self.current_trade.net_pnl = round(net, 4)
                self.current_trade.hold_bars = self.current_bar - self.current_trade.entry_bar
                self.current_trade.exit_time = ts

                self.cash_usd += exit_price * qty - exit_fee
                self.realized_net_usd += net
                self.realized_closes += 1
                self.total_fees += total_fee
                self.in_position = False
                
                # Update Loss Tracker
                res = self.tracker.record_close(self.product_id, won=(net > 0))
                if res["action"] == "blocked":
                    print(f"  🚨 FLEET-WIDE DEATH SPIRAL BLOCK: {self.product_id} blocked for {res['cooldown_seconds']}s after {res['consecutive_losses']} losses.")
                self.tracker.save()

                if event_path:
                    append_jsonl(event_path, {
                        "ts_utc": utc_now_iso(),
                        "action": "close_trade",
                        "symbol": self.product_id,
                        "direction": self.current_trade.direction,
                        "entry_price": self.current_trade.entry_price,
                        "exit_price": exit_price,
                        "entry_rsi": self.current_trade.entry_rsi,
                        "exit_rsi": current_rsi,
                        "entry_fee": round(self.current_trade.entry_fee, 4),
                        "exit_fee": round(exit_fee, 4),
                        "gross_pnl": round(gross, 4),
                        "fee": round(total_fee, 4),
                        "fee_bps_per_side": round(self.maker_fee_bps, 4),
                        "fee_model": self.fee_model,
                        "fee_source": self.fee_source,
                        "fee_tier": self.fee_tier,
                        "fill_model": self.fill_model,
                        "net_pnl": round(net, 4),
                        "hold_bars": self.current_trade.hold_bars,
                        "exit_reason": exit_reason,
                        "realized_net_usd": round(self.realized_net_usd, 4),
                    })

                self.current_trade = None

        # Check entry conditions (only if not in position)
        if not self.in_position and len(self.price_history) >= self.rsi_period + 1:
            # Death Spiral Block Check
            if self.tracker.is_blocked(self.product_id):
                return

            current_rsi = compute_rsi(self.price_history, self.rsi_period)
            self.signals_generated += 1

            if current_rsi <= self.oversold_threshold:
                deploy_usd = self.cash_usd * self.deploy_pct
                if deploy_usd >= 1.0:
                    entry_price = cl
                    entry_fee = entry_price * (deploy_usd / entry_price) * self.fee_rate
                    qty = (deploy_usd - entry_fee) / entry_price

                    if qty > 0:
                        self.cash_usd -= deploy_usd
                        self.total_fees += entry_fee
                        self.in_position = True
                        self.current_trade = RSITrade(
                            entry_time=ts,
                            entry_price=entry_price,
                            direction="BUY",
                            quantity=qty,
                            entry_rsi=round(current_rsi, 2),
                            entry_bar=self.current_bar,
                            entry_fee=round(entry_fee, 4),
                            highest_price=entry_price,
                        )

                        if event_path:
                            append_jsonl(event_path, {
                                "ts_utc": utc_now_iso(),
                                "action": "open_trade",
                                "symbol": self.product_id,
                                "direction": "BUY",
                                "entry_price": entry_price,
                                "entry_rsi": round(current_rsi, 2),
                                "quantity": round(qty, 6),
                                "entry_fee": round(entry_fee, 4),
                                "fee_bps_per_side": round(self.maker_fee_bps, 4),
                                "fee_model": self.fee_model,
                                "fee_source": self.fee_source,
                                "fee_tier": self.fee_tier,
                                "fill_model": self.fill_model,
                                "deploy_usd": round(deploy_usd, 2),
                                "cash_remaining": round(self.cash_usd, 2),
                            })

        self.current_bar += 1
        self.last_candle_time = ts


def save_state(path: Path, engine: CoinbaseRSIShadowEngine, runner: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": utc_now_iso(),
        "runner": runner or {},
        "state": engine.snapshot(),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def restore_engine_from_payload(
    engine: CoinbaseRSIShadowEngine,
    payload: dict[str, Any] | None,
    *,
    bootstrap_candles: list[dict[str, Any]],
) -> bool:
    state = payload.get("state") if isinstance(payload, dict) else None
    if not isinstance(state, dict):
        return False
    if str(state.get("product_id") or "").upper() != engine.product_id:
        return False

    config = state.get("config") if isinstance(state.get("config"), dict) else {}
    if not config.get("fee_model"):
        return False
    expected = {
        "rsi_period": engine.rsi_period,
        "oversold_threshold": engine.oversold_threshold,
        "overbought_threshold": engine.overbought_threshold,
        "profit_target_pct": engine.profit_target_pct,
        "stop_loss_pct": engine.stop_loss_pct,
        "max_hold_bars": engine.max_hold_bars,
        "deploy_pct": engine.deploy_pct,
        "candle_granularity": engine.candle_granularity,
    }
    for key, value in expected.items():
        if key in config and config.get(key) != value:
            return False

    engine.cash_usd = float(state.get("cash_usd") or engine.starting_cash_usd)
    engine.realized_net_usd = float(state.get("realized_net_usd") or 0.0)
    engine.realized_closes = int(state.get("realized_closes") or 0)
    engine.in_position = bool(state.get("in_position"))
    engine.current_bar = int(state.get("current_bar") or 0)
    engine.last_candle_time = int(state.get("last_candle_time") or 0)
    engine.total_fees = float(state.get("total_fees") or 0.0)
    engine.signals_generated = int(state.get("signals_generated") or 0)
    engine.price_history = [float(candle["close"]) for candle in bootstrap_candles[-(engine.rsi_period + 50):]]
    engine.candle_history = bootstrap_candles[-(engine.rsi_period + 50):]

    current_trade = state.get("current_trade") if isinstance(state.get("current_trade"), dict) else None
    if engine.in_position and current_trade:
        engine.current_trade = RSITrade(**current_trade)
        if engine.current_trade.highest_price <= 0:
            engine.current_trade.highest_price = engine.current_trade.entry_price
    else:
        engine.current_trade = None
    if engine.current_trade is None:
        engine.in_position = False
    return True


def fetch_recent_candles(
    client: CoinbaseAdvancedClient,
    product_id: str,
    granularity: str,
    count: int = 100,
    *,
    event_logger=None,
) -> list[dict]:
    """Fetch recent candles for bootstrapping."""
    resp = safe_market_candles_limit(
        client,
        product_id,
        granularity=granularity,
        limit=count,
        retries=4,
        base_delay=1.0,
    )
    if resp is None:
        if event_logger:
            event_logger(
                {
                    "ts_utc": utc_now_iso(),
                    "action": "rate_limit_skip_live_fetch",
                    "product": str(product_id).upper(),
                    "granularity": granularity,
                    "limit": int(count),
                }
            )
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
            "volume": float(c.get("volume", 0)),
        })
    return candles


def fetch_latest_candle(
    client: CoinbaseAdvancedClient,
    product_id: str,
    granularity: str,
    *,
    event_logger=None,
) -> dict | None:
    """Fetch the most recent completed candle."""
    candles = fetch_recent_candles(client, product_id, granularity, count=1, event_logger=event_logger)
    return candles[0] if candles else None


def apply_latest_candle(
    engine: CoinbaseRSIShadowEngine,
    latest: dict[str, Any] | None,
    *,
    runner_status: dict[str, Any],
    state_path: Path,
    event_path: Path,
) -> None:
    """Treat a missing latest candle as an idle poll, not a runner failure."""
    if latest is not None and int(latest["time"]) > engine.last_candle_time:
        engine.process_candle(latest, event_path=event_path)

    runner_status["heartbeat_at"] = utc_now_iso()
    runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
    runner_status["consecutive_exceptions"] = 0
    runner_status["last_exception_at"] = None
    runner_status["last_exception_type"] = ""
    runner_status["last_exception_message"] = ""
    save_state(state_path, engine, runner=runner_status)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Coinbase RSI mean reversion shadow runner")
    parser.add_argument("--product-id", default="ARB-USD")
    parser.add_argument("--rsi-period", type=int, default=7)
    parser.add_argument("--oversold", type=float, default=30.0)
    parser.add_argument("--overbought", type=float, default=70.0)
    parser.add_argument("--profit-target-pct", type=float, default=0.02)
    parser.add_argument("--stop-loss-pct", type=float, default=0.003)
    parser.add_argument("--max-hold-bars", type=int, default=48)
    parser.add_argument("--maker-fee-bps", type=float, default=5.0)
    parser.add_argument("--deploy-pct", type=float, default=0.9)
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--granularity", default="FIVE_MINUTE")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--state-path")
    parser.add_argument("--event-path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = CoinbaseAdvancedClient()

    state_path = Path(args.state_path or DEFAULT_STATE_PATH)
    event_path = Path(args.event_path or DEFAULT_EVENT_PATH)
    prior_payload = load_json(state_path)

    engine = CoinbaseRSIShadowEngine(
        product_id=args.product_id,
        starting_cash_usd=args.starting_cash,
        rsi_period=args.rsi_period,
        oversold_threshold=args.oversold,
        overbought_threshold=args.overbought,
        profit_target_pct=args.profit_target_pct,
        stop_loss_pct=args.stop_loss_pct,
        max_hold_bars=args.max_hold_bars,
        maker_fee_bps=args.maker_fee_bps,
        deploy_pct=args.deploy_pct,
        candle_granularity=args.granularity,
    )
    fee_tier = resolve_spot_fee_tier(client, fallback_taker_bps=float(args.maker_fee_bps))
    engine.apply_fee_tier(fee_tier)

    runner_status = {
        "pid": os.getpid(),
        "script": Path(__file__).name,
        "started_at": utc_now_iso(),
        "poll_seconds": max(1.0, float(args.poll_seconds)),
        "heartbeat_at": None,
        "last_successful_run_at": None,
        "consecutive_exceptions": 0,
        "last_exception_at": None,
        "last_exception_type": "",
        "last_exception_message": "",
        "fee_bps_per_side": round(engine.maker_fee_bps, 4),
        "fee_source": engine.fee_source,
        "fee_tier": engine.fee_tier,
    }

    # Bootstrap with historical candles to warm up RSI
    print(f"[{utc_now_iso()}] Bootstrapping RSI shadow for {args.product_id}...")
    bootstrap_candles = fetch_recent_candles(client, args.product_id, args.granularity, count=50, event_logger=lambda record: append_jsonl(event_path, record))
    print(f"  Bootstrapped with {len(bootstrap_candles)} historical candles")
    restored = restore_engine_from_payload(engine, prior_payload, bootstrap_candles=bootstrap_candles)
    if not restored:
        for c in bootstrap_candles[:-1]:  # Process all but the last one
            engine.process_candle(c, event_path=event_path)

    def run_once() -> None:
        latest = fetch_latest_candle(client, args.product_id, args.granularity, event_logger=lambda record: append_jsonl(event_path, record))
        apply_latest_candle(
            engine,
            latest,
            runner_status=runner_status,
            state_path=state_path,
            event_path=event_path,
        )

    try:
        run_once()
        if args.once:
            print(f"[{utc_now_iso()}] Run complete. Realized: ${engine.realized_net_usd:+.4f}, Trades: {engine.realized_closes}")
            return 0

        print(f"[{utc_now_iso()}] Shadow runner started. Polling every {args.poll_seconds}s")
        while True:
            time.sleep(max(1.0, float(args.poll_seconds)))
            try:
                run_once()
            except Exception as exc:
                runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
                runner_status["last_exception_at"] = utc_now_iso()
                runner_status["last_exception_type"] = type(exc).__name__
                runner_status["last_exception_message"] = str(exc)
                save_state(state_path, engine, runner=runner_status)
                log_runner_exception(event_path, exc, phase="loop_run_once")
    except Exception as exc:
        runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
        runner_status["last_exception_at"] = utc_now_iso()
        runner_status["last_exception_type"] = type(exc).__name__
        runner_status["last_exception_message"] = str(exc)
        save_state(state_path, engine, runner=runner_status)
        log_runner_exception(event_path, exc, phase="initial_run_once")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
