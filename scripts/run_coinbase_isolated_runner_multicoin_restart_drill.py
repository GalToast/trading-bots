#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import multi_coin_isolated_runner as runner


JSON_PATH = REPORTS / "coinbase_isolated_runner_multicoin_restart_drill.json"
MD_PATH = REPORTS / "coinbase_isolated_runner_multicoin_restart_drill.md"
DRILL_COINS = ["TRU-USD", "SUP-USD", "A8-USD"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def candle(ts: int, open_price: float, high: float, low: float, close: float) -> dict[str, str]:
    return {
        "start": str(ts),
        "open": f"{open_price:.6f}",
        "high": f"{high:.6f}",
        "low": f"{low:.6f}",
        "close": f"{close:.6f}",
    }


def build_sequences_for_coin(offset_bps: int) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    base_dt = datetime(2026, 4, 12, 14, 0, tzinfo=timezone.utc)
    base_ts = int(base_dt.timestamp())
    base_price = 1.00 + offset_bps / 10000.0
    backfill: list[dict[str, str]] = []
    for idx in range(11):
        ts = base_ts + idx * 300
        price = base_price + idx * 0.01
        backfill.append(candle(ts, price, price + 0.01, price - 0.01, price))

    live_entry_ts = base_ts + 11 * 300
    live_entry = candle(live_entry_ts, base_price + 0.20, base_price + 1.00, base_price + 0.19, base_price + 0.21)
    return backfill, [live_entry]


@dataclass
class MultiFetchPlan:
    sequences_by_coin: dict[str, list[list[dict[str, str]]]]
    indexes: dict[str, int] = field(default_factory=dict)

    def __call__(self, client: Any, pid: str, start: int, end: int, granularity: str = "FIVE_MINUTE") -> list[dict[str, str]]:
        plans = self.sequences_by_coin.get(pid, [])
        idx = self.indexes.get(pid, 0)
        if idx >= len(plans):
            return []
        self.indexes[pid] = idx + 1
        return plans[idx]


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def run_once_with_plan(
    state_path: Path,
    event_path: Path,
    fetch_plan: MultiFetchPlan,
    *,
    max_cycles: int,
) -> dict[str, Any]:
    argv = [
        "multi_coin_isolated_runner.py",
        "--total-cash",
        "48",
        "--coins",
        *DRILL_COINS,
        "--state-path",
        str(state_path),
        "--event-path",
        str(event_path),
        "--max-cycles",
        str(max_cycles),
    ]

    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    rc = None
    exc_text = ""
    with ExitStack() as stack:
        stack.enter_context(patch.object(sys, "argv", argv))
        stack.enter_context(patch.object(runner, "CoinbaseAdvancedClient", lambda: object()))
        stack.enter_context(patch.object(runner, "fetch_candles", fetch_plan))
        stack.enter_context(patch.object(runner.time, "sleep", lambda _seconds: None))
        stack.enter_context(patch.object(sys, "stdout", stdout_capture))
        stack.enter_context(patch.object(sys, "stderr", stderr_capture))
        try:
            rc = runner.main()
        except Exception as exc:  # pragma: no cover
            exc_text = f"{type(exc).__name__}: {exc}"

    return {
        "return_code": rc,
        "state": load_json(state_path),
        "stdout": stdout_capture.getvalue(),
        "stderr": stderr_capture.getvalue(),
        "exception": exc_text,
    }


def summarize_run(run_payload: dict[str, Any]) -> dict[str, Any]:
    ledgers = ((run_payload.get("state") or {}).get("ledgers") or {})
    coin_rows: dict[str, Any] = {}
    for coin in DRILL_COINS:
        ledger = ledgers.get(coin) or {}
        coin_rows[coin] = {
            "position": ledger.get("position"),
            "position_hold": int(ledger.get("position_hold") or 0),
            "signals": int(ledger.get("signals") or 0),
            "closes": int(ledger.get("closes") or 0),
            "last_candle_time": int(ledger.get("last_candle_time") or 0),
        }
    return {
        "return_code": run_payload.get("return_code"),
        "coins": coin_rows,
        "stdout": run_payload.get("stdout"),
        "stderr": run_payload.get("stderr"),
        "exception": run_payload.get("exception"),
    }


def build_payload() -> dict[str, Any]:
    seq_by_coin = {
        "TRU-USD": build_sequences_for_coin(0),
        "SUP-USD": build_sequences_for_coin(30),
        "A8-USD": build_sequences_for_coin(60),
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        state_path = tmp / "isolated_state.json"
        event_path = tmp / "isolated_events.jsonl"

        first = run_once_with_plan(
            state_path,
            event_path,
            MultiFetchPlan({coin: [backfill, live_entry] for coin, (backfill, live_entry) in seq_by_coin.items()}),
            max_cycles=1,
        )
        first_summary = summarize_run(first)

        second = run_once_with_plan(
            state_path,
            event_path,
            MultiFetchPlan({coin: [backfill + live_entry, []] for coin, (backfill, live_entry) in seq_by_coin.items()}),
            max_cycles=2,
        )
        second_summary = summarize_run(second)

        replay_exit_coins = []
        replay_close_coins = []
        hold_jump_coins = []
        for coin in DRILL_COINS:
            first_coin = first_summary["coins"][coin]
            second_coin = second_summary["coins"][coin]
            if first_coin["position"] == "active" and second_coin["position"] != "active":
                replay_exit_coins.append(coin)
            if second_coin["closes"] > first_coin["closes"]:
                replay_close_coins.append(coin)
            if second_coin["position_hold"] - first_coin["position_hold"] > 1:
                hold_jump_coins.append(coin)

        continuity_fail = bool(replay_exit_coins or replay_close_coins or hold_jump_coins)
        reasons = []
        if replay_exit_coins:
            reasons.append(f"recovered coins came back flat: {', '.join(replay_exit_coins)}")
        if replay_close_coins:
            reasons.append(f"close counts advanced during restart replay: {', '.join(replay_close_coins)}")
        if hold_jump_coins:
            reasons.append(f"hold bars jumped by >1 on: {', '.join(hold_jump_coins)}")
        if not reasons:
            reasons.append("all recovered active lanes retained expected continuity across restart")

        return {
            "generated_at": utc_now_iso(),
            "runner_path": str(SCRIPTS / "multi_coin_isolated_runner.py"),
            "drill_coins": DRILL_COINS,
            "first_run": first_summary,
            "second_run": second_summary,
            "continuity": {
                "verdict": "continuity_fail" if continuity_fail else "continuity_pass",
                "replay_exit_coins": replay_exit_coins,
                "replay_close_coins": replay_close_coins,
                "hold_jump_coins": hold_jump_coins,
                "read": "; ".join(reasons),
            },
        }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Isolated Runner Multi-Coin Restart Drill",
        "",
        f"Continuity verdict: `{payload['continuity']['verdict']}`",
        "",
    ]
    for coin in payload["drill_coins"]:
        first_coin = payload["first_run"]["coins"][coin]
        second_coin = payload["second_run"]["coins"][coin]
        lines.append(
            f"- `{coin}` first=`{first_coin['position']}` hold=`{first_coin['position_hold']}` second=`{second_coin['position']}` hold=`{second_coin['position_hold']}` closes=`{second_coin['closes']}`"
        )
    lines.append(f"- Read: {payload['continuity']['read']}")
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
