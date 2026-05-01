#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from kraken_spot_client import KrakenSpotClient, parse_pair  # noqa: E402
from process_singleton import acquire_singleton  # noqa: E402


DEFAULT_BOARD_PATH = REPORTS / "kraken_maker_opportunity_board.json"
DEFAULT_STATE_PATH = REPORTS / "kraken_maker_spread_only_challenger_tape_state.json"
DEFAULT_EVENT_PATH = REPORTS / "kraken_maker_spread_only_challenger_tape.jsonl"
DEFAULT_LOCK_PATH = REPORTS / "locks" / "kraken_maker_spread_only_challenger_tape.lock"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_horizons(value: str) -> list[int]:
    horizons = sorted({int(float(item.strip())) for item in str(value or "").split(",") if item.strip()})
    return [horizon for horizon in horizons if horizon > 0]


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def spread_bps(bid: float, ask: float) -> float:
    mid = (bid + ask) / 2.0
    return ((ask - bid) / mid) * 10000.0 if mid > 0 else 0.0


def candidate_rows(
    board_rows: list[dict[str, Any]],
    *,
    min_board_spread_bps: float,
    max_board_mer: float,
    min_vol_24h_usd: float,
) -> list[dict[str, Any]]:
    rows = []
    for row in board_rows:
        if str(row.get("playbook") or "") != "maker_harvest":
            continue
        if to_float(row.get("spread_bps")) < min_board_spread_bps:
            continue
        if to_float(row.get("mer"), 999.0) > max_board_mer:
            continue
        if to_float(row.get("vol_24h_usd")) < min_vol_24h_usd:
            continue
        rows.append(row)
    return sorted(rows, key=lambda item: (to_float(item.get("spread_bps")), -to_float(item.get("mer"))), reverse=True)


def build_pair_map(client: KrakenSpotClient) -> dict[str, str]:
    pair_map: dict[str, str] = {}
    for rest_pair, payload in client.asset_pairs().items():
        if not isinstance(payload, dict):
            continue
        pair = parse_pair(str(rest_pair), payload)
        if not pair or pair.status != "online":
            continue
        if pair.quote not in {"USD", "USDT", "USDC"}:
            continue
        pair_map[f"{pair.base}-{pair.quote}"] = pair.rest_pair
    return pair_map


def fetch_ticks(client: KrakenSpotClient, product_ids: list[str], pair_map: dict[str, str]) -> dict[str, dict[str, Any]]:
    request_pairs: list[str] = []
    inverse: dict[str, str] = {}
    for product_id in product_ids:
        rest_pair = pair_map.get(product_id) or product_id.replace("-", "")
        request_pairs.append(rest_pair)
        inverse[rest_pair] = product_id
    ticks: dict[str, dict[str, Any]] = {}
    now = int(time.time())
    for idx in range(0, len(request_pairs), 45):
        payload = client.ticker(request_pairs[idx : idx + 45])
        for rest_key, row in payload.items():
            product_id = inverse.get(str(rest_key))
            if not product_id:
                for requested, candidate_product_id in inverse.items():
                    if requested in str(rest_key) or str(rest_key) in requested:
                        product_id = candidate_product_id
                        break
            if not product_id or not isinstance(row, dict):
                continue
            bid = to_float((row.get("b") or [None])[0])
            ask = to_float((row.get("a") or [None])[0])
            last = to_float((row.get("c") or [None])[0])
            if bid <= 0.0 or ask <= 0.0 or ask < bid:
                continue
            ticks[product_id] = {
                "bid": bid,
                "ask": ask,
                "last": last,
                "spread_bps": spread_bps(bid, ask),
                "ts": now,
            }
    return ticks


def make_entry(
    *,
    row: dict[str, Any],
    tick: dict[str, Any],
    now_iso: str,
    now_epoch: float,
    quote_usd: float,
    maker_fee_bps: float,
    horizons: list[int],
) -> dict[str, Any]:
    bid = to_float(tick.get("bid"))
    ask = to_float(tick.get("ask"))
    maker_rate = maker_fee_bps / 10000.0
    quantity = quote_usd / (bid * (1.0 + maker_rate)) if bid > 0.0 else 0.0
    entry_value = bid * quantity
    entry_fee = entry_value * maker_rate
    product_id = str(row.get("product_id") or "")
    return {
        "entry_id": f"{product_id}:{int(now_epoch)}",
        "product_id": product_id,
        "opened_at": now_iso,
        "opened_epoch": now_epoch,
        "status": "pending",
        "horizons_seconds": horizons,
        "completed_horizons": [],
        "fill_model": "assumed_maker_bid_fill",
        "fill_supported": False,
        "fill_evidence_method": "not_seen",
        "fill_evidence_at": "",
        "fill_evidence_age_seconds": 0.0,
        "quote_usd": round(quote_usd, 6),
        "quantity": quantity,
        "cost_usd": round(entry_value + entry_fee, 8),
        "entry_bid": bid,
        "entry_ask": ask,
        "entry_last": to_float(tick.get("last")),
        "entry_fee": round(entry_fee, 8),
        "maker_fee_bps": maker_fee_bps,
        "board_spread_bps": to_float(row.get("spread_bps")),
        "board_mer": to_float(row.get("mer")),
        "board_atr_12_bps": to_float(row.get("atr_12_bps")),
        "board_vol_24h_usd": to_float(row.get("vol_24h_usd")),
        "live_spread_bps": to_float(tick.get("spread_bps")),
        "entry_row": row,
    }


def fill_evidence(entry: dict[str, Any], tick: dict[str, Any], *, now_iso: str, now_epoch: float) -> dict[str, Any]:
    entry_bid = to_float(entry.get("entry_bid"))
    bid = to_float(tick.get("bid"))
    ask = to_float(tick.get("ask"))
    last = to_float(tick.get("last"))
    age_seconds = round(now_epoch - to_float(entry.get("opened_epoch")), 3)
    method = ""
    supported = False
    if entry_bid > 0.0 and last > 0.0 and last <= entry_bid:
        supported = True
        method = "last_trade_at_or_below_entry_bid"
    elif entry_bid > 0.0 and ask > 0.0 and ask <= entry_bid:
        supported = True
        method = "ask_crossed_entry_bid"
    elif entry_bid > 0.0 and bid > 0.0 and bid < entry_bid:
        supported = True
        method = "best_bid_moved_through_entry_bid"
    return {
        "fill_supported": supported,
        "fill_evidence_method": method or "not_seen",
        "fill_evidence_at": now_iso if supported else "",
        "fill_evidence_age_seconds": age_seconds if supported else 0.0,
        "fill_evidence_bid": bid,
        "fill_evidence_ask": ask,
        "fill_evidence_last": last,
    }


def refresh_fill_evidence(entry: dict[str, Any], tick: dict[str, Any], *, now_iso: str, now_epoch: float) -> tuple[dict[str, Any], bool]:
    if bool(entry.get("fill_supported")):
        return entry, False
    evidence = fill_evidence(entry, tick, now_iso=now_iso, now_epoch=now_epoch)
    if not evidence["fill_supported"]:
        entry.setdefault("fill_supported", False)
        entry.setdefault("fill_evidence_method", "not_seen")
        return entry, False
    entry.update(evidence)
    return entry, True


def calc_exit_metrics(
    entry: dict[str, Any],
    tick: dict[str, Any],
    *,
    maker_fee_bps: float,
    taker_fee_bps: float,
) -> dict[str, Any]:
    bid = to_float(tick.get("bid"))
    ask = to_float(tick.get("ask"))
    quantity = to_float(entry.get("quantity"))
    cost_usd = to_float(entry.get("cost_usd"))
    maker_rate = maker_fee_bps / 10000.0
    taker_rate = taker_fee_bps / 10000.0
    bid_exit_value = bid * quantity
    ask_exit_value = ask * quantity
    bid_taker_fee = bid_exit_value * taker_rate
    ask_maker_fee = ask_exit_value * maker_rate
    bid_taker_net = bid_exit_value - bid_taker_fee - cost_usd
    ask_maker_net = ask_exit_value - ask_maker_fee - cost_usd
    return {
        "exit_bid": bid,
        "exit_ask": ask,
        "exit_live_spread_bps": to_float(tick.get("spread_bps")),
        "bid_taker_exit_fee": round(bid_taker_fee, 8),
        "bid_taker_net_usd": round(bid_taker_net, 8),
        "bid_taker_net_pct_on_cost": round((bid_taker_net / cost_usd) * 100.0, 8) if cost_usd > 0 else 0.0,
        "ask_maker_exit_fee": round(ask_maker_fee, 8),
        "ask_maker_net_usd": round(ask_maker_net, 8),
        "ask_maker_net_pct_on_cost": round((ask_maker_net / cost_usd) * 100.0, 8) if cost_usd > 0 else 0.0,
    }


def load_state(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    state = payload.get("state") if isinstance(payload.get("state"), dict) else payload
    pending = state.get("pending") if isinstance(state.get("pending"), dict) else {}
    cooldowns = state.get("cooldowns") if isinstance(state.get("cooldowns"), dict) else {}
    return {
        "pending": {str(key): value for key, value in pending.items() if isinstance(value, dict)},
        "cooldowns": {str(key): to_float(value) for key, value in cooldowns.items()},
        "poll_count": int(to_float(state.get("poll_count"))),
        "last_action": str(state.get("last_action") or ""),
    }


def save_state(path: Path, state: dict[str, Any]) -> None:
    write_json(path, {"updated_at": utc_now_iso(), "state": state})


def mark_pending(
    *,
    state: dict[str, Any],
    ticks: dict[str, dict[str, Any]],
    now_iso: str,
    now_epoch: float,
    event_path: Path,
    maker_fee_bps: float,
    taker_fee_bps: float,
    stop_bid_taker_net_pct: float,
    min_harvest_net_pct: float,
) -> int:
    emitted = 0
    pending = state.get("pending") if isinstance(state.get("pending"), dict) else {}
    for entry_id, entry in list(pending.items()):
        product_id = str(entry.get("product_id") or "")
        tick = ticks.get(product_id)
        if not tick:
            continue
        metrics = calc_exit_metrics(entry, tick, maker_fee_bps=maker_fee_bps, taker_fee_bps=taker_fee_bps)
        entry, fill_changed = refresh_fill_evidence(entry, tick, now_iso=now_iso, now_epoch=now_epoch)
        if fill_changed:
            append_jsonl(
                event_path,
                {
                    "ts_utc": now_iso,
                    "action": "spread_only_challenger_fill_supported",
                    "entry_id": entry_id,
                    "product_id": product_id,
                    "entry_bid": to_float(entry.get("entry_bid")),
                    "age_seconds": to_float(entry.get("fill_evidence_age_seconds")),
                    "method": str(entry.get("fill_evidence_method") or ""),
                    "evidence_bid": to_float(entry.get("fill_evidence_bid")),
                    "evidence_ask": to_float(entry.get("fill_evidence_ask")),
                    "evidence_last": to_float(entry.get("fill_evidence_last")),
                },
            )
            emitted += 1
        if to_float(metrics.get("bid_taker_net_pct_on_cost")) <= stop_bid_taker_net_pct:
            event = {
                "ts_utc": now_iso,
                "action": "spread_only_challenger_stop",
                "entry_id": entry_id,
                "product_id": product_id,
                "age_seconds": round(now_epoch - to_float(entry.get("opened_epoch")), 3),
                "stop_bid_taker_net_pct": stop_bid_taker_net_pct,
                "fill_supported": bool(entry.get("fill_supported")),
                "fill_evidence_method": str(entry.get("fill_evidence_method") or "not_seen"),
                **metrics,
            }
            append_jsonl(event_path, event)
            pending.pop(entry_id, None)
            emitted += 1
            continue
        completed = {int(horizon) for horizon in entry.get("completed_horizons") or []}
        for horizon in entry.get("horizons_seconds") or []:
            horizon = int(horizon)
            if horizon in completed:
                continue
            age = now_epoch - to_float(entry.get("opened_epoch"))
            if age < horizon:
                continue
            event = {
                "ts_utc": now_iso,
                "action": "spread_only_challenger_mark",
                "entry_id": entry_id,
                "product_id": product_id,
                "horizon_seconds": horizon,
                "age_seconds": round(age, 3),
                "spread_harvest_clears": to_float(metrics.get("ask_maker_net_pct_on_cost")) >= min_harvest_net_pct,
                "min_harvest_net_pct": min_harvest_net_pct,
                "fill_supported": bool(entry.get("fill_supported")),
                "fill_evidence_method": str(entry.get("fill_evidence_method") or "not_seen"),
                "fill_evidence_age_seconds": to_float(entry.get("fill_evidence_age_seconds")),
                **metrics,
            }
            append_jsonl(event_path, event)
            completed.add(horizon)
            emitted += 1
        entry["completed_horizons"] = sorted(completed)
        expected = {int(horizon) for horizon in entry.get("horizons_seconds") or []}
        if expected and expected.issubset(completed):
            append_jsonl(
                event_path,
                {
                    "ts_utc": now_iso,
                    "action": "spread_only_challenger_complete",
                    "entry_id": entry_id,
                    "product_id": product_id,
                    "age_seconds": round(now_epoch - to_float(entry.get("opened_epoch")), 3),
                    "fill_supported": bool(entry.get("fill_supported")),
                    "fill_evidence_method": str(entry.get("fill_evidence_method") or "not_seen"),
                },
            )
            pending.pop(entry_id, None)
            emitted += 1
        else:
            pending[entry_id] = entry
    state["pending"] = pending
    return emitted


def open_candidates(
    *,
    state: dict[str, Any],
    rows: list[dict[str, Any]],
    ticks: dict[str, dict[str, Any]],
    now_iso: str,
    now_epoch: float,
    event_path: Path,
    max_new_entries: int,
    cooldown_seconds: float,
    min_live_spread_bps: float,
    quote_usd: float,
    maker_fee_bps: float,
    horizons: list[int],
) -> int:
    pending = state.get("pending") if isinstance(state.get("pending"), dict) else {}
    cooldowns = state.get("cooldowns") if isinstance(state.get("cooldowns"), dict) else {}
    open_products = {str(entry.get("product_id") or "") for entry in pending.values() if isinstance(entry, dict)}
    opened = 0
    for row in rows:
        if opened >= max_new_entries:
            break
        product_id = str(row.get("product_id") or "")
        if not product_id or product_id in open_products:
            continue
        if to_float(cooldowns.get(product_id)) > now_epoch:
            continue
        tick = ticks.get(product_id)
        if not tick or to_float(tick.get("spread_bps")) < min_live_spread_bps:
            continue
        entry = make_entry(
            row=row,
            tick=tick,
            now_iso=now_iso,
            now_epoch=now_epoch,
            quote_usd=quote_usd,
            maker_fee_bps=maker_fee_bps,
            horizons=horizons,
        )
        pending[str(entry["entry_id"])] = entry
        cooldowns[product_id] = now_epoch + cooldown_seconds
        append_jsonl(event_path, {"ts_utc": now_iso, "action": "spread_only_challenger_open", **entry})
        opened += 1
    state["pending"] = pending
    state["cooldowns"] = cooldowns
    return opened


def build_once(args: argparse.Namespace, client: KrakenSpotClient, pair_map: dict[str, str]) -> dict[str, Any]:
    board = load_json(Path(args.board_path))
    board_rows = [row for row in board.get("rows", []) if isinstance(row, dict)]
    candidates = candidate_rows(
        board_rows,
        min_board_spread_bps=float(args.min_board_spread_bps),
        max_board_mer=float(args.max_board_mer),
        min_vol_24h_usd=float(args.min_vol_24h_usd),
    )
    product_ids = sorted(
        {
            str(row.get("product_id") or "")
            for row in candidates
        }
        | {
            str(entry.get("product_id") or "")
            for entry in load_state(Path(args.state_path)).get("pending", {}).values()
            if isinstance(entry, dict)
        }
    )
    ticks = fetch_ticks(client, [product_id for product_id in product_ids if product_id], pair_map)
    state = load_state(Path(args.state_path))
    now_epoch = time.time()
    now_iso = utc_now_iso()
    state["poll_count"] = int(to_float(state.get("poll_count"))) + 1
    marks = mark_pending(
        state=state,
        ticks=ticks,
        now_iso=now_iso,
        now_epoch=now_epoch,
        event_path=Path(args.event_path),
        maker_fee_bps=float(args.maker_fee_bps),
        taker_fee_bps=float(args.taker_fee_bps),
        stop_bid_taker_net_pct=float(args.stop_bid_taker_net_pct),
        min_harvest_net_pct=float(args.min_harvest_net_pct),
    )
    opens = open_candidates(
        state=state,
        rows=candidates,
        ticks=ticks,
        now_iso=now_iso,
        now_epoch=now_epoch,
        event_path=Path(args.event_path),
        max_new_entries=max(0, int(args.max_new_entries)),
        cooldown_seconds=float(args.cooldown_seconds),
        min_live_spread_bps=float(args.min_live_spread_bps),
        quote_usd=float(args.quote_usd),
        maker_fee_bps=float(args.maker_fee_bps),
        horizons=parse_horizons(str(args.horizons_seconds)),
    )
    state["last_action"] = "opened_or_marked" if opens or marks else "no_action"
    save_state(Path(args.state_path), state)
    return {
        "generated_at": now_iso,
        "mode": "kraken_maker_spread_only_challenger_tape",
        "shadow_only": True,
        "passive_only": True,
        "fill_model": "assumed_maker_bid_fill",
        "board_candidates": len(candidates),
        "live_ticks": len(ticks),
        "opened": opens,
        "marks_or_stops": marks,
        "pending": len(state.get("pending") or {}),
        "parameters": {
            "min_board_spread_bps": float(args.min_board_spread_bps),
            "max_board_mer": float(args.max_board_mer),
            "min_live_spread_bps": float(args.min_live_spread_bps),
            "quote_usd": float(args.quote_usd),
            "maker_fee_bps": float(args.maker_fee_bps),
            "taker_fee_bps": float(args.taker_fee_bps),
            "stop_bid_taker_net_pct": float(args.stop_bid_taker_net_pct),
            "min_harvest_net_pct": float(args.min_harvest_net_pct),
            "fill_realism": "public_ticker_touch_or_cross_required_for_fill_supported",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Passive Kraken spread-only low-MER challenger tape.")
    parser.add_argument("--board-path", default=str(DEFAULT_BOARD_PATH))
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--min-board-spread-bps", type=float, default=300.0)
    parser.add_argument("--max-board-mer", type=float, default=2.0)
    parser.add_argument("--min-vol-24h-usd", type=float, default=0.0)
    parser.add_argument("--min-live-spread-bps", type=float, default=300.0)
    parser.add_argument("--quote-usd", type=float, default=4.0)
    parser.add_argument("--maker-fee-bps", type=float, default=25.0)
    parser.add_argument("--taker-fee-bps", type=float, default=40.0)
    parser.add_argument("--stop-bid-taker-net-pct", type=float, default=-0.50)
    parser.add_argument("--min-harvest-net-pct", type=float, default=0.10)
    parser.add_argument("--horizons-seconds", default="30,60,180,300")
    parser.add_argument("--cooldown-seconds", type=float, default=600.0)
    parser.add_argument("--max-new-entries", type=int, default=2)
    parser.add_argument("--poll-seconds", type=float, default=20.0)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    lease = acquire_singleton(
        DEFAULT_LOCK_PATH,
        scope="kraken_maker_spread_only_challenger_tape",
        metadata={"state_path": str(args.state_path), "event_path": str(args.event_path)},
    )
    if not lease.acquired:
        print(f"Another Kraken spread-only challenger watcher is already active at pid {lease.owner_pid}; exiting.")
        return
    with lease:
        client = KrakenSpotClient()
        pair_map = build_pair_map(client)
        while True:
            payload = build_once(args, client, pair_map)
            print(json.dumps(payload, indent=2, sort_keys=True))
            if args.once:
                return
            time.sleep(max(1.0, float(args.poll_seconds)))


if __name__ == "__main__":
    main()
