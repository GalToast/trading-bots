# Performance Review

Last updated: 2026-04-08

## Aggregate

- **Total closed trades:** 852
- **Total PnL:** -$960.12
- **Wins:** 244 (avg +$17.19)
- **Losses:** 608 (avg -$8.48)
- **Win rate:** 28.6%

## Bleeding Symbols (top 10)

| Symbol | Trades | PnL | Win Rate | Status |
|--------|--------|-----|----------|--------|
| GER30 | 21 | -$748.70 | 43% | BANNED from PRICE |
| NZDUSD | 22 | -$522.92 | 27% | Consider banning |
| XAUUSD | 27 | -$397.52 | 22% | Consider banning |
| USDHKD | 25 | -$326.68 | 0% | BAN |
| JPN225 | 21 | -$236.38 | 19% | Consider banning |
| GBPNZD | 41 | -$156.49 | 49% | Monitor |
| EURDKK | 7 | -$76.77 | 0% | BAN |
| CHFJPY | 14 | -$52.88 | 14% | Consider banning |
| NZDCHF | 16 | -$37.83 | 25% | Monitor |
| AUS200 | 14 | -$23.72 | 21% | Monitor |

## Profitable Symbols (top 10)

| Symbol | Trades | PnL | Win Rate | Status |
|--------|--------|-----|----------|--------|
| AUDCHF | 31 | +$536.50 | 58% | FOCUS |
| GBPUSD | 33 | +$390.50 | 52% | FOCUS |
| NAS100 | 18 | +$228.28 | 22% | Profitable despite low WR |
| SPX500 | 11 | +$75.49 | 45% | OK |
| AUDJPY | 11 | +$75.39 | 36% | OK |
| AUDUSD | 28 | +$125.13 | 36% | OK |
| EURAUD | 9 | +$129.04 | 33% | OK |
| GBPAUD | 17 | +$70.27 | 6% | Profit from few big wins |
| US30 | 9 | +$54.82 | 33% | OK |
| USDCHF | 45 | +$48.55 | 56% | OK |

## Exit Mechanism Performance

### Losers

| Exit Reason | Trades | Total PnL | Avg |
|-------------|--------|-----------|-----|
| CONCENTRATION_RELEASE | 1 | -$1,467.42 | -$1,467 |
| SYNC_CLOSE (various) | ~50 | ~-$1,000 | ~-$20 |
| GEMINI_DEEP_LOSS_CUT | 2 | -$447.51 | -$224 |
| EARLY_FAIL | 179 | -$259.05 | -$1.45 |
| REVERSAL | 45 | -$21.85 | -$0.49 |
| GEMINI_ZOMBIE_CUT | 24 | -$18.29 | -$0.76 |
| ZOMBIE_CUT | 14 | -$8.05 | -$0.58 |

### Winners

| Exit Reason | Trades | Total PnL | Avg |
|-------------|--------|-----------|-----|
| FINANCED | 58 | +$1,734.60 | +$29.91 |
| CLOSE_POSITION | 235 | +$387.74 | +$1.65 |
| TRAIL | 90 | +$284.47 | +$3.16 |
| ADOPTED_WIN_BAG | 5 | +$214.94 | +$42.99 |
| GEMINI_PULLBACK_TRAIL | 1 | +$136.30 | +$136.30 |
| BE_PLUS_COST_TRAIL | 2 | +$91.31 | +$45.66 |
| TIGHT_TRAIL | 23 | +$17.93 | +$0.78 |
| GEMINI_CUT | 9 | +$0.28 | +$0.03 |

## Key Findings

### What's Bleeding

1. **0% win rate exotics** — USDHKD (25 trades, all losses) and EURDKK (7 trades, all losses) are pure bleed. Ban these symbols.

2. **EARLY_FAIL** — 179 trades averaging -$1.45 each = -$259 total. Entries getting killed by spread/conditions within seconds. This is either too-aggressive sizing on entry or the spread check isn't tight enough.

3. **CONCENTRATION_RELEASE** — Single trade lost $1,467. This exit mechanism has no position sizing cap.

4. **GEMINI_DEEP_LOSS_CUT** — 2 trades, -$448 avg. The deep loss cut is working (cutting losses) but the entries that trigger it are very wrong.

5. **GER30** — $-749 across 21 trades. The worst symbol by total drain.

### What's Working

1. **FINANCED exits** — $30 avg, 58 trades, +$1,734 total. This is the single best mechanism. It realizes profit from a green book while cleaning up a loser.

2. **TRAIL** — $3.16 avg, 90 trades, +$284 total. The most consistent scalable winner.

3. **AUDCHF** — +$537, 58% win rate, 31 trades. Best symbol by PnL.

4. **GBPUSD** — +$391, 52% win rate, 33 trades. Second best.

5. **GEMINI_PULLBACK_TRAIL** — Only 1 trade but +$136. The pullback trail variant is promising.

### What's Neutral

1. **CLOSE_POSITION** — 235 trades, +$388 total, $1.65 avg. These are forced cleanup exits that are slightly net positive. Volume is high but profit per trade is minimal.

2. **REVERSAL** — 45 trades, only -$22 total. The "never realize a loss on reversal" policy is working — most reversals exit flat or very small.

3. **ZOMBIE_CUT** — 14 trades, -$8 total. Working as designed — cutting small losses fast.

## Action Items

### Immediate (high confidence)

1. **Ban USDHKD and EURDKD** — 0% win rate, pure bleed
2. **Add cap on CONCENTRATION_RELEASE** — $1,467 single trade is unacceptable
3. **Investigate EARLY_FAIL timing** — 179 deaths at entry suggests spread/entry execution issue
4. **Focus entries on AUDCHF, GBPUSD** — best symbols by PnL and win rate

### Medium (needs verification)

1. **Evaluate GEMINI deep loss entries** — why are the 2 entries that triggered it so wrong?
2. **Consider banning GER30, XAUUSD, JPN225** — all heavily negative
3. **Scale up FINANCED exits** — best mechanism, should be the primary monetization path

### Low (exploratory)

1. **Investigate NAS100** — profitable ($228) despite 22% win rate. Big winners carry it.
2. **Test GEMINI_PULLBACK_TRAIL more** — only 1 data point but very strong.
