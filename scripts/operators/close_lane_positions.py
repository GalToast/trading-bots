#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import MetaTrader5 as mt5
import mt5_terminal_guard


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import live_penetration_lattice_mirror as mirror


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Close all tracked broker positions for a direct-live lane.")
    parser.add_argument("--exec-state-path", required=True)
    parser.add_argument("--exec-log-path", required=True)
    parser.add_argument("--live-magic", type=int, required=True)
    parser.add_argument("--live-comment-prefix", required=True)
    parser.add_argument("--lane-name", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state_path = Path(args.exec_state_path)
    log_path = Path(args.exec_log_path)
    state = mirror.load_state(state_path)
    positions = list(state.get("positions") or [])
    mt5_ready, mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5, require_trade_allowed=True)
    if not mt5_ready:
        print(mt5_terminal_guard.failure_summary(mt5_connection))
        return 1
    try:
        survivors: list[dict] = []
        results: list[dict] = []
        for tracked in positions:
            ticket = int(tracked.get("live_ticket", 0) or 0)
            result = mirror.close_live_position(
                ticket,
                live_magic=int(args.live_magic),
                comment_prefix=str(args.live_comment_prefix),
            )
            mirror.append_jsonl(
                log_path,
                {
                    "ts_utc": mirror.utc_now_iso(),
                    "action": "manual_pause_close_attempt",
                    "lane": args.lane_name,
                    "tracked": tracked,
                    "result": result,
                },
            )
            results.append({"ticket": ticket, "result": result})
            if not (result.get("ok") or result.get("reason") == "position_not_found"):
                survivors.append(tracked)
        state["positions"] = survivors
        mirror.save_state(state_path, state)
    finally:
        mt5.shutdown()
    print(json.dumps({"lane": args.lane_name, "closed_attempts": results, "remaining_positions": len(state.get("positions") or [])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
