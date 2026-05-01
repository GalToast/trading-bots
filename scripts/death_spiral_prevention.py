#!/usr/bin/env python3
"""Death Spiral Prevention — Shared utility for all shadow runners.

Problem: Runners keep re-entering products that repeatedly hit stop losses,
bleeding equity through many small losses (BERT 124 exits, BMB 90 exits,
AKE death spiral, Maker RSI 7 consecutive losses).

Solution: Per-product loss tracking with hard cooldowns.

Usage:
    from death_spiral_prevention import LossTracker

    tracker = LossTracker(max_consecutive_losses=3, cooldown_seconds=3600)

    # On trade close:
    tracker.record_close(product_id="BERT-USD", won=False)

    # Before opening:
    if tracker.is_blocked("BERT-USD"):
        print("BLOCKED: BERT-USD in death spiral cooldown")
        return

    # Periodic tick (for stale cleanup):
    tracker.tick()
"""
import time
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


class LossTracker:
    """Track per-product losses and block death spirals."""

    def __init__(
        self,
        max_consecutive_losses: int = 3,
        cooldown_seconds: int = 3600,
        state_path: str | Path | None = None,
    ):
        self.max_consecutive_losses = max_consecutive_losses
        self.cooldown_seconds = cooldown_seconds
        self.state_path = Path(state_path) if state_path else None

        # In-memory state
        self.consecutive_losses: dict[str, int] = {}
        self.blocked_until: dict[str, float] = {}
        self.total_losses: dict[str, int] = {}
        self.total_wins: dict[str, int] = {}

        # Load persisted state if available
        if self.state_path and self.state_path.exists():
            self._load()

    def record_close(self, product_id: str, won: bool) -> dict:
        """Record a trade close. Returns action dict if product should be blocked."""
        if won:
            self.consecutive_losses[product_id] = 0
            self.total_wins[product_id] = self.total_wins.get(product_id, 0) + 1
            # Unblock if was blocked
            if product_id in self.blocked_until:
                del self.blocked_until[product_id]
            return {"action": "win", "product_id": product_id}
        else:
            losses = self.consecutive_losses.get(product_id, 0) + 1
            self.consecutive_losses[product_id] = losses
            self.total_losses[product_id] = self.total_losses.get(product_id, 0) + 1

            if losses >= self.max_consecutive_losses:
                # Block this product
                unblock_at = time.time() + self.cooldown_seconds
                self.blocked_until[product_id] = unblock_at
                return {
                    "action": "blocked",
                    "product_id": product_id,
                    "consecutive_losses": losses,
                    "unblock_at": unblock_at,
                    "cooldown_seconds": self.cooldown_seconds,
                }

            return {
                "action": "loss",
                "product_id": product_id,
                "consecutive_losses": losses,
                "remaining_before_block": self.max_consecutive_losses - losses,
            }

    def is_blocked(self, product_id: str) -> bool:
        """Check if a product is currently blocked."""
        if (
            self.consecutive_losses.get(product_id, 0) >= self.max_consecutive_losses
            and product_id not in self.blocked_until
        ):
            self.blocked_until[product_id] = time.time() + self.cooldown_seconds
        if product_id not in self.blocked_until:
            return False
        if time.time() >= self.blocked_until[product_id]:
            # Cooldown expired, unblock
            del self.blocked_until[product_id]
            self.consecutive_losses[product_id] = 0  # Reset counter
            return False
        return True

    def get_blocked_products(self) -> dict[str, dict]:
        """Get all currently blocked products with info."""
        result = {}
        for pid, unblock_at in list(self.blocked_until.items()):
            if time.time() >= unblock_at:
                # Expired, clean up
                del self.blocked_until[pid]
                self.consecutive_losses[pid] = 0
            else:
                remaining = unblock_at - time.time()
                result[pid] = {
                    "consecutive_losses": self.consecutive_losses.get(pid, 0),
                    "total_losses": self.total_losses.get(pid, 0),
                    "total_wins": self.total_wins.get(pid, 0),
                    "cooldown_remaining_seconds": round(remaining, 0),
                    "unblock_at": datetime.fromtimestamp(
                        unblock_at, tz=timezone.utc
                    ).isoformat(),
                }
        return result

    def tick(self) -> list[str]:
        """Call periodically to clean up expired blocks. Returns unblocked products."""
        unblocked = []
        for pid in list(self.blocked_until.keys()):
            if time.time() >= self.blocked_until[pid]:
                del self.blocked_until[pid]
                self.consecutive_losses[pid] = 0
                unblocked.append(pid)
        return unblocked

    def summary(self) -> dict:
        """Get full summary."""
        return {
            "max_consecutive_losses": self.max_consecutive_losses,
            "cooldown_seconds": self.cooldown_seconds,
            "currently_blocked": self.get_blocked_products(),
            "consecutive_losses": dict(self.consecutive_losses),
            "total_losses": dict(self.total_losses),
            "total_wins": dict(self.total_wins),
        }

    def _load(self):
        """Load state from file."""
        try:
            with open(self.state_path) as f:
                data = json.load(f)
            self.consecutive_losses = data.get("consecutive_losses", {})
            self.total_losses = data.get("total_losses", {})
            self.total_wins = data.get("total_wins", {})
            blocked_until = data.get("blocked_until", {})
            if isinstance(blocked_until, dict):
                now = time.time()
                self.blocked_until = {
                    str(pid): float(unblock_at)
                    for pid, unblock_at in blocked_until.items()
                    if _to_float(unblock_at) > now
                }
        except (json.JSONDecodeError, KeyError):
            pass

    def save(self):
        """Persist state to file."""
        if not self.state_path:
            return
        self.tick()
        data = {
            "consecutive_losses": self.consecutive_losses,
            "total_losses": self.total_losses,
            "total_wins": self.total_wins,
            "blocked_until": self.blocked_until,
            "saved_at": utc_now_iso(),
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w") as f:
            json.dump(data, f, indent=2)


def _to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    # Demo
    import argparse

    parser = argparse.ArgumentParser(description="Death Spiral Prevention Demo")
    parser.add_argument("--max-losses", type=int, default=3)
    parser.add_argument("--cooldown", type=int, default=3600)
    args = parser.parse_args()

    tracker = LossTracker(
        max_consecutive_losses=args.max_losses,
        cooldown_seconds=args.cooldown,
    )

    print("=" * 60)
    print("DEATH SPIRAL PREVENTION — Demo")
    print("=" * 60)

    # Simulate BERT-USD death spiral
    products = ["BERT-USD", "BMB-USD", "APE-USD"]

    for i in range(10):
        for pid in products:
            if tracker.is_blocked(pid):
                print(f"  Poll {i}: {pid} BLOCKED (death spiral)")
                continue

            # Simulate: BERT always loses, APE always wins, BMB loses first 3 then wins
            if pid == "BERT-USD":
                won = False
            elif pid == "BMB-USD":
                won = i >= 3
            else:
                won = True

            result = tracker.record_close(pid, won)
            if result["action"] == "blocked":
                print(
                    f"  Poll {i}: {pid} → LOSS #{result['consecutive_losses']} → "
                    f"🚨 BLOCKED for {result['cooldown_seconds']}s"
                )
            elif result["action"] == "loss":
                print(
                    f"  Poll {i}: {pid} → LOSS #{result['consecutive_losses']} "
                    f"({result['remaining_before_block']} until block)"
                )
            else:
                print(f"  Poll {i}: {pid} → WIN ✅")

    print(f"\nSummary:")
    summary = tracker.summary()
    for pid in products:
        wins = summary["total_wins"].get(pid, 0)
        losses = summary["total_losses"].get(pid, 0)
        blocked = pid in summary["currently_blocked"]
        print(f"  {pid}: {wins}W/{losses}L {'🚨 BLOCKED' if blocked else ''}")
