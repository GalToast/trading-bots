#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kraken_spot_client import to_float  # noqa: E402
from run_kraken_maker_microfill_calibrator import BookTop, infer_post_only_fill_proxy, maker_price_at_offset  # noqa: E402


DEFAULT_TAPE_PATH = REPORTS / "cache" / "honey_1s_dislocation_tape.json"
DEFAULT_JSON_PATH = REPORTS / "kraken_honey_trigger_staged_roundtrip_replay.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_honey_trigger_staged_roundtrip_replay.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_honey_trigger_staged_roundtrip_replay.md"
FILL_LIKE_RESULTS = {"hard_cross_fill_proxy", "probable_queue_depletion_fill_proxy"}


@dataclass(frozen=True)
class ReplayTick:
    index: int
    ts_utc: str
    elapsed_seconds: float
    bid: float
    ask: float
    bid_depth_usd: float
    ask_depth_usd: float
    spread_bps: float
    ask_down_bps: float
    bid_up_bps: float

    @property
    def book(self) -> BookTop:
        bid_size = self.bid_depth_usd / self.bid if self.bid > 0 else 0.0
        ask_size = self.ask_depth_usd / self.ask if self.ask > 0 else 0.0
        return BookTop(bid=self.bid, ask=self.ask, bid_size=bid_size, ask_size=ask_size, ts_utc=self.ts_utc)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_offsets(raw: str) -> list[float]:
    values: list[float] = []
    for part in str(raw or "").replace(";", ",").split(","):
        clean = part.strip()
        if clean:
            values.append(float(clean))
    return values


def load_ticks(path: Path) -> tuple[dict[str, Any], list[ReplayTick]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    ticks: list[ReplayTick] = []
    for index, raw in enumerate(payload.get("ticks") or []):
        if not isinstance(raw, dict):
            continue
        bid = to_float(raw.get("bid"))
        ask = to_float(raw.get("ask"))
        if bid <= 0.0 or ask <= 0.0:
            continue
        ticks.append(
            ReplayTick(
                index=index,
                ts_utc=str(raw.get("ts") or ""),
                elapsed_seconds=to_float(raw.get("s") if raw.get("s") is not None else index),
                bid=bid,
                ask=ask,
                bid_depth_usd=to_float(raw.get("bid_depth_usd")),
                ask_depth_usd=to_float(raw.get("ask_depth_usd")),
                spread_bps=to_float(raw.get("spread_bps")),
                ask_down_bps=to_float(raw.get("ask_down_bps")),
                bid_up_bps=to_float(raw.get("bid_up_bps")),
            )
        )
    return payload, ticks


def net_roundtrip_bps(entry_price: float, exit_price: float, fee_bps_per_side: float) -> float:
    if entry_price <= 0.0 or exit_price <= 0.0:
        return 0.0
    return ((exit_price - entry_price) / entry_price * 10000.0) - (2.0 * float(fee_bps_per_side))


def minimum_exit_price(entry_price: float, fee_bps_per_side: float, min_exit_net_bps: float) -> float:
    if entry_price <= 0.0:
        return 0.0
    hurdle_bps = (2.0 * float(fee_bps_per_side)) + float(min_exit_net_bps)
    return entry_price * (1.0 + (hurdle_bps / 10000.0))


def price_above_ask_bps(price: float, ask: float) -> float:
    if price <= 0.0 or ask <= 0.0:
        return 0.0
    return max(0.0, ((price - ask) / ask) * 10000.0)


def depth_ok(tick: ReplayTick, notional_usd: float) -> bool:
    return notional_usd <= 0.0 or (tick.bid_depth_usd >= notional_usd and tick.ask_depth_usd >= notional_usd)


def is_entry_trigger(tick: ReplayTick, args: argparse.Namespace) -> bool:
    if tick.spread_bps < float(args.min_spread_bps):
        return False
    if not depth_ok(tick, float(args.depth_notional_usd)):
        return False
    mode = str(args.entry_trigger_mode or "ask_down").lower()
    ask_hit = tick.ask_down_bps >= float(args.min_entry_ask_down_bps)
    bid_hit = tick.bid_up_bps >= float(args.min_entry_bid_up_bps)
    if mode == "either":
        return ask_hit or bid_hit
    if mode == "both":
        return ask_hit and bid_hit
    if mode == "bid_up":
        return bid_hit
    return ask_hit


def is_exit_trigger(tick: ReplayTick, args: argparse.Namespace) -> bool:
    if not depth_ok(tick, float(args.depth_notional_usd)):
        return False
    mode = str(args.exit_trigger_mode or "bid_up").lower()
    bid_hit = tick.bid_up_bps >= float(args.min_exit_bid_up_bps)
    ask_hit = tick.ask_down_bps >= float(args.min_exit_ask_down_bps)
    if mode == "either":
        return bid_hit or ask_hit
    if mode == "both":
        return bid_hit and ask_hit
    if mode == "ask_down":
        return ask_hit
    return bid_hit


def scan_fill(
    ticks: list[ReplayTick],
    *,
    start_index: int,
    side: str,
    order_price: float,
    initial: ReplayTick,
    ttl_seconds: float,
    ghost_penalty_bps: float,
) -> dict[str, Any]:
    deadline = initial.elapsed_seconds + float(ttl_seconds)
    last_checked: ReplayTick | None = None
    last_result = "unfilled_timeout"
    last_reason = "ttl_elapsed_without_fill_proxy"
    samples = 0
    for current in ticks[start_index + 1 :]:
        if current.elapsed_seconds > deadline:
            break
        samples += 1
        last_checked = current
        result, reason = infer_post_only_fill_proxy(side, order_price, initial.book, current.book, ghost_penalty_bps)
        last_result = result
        last_reason = reason
        if result in FILL_LIKE_RESULTS:
            return {
                "fill_like": True,
                "result": result,
                "reason": reason,
                "fill_index": current.index,
                "fill_ts": current.ts_utc,
                "elapsed_to_fill_seconds": round(current.elapsed_seconds - initial.elapsed_seconds, 3),
                "samples": samples,
            }
    return {
        "fill_like": False,
        "result": last_result if last_checked is not None else "no_future_samples",
        "reason": last_reason if last_checked is not None else "no_ticks_inside_ttl",
        "fill_index": None,
        "fill_ts": None,
        "elapsed_to_fill_seconds": 0.0,
        "samples": samples,
    }


def replay_trigger(ticks: list[ReplayTick], trigger: ReplayTick, offset: float, args: argparse.Namespace) -> dict[str, Any]:
    entry_price = maker_price_at_offset("buy", trigger.book, offset)
    entry = scan_fill(
        ticks,
        start_index=trigger.index,
        side="buy",
        order_price=entry_price,
        initial=trigger,
        ttl_seconds=float(args.entry_ttl_seconds),
        ghost_penalty_bps=float(args.ghost_penalty_bps),
    )
    row: dict[str, Any] = {
        "trigger_index": trigger.index,
        "trigger_ts": trigger.ts_utc,
        "offset": round(float(offset), 6),
        "trigger_bid": trigger.bid,
        "trigger_ask": trigger.ask,
        "trigger_spread_bps": round(trigger.spread_bps, 6),
        "trigger_bid_depth_usd": round(trigger.bid_depth_usd, 6),
        "trigger_ask_depth_usd": round(trigger.ask_depth_usd, 6),
        "trigger_ask_down_bps": round(trigger.ask_down_bps, 6),
        "trigger_bid_up_bps": round(trigger.bid_up_bps, 6),
        "entry_price": entry_price,
        "entry_fill_like": bool(entry["fill_like"]),
        "entry_result": entry["result"],
        "entry_reason": entry["reason"],
        "entry_fill_index": entry["fill_index"],
        "entry_fill_ts": entry["fill_ts"],
        "entry_elapsed_seconds": entry["elapsed_to_fill_seconds"],
        "entry_samples": entry["samples"],
        "exit_attempted": False,
        "exit_signal_found": False,
        "exit_fill_like": False,
        "roundtrip_fill_like": False,
        "net_roundtrip_bps": None,
        "roundtrip_success": False,
    }
    if not entry["fill_like"]:
        return row

    entry_fill_index = int(entry["fill_index"])
    entry_fill_tick = ticks[entry_fill_index]
    exit_floor = minimum_exit_price(entry_price, float(args.maker_fee_bps), float(args.min_exit_net_bps))
    exit_wait_deadline = entry_fill_tick.elapsed_seconds + float(args.exit_wait_seconds)
    for exit_signal in ticks[entry_fill_index + 1 :]:
        if exit_signal.elapsed_seconds > exit_wait_deadline:
            break
        if not is_exit_trigger(exit_signal, args):
            continue
        exit_price_raw = maker_price_at_offset("sell", exit_signal.book, offset)
        exit_price = max(exit_price_raw, exit_floor)
        floor_above_ask = price_above_ask_bps(exit_price, exit_signal.ask)
        row.update(
            {
                "exit_signal_found": True,
                "exit_signal_index": exit_signal.index,
                "exit_signal_ts": exit_signal.ts_utc,
                "exit_signal_bid": exit_signal.bid,
                "exit_signal_ask": exit_signal.ask,
                "exit_signal_bid_up_bps": round(exit_signal.bid_up_bps, 6),
                "exit_signal_ask_down_bps": round(exit_signal.ask_down_bps, 6),
                "exit_price_raw": exit_price_raw,
                "exit_floor": exit_floor,
                "exit_price": exit_price,
                "exit_floor_above_ask_bps": round(floor_above_ask, 6),
            }
        )
        if float(args.max_exit_floor_above_ask_bps) >= 0.0 and floor_above_ask > float(args.max_exit_floor_above_ask_bps):
            row.update({"exit_attempted": False, "exit_veto_reason": "exit_floor_too_far_above_ask"})
            continue
        row["exit_attempted"] = True
        exit_fill = scan_fill(
            ticks,
            start_index=exit_signal.index,
            side="sell",
            order_price=exit_price,
            initial=exit_signal,
            ttl_seconds=float(args.exit_ttl_seconds),
            ghost_penalty_bps=float(args.ghost_penalty_bps),
        )
        net_bps = net_roundtrip_bps(entry_price, exit_price, float(args.maker_fee_bps))
        row.update(
            {
                "exit_fill_like": bool(exit_fill["fill_like"]),
                "exit_result": exit_fill["result"],
                "exit_reason": exit_fill["reason"],
                "exit_fill_index": exit_fill["fill_index"],
                "exit_fill_ts": exit_fill["fill_ts"],
                "exit_elapsed_seconds": exit_fill["elapsed_to_fill_seconds"],
                "exit_samples": exit_fill["samples"],
                "net_roundtrip_bps": round(net_bps, 6),
                "profit_floor_cleared": net_bps >= float(args.min_exit_net_bps),
            }
        )
        row["roundtrip_fill_like"] = bool(row["entry_fill_like"] and row["exit_fill_like"])
        row["roundtrip_success"] = bool(row["roundtrip_fill_like"] and row["profit_floor_cleared"])
        return row
    if not row["exit_signal_found"]:
        row["exit_veto_reason"] = "exit_signal_not_found"
    return row


def summarize_rows(rows: list[dict[str, Any]], offsets: list[float]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for offset in offsets:
        subset = [row for row in rows if abs(to_float(row.get("offset")) - float(offset)) < 1e-9]
        if not subset:
            continue
        entry_fills = sum(1 for row in subset if row.get("entry_fill_like"))
        exit_signals = sum(1 for row in subset if row.get("exit_signal_found"))
        exit_attempts = sum(1 for row in subset if row.get("exit_attempted"))
        exit_fills = sum(1 for row in subset if row.get("exit_fill_like"))
        roundtrips = sum(1 for row in subset if row.get("roundtrip_success"))
        net_values = [to_float(row.get("net_roundtrip_bps")) for row in subset if row.get("roundtrip_success")]
        out.append(
            {
                "offset": round(float(offset), 6),
                "triggers": len(subset),
                "entry_fill_like": entry_fills,
                "entry_fill_rate": round(entry_fills / len(subset), 6),
                "exit_signals_after_entry": exit_signals,
                "exit_attempts": exit_attempts,
                "exit_fill_like": exit_fills,
                "exit_fill_rate_per_entry_fill": round(exit_fills / entry_fills, 6) if entry_fills else 0.0,
                "roundtrip_success": roundtrips,
                "roundtrip_success_rate": round(roundtrips / len(subset), 6),
                "avg_success_net_bps": round(sum(net_values) / len(net_values), 6) if net_values else 0.0,
            }
        )
    return out


def markdown_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Kraken Trigger Staged Roundtrip Replay",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Tape: `{payload['parameters']['tape_path']}`",
        f"- Product: `{payload['product_id']}`",
        "",
        "## Read",
        "",
        "- Public tape replay only. No private endpoints, validate calls, or live orders.",
        "- Tests long-only path: trigger -> BUY fill proxy -> exit signal/profit floor -> SELL fill proxy.",
        "- Entry-only fill is not treated as profit proof.",
        "",
        "## Summary",
        "",
        "| Offset | Triggers | Entry Fill % | Exit Attempts | Exit Fill % / Entry | Roundtrip Success | Avg Success Net bps |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload.get("summary_by_offset") or []:
        lines.append(
            "| {offset:.2f} | {triggers} | {entry:.2f} | {attempts} | {exit_rate:.2f} | {rt} | {net:.2f} |".format(
                offset=to_float(row.get("offset")),
                triggers=int(to_float(row.get("triggers"))),
                entry=to_float(row.get("entry_fill_rate")) * 100.0,
                attempts=int(to_float(row.get("exit_attempts"))),
                exit_rate=to_float(row.get("exit_fill_rate_per_entry_fill")) * 100.0,
                rt=int(to_float(row.get("roundtrip_success"))),
                net=to_float(row.get("avg_success_net_bps")),
            )
        )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    tape_payload, ticks = load_ticks(Path(args.tape_path))
    offsets = parse_offsets(args.offsets)
    triggers = [tick for tick in ticks if is_entry_trigger(tick, args)]
    rows: list[dict[str, Any]] = []
    for trigger in triggers:
        for offset in offsets:
            rows.append(replay_trigger(ticks, trigger, offset, args))
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "kraken_trigger_staged_roundtrip_replay",
        "product_id": str(args.product_id),
        "shadow_only": True,
        "parameters": {
            "tape_path": str(args.tape_path),
            "offsets": offsets,
            "min_spread_bps": float(args.min_spread_bps),
            "depth_notional_usd": float(args.depth_notional_usd),
            "entry_trigger_mode": str(args.entry_trigger_mode),
            "min_entry_ask_down_bps": float(args.min_entry_ask_down_bps),
            "min_entry_bid_up_bps": float(args.min_entry_bid_up_bps),
            "exit_trigger_mode": str(args.exit_trigger_mode),
            "min_exit_bid_up_bps": float(args.min_exit_bid_up_bps),
            "min_exit_ask_down_bps": float(args.min_exit_ask_down_bps),
            "entry_ttl_seconds": float(args.entry_ttl_seconds),
            "exit_wait_seconds": float(args.exit_wait_seconds),
            "exit_ttl_seconds": float(args.exit_ttl_seconds),
            "maker_fee_bps": float(args.maker_fee_bps),
            "min_exit_net_bps": float(args.min_exit_net_bps),
            "ghost_penalty_bps": float(args.ghost_penalty_bps),
            "max_exit_floor_above_ask_bps": float(args.max_exit_floor_above_ask_bps),
        },
        "source_tape": {
            "generated": tape_payload.get("generated"),
            "total_ticks": len(ticks),
            "reported_triggered_count": tape_payload.get("triggered_count"),
        },
        "trigger_count": len(triggers),
        "summary_by_offset": summarize_rows(rows, offsets),
        "events": rows,
        "read": "Entry-only fill rates are scouting evidence. Live-readiness requires repeated roundtrip_success on BUY-entry then SELL-exit above fees/profit floor.",
    }
    Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if args.csv_path:
        Path(args.csv_path).parent.mkdir(parents=True, exist_ok=True)
        fieldnames = sorted({key for row in rows for key in row.keys()})
        with Path(args.csv_path).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    if args.md_path:
        Path(args.md_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md_path).write_text(markdown_report(payload), encoding="utf-8")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay a Kraken trigger tape as long-only staged BUY-entry/SELL-exit proof.")
    parser.add_argument("--tape-path", type=Path, default=DEFAULT_TAPE_PATH)
    parser.add_argument("--product-id", default="HONEY-USD")
    parser.add_argument("--offsets", default="0.10,0.25,0.50")
    parser.add_argument("--min-spread-bps", type=float, default=150.0)
    parser.add_argument("--depth-notional-usd", type=float, default=15.0)
    parser.add_argument("--entry-trigger-mode", choices=["ask_down", "bid_up", "either", "both"], default="ask_down")
    parser.add_argument("--min-entry-ask-down-bps", type=float, default=20.0)
    parser.add_argument("--min-entry-bid-up-bps", type=float, default=20.0)
    parser.add_argument("--exit-trigger-mode", choices=["bid_up", "ask_down", "either", "both"], default="bid_up")
    parser.add_argument("--min-exit-bid-up-bps", type=float, default=20.0)
    parser.add_argument("--min-exit-ask-down-bps", type=float, default=20.0)
    parser.add_argument("--entry-ttl-seconds", type=float, default=10.0)
    parser.add_argument("--exit-wait-seconds", type=float, default=30.0)
    parser.add_argument("--exit-ttl-seconds", type=float, default=10.0)
    parser.add_argument("--maker-fee-bps", type=float, default=25.0)
    parser.add_argument("--min-exit-net-bps", type=float, default=20.0)
    parser.add_argument("--ghost-penalty-bps", type=float, default=5.0)
    parser.add_argument("--max-exit-floor-above-ask-bps", type=float, default=100.0)
    parser.add_argument("--json-path", type=Path, default=DEFAULT_JSON_PATH)
    parser.add_argument("--csv-path", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--md-path", type=Path, default=DEFAULT_MD_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = run(args)
    print(
        json.dumps(
            {
                "json_path": str(args.json_path),
                "trigger_count": payload["trigger_count"],
                "summary_by_offset": payload["summary_by_offset"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
