# Candle V2.1 Iteration Log

## Goal
Isolate candlestick pattern performance on hourly crypto data.
Primary signal = TA-Lib CDL patterns. Supporting = RSI, BB, volume, trend.

## Data
- BTC/ETH/SOL/LINK hourly OHLCV, 2022-01-01 to 2026-03-05 (36,577 bars/asset)
- Fee: 0.10%, Initial capital: $1,000, Risk: 2% per trade

---

## V2.1.0 — Baseline (all 61 patterns, no filters)
- BTC: 8764t, 22.8% WR, -1.99%/mo, DD 99.9%
- ETH: 8580t, 26.9% WR, -1.99%/mo, DD 99.8%
- SOL: 8518t, 27.4% WR, -2.00%/mo, DD 99.9%
- LINK: 8369t, 31.1% WR, -1.99%/mo, DD 99.8%

**Findings:** Massive overtrading. CDLENGULFING fires 5000+ times per asset (noise on hourly). Opposing pattern exit kills 60% of trades at 11-23% WR.

---

## V2.1.1 — Cooldown + trend filter + no opposing exit
Changes: 6-bar cooldown, EMA21/50 trend filter, removed opposing pattern exit, widened time exit to 48 bars

- BTC: 2581t, 33.2% WR, -1.61%/mo, DD 81.1%
- ETH: 2533t, 34.6% WR, -1.38%/mo, DD 70.0%
- SOL: 2610t, 35.1% WR, -1.61%/mo, DD 83.5%
- LINK: 2682t, 35.2% WR, -1.65%/mo, DD 84.4%

**Findings:** Trades cut from 8500 to 2600, WR up to 33-35%. CDLMARUBOZU emerged as best pattern. STOP exits dominate at 5-8% WR.

---

## V2.1.2 — Filtered patterns + mandatory volume + all tiers need confirms
Changes: Only 13 best patterns, mandatory volume on pattern candle, T1 needs 1 confirm, T2/T3 need 2, 8-bar cooldown, 2.5:3.5 ATR R:R

- BTC: 1623t, 36.8% WR, -0.96%/mo, DD 51.4%, PF 0.84
- ETH: 1581t, 35.5% WR, -1.06%/mo, DD 58.6%, PF 0.85
- SOL: 1588t, 36.8% WR, -0.96%/mo, DD 54.7%, PF 0.91
- LINK: 1597t, 37.6% WR, -0.68%/mo, DD 50.4%, PF 0.94

**Best pattern:** CDLMARUBOZU: 45.6% WR, 263 trades, $+292 (ONLY profitable pattern)

---

## V2.1.3 — Counter-trend scoring system
Changes: Score-based entry (RSI+BB+volume+ADX), all patterns enabled, 2:2 ATR R:R

- BTC: 3444t, 42.2% WR, -1.80%/mo, DD 91.0%
- ETH: 3332t, 42.8% WR, -1.71%/mo, DD 87.2%
- SOL: 3267t, 42.1% WR, -1.62%/mo, DD 83.3%
- LINK: 3374t, 42.1% WR, -1.80%/mo, DD 92.8%

**Findings:** Highest WR yet (42%) but 1:1 R:R needs >50% to profit after fees. Counter-trend scoring effective but not enough edge.

**5 patterns >45% WR:** CDL3WHITESOLDIERS (60%, 15 trades), CDLGRAVESTONEDOJI (48.4%, 64t), CDLMARUBOZU (46%, 809t), CDLHIGHWAVE (45.9%, 194t), CDLBELTHOLD (45.7%, 300t)

---

## V2.1.4 — Tight stop / wide target (asymmetric R:R)
Changes: 1.5 ATR stop, 5.0 ATR target, 2x leverage, 2+ confirmations all tiers

- BTC: 2428t, 28.9% WR, -1.91%/mo, DD 95.7%
- ETH: 2357t, 28.2% WR, -1.97%/mo, DD 98.7%
- SOL: 2087t, 31.6% WR, -1.73%/mo, DD 93.1%
- LINK: 2192t, 31.7% WR, -1.92%/mo, DD 96.3%

**Findings:** Tight stops get blown out on hourly crypto (89% stop rate). Wide targets rarely hit. Worse than V2.1.3.

---

## V2.1.5 — Best patterns + scoring + leverage + no trailing
Changes: 9 best patterns only, scoring system from V2.1.3, 3x leverage T1, 2:2.5 R:R, NO trailing stop

- BTC: 2127t, 45.2% WR, -1.92%/mo, DD 97.0%, PF 0.79
- ETH: 2055t, 42.7% WR, -1.98%/mo, DD 99.3%, PF 0.81
- SOL: 2065t, 44.7% WR, -1.89%/mo, DD 95.7%, PF 0.85
- LINK: 2124t, 44.4% WR, -1.95%/mo, DD 98.0%, PF 0.86

**Best patterns (>45% WR):** MARUBOZU 48.9%, SPINNINGTOP 47.1%, SHORTLINE 46.0%, CLOSINGMARUBOZU 45.5%

**MARUBOZU-only variant sweep:**

| Config | BTC | ETH | SOL | LINK |
|--------|-----|-----|-----|------|
| 1x, 2:2.5 | -0.90, 49% DD | -0.65, 42% DD | -0.59, 38% DD | -0.55, 32% DD |
| 1x, 2:3.5 | -0.73, 43% DD | -0.39, 38% DD | -0.08, 26% DD | -0.13, 21% DD |
| 2x, 2:2.5 | -1.42, 75% DD | -1.05, 64% DD | -0.88, 55% DD | -0.98, 54% DD |
| 2x, 2:3 score>=2 | **-0.33, 22% DD** | -0.60, 43% DD | **+0.40, 17% DD** | -0.25, 27% DD |
| 3x, 2.5:3.5 | -1.61, 83% DD | -0.56, 56% DD | -0.88, 55% DD | -0.56, 46% DD |
| No filter | -1.26, 64% DD | -1.05, 58% DD | -0.41, 31% DD | -1.22, 64% DD |

**Only profitable config:** SOL MARUBOZU, Score>=2, 2x leverage, 2:3 R:R = +0.40%/mo, DD 16.9%

---

## V2.2.0 — Multi-Indicator Confirmation (score-based, all indicators)
Changes: Added 11 indicators on top of baseline (StochRSI, Williams%R, MACD, CCI, EMA alignment, ATR percentile, Keltner, MFI, OBV slope, Range position, HH/LL). Score-based entry.

### Iteration 1: Baseline + Individual Indicator Contribution

**Baseline (RSI+BB+Vol+ADX, score>=2, R:R 2:2.5):** 44.3% avg WR

**Individual indicator deltas (added to baseline, sorted by contribution):**
| Indicator | Avg WR Delta | Notes |
|-----------|-------------|-------|
| EMA alignment (8/21/50) | +2.0% | Best single contributor |
| OBV slope | +1.6% | Accumulation/distribution |
| CCI | +0.6% | Oversold/overbought extremes |
| Stochastic RSI | +0.6% | More sensitive momentum |
| Range position | +0.5% | Where price is in recent range |
| ATR percentile | +0.5% | Volatility context |
| Williams %R | +0.3% | Marginal |
| HH/LL detection | +0.2% | Negligible |
| MACD | +0.1% | Negligible |
| MFI | +0.1% | Negligible |
| Keltner Channels | +0.1% | Negligible |

**Key finding:** EMA alignment and OBV slope are the only meaningful contributors.

### Iteration 2: Combine + R:R tuning

**Best configs achieving 51%+ WR across all assets:**

| Config | BTC | ETH | SOL | LINK | Avg | Trades | P&L |
|--------|-----|-----|-----|------|-----|--------|-----|
| All ind, R:R 3:2, ADX<20 | 60.1% | 61.4% | 59.1% | 61.3% | 60.5% | 2890 | -$2512 |
| All ind, R:R 3:2, s>=3 | 61.1% | 59.6% | 60.5% | 58.5% | 59.9% | 6979 | -$3512 |
| EMA+RSI+Vol s>=2, R:R 2:2 | 51.7% | 51.4% | 52.4% | 51.3% | 51.7% | 6544 | -$3263 |
| EMA+OBV+CCI+Vol s>=2, R:R 2:2 | 53.2% | 50.6% | 52.0% | 51.8% | 51.9% | 8793 | -$3559 |
| top5 s>=2 R:R 2:2 | 52.4% | 50.1% | 53.0% | 49.8% | 51.3% | 10388 | -$3715 |

**MARUBOZU-only with all indicators, s>=4, R:R 2:2:** ETH 55.0%, SOL 51.9%, but BTC/LINK below 50%.

**Short-only bias:** 51.1% avg (BTC 51.3%, LINK 51.6%) — shorts slightly better than longs.

### V2.2 Best Config
- **Indicators:** All 15 enabled
- **R:R:** Stop 3.0 ATR / Target 2.0 ATR (asymmetric, wider stop)
- **Score threshold:** >= 3.0
- **ADX max:** 20 (only low-trend environments)
- **Cooldown:** 16 bars
- **Time exit:** 96 bars
- **Avg WR: 60.5%** (BTC 60.1%, ETH 61.4%, SOL 59.1%, LINK 61.3%)
- **Trades:** 2890 total (~720/asset over 4.2 years)
- **P&L:** -$2,512 (unprofitable — R:R 3:2 needs 60% WR to break even, fees push breakeven to ~62%)

### V2.2 Conclusions

1. **WR > 51% target: ACHIEVED.** Multiple configs cross 51% on all assets.
2. **WR 60.5% with asymmetric R:R** — widening stop (3 ATR) and tightening target (2 ATR) mechanically boosts WR. This is not "real" alpha — it's just R:R geometry.
3. **The real edge is tiny.** At balanced R:R (2:2), best WR is 51.7% — only 1.7% above random. Fees (0.20%/roundtrip) destroy this edge.
4. **EMA alignment is the single most valuable indicator** (+2.0% WR). OBV is second (+1.6%).
5. **Hard filter mode** produces very few trades (426 with 4 indicators) but higher quality (49-51% WR).
6. **Fundamental limitation:** Candlestick patterns + indicators on hourly data have an edge ceiling around 52% WR at balanced R:R. This is not enough to be a profitable standalone strategy after fees.
7. **Curated 3-indicator combos (EMA+RSI+Vol or EMA+OBV+Vol)** perform as well as 15-indicator kitchen sink with 1/3 the complexity.
8. **ADX < 20-25 dramatically improves WR** — patterns work best in low-trend / ranging markets.

---

## Key Findings

### Which candlestick patterns work on hourly crypto?

**CDLMARUBOZU is the ONLY consistently viable pattern.** At 45-49% WR across iterations with proper filtering, it represents the strongest candlestick conviction signal (candle opens at one extreme, closes at the other — full directional commitment).

**Tier performance:**
- Tier 1 (textbook reversal patterns): 37-44% WR — ENGULFING fires too often, HAMMER/SHOOTINGSTAR unreliable
- Tier 2 (good patterns): 31-33% WR — HARAMI and doji patterns are noise on hourly
- Tier 3 (weak patterns): 35-44% WR — surprisingly, some T3 patterns (BELTHOLD, HIKKAKE, SPINNINGTOP) outperform T2

**Pattern-specific verdicts:**
| Pattern | Verdict | Best WR | Notes |
|---------|---------|---------|-------|
| CDLMARUBOZU | BEST | 48.9% | Only pattern approaching profitability |
| CDLSPINNINGTOP | OK | 47.1% | Decent but marginal |
| CDLSHORTLINE | OK | 46.0% | Too few trades to be reliable |
| CDLCLOSINGMARUBOZU | OK | 45.5% | MARUBOZU variant, similar signal |
| CDLBELTHOLD | Marginal | 44.4% | Consistent but not enough edge |
| CDLHIKKAKE | Marginal | 44.1% | Good trade count, mediocre WR |
| CDLENGULFING | BAD | 43.9% | Way too frequent, dilutes edge |
| CDLHAMMER | BAD | 38.4% | Classic pattern, doesn't work on hourly crypto |
| CDLHARAMI | BAD | 33-40% | High volume of trades, low WR |

### Core conclusion

**Candlestick patterns alone do NOT have enough edge on hourly crypto data to be a profitable standalone strategy.** The maximum achievable WR is ~49% (MARUBOZU with heavy filtering), which is barely above the breakeven line for reasonable R:R ratios. After fees (0.10% per side), the tiny edge evaporates.

The only marginally profitable config found: SOL MARUBOZU-only with score>=2 confirmations, 2x leverage, 2:3 R:R = +0.40%/mo. This is not a viable trading strategy.

### Implications for V15/V16

1. **MARUBOZU as confirmation signal:** Can strengthen squeeze/breakout entries when MARUBOZU aligns
2. **Pattern-based exit filter:** Using bearish MARUBOZU/ENGULFING to close winning positions may have value
3. **Don't build strategies around candle patterns on hourly data** — they were designed for daily charts
4. **Volume + RSI + BB extremes matter more than the pattern itself** — the confirmations carry more weight than the candle shape

### Stop vs Target vs Time Exit Analysis

Across all iterations, the pattern is consistent:
- STOP exits: 0-8% WR (always lose)
- TARGET exits: ~100% WR (always win)
- TIME exits: 44-96% WR (depends on R:R and timing)

This means the strategy is entirely dependent on stop-target placement, NOT on pattern quality. The candle pattern just determines when to enter, but the R:R mechanics dominate the outcome.

---

## V2.3 — Push WR to 65%+ (Multi-Timeframe + Quality Filters)

### Goal
Push avg WR from V2.2's 60.5% to 65%+ across all assets with SAME generic params.

### New Features Tested

| Feature | Best Avg WR | Delta from V2.2 | Trades | Verdict |
|---------|-------------|-----------------|--------|---------|
| Score threshold sweep (3→6) | 60.5% (s>=3) | +0.0 | 2890 | No help — higher scores kill trades without improving WR |
| Volatility regime (BB width) | 60.6% (BB<80pct) | +0.1 | 2783 | Marginal — filtering vol doesn't help much |
| Body ratio > 0.5 | 61.6% | +1.2 | 2385 | Small help from quality candles |
| Body > 0.3 + Vol > 1.5x | 62.5% | +2.1 | 1409 | Good combo, quality + conviction |
| **MTF any (4H OR daily)** | **66.8%** | **+6.3** | **2575** | **TARGET MET — huge edge from HTF alignment** |
| **MTF both (4H AND daily)** | **78.0%** | **+17.6** | **1648** | **MASSIVE — requiring both TFs eliminates noise** |
| Candle sequences | 61.1% | +0.6 | 2932 | Negligible benefit |
| Time-of-day filter | 60.3% (8-16 UTC) | -0.2 | 1931 | No help |
| Prev candle same dir (momentum) | 61.8% | +1.3 | 2278 | Small help |
| Prev candle opposite (reversal) | 58.6% | -1.9 | 2412 | Hurts |

### Best Stacked Configs

| Stack | BTC | ETH | SOL | LINK | Avg WR | Trades | P&L |
|-------|-----|-----|-----|------|--------|--------|-----|
| MTF any + vol_pat 1.5x | 68.2% | 63.8% | 70.5% | 68.0% | 67.6% | 1512 | +$1,880 |
| MTF any + seq + s>=4 | 65.9% | 65.5% | 66.0% | 67.8% | 66.3% | 1949 | +$982 |
| MTF both (base 3:2) | 78.0% | 75.8% | 79.0% | 79.4% | 78.0% | 1648 | +$16,659 |
| MTF both + R:R 5:2 + ADX<25 | 84.3% | 84.1% | 85.8% | 86.6% | 85.2% | 2149 | +$15,523 |

### V2.3 Final Best Config
- **All 15 V2.2 indicators enabled**
- **MTF require=both** (hourly pattern + 4H pattern + daily pattern aligned)
- **R:R:** Stop 5.0 ATR / Target 2.0 ATR
- **ADX max:** 25
- **Score:** >= 3.0
- **Cooldown:** 12 bars
- **Time exit:** 144 bars
- **Avg WR: 85.2%** (BTC 84.3%, ETH 84.1%, SOL 85.8%, LINK 86.6%)
- **Trades:** 2149 total (~537/asset over 4.2 years)
- **P&L:** +$15,523 (PROFITABLE across all assets)
- **Max DD:** 6.1-9.5% per asset

### Exit Analysis (Final Best)
- STOP: 68-82 per asset, 0% WR
- TARGET: 423-485 per asset, 100% WR
- TIME: 2-7 per asset, 0-20% WR

### V2.3 Key Findings

1. **Multi-timeframe alignment is the single biggest edge source.** Requiring hourly + 4H + daily pattern agreement adds +17.6% WR at same R:R. This is NOT R:R manipulation.
2. **MTF "any" (4H OR daily) already meets 65% target** at +6.3% delta, with 2575 trades.
3. **MTF "both" is more selective but dramatically more accurate** — 78% WR at balanced R:R with 1648 trades.
4. **Body quality + volume on pattern candle is the second-best enhancement** (+2.1% combined).
5. **Candle sequences provide negligible benefit** (+0.6%) — not worth the complexity.
6. **Time-of-day filtering has NO edge** on hourly crypto candle patterns.
7. **Previous candle momentum (same direction) helps slightly** (+1.3%), while reversal (opposite) hurts.
8. **Score threshold increases HURT WR** — the V2.2 optimal of s>=3 is already correct.
9. **Volatility regime filtering has minimal impact** — patterns don't work better in low-vol; MTF alignment is a much better filter.
10. **The "candlestick patterns don't work" conclusion from V2.2 was WRONG for multi-timeframe.** Patterns on a single timeframe are weak. Patterns confirming across 3 timeframes represent genuine institutional agreement.

### Why MTF Works

Candlestick patterns capture market microstructure — who won the battle for a given time period. When a bullish pattern appears on hourly AND 4H AND daily simultaneously, it means:
- Short-term traders are bullish (hourly)
- Swing traders are bullish (4H)
- Position traders are bullish (daily)

This triple alignment represents broad market consensus, which is much more likely to produce follow-through than a single-timeframe signal.

### Architecture Note

V2.3 resamples hourly data to 4H and daily, then runs all 21 TA-Lib patterns on each timeframe. The higher-TF signals are forward-filled back to hourly bars. Entry requires the hourly pattern direction to match at least one (any) or both (both) higher timeframe patterns firing in the same direction.

---

## V2.4 — Push WR as High as Physically Possible (90%+ Target)

### Goal
Push avg WR from V2.3's 85.2% to 90%+. Pure WR optimization — P&L secondary, minimum 100 trades total.

### New Features Tested

1. **4H Primary Timeframe** — patterns on 4H candles instead of hourly, confirmed by daily
2. **Extreme R:R Ratios** — up to 20:1 (very wide stop, very tight target)
3. **Higher Score Thresholds** — s>=6, 6.5, 7.0, 7.5, 8.0
4. **All 21 Patterns** — broader pattern set with high score filter for quality
5. **Volume Spike Requirement** — require 2x+ avg volume on pattern candle
6. **Consecutive TF Agreement** — prev bar on higher TF must also agree
7. **Long/Short Split Tracking** — track WR by direction

### Phase 1 Results (Individual Features)

| Feature | Best Avg WR | Delta from V2.3 | Trades | Verdict |
|---------|-------------|-----------------|--------|---------|
| 4H primary, R:R 6:1.5 | 91.3% | +6.0 | 2747 | **Huge — 4H reduces noise** |
| R:R 12:1 | 95.1% | +9.9 | 2980 | **Extreme R:R mechanically pushes WR** |
| R:R 10:1 | 94.8% | +9.6 | 3013 | Near-identical to 12:1 |
| R:R 8:1 | 94.3% | +9.1 | 3102 | Still very high |
| s>=4.0 | 86.5% | +1.3 | 1279 | Small help from higher score |
| s>=6.0 | 86.2% | +1.0 | 252 | Trades drop too fast |
| MARUBOZU-only | 83.5% | -1.7 | 493 | **HURTS — fewer patterns = fewer quality entries** |
| Volume spike 2x | 81.2% | -4.0 | 756 | **HURTS — vol filter kills good trades** |
| Consecutive TF | 82.6% | -2.6 | 683 | **HURTS — too restrictive** |
| Long-only | 82.3% | -2.9 | 1532 | Longs weaker than shorts |
| Short-only | 86.8% | +1.6 | 1208 | Shorts slightly better |
| Body ratio > 0.5 | 84.4% | -0.8 | 1812 | Marginal negative |

### Key Stacked Configs

| Config | BTC | ETH | SOL | LINK | Avg WR | Trades | P&L | Long WR | Short WR |
|--------|-----|-----|-----|------|--------|--------|-----|---------|----------|
| V2.3 baseline | 84.3% | 84.1% | 85.8% | 86.6% | 85.2% | 2149 | +$15,523 | 83% | 88% |
| 4H + R:R 10:1 | 93.3% | 95.5% | 95.2% | 96.1% | 95.0% | 3493 | — | 94% | 96% |
| all_pat + s>=5 + R:R 8:1 | 91.9% | 94.8% | 96.6% | 97.8% | 95.3% | 1117 | — | 95% | 95% |
| **all_pat + s>=6 + R:R 10:1** | **93.8%** | **98.0%** | **97.8%** | **100.0%** | **97.4%** | **374** | **+$781** | **98%** | **96%** |
| all_pat + s>=6 + R:R 10:1 + ADX<40 | 96.1% | 98.0% | 98.6% | 100.0% | **98.2%** | 596 | +$781 | 98% | 98% |
| **all_pat + s>=6.5 + R:R 10:1 + ADX<40** | **98.1%** | **100.0%** | **100.0%** | **100.0%** | **99.5%** | **357** | **+$553** | **99%** | **100%** |
| **all_pat + s>=7.0 + R:R 10:1 + ADX<40** | **98.2%** | **100.0%** | **100.0%** | **100.0%** | **99.6%** | **206** | **+$320** | **99%** | **100%** |
| 4H + s>=7.0 + R:R 10:1 | 95.9% | 100.0% | 99.2% | 100.0% | 98.8% | 486 | +$728 | 98% | 99% |
| 4H + s>=6.5 + R:R 10:1 | 96.0% | 99.4% | 99.5% | 100.0% | 98.7% | 778 | +$1,216 | 99% | 98% |

### V2.4 Final Best Config (Max WR)
- **Pattern set:** ALL 21 TA-Lib patterns
- **All 15 V2.2 indicators enabled**
- **MTF require=both** (hourly + 4H + daily aligned)
- **Score threshold:** >= 7.0
- **R:R:** Stop 10.0 ATR / Target 1.0 ATR
- **ADX max:** 40
- **Cooldown:** 12 bars
- **Time exit:** 144 bars
- **Avg WR: 99.6%** (BTC 98.2%, ETH 100.0%, SOL 100.0%, LINK 100.0%)
- **Trades:** 206 total (~52/asset over 4.2 years)
- **P&L:** +$320 (profitable but modest due to tight 1 ATR targets)
- **Long WR: 99%, Short WR: 100%**

### V2.4 Higher-Trade Alternatives
| Config | Avg WR | Trades | Best For |
|--------|--------|--------|----------|
| s>=7.0 | 99.6% | 206 | Max WR |
| s>=6.5 | 99.5% | 357 | Best balance |
| s>=6.0 + ADX<40 | 98.2% | 596 | More trades |
| 4H + s>=6.5 | 98.7% | 778 | Volume + WR |
| 4H + s>=6.0 | 98.1% | 1165 | High volume |

### Exit Analysis (s>=7.0 Final)
- BTC: 57 trades — STOP:1t/0%w, TARGET:56t/100%w
- ETH: 49 trades — TARGET:49t/100%w (perfect)
- SOL: 47 trades — TARGET:47t/100%w (perfect)
- LINK: 53 trades — TARGET:53t/100%w (perfect)

### V2.4 Key Findings

1. **99.6% WR achieved** with s>=7.0 + R:R 10:1 + all patterns + MTF both + ADX<40. Only 1 losing trade (BTC stop) out of 206.
2. **The three pillars of ultra-high WR:** (a) MTF both alignment, (b) extreme R:R (10:1 stop:target), (c) high score threshold (7+ indicators confirming). Each independently adds 5-10% WR; stacked they approach 100%.
3. **All 21 patterns + high score > selective patterns.** Using all patterns with s>=6+ acts as a natural quality filter — only entries where many indicators agree get through. This is BETTER than hand-picking patterns like MARUBOZU.
4. **Volume spike filters HURT.** Counter-intuitive but requiring 2x volume kills 4% WR. The MTF+score filter already selects quality; volume adds noise.
5. **Consecutive TF agreement HURTS.** Requiring the previous 4H/daily bar to also show patterns is too restrictive and removes valid setups.
6. **Shorts consistently beat longs** across all configs (88% vs 83% at baseline, 100% vs 99% at max). Crypto markets' upward bias makes short-side mean reversion more reliable.
7. **4H primary adds 1-6% WR** depending on R:R. Best at moderate R:R (6:1.5 → 91.3%). At extreme R:R the benefit narrows.
8. **R:R is the single biggest mechanical WR lever.** Going from 5:2 to 10:1 adds ~10% WR by making targets trivially easy to hit. This is expected — the trade-off is smaller per-trade profit.
9. **ADX<40 is optimal.** Raising from ADX<25 (V2.3) to ADX<40 adds more trades without hurting WR, since the MTF+score filter already ensures quality.
10. **Diminishing returns beyond s>=7.0.** s>=7.5 and s>=8.0 drop trades below 100 with marginal WR improvement. s>=7.0 is the sweet spot.

### Honest Assessment

The 99.6% WR is mechanically achievable but comes with trade-offs:
- **Tight targets (1 ATR)** mean each winning trade captures a small move
- **Wide stops (10 ATR)** mean rare losses are large
- **206 trades over 4.2 years** = ~4 trades/month/asset
- **P&L is +$320** — profitable but not spectacular
- The strategy is essentially "wait for extreme multi-timeframe consensus, take a tiny profit, and rarely get stopped out"

For PRACTICAL trading, the **s>=6.5 config (99.5% WR, 357 trades)** or **s>=6.0 + ADX<40 (98.2%, 596 trades)** offer better trade volume with near-identical WR.
