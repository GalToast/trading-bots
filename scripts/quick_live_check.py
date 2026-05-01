#!/usr/bin/env python3
from __future__ import annotations

import MetaTrader5 as mt5

from build_penetration_lane_scoreboard import LANES, load_json, summarize_live_lane


def main() -> int:
    spec = next((lane for lane in LANES if lane.lane_id == "live_rearm_941777"), None)
    if spec is None or not spec.state_path.exists():
        print("live_rearm_941777 state not found")
        return 1
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1
    try:
        rows = summarize_live_lane(spec, load_json(spec.state_path))
    finally:
        mt5.shutdown()

    for row in rows:
        if row["symbol"] == "TOTAL":
            continue
        print(
            f"{row['symbol']}: basis={row['realized_basis']} realized=${row['realized_usd']:.2f} "
            f"modeled=${row['modeled_realized_usd']:.2f} gap=${row['realized_gap_usd']:.2f} "
            f"floating=${row['floating_usd']:.2f} net=${row['net_usd']:.2f} "
            f"closes={row['closes']} open={row['open_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
