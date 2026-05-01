"""Hungry Hippo Tier 0 -- Offensive Extreme Closure.

Closes extreme positions that are approaching breakeven after being in profit.
These are the FIRST positions to become deep losers when trend reverses.

This module is called from process_tick() BEFORE the defensive escape tiers
(Tier 1 time-based, Tier 2 surgical cut, Tier 3 full kill).

Usage:
    from hungry_hippo_tier0_offensive_escape import check_offensive_escape

    # Called from process_tick() BEFORE defensive escape tiers
    escaped = check_offensive_escape(
        open_tickets=engine.state.open_tickets,
        anchor=engine.state.anchor,
        step=engine.state.base_step_px,
        max_levels=engine.state.max_open_total,
        current_price=latest_price,
        pip_value=symbol_meta["pip_value"],
        volume=0.01,
        escape_profit_threshold_pct=0.001,  # Close if profit < 0.1%
        escape_loss_threshold_pct=0.0005,   # Cut if loss < 0.05%
    )

    for action in escaped:
        # action = {"ticket": ticket, "reason": "tier0_offensive_extreme", "pnl": pnl}
        execute_close(action["ticket"])
        log_escape_event(action)
"""
from __future__ import annotations

from typing import Any


def _compute_pnl_usd(
    direction: str,
    entry_price: float,
    current_price: float,
    pip_value: float,
    volume: float,
) -> float:
    """Compute PnL in USD for a position.

    For BUY:  (current_price - entry_price) * pip_value * volume
    For SELL: (entry_price - current_price) * pip_value * volume
    """
    if direction.upper() == "BUY":
        return (current_price - entry_price) * pip_value * volume
    else:
        return (entry_price - current_price) * pip_value * volume


def _distance_in_steps(entry_price: float, anchor: float, step: float) -> float:
    """Compute distance from anchor in steps."""
    if step <= 0:
        return 0.0
    return abs(entry_price - anchor) / step


def check_offensive_escape(
    open_tickets: list[dict[str, Any]],
    anchor: float,
    step: float,
    max_levels: int,
    current_price: float,
    pip_value: float,
    volume: float = 0.01,
    escape_profit_threshold_pct: float = 0.001,
    escape_loss_threshold_pct: float = 0.0005,
) -> list[dict[str, Any]]:
    """Identify extreme positions that should be closed offensively.

    An EXTREME position is one near the edge of the lattice:
        distance_in_steps >= (max_levels - 2)

    Escape conditions:
        - PnL > 0 but PnL < (entry_price * escape_profit_threshold_pct): close NOW (book the pennies)
        - PnL < 0 but abs(PnL) < (entry_price * escape_loss_threshold_pct): close NOW (cut before it widens)

    Args:
        open_tickets: List of ticket dicts from engine.state.open_tickets.
        anchor: Current lattice anchor price.
        step: Base step size in price units (e.g. engine.state.base_step_px).
        max_levels: Maximum total open levels (e.g. engine.state.max_open_total).
        current_price: Current mid/bid/ask price.
        pip_value: Dollar value per pip for this symbol.
        volume: Position volume (default 0.01).
        escape_profit_threshold_pct: Fractional threshold -- close if profit is
            positive but below this fraction of entry price.
        escape_loss_threshold_pct: Fractional threshold -- cut if loss is
            negative but abs(loss) is below this fraction of entry price.

    Returns:
        List of action dicts:
            {"ticket": ticket_dict, "reason": "tier0_offensive_extreme",
             "pnl": float, "direction": str, "entry_price": float,
             "distance_steps": float}
    """
    if not open_tickets:
        return []
    if anchor <= 0 or step <= 0:
        return []
    if max_levels <= 0:
        return []

    extreme_threshold_steps = max(1, max_levels - 2)
    actions: list[dict[str, Any]] = []

    for ticket in open_tickets:
        direction = str(ticket.get("direction", "")).upper()
        if direction not in ("BUY", "SELL"):
            continue

        # Use fill_price as the actual entry price; fall back to trigger_level
        entry_price = float(
            ticket.get("fill_price", 0.0)
            or ticket.get("entry_fill_price", 0.0)
            or ticket.get("trigger_level", 0.0)
        )
        if entry_price <= 0:
            continue

        # How far from anchor?
        dist_steps = _distance_in_steps(entry_price, anchor, step)
        if dist_steps < extreme_threshold_steps:
            continue  # Not an extreme position

        # Compute PnL
        pnl = _compute_pnl_usd(direction, entry_price, current_price, pip_value, volume)

        # Fractional thresholds relative to notional entry value
        profit_threshold = entry_price * escape_profit_threshold_pct
        loss_threshold = entry_price * escape_loss_threshold_pct

        should_escape = False
        if pnl > 0 and pnl < profit_threshold:
            # In profit but barely -- book the pennies before reversal
            should_escape = True
        elif pnl < 0 and abs(pnl) < loss_threshold:
            # Small loss -- cut before it widens
            should_escape = True

        if should_escape:
            actions.append({
                "ticket": ticket,
                "reason": "tier0_offensive_extreme",
                "pnl": pnl,
                "direction": direction,
                "entry_price": entry_price,
                "distance_steps": round(dist_steps, 2),
            })

    return actions


def apply_offensive_escape_to_engine(
    engine: Any,
    current_price: float,
    pip_value: float,
    volume: float = 0.01,
    escape_profit_threshold_pct: float = 0.001,
    escape_loss_threshold_pct: float = 0.0005,
) -> list[dict[str, Any]]:
    """Convenience wrapper that reads state from a TickStatefulRearmEngine and returns escape actions.

    The caller is responsible for executing the closes and removing the tickets
    from engine.state.open_tickets.

    Args:
        engine: TickStatefulRearmEngine instance.
        current_price: Current mid price.
        pip_value: Dollar value per pip.
        volume: Position volume.
        escape_profit_threshold_pct: Profit threshold fraction.
        escape_loss_threshold_pct: Loss threshold fraction.

    Returns:
        List of escape action dicts.
    """
    state = engine.state
    return check_offensive_escape(
        open_tickets=state.open_tickets,
        anchor=state.anchor,
        step=engine.base_step_px,
        max_levels=getattr(state, "max_open_total", 24),
        current_price=current_price,
        pip_value=pip_value,
        volume=volume,
        escape_profit_threshold_pct=escape_profit_threshold_pct,
        escape_loss_threshold_pct=escape_loss_threshold_pct,
    )
