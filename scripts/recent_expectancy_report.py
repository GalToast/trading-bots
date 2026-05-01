from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from mt5_config import BOT_COMMENT_PREFIX, BOT_MAGIC, LOGIN, PASSWORD, SERVER


MODE_RE = re.compile(rf"^{re.escape(BOT_COMMENT_PREFIX)}-(SNIPER|SHOTGUN|REVERSION|MACHINE_GUN)\b")
VALID_MODES = ("SNIPER", "SHOTGUN", "REVERSION", "MACHINE_GUN", "UNKNOWN")


@dataclass
class ClosedTrade:
    position_id: int
    symbol: str
    mode: str
    open_time: datetime | None
    close_time: datetime | None
    entry_volume: float
    exit_volume: float
    net_pnl: float
    gross_profit: float
    commissions: float
    swap: float
    fee: float
    exit_comment: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recent realized expectancy report from MT5 history.")
    parser.add_argument("--lookback-days", type=int, default=7, help="Closed-trade lookback window.")
    parser.add_argument(
        "--history-days",
        type=int,
        default=14,
        help="History fetch depth used to reconstruct positions.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a text report.",
    )
    return parser.parse_args()


def deal_time(deal: Any) -> datetime:
    return datetime.fromtimestamp(int(getattr(deal, "time", 0) or 0))


def deal_comment(deal: Any) -> str:
    return str(getattr(deal, "comment", "") or "")


def deal_mode(deal: Any) -> str | None:
    match = MODE_RE.match(deal_comment(deal))
    return match.group(1) if match else None


def is_bot_deal(deal: Any) -> bool:
    comment = deal_comment(deal)
    magic = getattr(deal, "magic", None)
    return magic == BOT_MAGIC or comment.startswith(f"{BOT_COMMENT_PREFIX}-") or comment.startswith(f"{BOT_COMMENT_PREFIX} ")


def load_deals(history_days: int) -> list[Any]:
    now = datetime.now()
    start = now - timedelta(days=history_days)
    deals = mt5.history_deals_get(start, now) or []
    return sorted(deals, key=lambda item: (int(getattr(item, "time", 0) or 0), int(getattr(item, "ticket", 0) or 0)))


def current_open_position_ids() -> set[int]:
    ids: set[int] = set()
    for pos in mt5.positions_get() or []:
        comment = str(getattr(pos, "comment", "") or "")
        if getattr(pos, "magic", None) == BOT_MAGIC or comment.startswith(f"{BOT_COMMENT_PREFIX}-"):
            ids.add(int(getattr(pos, "ticket", 0) or 0))
    return ids


def reconstruct_closed_trades(lookback_days: int, history_days: int) -> list[ClosedTrade]:
    deals = load_deals(history_days)
    open_ids = current_open_position_ids()
    grouped: dict[int, list[Any]] = defaultdict(list)

    for deal in deals:
        position_id = int(getattr(deal, "position_id", 0) or 0)
        if position_id <= 0:
            continue
        grouped[position_id].append(deal)

    close_cutoff = datetime.now() - timedelta(days=lookback_days)
    closed_trades: list[ClosedTrade] = []

    for position_id, position_deals in grouped.items():
        bot_group = any(is_bot_deal(deal) for deal in position_deals)
        if not bot_group or position_id in open_ids:
            continue

        entry_deals = [deal for deal in position_deals if int(getattr(deal, "entry", -1)) == 0]
        exit_deals = [deal for deal in position_deals if int(getattr(deal, "entry", -1)) == 1]
        if not exit_deals:
            continue

        close_time = max(deal_time(deal) for deal in exit_deals)
        if close_time < close_cutoff:
            continue

        mode = "UNKNOWN"
        for deal in entry_deals:
            mode = deal_mode(deal) or mode
            if mode != "UNKNOWN":
                break

        symbol = str(getattr(position_deals[0], "symbol", "") or "")
        open_time = min((deal_time(deal) for deal in entry_deals), default=None)
        exit_comment = deal_comment(exit_deals[-1])
        gross_profit = sum(float(getattr(deal, "profit", 0.0) or 0.0) for deal in position_deals)
        commissions = sum(float(getattr(deal, "commission", 0.0) or 0.0) for deal in position_deals)
        swap = sum(float(getattr(deal, "swap", 0.0) or 0.0) for deal in position_deals)
        fee = sum(float(getattr(deal, "fee", 0.0) or 0.0) for deal in position_deals)
        net_pnl = gross_profit + commissions + swap + fee
        entry_volume = sum(float(getattr(deal, "volume", 0.0) or 0.0) for deal in entry_deals)
        exit_volume = sum(float(getattr(deal, "volume", 0.0) or 0.0) for deal in exit_deals)

        closed_trades.append(
            ClosedTrade(
                position_id=position_id,
                symbol=symbol,
                mode=mode,
                open_time=open_time,
                close_time=close_time,
                entry_volume=entry_volume,
                exit_volume=exit_volume,
                net_pnl=net_pnl,
                gross_profit=gross_profit,
                commissions=commissions,
                swap=swap,
                fee=fee,
                exit_comment=exit_comment,
            )
        )

    closed_trades.sort(key=lambda trade: (trade.close_time or datetime.min, trade.position_id))
    return closed_trades


def summarize(trades: list[ClosedTrade], key_fn) -> list[dict[str, Any]]:
    buckets: dict[str, list[ClosedTrade]] = defaultdict(list)
    for trade in trades:
        buckets[key_fn(trade)].append(trade)

    rows: list[dict[str, Any]] = []
    for key, bucket in buckets.items():
        pnls = [trade.net_pnl for trade in bucket]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [pnl for pnl in pnls if pnl <= 0]
        streak = 0
        max_loss_streak = 0
        worst_cluster = 0.0
        rolling_cluster = 0.0
        for pnl in pnls:
            if pnl <= 0:
                streak += 1
                rolling_cluster += pnl
                max_loss_streak = max(max_loss_streak, streak)
                worst_cluster = min(worst_cluster, rolling_cluster)
            else:
                streak = 0
                rolling_cluster = 0.0
        rows.append(
            {
                "key": key,
                "trades": len(bucket),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": (len(wins) / len(bucket)) if bucket else 0.0,
                "net_pnl": sum(pnls),
                "expectancy": (sum(pnls) / len(bucket)) if bucket else 0.0,
                "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
                "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
                "payoff": ((sum(wins) / len(wins)) / abs(sum(losses) / len(losses))) if wins and losses and sum(losses) != 0 else None,
                "max_loss_streak": max_loss_streak,
                "worst_cluster_pnl": worst_cluster,
            }
        )

    rows.sort(key=lambda row: (row["expectancy"], row["net_pnl"], -row["trades"]))
    return rows


def ensure_mode_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {row["key"]: row for row in rows}
    ordered: list[dict[str, Any]] = []
    for mode in ("SNIPER", "SHOTGUN", "REVERSION", "MACHINE_GUN"):
        ordered.append(
            by_key.get(
                mode,
                {
                    "key": mode,
                    "trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_rate": 0.0,
                    "net_pnl": 0.0,
                    "expectancy": 0.0,
                    "avg_win": 0.0,
                    "avg_loss": 0.0,
                    "payoff": None,
                    "max_loss_streak": 0,
                    "worst_cluster_pnl": 0.0,
                },
            )
        )
    return ordered


def strongest_and_toxic(rows: list[dict[str, Any]], min_trades: int = 2) -> dict[str, list[dict[str, Any]]]:
    eligible = [row for row in rows if row["trades"] >= min_trades]
    toxic = [row for row in sorted(eligible, key=lambda row: (row["expectancy"], row["worst_cluster_pnl"])) if row["expectancy"] < 0][:5]
    strong = [row for row in sorted(eligible, key=lambda row: (row["expectancy"], row["net_pnl"]), reverse=True) if row["expectancy"] > 0][:5]
    return {"toxic": toxic, "strong": strong}


def fmt_money(value: float) -> str:
    return f"${value:+.2f}"


def render_table(title: str, rows: list[dict[str, Any]]) -> str:
    lines = [title]
    for row in rows:
        payoff = f"{row['payoff']:.2f}" if row["payoff"] is not None else "-"
        lines.append(
            "  "
            f"{row['key']:<20} trades={row['trades']:<3} "
            f"wr={row['win_rate']*100:>5.1f}% "
            f"net={fmt_money(row['net_pnl']):>9} "
            f"exp={fmt_money(row['expectancy']):>9} "
            f"avgW={fmt_money(row['avg_win']):>9} "
            f"avgL={fmt_money(row['avg_loss']):>9} "
            f"payoff={payoff:>5} "
            f"loss_streak={row['max_loss_streak']:<2} "
            f"worst_cluster={fmt_money(row['worst_cluster_pnl'])}"
        )
    if len(lines) == 1:
        lines.append("  (no trades)")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    if not mt5.initialize(login=LOGIN, password=PASSWORD, server=SERVER):
        print(json.dumps({"error": f"MT5 initialize failed: {mt5.last_error()}"}))
        return 1

    try:
        trades = reconstruct_closed_trades(args.lookback_days, args.history_days)
    finally:
        mt5.shutdown()

    mode_rows = ensure_mode_rows(summarize(trades, lambda trade: trade.mode))
    symbol_rows = summarize(trades, lambda trade: trade.symbol)
    mode_symbol_rows = summarize(trades, lambda trade: f"{trade.mode}|{trade.symbol}")
    highlights = strongest_and_toxic(mode_symbol_rows)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "lookback_days": args.lookback_days,
        "history_days": args.history_days,
        "closed_trade_count": len(trades),
        "modes": mode_rows,
        "symbols": symbol_rows,
        "mode_symbols": mode_symbol_rows,
        "highlights": highlights,
        "trades": [
            {
                "position_id": trade.position_id,
                "symbol": trade.symbol,
                "mode": trade.mode,
                "open_time": trade.open_time.isoformat(sep=" ", timespec="seconds") if trade.open_time else None,
                "close_time": trade.close_time.isoformat(sep=" ", timespec="seconds") if trade.close_time else None,
                "net_pnl": trade.net_pnl,
                "gross_profit": trade.gross_profit,
                "commissions": trade.commissions,
                "swap": trade.swap,
                "fee": trade.fee,
                "entry_volume": trade.entry_volume,
                "exit_volume": trade.exit_volume,
                "exit_comment": trade.exit_comment,
            }
            for trade in trades
        ],
    }

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    lines = [
        f"Recent realized expectancy report | lookback={args.lookback_days}d | closed_trades={len(trades)}",
        render_table("By mode", mode_rows),
        render_table("By symbol", symbol_rows[:15]),
        render_table("Strong mode+symbol pairs", highlights["strong"]),
        render_table("Toxic mode+symbol pairs", highlights["toxic"]),
    ]
    print("\n\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
