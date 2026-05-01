#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import mt5_terminal_guard


def describe_retcode(retcode: Any) -> str:
    try:
        normalized = int(retcode)
    except Exception:
        return str(retcode)
    names = [
        name
        for name in dir(mt5)
        if name.startswith("TRADE_RETCODE_") and int(getattr(mt5, name, -1) or -1) == normalized
    ]
    label = "|".join(sorted(names))
    if not label:
        return str(normalized)
    return f"{normalized}({label})"


def parse_int_set(values: list[str]) -> set[int]:
    parsed: set[int] = set()
    for value in values:
        try:
            parsed.add(int(value))
        except Exception:
            continue
    return parsed


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def position_matches(
    pos: Any,
    *,
    tickets: set[int],
    magics: set[int],
    symbols: set[str],
    comment_contains: str,
) -> bool:
    ticket = int(getattr(pos, "ticket", 0) or 0)
    magic = int(getattr(pos, "magic", 0) or 0)
    symbol = normalize_text(getattr(pos, "symbol", "")).upper()
    comment = normalize_text(getattr(pos, "comment", ""))

    if tickets and ticket not in tickets:
        return False
    if magics and magic not in magics:
        return False
    if symbols and symbol not in symbols:
        return False
    if comment_contains and comment_contains.lower() not in comment.lower():
        return False
    return bool(tickets or magics or symbols or comment_contains)


def select_positions(
    positions: list[Any],
    *,
    tickets: set[int],
    magics: set[int],
    symbols: set[str],
    comment_contains: str,
) -> list[Any]:
    return [
        pos
        for pos in positions
        if position_matches(
            pos,
            tickets=tickets,
            magics=magics,
            symbols=symbols,
            comment_contains=comment_contains,
        )
    ]


def format_position(pos: Any) -> str:
    side = "SELL" if int(getattr(pos, "type", 0) or 0) == 1 else "BUY"
    return (
        f"ticket={int(getattr(pos, 'ticket', 0) or 0)} "
        f"symbol={normalize_text(getattr(pos, 'symbol', '')) or '-'} "
        f"magic={int(getattr(pos, 'magic', 0) or 0)} "
        f"side={side} vol={float(getattr(pos, 'volume', 0.0) or 0.0):.2f} "
        f"open={float(getattr(pos, 'price_open', 0.0) or 0.0):.5f} "
        f"pnl={float(getattr(pos, 'profit', 0.0) or 0.0):+.2f} "
        f"comment={normalize_text(getattr(pos, 'comment', '')) or '-'}"
    )


def close_position(pos: Any) -> tuple[bool, str]:
    tick = mt5.symbol_info_tick(pos.symbol)
    if not tick:
        return False, "no_tick"

    if int(getattr(pos, "type", 0) or 0) == 0:
        close_type = mt5.ORDER_TYPE_SELL
        price = float(getattr(tick, "bid", 0.0) or 0.0)
    else:
        close_type = mt5.ORDER_TYPE_BUY
        price = float(getattr(tick, "ask", 0.0) or 0.0)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": int(getattr(pos, "ticket", 0) or 0),
        "symbol": str(getattr(pos, "symbol", "") or ""),
        "volume": float(getattr(pos, "volume", 0.0) or 0.0),
        "type": close_type,
        "price": price,
        "deviation": 20,
        "magic": int(getattr(pos, "magic", 0) or 0),
        "comment": "filtered_close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_FOK,
    }
    result = mt5.order_send(request)
    if result and getattr(result, "retcode", None) == mt5.TRADE_RETCODE_DONE:
        return True, "closed"
    if result:
        return False, (
            f"retcode={describe_retcode(getattr(result, 'retcode', 'unknown'))} "
            f"comment={normalize_text(getattr(result, 'comment', '')) or '-'}"
        )
    return False, "no_result"


def validate_expectations(
    matched: list[Any],
    *,
    expect_count: int | None,
) -> tuple[bool, str]:
    if expect_count is not None and len(matched) != expect_count:
        return (
            False,
            f"expected_match_count={expect_count} actual_match_count={len(matched)}",
        )
    return True, ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely close filtered MT5 positions; dry-run by default")
    parser.add_argument("--ticket", action="append", default=[], help="Position ticket to target; repeatable")
    parser.add_argument("--magic", action="append", default=[], help="Magic number to target; repeatable")
    parser.add_argument("--symbol", action="append", default=[], help="Symbol to target; repeatable")
    parser.add_argument("--comment-contains", default="", help="Only target positions whose comment contains this text")
    parser.add_argument(
        "--expect-count",
        type=int,
        default=None,
        help="Require the current matched position count to equal this value before continuing",
    )
    parser.add_argument("--apply", action="store_true", help="Actually send close orders; default is dry-run")
    args = parser.parse_args()

    tickets = parse_int_set(list(args.ticket or []))
    magics = parse_int_set(list(args.magic or []))
    symbols = {normalize_text(value).upper() for value in list(args.symbol or []) if normalize_text(value)}
    comment_contains = normalize_text(args.comment_contains)

    if not (tickets or magics or symbols or comment_contains):
        print("Refusing to run without at least one filter (--ticket/--magic/--symbol/--comment-contains).")
        return 2
    if args.expect_count is not None and args.expect_count < 0:
        print("Refusing to run with a negative --expect-count.")
        return 2
    if args.apply and args.expect_count is None:
        print("Refusing to apply without --expect-count. Dry-run first, then re-run with the confirmed expected count.")
        return 2

    mt5_ready, mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5, require_trade_allowed=bool(args.apply))
    if not mt5_ready:
        print(mt5_terminal_guard.failure_summary(mt5_connection))
        return 1

    try:
        positions = list(mt5.positions_get() or [])
        matched = select_positions(
            positions,
            tickets=tickets,
            magics=magics,
            symbols=symbols,
            comment_contains=comment_contains,
        )

        expectations_ok, expectation_detail = validate_expectations(
            matched,
            expect_count=args.expect_count,
        )

        print(
            f"mode={'apply' if args.apply else 'dry_run'} matched_positions={len(matched)} "
            f"expect_count={'-' if args.expect_count is None else args.expect_count}"
        )
        for pos in matched:
            print(f"  {format_position(pos)}")

        if not expectations_ok:
            print(f"expectation_failed detail={expectation_detail}")
            return 2

        if not args.apply:
            return 0

        failures = 0
        for pos in matched:
            ok, detail = close_position(pos)
            print(f"  close ticket={int(getattr(pos, 'ticket', 0) or 0)} ok={str(ok).lower()} detail={detail}")
            if not ok:
                failures += 1
                print("stopping_after_first_failure=true")
                break

        remaining_positions = list(mt5.positions_get() or [])
        remaining_matched = select_positions(
            remaining_positions,
            tickets=tickets,
            magics=magics,
            symbols=symbols,
            comment_contains=comment_contains,
        )
        print(f"post_apply_remaining_matches={len(remaining_matched)}")
        for pos in remaining_matched:
            print(f"  remaining {format_position(pos)}")
        return 0 if failures == 0 and not remaining_matched else 1
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
