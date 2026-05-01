#!/usr/bin/env python3
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

import shadow_gbpusd_tick_forward as lane


def test_parse_args_accepts_no_offensive_escape() -> None:
    argv_before = list(sys.argv)
    try:
        sys.argv = [
            "shadow_gbpusd_tick_forward.py",
            "--no-offensive-escape",
        ]
        args = lane.parse_args()
    finally:
        sys.argv = argv_before

    assert args.no_offensive_escape is True


def test_build_engine_uses_explicit_offensive_closure_switch() -> None:
    fake_engine = object()
    with patch.object(lane, "engine_from_args", return_value=fake_engine) as mocked:
        result = lane.build_engine(no_offensive_escape=True)

    assert result is fake_engine
    assert mocked.call_args.kwargs["offensive_closure_enabled"] is False


def test_build_payload_carries_no_escape_metadata() -> None:
    fake_state = SimpleNamespace(
        anchor=1.0,
        next_sell_level=1.1,
        next_buy_level=0.9,
        open_tickets=[],
        rearm_tokens=[],
        rearm_opens=0,
        realized_net_usd=0.0,
        realized_closes=0,
        anchor_resets=0,
        max_open_total=0,
        lattice_started_time=0,
        last_tick_time=0,
        last_tick_msc=0,
        last_bar_time=0,
        last_tick={},
    )
    fake_engine = SimpleNamespace(state=fake_state)

    payload = lane.build_payload(
        prior_payload={"metadata": {"no_offensive_escape": True}},
        engine=fake_engine,
        runner_status={"started_at": "2026-04-15T00:00:00+00:00", "poll_seconds": 5},
        total_ticks=0,
        cycles=0,
        errors=[],
        initialized_from_state=False,
        event_path=lane.DEFAULT_EVENT_PATH,
    )

    assert payload["metadata"]["no_offensive_escape"] is True
    assert payload["metadata"]["offensive_closure_enabled"] is False


if __name__ == "__main__":
    test_parse_args_accepts_no_offensive_escape()
    test_build_engine_uses_explicit_offensive_closure_switch()
    test_build_payload_carries_no_escape_metadata()
    print("ok")
