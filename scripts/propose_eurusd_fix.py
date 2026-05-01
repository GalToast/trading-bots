"""
EURUSD Spread Gate Fix Proposal Generator

Reads the current spread gate configuration from mt5_bot_v10.py,
documents the EURUSD findings, proposes two fix options, and writes
the proposal to docs/eurusd_fix_proposal.md.
"""
import os
import re
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_FILE = os.path.join(REPO_ROOT, "mt5_bot_v10.py")
OUTPUT_FILE = os.path.join(REPO_ROOT, "docs", "eurusd_fix_proposal.md")

# --- Findings (from analysis) ---
FINDINGS = {
    "total_trades": 31,
    "spread_kills": 13,
    "early_fails": 6,
    "consecutive_losses": 8,
    "total_pnl": -1921.00,
    "total_losses": 22,
    "losses_prevented_by_0_5pip": 13,
}

# --- Current spread gate constants from mt5_bot_v10.py ---
CURRENT_CONSTANTS = {
    "MAX_SPREAD_PCT_FOREX": "0.04",          # 4 pips for forex (EURUSD is forex)
    "MAX_SPREAD_PCT_CRYPTO": "0.12",
    "MAX_SPREAD_PCT_EXOTIC": "0.08",
    "EXOTIC_SPREAD_MULTIPLIER": "0.33",
    "SPREAD_VS_STOP_MAX_RATIO": "0.30",
}

PROPOSED_CONSTANTS = {
    "MAX_SPREAD_PCT_FOREX": "0.005",         # 0.5 pips for forex (EURUSD is forex)
}


def read_current_constants():
    """Read actual current values from mt5_bot_v10.py."""
    values = {}
    try:
        with open(BOT_FILE, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        for name in CURRENT_CONSTANTS:
            # Match: NAME = value  (with optional comment)
            pattern = rf'^{re.escape(name)}\s*=\s*([^\n#]+)'
            m = re.search(pattern, content, re.MULTILINE)
            if m:
                values[name] = m.group(1).strip()
            else:
                values[name] = CURRENT_CONSTANTS[name]
    except FileNotFoundError:
        values = dict(CURRENT_CONSTANTS)
    return values


def build_proposal():
    current = read_current_constants()
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Calculate what 0.5 pips means in the bot's units
    # Current MAX_SPREAD_PCT_FOREX = 0.04 = 4 pips
    # Proposed = 0.005 = 0.5 pips
    current_pips = float(current["MAX_SPREAD_PCT_FOREX"]) * 100  # 0.04 -> 4 pips
    proposed_pips = 0.5

    total_pnl_str = f"${FINDINGS['total_pnl']:,.2f}"
    proposal = f"""# EURUSD Spread Gate Fix Proposal

**Date:** {timestamp}
**Status:** PROPOSAL — do NOT apply to live config without review
**Author:** AI analysis + team review

---

## 1. Problem Statement

EURUSD has been a persistent bleed in the live rearm lane:

| Metric | Value |
|--------|-------|
| Total trades | {FINDINGS['total_trades']} |
| Total PnL | **{total_pnl_str}** |
| Spread kills (SPREAD_KILL) | {FINDINGS['spread_kills']} |
| Early fails (EARLY_FAIL) | {FINDINGS['early_fails']} |
| Worst consecutive losses | {FINDINGS['consecutive_losses']} |
| Total losing trades | {FINDINGS['total_losses']} |

**19 of {FINDINGS['total_trades']} losing trades** (61%) ended via SPREAD_KILL or EARLY_FAIL — both spread-related failure modes.
EURUSD has burned **{total_pnl_str}** with no edge recovery.

---

## 2. Current Spread Gate

The spread gate is defined in `mt5_bot_v10.py` (lines ~242-244):

```python
MAX_SPREAD_PCT_FOREX = {current['MAX_SPREAD_PCT_FOREX']}   # = {current_pips:.1f} pips
MAX_SPREAD_PCT_CRYPTO = {current['MAX_SPREAD_PCT_CRYPTO']}
MAX_SPREAD_PCT_EXOTIC = {current['MAX_SPREAD_PCT_EXOTIC']}
```

For EURUSD (a forex symbol), the current gate allows spreads up to **{current_pips:.1f} pips** ({current['MAX_SPREAD_PCT_FOREX']} in bot units).

### How the gate works

The spread check runs at multiple points:
1. **Scoring phase** (`mt5_bot_v10.py:7157-7170`): Symbols with spread > max are excluded from scoring.
2. **Lane scoring** (`mt5_bot_v10.py:7249-7255`): Spread violation marks the symbol with a penalty.
3. **Pre-entry re-check** (`mt5_bot_v10.py:10564-10623`): Final spread verification before order submission, including:
   - Hard block at `max_spread * 1.2` (20% slippage buffer)
   - Lot size penalty when spread > 50% of max
   - Spread-vs-stop ratio check (`SPREAD_VS_STOP_MAX_RATIO = {current['SPREAD_VS_STOP_MAX_RATIO']}`)

### The Gap

{current_pips:.1f} pips is **too wide** for EURUSD. At 4-pip spread tolerance, the bot is entering positions where the spread alone consumes meaningful edge — especially on M15 timeframes where typical moves may only be 5-10 pips. The 13 SPREAD_KILL events confirm this.

---

## 3. Proposed Fixes

### Option A: Tighten Spread Gate to 0.5 Pips (RECOMMENDED)

Reduce `MAX_SPREAD_PCT_FOREX` from `{current['MAX_SPREAD_PCT_FOREX']}` ({current_pips:.1f} pips) to `0.005` ({proposed_pips} pips).

**Rationale:**
- 0.5 pips is a realistic tight-spread threshold for EURUSD during liquid hours
- Would have prevented **{FINDINGS['losses_prevented_by_0_5pip']} of {FINDINGS['total_losses']}** losing trades
- Less disruptive than a full blocklist — EURUSD can still trade during good conditions
- Forces entries only when market quality is genuinely favorable

**Expected impact:**
- Fewer entries overall (EURUSD will only fire during tight-spread windows)
- Higher average trade quality on EURUSD
- Reduced bleed from spread-driven losses

**Config diff:**

```diff
--- a/mt5_bot_v10.py
+++ b/mt5_bot_v10.py
@@ -239,7 +239,7 @@
 # === SPREAD LIMITS (percentage of price) ===
 # 0.04 = 4 pips on EURUSD, 0.12 = 12 pips on BTC
-MAX_SPREAD_PCT_FOREX = {current['MAX_SPREAD_PCT_FOREX']}
+MAX_SPREAD_PCT_FOREX = 0.005
 MAX_SPREAD_PCT_CRYPTO = {current['MAX_SPREAD_PCT_CRYPTO']}
 MAX_SPREAD_PCT_EXOTIC = {current['MAX_SPREAD_PCT_EXOTIC']}
```

**Also affected symbols:** All forex pairs (EURGBP, EURJPY, EURCHF, EURAUD, EURCAD, EURNZD, EURNOK, EURSEK, EURDKK, EURZAR, EURHKD, GBPUSD, GBPJPY, etc.) will inherit the tighter gate. This is intentional — tight spreads are a quality signal across all forex.

### Option B: Blocklist EURUSD from Live Rearm Lane (FALLBACK)

If Option A is applied and EURUSD continues to lose, or if the team decides EURUSD geometry needs a full retune:

1. Remove `EURUSD` from the active symbol pool in the live rearm lane configuration
2. Keep EURUSD in backtest/simulation lanes for geometry retuning
3. Re-admit EURUSD only after a validated backtest shows positive expectancy with the new geometry

**How to blocklist:**

In the rearm lane config (`configs/universal_10symbol_rearm.json` or equivalent), remove EURUSD from the symbol list. Alternatively, add a symbol-specific block in the bot:

```python
# In mt5_bot_v10.py, near the top of the entry gate:
BLOCKLISTED_SYMBOLS = {"EURUSD"}  # Temporarily removed pending geometry retune
```

**When to use:** If Option A reduces EURUSD entries to zero (too restrictive) OR if EURUSD continues to lose after tightening.

---

## 4. Recommendation

**Apply Option A first.** It is:
- Less disruptive (EURUSD still trades when spreads are tight)
- Reversible (one-line change)
- Addresses the root cause (spread quality at entry)
- Preserves EURUSD edge during good market conditions

**Escalate to Option B if:**
- EURUSD entries drop to zero (0.5 pip gate is never met)
- EURUSD continues to lose after 20+ trades under the tighter gate
- The team decides EURUSD geometry needs a full rebuild

---

## 5. Rollout Plan

1. **Review this proposal** — team discussion
2. **Apply Option A diff** to `mt5_bot_v10.py` on a test branch
3. **Backtest** — verify EURUSD behavior with 0.5 pip gate on historical data
4. **Canary deploy** — run in live rearm lane for 48 hours
5. **Evaluate** — if EURUSD entries are reasonable and losses decrease, promote to main
6. **Fallback** — if EURUSD still bleeds, apply Option B blocklist

---

## 6. Related Files

- `mt5_bot_v10.py` — spread gate constants (lines ~242-244)
- `bot/admission.py` — spread check logic at entry (lines ~1137-1370)
- `configs/universal_10symbol_rearm.json` — lane symbol pool
- `scripts/analyze_eurusd.py` — existing EURUSD analysis script
- `symbol_learner.json` — EURUSD learning state

---

*This is a PROPOSAL only. No live config files have been modified.*
"""

    return proposal


def main():
    proposal = build_proposal()

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        f.write(proposal)

    print(f"Proposal written to: {OUTPUT_FILE}")
    print()

    # Print summary
    current = read_current_constants()
    current_pips = float(current["MAX_SPREAD_PCT_FOREX"]) * 100
    print("=== EURUSD SPREAD GATE FIX PROPOSAL ===")
    print(f"Current spread gate (forex): {current['MAX_SPREAD_PCT_FOREX']} = {current_pips:.1f} pips")
    print(f"Proposed spread gate (forex): 0.005 = 0.5 pips")
    print()
    print(f"Findings: {FINDINGS['spread_kills']} SPREAD_KILL, {FINDINGS['early_fails']} EARLY_FAIL, "
          f"{FINDINGS['consecutive_losses']} consecutive losses, ${FINDINGS['total_pnl']:+,.2f} total")
    print(f"Option A (tighten gate) would have prevented {FINDINGS['losses_prevented_by_0_5pip']} of "
          f"{FINDINGS['total_losses']} losses")
    print(f"Option B (blocklist EURUSD) available as fallback")
    print()
    print("Recommendation: Apply Option A first, escalate to B if losses continue.")


if __name__ == "__main__":
    main()
