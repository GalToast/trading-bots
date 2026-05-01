from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = ROOT / "blocked_quality_candidates.jsonl"
TIMEFRAME = mt5.TIMEFRAME_M1


@dataclass
class CandidateOutcome:
    symbol: str
    reason: str
    signal: str
    confidence: float
    horizon_min: int
    return_bps: float
    favorable: bool
    entry_price: float
    exit_price: float


def load_candidates(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def dedupe_candidates(candidates: list[dict[str, Any]], dedupe_seconds: int) -> list[dict[str, Any]]:
    if dedupe_seconds <= 0:
        return candidates

    deduped: list[dict[str, Any]] = []
    last_seen_by_key: dict[tuple[str, ...], datetime] = {}
    for row in candidates:
        recorded_at = parse_timestamp(str(row.get("recorded_at_utc", "")))
        if recorded_at is None:
            continue
        key = (
            str(row.get("symbol", "")),
            str(row.get("reason", "")),
            str(row.get("signal", "")),
            str(row.get("trigger", "")),
            str(row.get("mode", "")),
            str(row.get("regime", "")),
            f"{float(row.get('confidence', 0.0) or 0.0):.2f}",
        )
        last_seen = last_seen_by_key.get(key)
        if last_seen is not None and (recorded_at - last_seen).total_seconds() <= dedupe_seconds:
            continue
        last_seen_by_key[key] = recorded_at
        deduped.append(row)
    return deduped


def fetch_m1_bars(symbol: str, start_utc: datetime, end_utc: datetime) -> pd.DataFrame:
    rates = mt5.copy_rates_range(symbol, TIMEFRAME, start_utc, end_utc)
    if rates is None or len(rates) == 0:
        return pd.DataFrame()
    frame = pd.DataFrame(rates)
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    return frame.sort_values("time").reset_index(drop=True)


def price_at_or_after(frame: pd.DataFrame, target_utc: datetime) -> float | None:
    if frame.empty:
        return None
    rows = frame[frame["time"] >= target_utc]
    if rows.empty:
        return None
    return float(rows.iloc[0]["close"])


def direction_multiplier(signal: str) -> int | None:
    normalized = (signal or "").upper()
    if normalized == "BUY":
        return 1
    if normalized == "SELL":
        return -1
    return None


def score_candidates(
    candidates: list[dict[str, Any]],
    horizons: list[int],
    now_utc: datetime,
) -> list[CandidateOutcome]:
    eligible: list[dict[str, Any]] = []
    for row in candidates:
        recorded_at = parse_timestamp(str(row.get("recorded_at_utc", "")))
        if recorded_at is None:
            continue
        direction = direction_multiplier(str(row.get("signal", "")))
        if direction is None:
            continue
        matured_horizons = [
            int(horizon_min)
            for horizon_min in horizons
            if recorded_at + timedelta(minutes=int(horizon_min)) <= now_utc
        ]
        if not matured_horizons:
            continue
        eligible.append(
            {
                **row,
                "_recorded_at": recorded_at,
                "_direction": direction,
                "_matured_horizons": matured_horizons,
            }
        )

    if not eligible:
        return []

    symbol_ranges: dict[str, tuple[datetime, datetime]] = {}
    for row in eligible:
        symbol = str(row.get("symbol", ""))
        recorded_at = row["_recorded_at"]
        start, end = symbol_ranges.get(symbol, (recorded_at, recorded_at))
        symbol_ranges[symbol] = (
            min(start, recorded_at - timedelta(minutes=2)),
            max(end, recorded_at + timedelta(minutes=max(horizons) + 2)),
        )

    bars_by_symbol = {
        symbol: fetch_m1_bars(symbol, start_utc, end_utc)
        for symbol, (start_utc, end_utc) in symbol_ranges.items()
    }

    outcomes: list[CandidateOutcome] = []
    for row in eligible:
        symbol = str(row.get("symbol", ""))
        signal = str(row.get("signal", ""))
        frame = bars_by_symbol.get(symbol, pd.DataFrame())
        if frame.empty:
            continue
        recorded_at = row["_recorded_at"]
        direction = int(row["_direction"])
        matured_horizons = list(row["_matured_horizons"])
        entry_price = float(row.get("reference_price", 0.0) or 0.0)
        if entry_price <= 0.0:
            fetched_entry = price_at_or_after(frame, recorded_at)
            if fetched_entry is None:
                continue
            entry_price = fetched_entry
        for horizon_min in matured_horizons:
            exit_price = price_at_or_after(frame, recorded_at + timedelta(minutes=horizon_min))
            if exit_price is None or entry_price <= 0.0:
                continue
            signed_return = direction * ((exit_price / entry_price) - 1.0)
            return_bps = signed_return * 10000.0
            outcomes.append(
                CandidateOutcome(
                    symbol=symbol,
                    reason=str(row.get("reason", "unknown")),
                    signal=signal,
                    confidence=float(row.get("confidence", 0.0) or 0.0),
                    horizon_min=int(horizon_min),
                    return_bps=float(return_bps),
                    favorable=return_bps > 0.0,
                    entry_price=float(entry_price),
                    exit_price=float(exit_price),
                )
            )
    return outcomes


def summarize_outcomes(outcomes: list[CandidateOutcome]) -> list[str]:
    if not outcomes:
        return ["No mature blocked candidates available yet."]

    grouped: dict[tuple[int, str], list[CandidateOutcome]] = defaultdict(list)
    for outcome in outcomes:
        grouped[(outcome.horizon_min, outcome.reason)].append(outcome)

    lines = ["Summary by horizon/reason:"]
    for (horizon_min, reason), rows in sorted(grouped.items()):
        win_rate = sum(1 for row in rows if row.favorable) / len(rows)
        avg_bps = sum(row.return_bps for row in rows) / len(rows)
        avg_conf = sum(row.confidence for row in rows) / len(rows)
        lines.append(
            f"- {horizon_min:>2}m {reason}: n={len(rows)} "
            f"win_rate={win_rate:.0%} avg_bps={avg_bps:+.1f} avg_conf={avg_conf:.2f}"
        )
    return lines


def recent_rows(outcomes: list[CandidateOutcome], limit: int) -> list[str]:
    if not outcomes:
        return []
    lines = ["Recent scored rows:"]
    for row in outcomes[-limit:]:
        lines.append(
            f"- {row.horizon_min:>2}m {row.symbol} {row.signal} "
            f"conf={row.confidence:.2f} {row.reason} return_bps={row.return_bps:+.1f}"
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score blocked quality-gate candidates against later M1 price movement."
    )
    parser.add_argument(
        "--log-path",
        default=str(LOG_FILE),
        help="Path to blocked_quality_candidates.jsonl",
    )
    parser.add_argument(
        "--horizons",
        nargs="+",
        type=int,
        default=[5, 15, 30],
        help="Forward horizons in minutes",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=12,
        help="Number of recent scored rows to print",
    )
    parser.add_argument(
        "--dedupe-seconds",
        type=int,
        default=60,
        help="Collapse repeated blocked rows for the same candidate burst within this many seconds",
    )
    args = parser.parse_args()

    path = Path(args.log_path)
    raw_candidates = load_candidates(path)
    if not raw_candidates:
        print(f"No blocked candidate log found at {path}")
        return 0
    candidates = dedupe_candidates(raw_candidates, max(0, int(args.dedupe_seconds)))

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1
    try:
        outcomes = score_candidates(
            candidates=candidates,
            horizons=sorted(set(int(v) for v in args.horizons if int(v) > 0)),
            now_utc=datetime.now(timezone.utc),
        )
    finally:
        mt5.shutdown()

    print(
        f"Scoring {len(candidates)} deduped candidates from {len(raw_candidates)} raw rows "
        f"(dedupe={int(args.dedupe_seconds)}s)"
    )
    for line in summarize_outcomes(outcomes):
        print(line)
    for line in recent_rows(outcomes, args.limit):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
