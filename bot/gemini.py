from __future__ import annotations


def get_gemini_signal(
    *,
    symbol,
    diagnostics=None,
    timeframe_m5,
    get_bars,
    calc_atr,
    calc_ema,
    calc_rsi,
):
    """
    DISABLED — 2026-04-09

    215 trades, -$5,786 in 24hrs (52% of total loss).
    297 historical trades with dozens of $100+ losers.
    Root cause: structurally negative expectancy — EMA cross + RSI on lagging data.

    The v2 hardening (volume confirm, M15 alignment, 0.82 conf) was insufficient.
    At $463/hour account bleed, we cannot afford to test in production.

    To re-enable: restore the v2 logic from git history.
    """
    return None, 0.0, 0, None, None
