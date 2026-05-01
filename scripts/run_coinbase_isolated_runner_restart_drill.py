#!/usr/bin/env python3
from __future__ import annotations

import json
import io
import sys
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import multi_coin_isolated_runner as runner


JSON_PATH = REPORTS / "coinbase_isolated_runner_restart_drill.json"
MD_PATH = REPORTS / "coinbase_isolated_runner_restart_drill.md"


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


def build_candle_sequences() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    base_dt = datetime(2026, 4, 12, 14, 0, tzinfo=timezone.utc)
    base_ts = int(base_dt.timestamp())
    backfill: list[dict[str, str]] = []
    for idx in range(11):
        ts = base_ts + idx * 300
        price = 1.00 + idx * 0.01
        backfill.append(candle(ts, price, price + 0.01, price - 0.01, price))

    live_entry_ts = base_ts + 11 * 300
    live_entry = candle(live_entry_ts, 1.20, 2.00, 1.19, 1.21)
    return backfill, [live_entry]


@dataclass
class FetchPlan:
    sequences: list[list[dict[str, str]]]
    idx: int = 0

    def __call__(self, client: Any, pid: str, start: int, end: int, granularity: str = "FIVE_MINUTE") -> list[dict[str, str]]:
        if self.idx >= len(self.sequences):
            return []
        out = self.sequences[self.idx]
        self.idx += 1
        return out


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def run_once_with_plan(
    state_path: Path,
    event_path: Path,
    fetch_plan: FetchPlan,
    *,
    max_cycles: int = 1,
) -> dict[str, Any]:
    argv = [
        "multi_coin_isolated_runner.py",
        "--total-cash",
        "48",
        "--coins",
        "TRU-USD",
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
        except Exception as exc:  # pragma: no cover - surfaced in payload
            exc_text = f"{type(exc).__name__}: {exc}"

    return {
        "return_code": rc,
        "state": load_json(state_path),
        "events_path_exists": event_path.exists(),
        "stdout": stdout_capture.getvalue(),
        "stderr": stderr_capture.getvalue(),
        "exception": exc_text,
    }


def build_payload() -> dict[str, Any]:
    backfill, live_entry = build_candle_sequences()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        state_path = tmp / "isolated_state.json"
        event_path = tmp / "isolated_events.jsonl"

        first = run_once_with_plan(
            state_path,
            event_path,
            FetchPlan([backfill, live_entry]),
            max_cycles=1,
        )
        first_ledger = (((first.get("state") or {}).get("ledgers") or {}).get("TRU-USD") or {})

        second = run_once_with_plan(
            state_path,
            event_path,
            FetchPlan([backfill + live_entry, []]),
            max_cycles=2,
        )
        second_ledger = (((second.get("state") or {}).get("ledgers") or {}).get("TRU-USD") or {})

        first_hold = int(first_ledger.get("position_hold") or 0)
        second_hold = int(second_ledger.get("position_hold") or 0)
        hold_delta = second_hold - first_hold
        replay_exit_detected = first_ledger.get("position") == "active" and second_ledger.get("position") != "active"
        replay_close_detected = int(second_ledger.get("closes") or 0) > int(first_ledger.get("closes") or 0)
        continuity_verdict = "continuity_fail" if replay_exit_detected or replay_close_detected or hold_delta > 1 else "continuity_pass"
        continuity_reasons = []
        if replay_exit_detected:
            continuity_reasons.append("restored active position came back flat after restart replay")
        if replay_close_detected:
            continuity_reasons.append("close count advanced during restart replay")
        if hold_delta > 1:
            continuity_reasons.append(f"hold bars jumped by {hold_delta}")
        if not continuity_reasons:
            continuity_reasons.append("restored active position retained expected hold continuity")

        return {
            "generated_at": utc_now_iso(),
            "runner_path": str(SCRIPTS / "multi_coin_isolated_runner.py"),
            "drill_coin": "TRU-USD",
            "first_run": {
                "return_code": first.get("return_code"),
                "position": first_ledger.get("position"),
                "position_hold": first_hold,
                "signals": first_ledger.get("signals"),
                "closes": first_ledger.get("closes"),
                "exception": first.get("exception"),
                "stdout": first.get("stdout"),
                "stderr": first.get("stderr"),
            },
            "second_run": {
                "return_code": second.get("return_code"),
                "position": second_ledger.get("position"),
                "position_hold": second_hold,
                "signals": second_ledger.get("signals"),
                "closes": second_ledger.get("closes"),
                "exception": second.get("exception"),
                "stdout": second.get("stdout"),
                "stderr": second.get("stderr"),
            },
            "continuity": {
                "verdict": continuity_verdict,
                "hold_delta": hold_delta,
                "replay_exit_detected": replay_exit_detected,
                "replay_close_detected": replay_close_detected,
                "reasons": continuity_reasons,
                "read": "; ".join(continuity_reasons),
            },
        }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Isolated Runner Restart Drill",
        "",
        f"Continuity verdict: `{payload['continuity']['verdict']}`",
        "",
        f"- Drill coin: `{payload['drill_coin']}`",
        f"- First run position: `{payload['first_run']['position']}` hold=`{payload['first_run']['position_hold']}`",
        f"- Second run position: `{payload['second_run']['position']}` hold=`{payload['second_run']['position_hold']}` closes=`{payload['second_run']['closes']}`",
        f"- Hold delta across restart: `{payload['continuity']['hold_delta']}`",
        f"- Read: {payload['continuity']['read']}",
    ]
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
