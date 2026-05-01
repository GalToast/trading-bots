#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path

import MetaTrader5 as mt5

from live_penetration_lattice_shadow import REARM_VARIANTS, StatefulRearmRawEngine
from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import spread_price, unit_pnl_usd


ROOT = Path(__file__).resolve().parent.parent
DAYS = 90
TIMEFRAME = mt5.TIMEFRAME_M15

CLAIMS = [
    {"symbol": "ETHUSD", "step": 5.0, "max_open": 80, "gap": 1, "alpha": 1.0, "momentum": True},
    {"symbol": "XRPUSD", "step": 0.01, "max_open": 80, "gap": 1, "alpha": 1.0, "momentum": True},
    {"symbol": "SOLUSD", "step": 1.0, "max_open": 80, "gap": 1, "alpha": 1.0, "momentum": False},
]


def load_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 1, 96 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "tick_volume": int(r[5]),
        }
        for r in rates
    ]


def run_claim(symbol: str, bars: list[dict], info, step: float, max_open: int, gap: int, alpha: float, momentum: bool) -> dict:
    variant = REARM_VARIANTS["rearm_lvl2_exc1"]
    cfg = RawConfig(
        step_pips=step,
        max_open_per_side=max_open,
        close_mode="one_level" if gap == 1 else "two_level",
        step_is_price_units=True,
    )
    engine = StatefulRearmRawEngine(
        symbol,
        cfg,
        info,
        variant=variant,
        close_alpha=alpha,
        cooldown_bars=0,
        momentum_gate=momentum,
        sell_gap=gap,
        buy_gap=gap,
    )
    engine.replay(bars)

    final_close = float(bars[-1]["close"])
    spread_px = spread_price(info)
    tickets = [type("T", (), t)() for t in engine.state.open_tickets]
    floating = sum(unit_pnl_usd(symbol, t.direction, t.entry_price, final_close, spread_px) for t in tickets)
    realized = float(engine.state.realized_net_usd)
    return {
        "symbol": symbol,
        "step": step,
        "max_open": max_open,
        "gap": gap,
        "alpha": alpha,
        "momentum": momentum,
        "combined": realized + floating,
        "realized": realized,
        "floating": floating,
        "closes": int(engine.state.realized_closes),
        "rearm_opens": int(engine.state.rearm_opens),
        "max_seen": int(engine.state.max_open_total),
    }


def main() -> int:
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1
    try:
        rows: list[dict] = []
        for claim in CLAIMS:
            symbol = claim["symbol"]
            info = mt5.symbol_info(symbol)
            if info is None:
                print(f"{symbol}: missing symbol info")
                continue
            bars = load_bars(symbol, DAYS)
            if not bars:
                print(f"{symbol}: no bars")
                continue
            row = run_claim(symbol=symbol, bars=bars, info=info, **{k: claim[k] for k in ("step", "max_open", "gap", "alpha", "momentum")})
            rows.append(row)
            print(
                f"{symbol} M15 step={row['step']} mo={row['max_open']} mom={'ON' if row['momentum'] else 'OFF'} "
                f"-> combined=${row['combined']:,.2f} realized=${row['realized']:,.2f} floating=${row['floating']:,.2f} "
                f"closes={row['closes']} max_seen={row['max_seen']}"
            )

        out_path = ROOT / "reports" / "validated_crypto_m15_claims.csv"
        if rows:
            with out_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            print(f"Wrote {out_path}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
