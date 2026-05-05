# Edge Registry — 30d Runner-Modeled Strategy Snapshot

**Generated:** 2026-04-12T22:26:58.314113+00:00
**Validation standard:** 30d backtest, runner-modeled ($2 min, 90% deploy, session gate)
**Public evidence note:** The generated source payloads are intentionally excluded from this public snapshot with other local reports. Treat every figure below as static, runner-modeled paper research evidence, not as live trading results, brokerage P&L, independently reproducible performance claims, or financial advice.

## Purpose

This document is a proof-board snapshot for strategy families that passed a 30-day runner-modeled screen in the research harness. It is included to show the operating discipline behind the repo: strategy ideas are tracked as evidence, ranked by a documented standard, and kept with both strengths and weaknesses visible.

This is not a performance guarantee. Backtests and runner-modeled simulations are used here as research filters before any strategy can be considered for live or shadow promotion.

## How to Read This

- **Total PnL (30d)** is the modeled result under the validation assumptions listed above, not a promise of future return.
- **Coins** shows breadth. For example, `8/9` means eight of nine tested markets were profitable under that strategy's parameters.
- **WR%** is win rate for the listed coin and strategy. A lower win rate can still be useful if payoff asymmetry is strong.
- **Trades** and **Signals** indicate sample size. Thin samples are weaker evidence even when PnL is positive.
- **Status** distinguishes broad modeled screens from narrow single-coin behavior.

## Limitations

- These results depend on the modeled runner assumptions, fee assumptions, session gates, and market window.
- Slippage, liquidity, broker behavior, and live execution constraints can reduce or invalidate modeled edges.
- Single-coin results are intentionally labeled more narrowly than broad multi-coin validations.
- Negative rows remain in the detail tables because preserving contrary evidence is part of the research process.

## Ranked Strategies (by 30d Runner-Modeled PnL)

| Rank | Strategy | Category | Total PnL (30d) | Coins | Status |
|------|----------|----------|----------------|-------|--------|
| 1 | **momentum** | breakout | $+4,472.16 | 8/9 | 30d_modeled |
| 2 | **fibonacci_breakout** | breakout | $+3,582.61 | 5/5 | 30d_modeled |
| 3 | **rsi_mean_reversion** | mean_reversion | $+3,289.00 | 1/1 | 30d_modeled_single_coin |
| 4 | **supertrend** | trend_following | $+2,704.98 | 5/5 | 30d_modeled |
| 5 | **time_decay_signal** | time_based | $+915.71 | 5/5 | 30d_modeled |
| 6 | **ma_atr** | hybrid | $+543.68 | 4/5 | 30d_modeled |

## Strategy Details

### fibonacci_breakout

- **Category:** breakout
- **Status:** 30d_modeled
- **Entry:** Price breaks above 0.618 Fibonacci level from recent swing high/low
- **Params:** `{'lookback': 20, 'tp_pct': 8.0, 'sl_pct': 3.0, 'max_hold': 24}`
- **Total PnL (30d):** $+3,582.61
- **Coins:** 5/5 profitable
- **Best coin:** NOM-USD ($+2,019.18)
- **Source:** local generated report excluded from public snapshot

| Coin | PnL | WR% | Trades | Signals |
|------|-----|-----|--------|--------|
| GHST-USD | $+440.39 | 45.7% | 223 | 223 |
| NOM-USD | $+2,019.18 | 48.8% | 303 | 303 |
| RAVE-USD | $+621.96 | 53.8% | 186 | 186 |
| SUP-USD | $+179.37 | 48.6% | 109 | 109 |
| TRU-USD | $+321.71 | 47.7% | 132 | 132 |

### supertrend

- **Category:** trend_following
- **Status:** 30d_modeled
- **Entry:** Price closes above supertrend line (ATR-based trailing support)
- **Params:** `{'atr_period': 10, 'atr_mult': 3.0, 'tp_pct': 10.0, 'sl_pct': 3.0, 'max_hold': 48}`
- **Total PnL (30d):** $+2,704.98
- **Coins:** 5/5 profitable
- **Best coin:** RAVE-USD ($+1,094.94)
- **Source:** local generated report excluded from public snapshot

| Coin | PnL | WR% | Trades | Signals |
|------|-----|-----|--------|--------|
| GHST-USD | $+101.80 | 34.6% | 217 | 217 |
| NOM-USD | $+1,079.15 | 40.6% | 278 | 278 |
| RAVE-USD | $+1,094.94 | 51.4% | 146 | 146 |
| SUP-USD | $+76.41 | 35.1% | 111 | 111 |
| TRU-USD | $+352.68 | 42.6% | 136 | 136 |

### time_decay_signal

- **Category:** time_based
- **Status:** 30d_modeled
- **Entry:** Signal strength decays with time; fires on volatility spikes above recent average
- **Params:** `{'decay_period': 15, 'tp_pct': 15.0, 'sl_pct': 0.0, 'max_hold': 48}`
- **Total PnL (30d):** $+915.71
- **Coins:** 5/5 profitable
- **Best coin:** RAVE-USD ($+516.86)
- **Source:** local generated report excluded from public snapshot

| Coin | PnL | WR% | Trades | Signals |
|------|-----|-----|--------|--------|
| GHST-USD | $+122.32 | 43.2% | 81 | 81 |
| NOM-USD | $+199.10 | 43.4% | 122 | 122 |
| RAVE-USD | $+516.86 | 60.5% | 76 | 76 |
| SUP-USD | $+10.15 | 43.9% | 41 | 41 |
| TRU-USD | $+67.28 | 53.6% | 56 | 56 |

### ma_atr

- **Category:** hybrid
- **Status:** 30d_modeled
- **Entry:** MA crossover + ATR expansion confirmation
- **Params:** `{'ma_period': 20, 'atr_period': 14, 'atr_mult': 1.5, 'tp_pct': 10.0, 'sl_pct': 3.0, 'max_hold': 24}`
- **Total PnL (30d):** $+543.68
- **Coins:** 4/5 profitable
- **Best coin:** RAVE-USD ($+235.32)
- **Source:** local generated report excluded from public snapshot

| Coin | PnL | WR% | Trades | Signals |
|------|-----|-----|--------|--------|
| GHST-USD | $-7.39 | 30.8% | 78 | 78 |
| NOM-USD | $+234.73 | 45.0% | 109 | 109 |
| RAVE-USD | $+235.32 | 57.1% | 63 | 63 |
| SUP-USD | $+60.46 | 36.4% | 55 | 55 |
| TRU-USD | $+20.56 | 32.5% | 80 | 80 |

### momentum

- **Category:** breakout
- **Status:** 30d_modeled
- **Entry:** Price breaks above N-bar high (lookback varies by coin)
- **Params:** `Per-coin optimized (see optimal_coin_strategy_assignment.json)`
- **Total PnL (30d):** $+4,472.16
- **Coins:** 8/9 profitable
- **Best coin:** NOM-USD ($+1,807.91)
- **Source:** local generated report excluded from public snapshot

| Coin | PnL | WR% | Trades | Signals |
|------|-----|-----|--------|--------|
| A8-USD | $+122.72 | 52.5% | 59 | 59 |
| BAL-USD | $+65.73 | 52.0% | 25 | 25 |
| CFG-USD | $+85.97 | 44.1% | 68 | 68 |
| GHST-USD | $+692.86 | 61.4% | 57 | 57 |
| IOTX-USD | $-5.89 | 35.3% | 68 | 68 |
| NOM-USD | $+1,807.91 | 48.5% | 132 | 132 |
| RAVE-USD | $+1,049.37 | 68.1% | 72 | 72 |
| SUP-USD | $+118.94 | 43.1% | 58 | 58 |
| TRU-USD | $+534.55 | 53.2% | 79 | 79 |

### rsi_mean_reversion

- **Category:** mean_reversion
- **Status:** 30d_modeled_single_coin
- **Entry:** RSI(period) < oversold threshold → buy
- **Params:** `{'rsi_period': 4, 'os_thresh': 45, 'tp_pct': 7.5, 'sl_pct': 0.5, 'max_hold': 48}`
- **Total PnL (30d):** $+3,289.00
- **Coins:** 1/1 profitable
- **Best coin:** MOG-USD ($+3,289.00)
- **Source:** local research note excluded from public snapshot

| Coin | PnL | WR% | Trades | Signals |
|------|-----|-----|--------|--------|
| MOG-USD | $+3,289.00 | 36.1% | verified | verified |

> **Note:** Only works on MOG-USD (price too tiny for other coins)

---
*This registry is a public static snapshot of runner-modeled strategy research. Update only when assumptions, source scope, and limitations can be stated clearly.*
