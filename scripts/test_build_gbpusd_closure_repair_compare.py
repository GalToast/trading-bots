#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_gbpusd_closure_repair_compare as board


def test_build_payload_marks_missing_no_escape_lane() -> None:
    baseline_payload = {
        "updated_at": "2026-04-15T17:00:00+00:00",
        "runner": {"heartbeat_at": "2026-04-15T17:00:00+00:00"},
        "metadata": {"no_offensive_escape": False, "offensive_closure_enabled": True},
        "symbols": {
            "GBPUSD": {
                "realized_net_usd": -1939.95,
                "realized_closes": 7272,
                "open_tickets": [{}, {}],
            }
        },
    }

    payload = board.build_payload(baseline_payload, None)

    assert payload["summary"]["paired_experiment_live"] is False
    assert payload["summary"]["next_action"] == "launch_or_restore_shadow_gbpusd_tick_forward_no_escape_before_judging_closure_repair"
    assert payload["lanes"][1]["status"] == "missing"


def test_render_markdown_mentions_next_action() -> None:
    payload = {
        "generated_at": "2026-04-15T17:00:00+00:00",
        "leadership_read": ["one"],
        "summary": {
            "next_action": "launch_or_restore_shadow_gbpusd_tick_forward_no_escape_before_judging_closure_repair",
            "paired_experiment_live": False,
        },
        "lanes": [
            {"lane": "baseline", "present": True, "state_path": "reports/a.json", "status": "present", "updated_at": "", "heartbeat_at": "", "realized_net_usd": 1.0, "realized_closes": 1, "avg_per_close": 1.0, "open_count": 0, "no_offensive_escape": False, "offensive_closure_enabled": True},
            {"lane": "no_escape", "present": False, "state_path": "reports/b.json", "status": "missing"},
        ],
    }

    markdown = board.render_markdown(payload)

    assert "GBPUSD Closure Repair Compare" in markdown
    assert "launch_or_restore_shadow_gbpusd_tick_forward_no_escape_before_judging_closure_repair" in markdown


if __name__ == "__main__":
    test_build_payload_marks_missing_no_escape_lane()
    test_render_markdown_mentions_next_action()
    print("ok")
