#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from coinbase_advanced_client import CoinbaseAdvancedClient
from live_penetration_lattice_shadow import append_jsonl, log_runner_exception, utc_now_iso
from tick_penetration_lattice_core import (
    REARM_VARIANTS,
    TickEngineState,
    TickRearmToken,
    TickTicket,
    bucket_start,
    timeframe_seconds,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = ROOT / "reports" / "coinbase_futures_shadow_btc_perp_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "coinbase_futures_shadow_btc_perp_events.jsonl"


def future_pnl_usd(direction: str, entry_price: float, exit_price: float, contract_size: float, contracts: int) -> float:
    qty = max(1, int(contracts)) * float(contract_size)
    if str(direction or "").upper() == "BUY":
        return (float(exit_price) - float(entry_price)) * qty
    return (float(entry_price) - float(exit_price)) * qty


class CoinbaseFuturesTickShadowEngine:
    def __init__(
        self,
        *,
        product_id: str,
        timeframe_name: str,
        step: float,
        max_open_per_side: int,
        variant_name: str,
        momentum_gate: bool = False,
        cooldown_bars: int = 0,
        sell_gap: int = 1,
        buy_gap: int = 1,
        contracts: int = 1,
        contract_size: float = 0.01,
        price_increment: float = 0.01,
        taker_fee_bps: float = 5.0,
    ) -> None:
        variant = REARM_VARIANTS.get(str(variant_name or ""))
        if variant is None:
            raise RuntimeError(f"Unknown rearm variant: {variant_name}")
        self.product_id = str(product_id or "").upper()
        self.timeframe_name = str(timeframe_name or "H1").upper()
        self.base_step_px = float(step)
        self.max_open_per_side = int(max_open_per_side)
        self.variant = variant
        self.momentum_gate = bool(momentum_gate)
        self.cooldown_bars = max(0, int(cooldown_bars))
        self.sell_gap = max(0, int(sell_gap))
        self.buy_gap = max(0, int(buy_gap))
        self.contracts = max(1, int(contracts))
        self.contract_size = float(contract_size)
        self.price_increment = float(price_increment)
        self.taker_fee_bps = float(taker_fee_bps)
        self.state = TickEngineState(symbol=self.product_id, timeframe=self.timeframe_name, mode="coinbase_futures_tick_stateful_rearm")

    def snapshot(self) -> dict[str, Any]:
        payload = asdict(self.state)
        payload["open_tickets"] = [
            {
                "direction": str(ticket.get("direction", "") or "").upper(),
                "entry_price": float(ticket.get("trigger_level", ticket.get("entry_price", 0.0)) or 0.0),
                "trigger_level": float(ticket.get("trigger_level", ticket.get("entry_price", 0.0)) or 0.0),
                "fill_price": float(ticket.get("fill_price", ticket.get("entry_fill_price", ticket.get("entry_price", 0.0))) or 0.0),
                "entry_fill_price": float(ticket.get("fill_price", ticket.get("entry_fill_price", ticket.get("entry_price", 0.0))) or 0.0),
                "opened_time": int(ticket.get("opened_time", 0) or 0),
                "opened_msc": int(ticket.get("opened_msc", 0) or 0),
                "level_idx": int(ticket.get("level_idx", 0) or 0),
                "from_rearm": bool(ticket.get("from_rearm", False)),
                "live_ticket": 0,
                "position_comment": str(ticket.get("position_comment", "") or ""),
            }
            for ticket in payload.get("open_tickets") or []
        ]
        payload["base_step_px"] = self.base_step_px
        payload["price_increment"] = self.price_increment
        payload["contract_size"] = self.contract_size
        payload["contracts"] = self.contracts
        payload["open_realism_mode"] = "tick_native"
        payload["close_realism_mode"] = "tick_native"
        payload["variant"] = self.variant.name
        payload["momentum_gate"] = self.momentum_gate
        payload["venue"] = "coinbase_advanced_futures_shadow"
        return payload

    def load_snapshot(self, payload: dict[str, Any]) -> None:
        converted = dict(payload or {})
        converted["open_tickets"] = [
            {
                "direction": str(ticket.get("direction", "") or "").upper(),
                "trigger_level": float(ticket.get("trigger_level", ticket.get("entry_price", 0.0)) or 0.0),
                "fill_price": float(ticket.get("fill_price", ticket.get("entry_fill_price", ticket.get("entry_price", 0.0))) or 0.0),
                "opened_time": int(ticket.get("opened_time", 0) or 0),
                "opened_msc": int(ticket.get("opened_msc", 0) or 0),
                "level_idx": int(ticket.get("level_idx", 0) or 0),
                "from_rearm": bool(ticket.get("from_rearm", False)),
                "live_ticket": 0,
                "position_comment": str(ticket.get("position_comment", "") or ""),
            }
            for ticket in (payload.get("open_tickets") or [])
        ]
        converted["rearm_tokens"] = [
            {
                "direction": str(token.get("direction", "") or "").upper(),
                "level": float(token.get("level", token.get("entry_price", 0.0)) or 0.0),
                "level_idx": int(token.get("level_idx", 0) or 0),
                "armed": bool(token.get("armed", False)),
                "cooldown_until_time": int(token.get("cooldown_until_time", 0) or 0),
            }
            for token in (payload.get("rearm_tokens") or [])
        ]
        for key, value in converted.items():
            if hasattr(self.state, key):
                setattr(self.state, key, value)

    def prime(self, anchor_price: float, anchor_time: int) -> None:
        anchor = float(anchor_price)
        self.state.anchor = anchor
        self.state.next_sell_level = anchor + self.base_step_px
        self.state.next_buy_level = anchor - self.base_step_px
        self.state.last_bar_time = bucket_start(int(anchor_time), self.timeframe_name)

    def _tick_mid(self, tick: dict[str, Any]) -> float:
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        return (bid + ask) / 2.0 if bid > 0.0 and ask > 0.0 else (ask if ask > 0.0 else bid)

    def _record_event(self, event_path: Path | None, action: str, tick: dict[str, Any], **extra: Any) -> None:
        if event_path is None:
            return
        payload = dict(extra)
        trigger_level = payload.get("trigger_level")
        if trigger_level is not None and "entry_price" not in payload:
            payload["entry_price"] = trigger_level
        if "exit_fill_price" in payload and "exit_price" not in payload:
            payload["exit_price"] = payload["exit_fill_price"]
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": action,
                "symbol": self.product_id,
                "mode": self.state.mode,
                "time": int(tick["time"]),
                "time_msc": int(tick["time_msc"]),
                "bid": float(tick["bid"]),
                "ask": float(tick["ask"]),
                **payload,
            },
        )

    def _update_token_arming(self, tokens: list[TickRearmToken], tick: dict[str, Any]) -> None:
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        tick_time = int(tick["time"])
        excursion_px = float(self.variant.excursion_levels) * self.base_step_px
        for token in tokens:
            if token.armed:
                continue
            if tick_time < int(token.cooldown_until_time or 0):
                continue
            if token.direction == "SELL":
                if bid <= float(token.level) - excursion_px:
                    token.armed = True
            else:
                if ask >= float(token.level) + excursion_px:
                    token.armed = True

    def _momentum_gate_allows(self, direction: str, level: float, tick: dict[str, Any]) -> bool:
        if not self.momentum_gate:
            return True
        if str(direction or "").upper() == "SELL":
            return float(tick["bid"]) < float(level)
        return float(tick["ask"]) > float(level)

    def _ticket_level_idx(self, direction: str, trigger_level: float) -> int:
        if self.base_step_px <= 0:
            return 0
        if str(direction or "").upper() == "SELL":
            return max(1, int(round((float(trigger_level) - float(self.state.anchor)) / self.base_step_px)))
        return max(1, int(round((float(self.state.anchor) - float(trigger_level)) / self.base_step_px)))

    def process_tick(self, tick: dict[str, Any], *, event_path: Path | None = None, emit: bool = True) -> None:
        if self.state.anchor == 0.0:
            self.prime(self._tick_mid(tick), int(tick["time"]))
        tickets = [TickTicket(**t) for t in self.state.open_tickets]
        tokens = [TickRearmToken(**t) for t in self.state.rearm_tokens]
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        tick_time = int(tick["time"])
        tick_msc = int(tick["time_msc"])
        self._update_token_arming(tokens, tick)

        open_sell_main = sum(1 for t in tickets if t.direction == "SELL" and not bool(t.from_rearm))
        open_buy_main = sum(1 for t in tickets if t.direction == "BUY" and not bool(t.from_rearm))

        while bid >= float(self.state.next_sell_level) and open_sell_main < int(self.max_open_per_side):
            trigger_level = float(self.state.next_sell_level)
            level_idx = self._ticket_level_idx("SELL", trigger_level)
            ticket_obj = TickTicket(
                direction="SELL",
                trigger_level=trigger_level,
                fill_price=bid,
                opened_time=tick_time,
                opened_msc=tick_msc,
                level_idx=level_idx,
                from_rearm=False,
            )
            tickets.append(ticket_obj)
            open_sell_main += 1
            if emit:
                self._record_event(event_path, "open_ticket", tick, direction="SELL", trigger_level=round(trigger_level, 6), fill_price=round(ticket_obj.fill_price, 6), level_idx=level_idx)
            self.state.next_sell_level += self.base_step_px

        while ask <= float(self.state.next_buy_level) and open_buy_main < int(self.max_open_per_side):
            trigger_level = float(self.state.next_buy_level)
            level_idx = self._ticket_level_idx("BUY", trigger_level)
            ticket_obj = TickTicket(
                direction="BUY",
                trigger_level=trigger_level,
                fill_price=ask,
                opened_time=tick_time,
                opened_msc=tick_msc,
                level_idx=level_idx,
                from_rearm=False,
            )
            tickets.append(ticket_obj)
            open_buy_main += 1
            if emit:
                self._record_event(event_path, "open_ticket", tick, direction="BUY", trigger_level=round(trigger_level, 6), fill_price=round(ticket_obj.fill_price, 6), level_idx=level_idx)
            self.state.next_buy_level -= self.base_step_px

        for direction in ("SELL", "BUY"):
            side_open = sum(1 for t in tickets if t.direction == direction and bool(t.from_rearm))
            for token in list(tokens):
                if token.direction != direction or not token.armed:
                    continue
                if side_open >= int(self.max_open_per_side):
                    break
                if not self._momentum_gate_allows(direction, token.level, tick):
                    continue
                if direction == "SELL" and bid < float(token.level):
                    continue
                if direction == "BUY" and ask > float(token.level):
                    continue
                ticket_obj = TickTicket(
                    direction=direction,
                    trigger_level=float(token.level),
                    fill_price=float(bid if direction == "SELL" else ask),
                    opened_time=tick_time,
                    opened_msc=tick_msc,
                    level_idx=int(token.level_idx),
                    from_rearm=True,
                )
                tickets.append(ticket_obj)
                tokens.remove(token)
                side_open += 1
                self.state.rearm_opens += 1
                if emit:
                    self._record_event(event_path, "open_ticket", tick, direction=direction, trigger_level=round(ticket_obj.trigger_level, 6), fill_price=round(ticket_obj.fill_price, 6), level_idx=int(token.level_idx), rearm_open=True, rearm_variant=self.variant.name)

        sells = sorted((t for t in tickets if t.direction == "SELL"), key=lambda t: t.trigger_level, reverse=True)
        while len(sells) > self.sell_gap and ask <= float(sells[self.sell_gap].trigger_level):
            outer = sells[0]
            close_fill = ask
            pnl = future_pnl_usd("SELL", outer.fill_price, close_fill, self.contract_size, self.contracts)
            # Taker fee on entry + exit: both sides charge fee on notional
            entry_fee = outer.fill_price * self.contract_size * self.contracts * self.taker_fee_bps / 10000.0
            exit_fee = close_fill * self.contract_size * self.contracts * self.taker_fee_bps / 10000.0
            net_pnl = pnl - entry_fee - exit_fee
            self.state.realized_net_usd += net_pnl
            self.state.realized_closes += 1
            self.state.total_fees_usd = getattr(self.state, "total_fees_usd", 0.0) + entry_fee + exit_fee
            tickets.remove(outer)
            if int(outer.level_idx or 0) >= int(self.variant.min_level_idx):
                tokens.append(
                    TickRearmToken(
                        direction="SELL",
                        level=float(outer.trigger_level),
                        level_idx=int(outer.level_idx),
                        cooldown_until_time=tick_time + (self.cooldown_bars * timeframe_seconds(self.timeframe_name)),
                    )
                )
            if emit:
                self._record_event(event_path, "close_ticket", tick, direction="SELL", trigger_level=round(outer.trigger_level, 6), entry_fill_price=round(outer.fill_price, 6), exit_fill_price=round(close_fill, 6), realized_pnl=round(net_pnl, 3), gross_pnl=round(pnl, 3), round_trip_fee=round(entry_fee + exit_fee, 4))
            sells = sorted((t for t in tickets if t.direction == "SELL"), key=lambda t: t.trigger_level, reverse=True)

        buys = sorted((t for t in tickets if t.direction == "BUY"), key=lambda t: t.trigger_level)
        while len(buys) > self.buy_gap and bid >= float(buys[self.buy_gap].trigger_level):
            outer = buys[0]
            close_fill = bid
            pnl = future_pnl_usd("BUY", outer.fill_price, close_fill, self.contract_size, self.contracts)
            # Taker fee on entry + exit: both sides charge fee on notional
            entry_fee = outer.fill_price * self.contract_size * self.contracts * self.taker_fee_bps / 10000.0
            exit_fee = close_fill * self.contract_size * self.contracts * self.taker_fee_bps / 10000.0
            net_pnl = pnl - entry_fee - exit_fee
            self.state.realized_net_usd += net_pnl
            self.state.realized_closes += 1
            self.state.total_fees_usd = getattr(self.state, "total_fees_usd", 0.0) + entry_fee + exit_fee
            tickets.remove(outer)
            if int(outer.level_idx or 0) >= int(self.variant.min_level_idx):
                tokens.append(
                    TickRearmToken(
                        direction="BUY",
                        level=float(outer.trigger_level),
                        level_idx=int(outer.level_idx),
                        cooldown_until_time=tick_time + (self.cooldown_bars * timeframe_seconds(self.timeframe_name)),
                    )
                )
            if emit:
                self._record_event(event_path, "close_ticket", tick, direction="BUY", trigger_level=round(outer.trigger_level, 6), entry_fill_price=round(outer.fill_price, 6), exit_fill_price=round(close_fill, 6), realized_pnl=round(net_pnl, 3), gross_pnl=round(pnl, 3), round_trip_fee=round(entry_fee + exit_fee, 4))
            buys = sorted((t for t in tickets if t.direction == "BUY"), key=lambda t: t.trigger_level)

        if not tickets:
            mark = self._tick_mid(tick)
            if abs(mark - float(self.state.anchor)) >= self.base_step_px:
                self.state.anchor = mark
                self.state.next_sell_level = mark + self.base_step_px
                self.state.next_buy_level = mark - self.base_step_px
                self.state.anchor_resets += 1

        self.state.open_tickets = [asdict(t) for t in tickets]
        self.state.rearm_tokens = [asdict(t) for t in tokens]
        self.state.last_tick_time = tick_time
        self.state.last_tick_msc = tick_msc
        self.state.last_bar_time = bucket_start(tick_time, self.timeframe_name)
        self.state.max_open_total = max(int(self.state.max_open_total or 0), len(tickets))


def save_state(path: Path, engine: CoinbaseFuturesTickShadowEngine, metadata: dict[str, Any], runner: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": utc_now_iso(),
        "metadata": metadata,
        "runner": runner or {},
        "symbols": {
            engine.product_id: engine.snapshot(),
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_state(path: Path, engine: CoinbaseFuturesTickShadowEngine) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    snap = (payload.get("symbols") or {}).get(engine.product_id)
    if not snap:
        return False
    engine.load_snapshot(snap or {})
    return True


def fetch_coinbase_tick(client: CoinbaseAdvancedClient, product_id: str) -> dict[str, Any]:
    payload = client.best_bid_ask([product_id])
    books = payload.get("pricebooks") or []
    if not books:
        raise RuntimeError(f"No pricebook returned for {product_id}")
    book = books[0]
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        raise RuntimeError(f"Incomplete best bid/ask for {product_id}")
    now_msc = int(time.time() * 1000)
    now_sec = int(now_msc // 1000)
    return {
        "time": now_sec,
        "time_msc": now_msc,
        "bid": float(bids[0]["price"]),
        "ask": float(asks[0]["price"]),
        "product_time": str(book.get("time") or ""),
    }


def load_product_details(client: CoinbaseAdvancedClient, product_id: str) -> dict[str, Any]:
    payload = client.get_product(product_id)
    if str(payload.get("product_type") or "").upper() != "FUTURE":
        raise RuntimeError(f"{product_id} is not a FUTURE product")
    return payload


def bootstrap(
    engine: CoinbaseFuturesTickShadowEngine,
    client: CoinbaseAdvancedClient,
    *,
    state_path: Path,
    event_path: Path,
    metadata: dict[str, Any],
    fresh_start: bool,
) -> None:
    if state_path.exists() and not fresh_start:
        if load_state(state_path, engine):
            return
    tick = fetch_coinbase_tick(client, engine.product_id)
    engine.prime((float(tick["bid"]) + float(tick["ask"])) / 2.0, int(tick["time"]))
    save_state(state_path, engine, metadata)
    append_jsonl(
        event_path,
        {
            "ts_utc": utc_now_iso(),
            "action": "fresh_start_prime" if fresh_start else "bootstrap_complete",
            "symbols": [engine.product_id],
            **metadata,
        },
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Coinbase Advanced futures shadow runner for BTC lattice benchmarking.")
    parser.add_argument("--product-id", default="BIP-20DEC30-CDE")
    parser.add_argument("--timeframe", default="H1", choices=["M1", "M5", "M15", "H1", "H4"])
    parser.add_argument("--step", type=float, required=True)
    parser.add_argument("--max-open-per-side", type=int, default=50)
    parser.add_argument("--raw-rearm-variant", default="rearm_lvl2_exc2")
    parser.add_argument("--raw-rearm-cooldown-bars", type=int, default=0)
    parser.add_argument("--raw-rearm-momentum-gate", action="store_true")
    parser.add_argument("--raw-sell-gap", type=int, default=1)
    parser.add_argument("--raw-buy-gap", type=int, default=1)
    parser.add_argument("--contracts", type=int, default=1)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--taker-fee-bps", type=float, default=5.0)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = CoinbaseAdvancedClient()
    product_id = str(args.product_id or "").upper()
    product = load_product_details(client, product_id)
    futures = product.get("future_product_details") or {}
    engine = CoinbaseFuturesTickShadowEngine(
        product_id=product_id,
        timeframe_name=str(args.timeframe).upper(),
        step=float(args.step),
        max_open_per_side=int(args.max_open_per_side),
        variant_name=str(args.raw_rearm_variant),
        momentum_gate=bool(args.raw_rearm_momentum_gate),
        cooldown_bars=int(args.raw_rearm_cooldown_bars),
        sell_gap=int(args.raw_sell_gap),
        buy_gap=int(args.raw_buy_gap),
        contracts=int(args.contracts),
        contract_size=float(futures.get("contract_size") or 0.01),
        price_increment=float(product.get("price_increment") or 0.01),
        taker_fee_bps=float(args.taker_fee_bps),
    )
    state_path = Path(args.state_path)
    event_path = Path(args.event_path)
    metadata = {
        "venue": "coinbase_advanced",
        "product_id": product_id,
        "product_type": str(product.get("product_type") or ""),
        "product_venue": str(product.get("product_venue") or ""),
        "display_name": str(product.get("display_name") or ""),
        "future_display_name": str(futures.get("display_name") or ""),
        "timeframe": str(args.timeframe).upper(),
        "step": float(args.step),
        "max_open_per_side": int(args.max_open_per_side),
        "raw_rearm_variant": str(args.raw_rearm_variant),
        "raw_rearm_cooldown_bars": int(args.raw_rearm_cooldown_bars),
        "raw_rearm_momentum_gate": bool(args.raw_rearm_momentum_gate),
        "raw_sell_gap": int(args.raw_sell_gap),
        "raw_buy_gap": int(args.raw_buy_gap),
        "contracts": int(args.contracts),
        "contract_size": float(futures.get("contract_size") or 0.01),
        "taker_fee_bps": float(args.taker_fee_bps),
        "tick_native": True,
        "live_open_realism_mode": "tick_native",
        "live_close_realism_mode": "tick_native",
        "shadow_only": True,
    }
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
    }
    bootstrap(engine, client, state_path=state_path, event_path=event_path, metadata=metadata, fresh_start=bool(args.fresh_start))

    def run_once() -> None:
        tick = fetch_coinbase_tick(client, product_id)
        if int(tick["time_msc"]) <= int(engine.state.last_tick_msc or 0):
            if runner_status is not None:
                runner_status["heartbeat_at"] = utc_now_iso()
                runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
                runner_status["consecutive_exceptions"] = 0
            save_state(state_path, engine, metadata, runner=runner_status)
            return
        engine.process_tick(tick, event_path=event_path, emit=True)
        runner_status["heartbeat_at"] = utc_now_iso()
        runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
        runner_status["consecutive_exceptions"] = 0
        save_state(state_path, engine, metadata, runner=runner_status)

    try:
        run_once()
        if args.once:
            return 0
        while True:
            time.sleep(max(1.0, float(args.poll_seconds)))
            try:
                run_once()
            except Exception as exc:
                runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
                runner_status["last_exception_at"] = utc_now_iso()
                runner_status["last_exception_type"] = type(exc).__name__
                runner_status["last_exception_message"] = str(exc)
                save_state(state_path, engine, metadata, runner=runner_status)
                log_runner_exception(event_path, exc, phase="loop_run_once")
    except Exception as exc:
        runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
        runner_status["last_exception_at"] = utc_now_iso()
        runner_status["last_exception_type"] = type(exc).__name__
        runner_status["last_exception_message"] = str(exc)
        save_state(state_path, engine, metadata, runner=runner_status)
        log_runner_exception(event_path, exc, phase="initial_run_once")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
