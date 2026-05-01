#!/usr/bin/env python3
"""MFE Capture Tracker — tracks how much of the predicted/actual MFE is captured per trade.

This module integrates into the shadow runner to measure the ONE metric that
determines if the edge is real: the MFE capture rate.

Usage:
    tracker = MFETracker()
    tracker.on_entry(trade_id, entry_price, predicted_mfe_pct=0.01)
    tracker.on_heartbeat(trade_id, current_high)
    result = tracker.on_exit(trade_id, exit_price)
    # result: {capture_rate, actual_mfe, predicted_mfe, net_pct, ...}

Break-even capture rates:
    Coinbase (240bps): ~15-20%
    Kraken (80bps): <10%
"""
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class TradeRecord:
    trade_id: str
    product_id: str
    entry_time: float
    entry_price: float
    predicted_mfe_pct: float  # e.g., 0.01 = predicted 1% MFE
    predicted_mfe_price: float
    actual_mfe_price: float = 0.0
    actual_mfe_pct: float = 0.0
    exit_time: Optional[float] = None
    exit_price: Optional[float] = None
    capture_rate: Optional[float] = None  # (exit - entry) / (actual_mfe - entry)
    predicted_capture_rate: Optional[float] = None  # (exit - entry) / (predicted_mfe - entry)
    net_pct: Optional[float] = None
    gross_pct: Optional[float] = None
    fee_bps: float = 240.0  # default Coinbase
    fee_pct: float = 2.4  # 240bps
    hold_seconds: Optional[float] = None
    status: str = "open"  # open, closed


class MFETracker:
    def __init__(self, default_fee_bps: float = 240.0, output_path: Optional[Path] = None):
        self.trades: dict[str, TradeRecord] = {}
        self.default_fee_bps = default_fee_bps
        self.default_fee_pct = default_fee_bps / 10000.0  # 240bps → 0.024 = 2.4%
        self.output_path = output_path

    def on_entry(self, trade_id: str, product_id: str, entry_price: float,
                 predicted_mfe_pct: float = 0.01, fee_bps: Optional[float] = None):
        """Record a new trade entry."""
        fee_pct = (fee_bps or self.default_fee_bps) / 10000.0
        record = TradeRecord(
            trade_id=trade_id,
            product_id=product_id,
            entry_time=time.time(),
            entry_price=entry_price,
            predicted_mfe_pct=predicted_mfe_pct,
            predicted_mfe_price=entry_price * (1.0 + predicted_mfe_pct),
            actual_mfe_price=entry_price,  # starts at entry, only goes up
            fee_bps=fee_bps or self.default_fee_bps,
            fee_pct=fee_pct,
        )
        self.trades[trade_id] = record
        return record

    def on_heartbeat(self, trade_id: str, current_high: float):
        """Update the actual MFE for an open trade."""
        record = self.trades.get(trade_id)
        if record is None or record.status != "open":
            return None
        if current_high > record.actual_mfe_price:
            record.actual_mfe_price = current_high
            record.actual_mfe_pct = (current_high / record.entry_price) - 1.0
        return record

    def on_exit(self, trade_id: str, exit_price: float) -> Optional[TradeRecord]:
        """Record trade exit and compute capture rates."""
        record = self.trades.get(trade_id)
        if record is None or record.status != "open":
            return None

        record.exit_time = time.time()
        record.exit_price = exit_price
        record.status = "closed"
        record.hold_seconds = record.exit_time - record.entry_time

        # Gross return
        record.gross_pct = (exit_price / record.entry_price) - 1.0

        # Net return after fees
        record.net_pct = record.gross_pct - record.fee_pct

        # MFE capture rate: how much of the actual MFE did we capture?
        mfe_range = record.actual_mfe_price - record.entry_price
        if mfe_range > 0:
            record.capture_rate = (exit_price - record.entry_price) / mfe_range
        else:
            record.capture_rate = 0.0  # no MFE achieved

        # Predicted MFE capture rate: how much of the predicted MFE did we capture?
        predicted_mfe_range = record.predicted_mfe_price - record.entry_price
        if predicted_mfe_range > 0:
            record.predicted_capture_rate = (exit_price - record.entry_price) / predicted_mfe_range
        else:
            record.predicted_capture_rate = 0.0

        return record

    def get_open_trades(self) -> list[TradeRecord]:
        return [t for t in self.trades.values() if t.status == "open"]

    def get_closed_trades(self) -> list[TradeRecord]:
        return [t for t in self.trades.values() if t.status == "closed"]

    def get_stats(self) -> dict:
        closed = self.get_closed_trades()
        if not closed:
            return {"total_trades": 0, "open_trades": len(self.get_open_trades())}

        capture_rates = [t.capture_rate for t in closed if t.capture_rate is not None]
        net_pcts = [t.net_pct for t in closed if t.net_pct is not None]

        return {
            "total_trades": len(self.trades),
            "open_trades": len(self.get_open_trades()),
            "closed_trades": len(closed),
            "avg_capture_rate": sum(capture_rates) / len(capture_rates) if capture_rates else 0.0,
            "median_capture_rate": sorted(capture_rates)[len(capture_rates) // 2] if capture_rates else 0.0,
            "min_capture_rate": min(capture_rates) if capture_rates else 0.0,
            "max_capture_rate": max(capture_rates) if capture_rates else 0.0,
            "avg_net_pct": sum(net_pcts) / len(net_pcts) if net_pcts else 0.0,
            "cumulative_net_pct": sum(net_pcts),
            "win_rate": sum(1 for n in net_pcts if n > 0) / len(net_pcts) if net_pcts else 0.0,
            "trades_above_15pct_capture": sum(1 for c in capture_rates if c >= 0.15),
            "trades_above_20pct_capture": sum(1 for c in capture_rates if c >= 0.20),
        }

    def save(self, path: Optional[Path] = None):
        output = path or self.output_path
        if output is None:
            return
        data = [asdict(t) for t in self.trades.values()]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load(self, path: Optional[Path] = None):
        input_path = path or self.output_path
        if input_path is None or not input_path.exists():
            return
        data = json.loads(input_path.read_text(encoding="utf-8"))
        for d in data:
            self.trades[d["trade_id"]] = TradeRecord(**d)


def main():
    """Test the MFE tracker with simulated trades."""
    tracker = MFETracker(default_fee_bps=240.0)

    # Simulate a trade: entry at $100, predicted 1% MFE ($101)
    tracker.on_entry("test-1", "RAVE-USD", 100.0, predicted_mfe_pct=0.01)

    # During the trade, price goes to $101.50 (1.5% MFE)
    tracker.on_heartbeat("test-1", 101.50)

    # Exit at $100.80 (0.8% gross)
    result = tracker.on_exit("test-1", 100.80)

    print(f"Trade: {result.trade_id}")
    print(f"  Entry: ${result.entry_price:.2f}")
    print(f"  Predicted MFE: ${result.predicted_mfe_price:.2f} ({result.predicted_mfe_pct:.1%})")
    print(f"  Actual MFE: ${result.actual_mfe_price:.2f} ({result.actual_mfe_pct:.1%})")
    print(f"  Exit: ${result.exit_price:.2f}")
    print(f"  Gross: {result.gross_pct:.2%}")
    print(f"  Net (after {result.fee_bps}bps fees): {result.net_pct:.2%}")
    print(f"  Capture rate: {result.capture_rate:.1%}")
    print(f"  Predicted capture rate: {result.predicted_capture_rate:.1%}")
    print(f"  Hold: {result.hold_seconds:.0f}s")

    # Simulate another trade that captures less
    tracker.on_entry("test-2", "BLUR-USD", 50.0, predicted_mfe_pct=0.02)
    tracker.on_heartbeat("test-2", 51.50)  # 3% MFE
    result2 = tracker.on_exit("test-2", 49.80)  # exit below entry

    print(f"\nTrade: {result2.trade_id}")
    print(f"  Capture rate: {result2.capture_rate:.1%}")
    print(f"  Net: {result2.net_pct:.2%}")

    # Stats
    stats = tracker.get_stats()
    print(f"\nStats:")
    print(f"  Total trades: {stats['total_trades']}")
    print(f"  Avg capture rate: {stats['avg_capture_rate']:.1%}")
    print(f"  Avg net: {stats['avg_net_pct']:.2%}")
    print(f"  Win rate: {stats['win_rate']:.1%}")
    print(f"  Trades above 15% capture: {stats['trades_above_15pct_capture']}")
    print(f"  Trades above 20% capture: {stats['trades_above_20pct_capture']}")


if __name__ == "__main__":
    main()
