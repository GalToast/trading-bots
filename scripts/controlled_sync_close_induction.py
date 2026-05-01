from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import MetaTrader5 as mt5

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from mt5_config import BOT_COMMENT_PREFIX, BOT_MAGIC


def log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def get_v10_positions():
    positions = mt5.positions_get() or []
    managed = []
    for pos in positions:
        comment = str(getattr(pos, "comment", "") or "")
        magic = int(getattr(pos, "magic", 0) or 0)
        if magic != BOT_MAGIC:
            continue
        if not comment.startswith(BOT_COMMENT_PREFIX):
            continue
        managed.append(pos)
    return managed


def describe_position(pos) -> str:
    side = "BUY" if int(pos.type) == mt5.POSITION_TYPE_BUY else "SELL"
    return (
        f"ticket={int(pos.ticket)} symbol={pos.symbol} side={side} "
        f"volume={float(pos.volume):.2f} pnl=${float(pos.profit):+.2f} "
        f"comment={getattr(pos, 'comment', '')}"
    )


def close_position(pos, deviation: int) -> bool:
    tick = mt5.symbol_info_tick(pos.symbol)
    if not tick:
        log(f"close failed: no tick for {pos.symbol}")
        return False

    if int(pos.type) == mt5.POSITION_TYPE_BUY:
        price = tick.bid
        order_type = mt5.ORDER_TYPE_SELL
    else:
        price = tick.ask
        order_type = mt5.ORDER_TYPE_BUY

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pos.symbol,
        "volume": float(pos.volume),
        "type": order_type,
        "price": price,
        "position": int(pos.ticket),
        "deviation": deviation,
        "magic": BOT_MAGIC,
        "comment": f"{BOT_COMMENT_PREFIX} ControlledInduction",
        "type_time": mt5.ORDER_TIME_GTC,
    }

    for filling_mode in (
        mt5.ORDER_FILLING_FOK,
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_RETURN,
    ):
        request["type_filling"] = filling_mode
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log(
                "close done: "
                f"ticket={int(pos.ticket)} symbol={pos.symbol} pnl=${float(pos.profit):+.2f} "
                f"retcode={result.retcode} order={getattr(result, 'order', None)}"
            )
            return True
        if result is not None:
            log(
                "close attempt failed: "
                f"ticket={int(pos.ticket)} symbol={pos.symbol} retcode={result.retcode} "
                f"comment={getattr(result, 'comment', '')}"
            )
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Controlled benchmark induction: wait for exactly one losing V10 position "
            "and close it so the direct book goes flat."
        )
    )
    parser.add_argument(
        "--loss-threshold",
        type=float,
        default=-1.0,
        help="Only close when the single managed position profit is at or below this value.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=2.0,
        help="Polling interval while waiting for a qualifying setup.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=1800.0,
        help="Maximum time to wait before exiting without action.",
    )
    parser.add_argument(
        "--deviation",
        type=int,
        default=50,
        help="Allowed slippage in points for the close order.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the first qualifying setup but do not send the close order.",
    )
    args = parser.parse_args()

    if not mt5.initialize():
        log(f"MT5 initialize failed: {mt5.last_error()}")
        return 1

    start = time.time()
    last_signature = None
    last_count = None
    last_single_ticket = None
    last_single_bucket = None
    log(
        "armed for controlled induction: "
        f"loss_threshold=${args.loss_threshold:+.2f} poll={args.poll_seconds:.1f}s "
        f"timeout={args.timeout_seconds:.0f}s dry_run={args.dry_run}"
    )

    try:
        while True:
            elapsed = time.time() - start
            if elapsed > args.timeout_seconds:
                log("timeout reached without qualifying setup")
                return 2

            positions = get_v10_positions()
            if len(positions) != 1:
                if len(positions) != last_count:
                    log(f"waiting: managed_v10_positions={len(positions)}")
                    last_count = len(positions)
                    last_single_ticket = None
                    last_single_bucket = None
                time.sleep(args.poll_seconds)
                continue

            pos = positions[0]
            pnl = float(pos.profit)
            bucket = round(pnl / 5.0) if pnl >= 0 else round(pnl / 1.0)
            signature = (int(pos.ticket), bucket)
            if int(pos.ticket) != last_single_ticket or bucket != last_single_bucket:
                log(f"single-position setup: {describe_position(pos)}")
                last_single_ticket = int(pos.ticket)
                last_single_bucket = bucket
            last_count = 1

            if pnl > args.loss_threshold:
                time.sleep(args.poll_seconds)
                continue

            log(
                "qualifying loser detected: "
                f"{describe_position(pos)} threshold=${args.loss_threshold:+.2f}"
            )
            if args.dry_run:
                log("dry-run active: no close sent")
                return 0

            if close_position(pos, deviation=args.deviation):
                return 0

            log("close did not complete successfully")
            return 3
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())
