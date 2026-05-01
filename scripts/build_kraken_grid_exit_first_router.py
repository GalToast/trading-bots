#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_spot_numeraire_accumulation_board import product_id_for_pair  # noqa: E402
from kraken_config import DEFAULT_MAKER_FEE_BPS  # noqa: E402
from kraken_spot_client import KrakenPair, KrakenSpotClient, parse_pair, parse_ticker  # noqa: E402
from run_kraken_grid_shadow_tape import TradePrint, parse_trades  # noqa: E402
from run_kraken_vulture_trigger_tape import parse_book, spread_bps  # noqa: E402


DEFAULT_JSON_PATH = REPORTS / "kraken_grid_exit_first_router.json"
DEFAULT_MD_PATH = REPORTS / "kraken_grid_exit_first_router.md"


@dataclass(frozen=True)
class ReplayResult:
    entry_ok: bool
    exit_ok: bool
    entry_qty: float
    exit_qty: float
    entry_notional: float
    exit_notional: float
    entry_trade_count: int
    exit_trade_count: int
    entry_first_ts: float
    exit_first_ts: float
    reason: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_usd_pairs(client: KrakenSpotClient) -> list[KrakenPair]:
    pairs: list[KrakenPair] = []
    for rest_pair, payload in client.asset_pairs().items():
        if not isinstance(payload, dict):
            continue
        pair = parse_pair(str(rest_pair), payload)
        if pair is None:
            continue
        if pair.quote != "USD" or pair.status.lower() not in {"online", "post_only", ""}:
            continue
        pairs.append(pair)
    return pairs


def rank_by_ticker_volume(client: KrakenSpotClient, pairs: list[KrakenPair], *, top_n: int) -> list[tuple[float, KrakenPair]]:
    by_rest = {pair.rest_pair: pair for pair in pairs}
    rows: list[tuple[float, KrakenPair]] = []
    rest_pairs = list(by_rest)
    for start in range(0, len(rest_pairs), 80):
        chunk = rest_pairs[start : start + 80]
        ticker_payload = client.ticker(chunk)
        for rest_pair in chunk:
            payload = ticker_payload.get(rest_pair)
            pair = by_rest[rest_pair]
            if not isinstance(payload, dict):
                payload = ticker_payload.get(pair.altname)
            if not isinstance(payload, dict):
                continue
            top = parse_ticker(pair.rest_pair, pair.wsname, payload)
            if top is None:
                continue
            rows.append((top.volume_24h * top.last, pair))
    rows.sort(key=lambda row: row[0], reverse=True)
    return rows[: max(1, int(top_n))]


def recent_trades(trades: list[TradePrint], *, lookback_seconds: float, now: float) -> list[TradePrint]:
    cutoff = now - max(0.0, float(lookback_seconds))
    return [trade for trade in trades if trade.ts >= cutoff]


def replay_recent_roundtrip(
    trades: list[TradePrint],
    *,
    buy_price: float,
    target_price: float,
    allocation_usd: float,
    participation: float,
) -> ReplayResult:
    qty_needed = allocation_usd / buy_price if buy_price > 0.0 else 0.0
    if qty_needed <= 0.0:
        return ReplayResult(False, False, 0.0, 0.0, 0.0, 0.0, 0, 0, 0.0, 0.0, "zero_qty")
    effective_participation = min(1.0, max(0.0, float(participation)))
    if effective_participation <= 0.0:
        return ReplayResult(False, False, 0.0, 0.0, 0.0, 0.0, 0, 0, 0.0, 0.0, "zero_participation")

    entry_qty = 0.0
    entry_notional = 0.0
    entry_trade_count = 0
    entry_first_ts = 0.0
    exit_qty = 0.0
    exit_notional = 0.0
    exit_trade_count = 0
    exit_first_ts = 0.0
    in_position = False

    for trade in sorted(trades, key=lambda item: (item.ts, item.trade_id or 0)):
        if not in_position:
            if trade.side != "s" or trade.price > buy_price:
                continue
            take_qty = min(qty_needed - entry_qty, trade.size * effective_participation)
            if take_qty <= 0.0:
                continue
            if entry_first_ts <= 0.0:
                entry_first_ts = trade.ts
            entry_qty += take_qty
            entry_notional += take_qty * buy_price
            entry_trade_count += 1
            if entry_qty + 1e-12 >= qty_needed:
                in_position = True
            continue

        if trade.side != "b" or trade.price < target_price:
            continue
        take_qty = min(entry_qty - exit_qty, trade.size * effective_participation)
        if take_qty <= 0.0:
            continue
        if exit_first_ts <= 0.0:
            exit_first_ts = trade.ts
        exit_qty += take_qty
        exit_notional += take_qty * target_price
        exit_trade_count += 1
        if exit_qty + 1e-12 >= entry_qty:
            break

    entry_ok = entry_qty + 1e-12 >= qty_needed
    exit_ok = entry_ok and exit_qty + 1e-12 >= entry_qty
    if exit_ok:
        reason = "roundtrip_supported"
    elif entry_ok:
        reason = "entry_supported_exit_missing"
    elif entry_qty > 0.0:
        reason = "partial_entry_only"
    else:
        reason = "no_entry_touch"
    return ReplayResult(
        entry_ok=entry_ok,
        exit_ok=exit_ok,
        entry_qty=entry_qty,
        exit_qty=exit_qty,
        entry_notional=entry_notional,
        exit_notional=exit_notional,
        entry_trade_count=entry_trade_count,
        exit_trade_count=exit_trade_count,
        entry_first_ts=entry_first_ts,
        exit_first_ts=exit_first_ts,
        reason=reason,
    )


def product_row(
    *,
    product_id: str,
    pair: KrakenPair,
    volume_24h_usd: float,
    bid: float,
    ask: float,
    trades: list[TradePrint],
    args: argparse.Namespace,
    now: float,
) -> dict[str, Any]:
    mid = (bid + ask) / 2.0
    allocation = float(args.initial_capital) / max(1, int(args.levels))
    buy_price = mid * (1.0 - max(0.0, float(args.entry_offset_mult)) * float(args.spacing_bps) / 10000.0)
    target_price = buy_price * (1.0 + float(args.spacing_bps) / 10000.0)
    qty = allocation / buy_price if buy_price > 0.0 else 0.0
    blockers: list[str] = []
    if allocation < pair.cost_min:
        blockers.append("below_cost_min")
    if qty < pair.order_min:
        blockers.append("below_order_min")

    replay = replay_recent_roundtrip(
        trades,
        buy_price=buy_price,
        target_price=target_price,
        allocation_usd=allocation,
        participation=float(args.trade_volume_participation),
    )
    sell_touch_notional = sum(trade.price * trade.size for trade in trades if trade.side == "s" and trade.price <= buy_price)
    buy_target_notional = sum(trade.price * trade.size for trade in trades if trade.side == "b" and trade.price >= target_price)
    tape_low = min((trade.price for trade in trades), default=0.0)
    tape_high = max((trade.price for trade in trades), default=0.0)
    tape_range_bps = ((tape_high / tape_low) - 1.0) * 10000.0 if tape_low > 0.0 else 0.0
    gross_edge_bps = float(args.spacing_bps) - 2.0 * float(args.maker_fee_bps)
    row_spread_bps = spread_bps(bid, ask)
    score = 0.0
    score += 10_000.0 if replay.exit_ok else 0.0
    score += 1_000.0 if replay.entry_ok else 0.0
    score += min(500.0, sell_touch_notional)
    score += min(500.0, buy_target_notional)
    score += min(250.0, tape_range_bps)
    score -= row_spread_bps * 10.0
    score -= len(blockers) * 10_000.0
    if gross_edge_bps <= float(args.min_net_edge_bps):
        blockers.append("below_min_net_edge_bps")
        score -= 10_000.0
    roundtrip_seconds = replay.exit_first_ts - replay.entry_first_ts if replay.entry_first_ts > 0.0 and replay.exit_first_ts > 0.0 else 0.0
    if replay.exit_ok and float(args.max_roundtrip_seconds) > 0.0 and roundtrip_seconds > float(args.max_roundtrip_seconds):
        blockers.append("stale_roundtrip")
        score -= 10_000.0
    roundtrip_exit_age = now - replay.exit_first_ts if replay.exit_first_ts > 0.0 else 0.0
    roundtrip_entry_age = now - replay.entry_first_ts if replay.entry_first_ts > 0.0 else 0.0
    if replay.exit_ok and float(args.max_signal_age_seconds) > 0.0 and roundtrip_exit_age > float(args.max_signal_age_seconds):
        blockers.append("stale_signal")
        score -= 10_000.0
    if roundtrip_seconds > 0.0:
        score -= min(1_000.0, roundtrip_seconds)
    if roundtrip_exit_age > 0.0:
        score -= min(1_000.0, roundtrip_exit_age)

    return {
        "product_id": product_id,
        "rest_pair": pair.rest_pair,
        "volume_24h_usd": round(volume_24h_usd, 2),
        "bid": bid,
        "ask": ask,
        "spread_bps": round(row_spread_bps, 6),
        "buy_price": round(buy_price, pair.pair_decimals),
        "target_price": round(target_price, pair.pair_decimals),
        "allocation_usd": round(allocation, 8),
        "qty": round(qty, pair.lot_decimals),
        "gross_edge_bps_after_maker_fees": round(gross_edge_bps, 6),
        "recent_trades": len(trades),
        "recent_sell_touch_notional": round(sell_touch_notional, 8),
        "recent_buy_target_notional": round(buy_target_notional, 8),
        "recent_tape_low": tape_low,
        "recent_tape_high": tape_high,
        "recent_tape_range_bps": round(tape_range_bps, 6),
        "roundtrip_entry_ok": replay.entry_ok,
        "roundtrip_exit_ok": replay.exit_ok,
        "roundtrip_reason": replay.reason,
        "roundtrip_entry_notional": round(replay.entry_notional, 8),
        "roundtrip_exit_notional": round(replay.exit_notional, 8),
        "roundtrip_entry_trade_count": replay.entry_trade_count,
        "roundtrip_exit_trade_count": replay.exit_trade_count,
        "roundtrip_seconds_to_exit": round(roundtrip_seconds, 3),
        "roundtrip_entry_age_seconds": round(roundtrip_entry_age, 3),
        "roundtrip_exit_age_seconds": round(roundtrip_exit_age, 3),
        "blockers": blockers,
        "score": round(score, 6),
    }


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = payload.get("rows") or []
    lines = [
        "# Kraken Grid Exit-First Router",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Lookback seconds: `{payload.get('lookback_seconds')}`",
        f"- Spacing bps: `{payload.get('spacing_bps')}`",
        f"- Allocation per grid level: `${payload.get('allocation_usd')}`",
        f"- Max signal age seconds: `{payload.get('max_signal_age_seconds')}`",
        f"- Fire candidates: `{payload.get('fire_candidates')}`",
        "",
        "| Rank | Product | Spread bps | Trades | Entry | Exit | Buy | Target | Entry $ | Exit $ | Reason | Blockers | Score |",
        "|---:|---|---:|---:|---|---|---:|---:|---:|---:|---|---|---:|",
    ]
    for idx, row in enumerate(rows[:50], start=1):
        lines.append(
            "| {idx} | {product_id} | {spread_bps:.3f} | {recent_trades} | {entry} | {exit} | {buy_price} | {target_price} | {entry_notional:.2f} | {exit_notional:.2f} | {reason} | {blockers} | {score:.2f} |".format(
                idx=idx,
                product_id=row["product_id"],
                spread_bps=float(row["spread_bps"]),
                recent_trades=row["recent_trades"],
                entry="Y" if row["roundtrip_entry_ok"] else "n",
                exit="Y" if row["roundtrip_exit_ok"] else "n",
                buy_price=row["buy_price"],
                target_price=row["target_price"],
                entry_notional=float(row["roundtrip_entry_notional"]),
                exit_notional=float(row["roundtrip_exit_notional"]),
                reason=row["roundtrip_reason"],
                blockers=",".join(row.get("blockers") or []),
                score=float(row["score"]),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rank Kraken USD spot products for exit-first grid shadow routing.")
    parser.add_argument("--top-n-volume", type=int, default=60)
    parser.add_argument("--lookback-seconds", type=float, default=900.0)
    parser.add_argument("--trade-count", type=int, default=1000)
    parser.add_argument("--spacing-bps", type=float, default=52.0)
    parser.add_argument("--levels", type=int, default=10)
    parser.add_argument("--entry-offset-mult", type=float, default=0.0)
    parser.add_argument("--initial-capital", type=float, default=50.0)
    parser.add_argument("--maker-fee-bps", type=float, default=DEFAULT_MAKER_FEE_BPS)
    parser.add_argument("--min-net-edge-bps", type=float, default=0.0)
    parser.add_argument("--max-spread-bps", type=float, default=25.0)
    parser.add_argument("--min-recent-trades", type=int, default=3)
    parser.add_argument("--max-roundtrip-seconds", type=float, default=0.0)
    parser.add_argument("--max-signal-age-seconds", type=float, default=0.0)
    parser.add_argument("--trade-volume-participation", type=float, default=1.0)
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def build_payload(client: KrakenSpotClient, args: argparse.Namespace) -> dict[str, Any]:
    pairs = load_usd_pairs(client)
    ranked = rank_by_ticker_volume(client, pairs, top_n=int(args.top_n_volume))
    now = time.time()
    rows: list[dict[str, Any]] = []
    for volume_24h_usd, pair in ranked:
        product_id = product_id_for_pair(pair)
        try:
            book = parse_book(client.depth(pair.rest_pair, count=20))
            if book is None:
                continue
            if spread_bps(book.bid, book.ask) > float(args.max_spread_bps):
                continue
            trades = recent_trades(
                parse_trades(client.trades(pair.rest_pair, count=int(args.trade_count)), rest_pair=pair.rest_pair),
                lookback_seconds=float(args.lookback_seconds),
                now=now,
            )
        except Exception as exc:
            rows.append(
                {
                    "product_id": product_id,
                    "rest_pair": pair.rest_pair,
                    "blockers": [f"fetch_error:{type(exc).__name__}"],
                    "score": -100000.0,
                    "roundtrip_entry_ok": False,
                    "roundtrip_exit_ok": False,
                    "roundtrip_reason": "fetch_error",
                    "spread_bps": 0.0,
                    "recent_trades": 0,
                    "buy_price": 0.0,
                    "target_price": 0.0,
                    "roundtrip_entry_notional": 0.0,
                    "roundtrip_exit_notional": 0.0,
                }
            )
            continue
        if len(trades) < int(args.min_recent_trades):
            continue
        rows.append(
            product_row(
                product_id=product_id,
                pair=pair,
                volume_24h_usd=volume_24h_usd,
                bid=book.bid,
                ask=book.ask,
                trades=trades,
                args=args,
                now=now,
            )
        )
    fire_rows = [row for row in rows if row.get("roundtrip_exit_ok") and not row.get("blockers")]
    rows.sort(
        key=lambda row: (
            bool(row.get("roundtrip_exit_ok") and not row.get("blockers")),
            bool(row.get("roundtrip_exit_ok")),
            bool(row.get("roundtrip_entry_ok")),
            float(row.get("score", 0.0)),
        ),
        reverse=True,
    )
    return {
        "generated_at": utc_now_iso(),
        "top_n_volume": int(args.top_n_volume),
        "lookback_seconds": float(args.lookback_seconds),
        "spacing_bps": float(args.spacing_bps),
        "levels": int(args.levels),
        "entry_offset_mult": float(args.entry_offset_mult),
        "initial_capital": float(args.initial_capital),
        "allocation_usd": round(float(args.initial_capital) / max(1, int(args.levels)), 8),
        "maker_fee_bps": float(args.maker_fee_bps),
        "min_net_edge_bps": float(args.min_net_edge_bps),
        "max_spread_bps": float(args.max_spread_bps),
        "max_roundtrip_seconds": float(args.max_roundtrip_seconds),
        "max_signal_age_seconds": float(args.max_signal_age_seconds),
        "rows_scored": len(rows),
        "fire_candidates": len(fire_rows),
        "best_product": fire_rows[0]["product_id"] if fire_rows else (rows[0]["product_id"] if rows else ""),
        "best_is_fire_candidate": bool(fire_rows),
        "rows": rows,
    }


def main() -> None:
    args = parse_args()
    client = KrakenSpotClient()
    payload = build_payload(client, args)
    json_path = Path(args.json_path)
    md_path = Path(args.md_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(md_path, payload)
    print(json.dumps({k: payload[k] for k in ("rows_scored", "fire_candidates", "best_product", "best_is_fire_candidate")}, indent=2))


if __name__ == "__main__":
    main()
