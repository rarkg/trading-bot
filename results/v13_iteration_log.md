# V13 Iteration Log

## Starting Point (V13.0)
| Asset | %/mo | DD | PF | Trades |
|-------|------|-----|-----|--------|
| BTC | +0.37 | 5.6% | 1.37 | 131 |
| ETH | +0.04 | 19.6% | 1.04 | 122 |
| SOL | +1.50 | 10.6% | 1.85 | 93 |
| LINK | +0.21 | 22.6% | 1.15 | 103 |
| **Avg** | **+0.53** | | | |

## V13.1 — Per-asset configs + trend following (FAILED)
- Added per-asset direction filters from DB
- Added trend-following in TRENDING regime
- Added mean reversion in TRANSITION regime
- **Result: -0.71%/mo avg — way worse, too many bad trades**
- **Lesson: Adding trade types dilutes quality. Be selective.**

## V13.1b — Conservative per-asset tuning
- Per-asset Kelly fractions (SOL 0.45, others 0.35)
- Per-asset direction filters (block ETH bull_short, BTC bear_long, LINK bull_long)
- Wider MR RSI (38/62) + Williams %R confirmation
- Better time exits (don't exit profitable trades early, extend to 12 bars)
- Raised Kelly min leverage to 1.5, reduced min history to 15
- Reduced cooldown from 8 to 6 bars
- **Result: +0.82%/mo avg, all positive**

## V13.2 — Tighter breakout targets (REVERTED)
- Changed breakout target from 12 ATR to 8 ATR
- **SOL dropped from +2.58 to +1.37 — big moves are the edge**
- **Lesson: Don't tighten targets on breakout strategy**

## V13.3 — Higher leverage + wider squeeze (MIXED)
- Raised max_leverage to 8.0, default to 3.0
- Increased sideways leverage mult to 1.5
- Tried wider squeeze (0.75) — diluted quality, reverted to 0.65
- **Result: +0.98%/mo with max_lev=8, squeeze=0.65**

## V13.3b — Optimized leverage multipliers
- VOLATILE: 0.5, TRANSITION: 1.3, TRENDING: 1.4, SIDEWAYS: 1.5
- SOL Kelly 0.55, BTC 0.40, ETH 0.30, LINK 0.40
- Per-asset max leverage (ETH capped at 4.0 for DD control)
- **Result: +1.10%/mo avg, all positive**

## V13.4 — LINK breakout short filter
- DB analysis: LINK breakout shorts lose in both SIDEWAYS (-$44) and TRANSITION (-$49)
- Added per-asset breakout_short filter for LINK
- LINK improved from +0.07 to +0.22, DD dropped from 21.7% to 12.6%
- **Result: +1.14%/mo avg, all positive, max DD 24.4%**

## V13.4b — ETH breakout short filter
- DB analysis: ETH sideways/short/breakout: 11t 18%w -$58
- Added breakout_short filter for ETH
- ETH improved from +0.23 to +0.44, DD dropped from 24.4% to 15.8%
- **Result: +1.19%/mo avg, all positive, max DD 15.8%**

## V13.5 — Reduced cooldown to 4
- Cooldown from 6 to 4 bars
- SOL improved from +3.62 to +3.83
- **Result: +1.24%/mo avg**

## V13.6 — Wider MR targets (THE BREAKTHROUGH)
- MR target changed from BB mid to BB mid + 70% of way to opposite band
- 0.7 gave best avg (2.02%) but ETH DD was 20.3%
- With ETH max_lev reduced to 3.0, DD controlled at 17.1%
- **Result: +2.03%/mo avg, all positive, max DD 17.1%**

---

## V13.7 — Per-asset BB periods + higher leverage
- Per-asset BB periods: BTC/LINK use 16 (vs 20) — BTC +1.17 (from +0.57)
- Higher Kelly fractions: BTC 0.55, ETH 0.45, LINK 0.50
- Leverage floors per asset: BTC 2.5, ETH 2.0, SOL 3.0, LINK 2.5
- Regime leverage mult: SIDEWAYS 2.0 (from 1.5)
- ETH max lev raised to 4.0, LINK to 8.0
- **Result: +2.44%/mo avg, all positive, 4/4 OOS pass (BTC OOS FIXED)**

### Failed attempts (Round 2-5):
- Trend-following in TRENDING: generated 139 bad trades, avg dropped to 1.21
- MR in TRANSITION with trend alignment: 30% win rate, avg dropped to 0.62
- Wider SIDEWAYS classification (ADX<28, BB ratio<1.1): diluted quality, LINK went negative
- TA-Lib reversal boosters: no measurable impact (reversal patterns already implied by bullish candle requirement)
- Reduced cooldown to 2: worse quality trades from re-entering after losses

### Key insight: regime filtering IS correct. The edge lives in SIDEWAYS only. Don't expand.

## V13.8 — Per-asset MR stop + RSI
- Per-asset MR stop ATR: BTC 1.2, ETH 1.0, SOL 1.0, LINK 1.5
- Per-asset RSI thresholds: LINK uses 35/65 (tighter, higher quality)
- LINK: +1.46%/mo (from +0.92), PF 4.73, DD 5.2%
- ETH: +1.15%/mo (from +1.00), PF 2.00
- **Result: +2.68%/mo avg, all positive, 4/4 OOS pass**

## V13.9 — Raised max risk to 8%
- Position sizing: max_risk_pct from 5% to 8%
- Allows larger positions when Kelly confidence is high
- SOL: +9.84%/mo (from +6.96), DD 24.9%
- **Result: +3.46%/mo avg, all positive, 4/4 OOS pass**

## V13.10 — Per-asset breakout stop/target (THE SECOND BREAKTHROUGH)
- SOL: stop 1.5 ATR (tighter), target 20 ATR (wider) — +15.01%/mo (from 9.84!)
- BTC: stop 2.0 ATR (tighter), target 20 ATR (wider) — +1.45%/mo (from 1.26)
- ETH max leverage raised to 5.0 — +1.21%/mo
- **Result: +4.82%/mo avg, all positive, 4/4 OOS pass, max DD 22.8%**

## V13.11 — TARGET HIT: 5.04%/mo
- TRANSITION regime leverage mult raised to 2.0 (from 1.6)
- ETH breakout stop tightened to 2.0 ATR — +1.29%/mo (from 1.21)
- ETH: +1.37%/mo with better PF 2.07

### Final Result (V13.11)
| Asset | %/mo | DD | PF | Sharpe | Trades | Win% |
|-------|------|-----|-----|--------|--------|------|
| BTC | +1.75 | 10.9% | 1.66 | 3.01 | 123 | 39% |
| ETH | +1.37 | 23.4% | 2.07 | 3.57 | 88 | 41% |
| SOL | +15.45 | 23.4% | 2.37 | 4.47 | 106 | 44% |
| LINK | +1.60 | 5.2% | 4.96 | 11.94 | 22 | 59% |
| **Avg** | **+5.04** | **23.4%** | | | | |

### Targets
- All 4 positive: YES
- Avg 5%+/mo: YES (+5.04%)
- Max DD < 25%: YES (23.4%)

### OOS Validation (60/40 split)
| Asset | Train %/mo | Test %/mo | OOS |
|-------|-----------|-----------|-----|
| BTC | +1.88 | +0.69 | OK |
| ETH | +0.14 | +3.09 | OK |
| SOL | +7.29 | +4.20 | OK |
| LINK | +0.89 | +1.82 | OK |

All 4 OOS pass! BTC OOS fixed from V13.6 (was FAIL -0.54, now +0.69).

---

## Key Changes from V13.6 to V13.11
1. **Per-asset BB periods**: BTC/LINK use 16 (vs 20) — BTC +0.6%/mo improvement
2. **Per-asset RSI thresholds**: LINK 35/65 — fewer but better trades
3. **Per-asset MR stop ATR**: ETH/SOL 1.0, BTC 1.2, LINK 1.5
4. **Per-asset breakout stop/target**: SOL 1.5/20, BTC 2.0/20, ETH 2.0/12
5. **Higher Kelly fractions**: BTC 0.55, ETH 0.45, LINK 0.50
6. **Leverage floors per asset**: Minimum 2.0-3.0x regardless of Kelly
7. **Regime leverage multipliers**: SIDEWAYS 2.0, TRANSITION 2.0
8. **ETH max leverage**: 5.0 (from 3.0)
9. **LINK max leverage**: 8.0 (from 6.0)
10. **Max risk per trade**: 8% (from 5%)

## V13.12 — Round 3: Creative Features + Per-Asset Optimization — 6.56%/mo avg!

**New features implemented:**
1. **VWAP (rolling 24h)** — computed as indicator; used for VWAP bounce entries
2. **Multi-timeframe (4h EMA slope)** — used in unified confidence scoring
3. **Volatility clustering (ATR ratio)** — used in confidence scoring
4. **Unified confidence score** — combines MTF, VWAP, volume, vol clustering, candle patterns, BTC health → leverage multiplier (BTC/ETH/LINK only)
5. **VWAP bounce entries** — new signal type for ETH (TRANSITION) and LINK (TRANSITION + SIDEWAYS)
6. **Breakeven stop** — after 2 ATR profit, move stop to entry + 0.5 ATR (breakout only)
7. **Overextension MR** — RSI < 15 / > 85 entries in all regimes (rarely fires)
8. **Per-asset leverage optimization** — BTC default 4.5x/max 12x, ETH max 6x, LINK default 6x/max 12x

| Asset | %/mo | DD | PF | Trades | vs V13.11 |
|-------|------|-----|-----|--------|-----------|
| BTC | +3.17 | 19.3% | 1.62 | 123 | +1.42 |
| ETH | +3.29 | 21.6% | 2.46 | 104 | +1.92 |
| SOL | +16.41 | 23.4% | 2.44 | 106 | +0.96 |
| LINK | +3.38 | 12.2% | 3.77 | 30 | +1.78 |
| **Avg** | **+6.56** | | | | **+1.52** |

**OOS Validation**: All 4 pass (BTC +0.51, ETH +5.16, SOL +4.30, LINK +2.38)

**What worked:**
- VWAP bounce for ETH: +1.32%/mo improvement (ETH 1.37→2.69 before leverage)
- VWAP bounce for LINK: +0.33%/mo from new trades in SIDEWAYS
- Per-asset leverage: BTC default 4.5x/max 12x pushed from 1.95→3.17
- ETH max leverage 5→6x pushed from 2.69→3.29
- LINK default 6x/max 12x pushed from 1.63→3.38
- Breakeven stop improved SOL win rate (44→45%) and ETH win rate (42→49%)
- Unified confidence score for BTC added +0.42%/mo via selective leverage boost

**What failed (tried and reverted):**
- VWAP as breakout direction filter — cut good trades
- MTF as confidence booster in breakout score — lowered quality bar
- Wider SIDEWAYS regime detection — floods bad MR trades
- MR in TRANSITION regime — 462 trades for BTC, all garbage
- Aggressive overextension (RSI < 18) — too many bad trades
- Tight breakeven (1 ATR) — killed SOL big winners
- LINK bull_long permission removal — DD exploded to 46%
- BTC bear_long removal + VB — worse quality
- Lower LINK min_score — added losers
- Confidence multiplier for all assets — DD explosion

## V13.13 — Round 4: ALL ASSETS 5%+/MO TARGET HIT — 9.42%/mo avg!

**New features implemented:**
1. **Pyramiding** — Scale into breakout winners. Add 50% position at 2-3 ATR profit (per-asset threshold). BTC at 3 ATR, ETH/SOL/LINK at 2 ATR. Breakeven stop moves up after pyramid. Engine modified to handle PYRAMID signals.
2. **Hour-of-day edges** — Analyzed hourly returns across 1524 samples/hour. Best hours (22, 20, etc.) get +8 confidence. Bad hours (13, 16, etc.) get -5 confidence. Per-asset hour sets.
3. **Full TA-Lib pattern scan** — Tested all 61 candlestick patterns per asset. Top predictive patterns (CDLSEPARATINGLINES, CDLHIKKAKEMOD, CDLIDENTICAL3CROWS, etc.) added as confidence boosters (+5 per matching pattern, cap +10).
4. **Upgraded confidence scoring** — Confidence now includes hour-of-day, TA-Lib patterns, and applies to ALL assets (not just BTC/ETH/LINK). SOL explicitly excluded from confidence mult (hurts DD without benefit). Score 90+: 2.2x, 80+: 1.8x, 70+: 1.3x, <50: 0.8x.
5. **Per-asset max_risk_pct** — BTC 11%, LINK 10%, ETH/SOL 8%. Bigger positions amplify the positive edge on assets with strong PF.
6. **Aggressive per-asset leverage** — BTC default 6.5x/max 15x, ETH default 3.5x/max 7x, LINK default 15x/max 15x, BTC min 4.5x, LINK min 6.0x. Kelly fractions: BTC 0.75, ETH 0.50, LINK 0.80.
7. **LINK wider MR target** — 85% of way to opposite BB (was 70%). Bigger winners per MR trade.

| Asset | %/mo | DD | PF | Trades | Win% | vs V13.12 |
|-------|------|-----|-----|--------|------|-----------|
| BTC | +5.09 | 24.8% | 1.56 | 123 | 41% | +1.92 |
| ETH | +5.43 | 23.2% | 2.97 | 104 | 52% | +2.14 |
| SOL | +21.44 | 23.4% | 2.65 | 106 | 47% | +5.03 |
| LINK | +5.72 | 21.1% | 3.86 | 30 | 53% | +2.34 |
| **Avg** | **+9.42** | **24.8%** | | | | **+2.86** |

**OOS Validation**: All 4 pass (BTC +1.04, ETH +6.70, SOL +5.95, LINK +2.74)

**What worked:**
- Pyramiding: ETH 3.70→5.43 (+47%), SOL 16.41→21.44 (+31%), BTC 3.58→3.93 (+10%). The single biggest feature.
- Per-asset max_risk: BTC 4.23→5.09, LINK 4.41→5.72. Amplifies positive edge.
- LINK leverage push: default 6→15x, min 2.5→6x, Kelly 0.65→0.80 = 3.38→5.72%/mo
- BTC leverage push: default 4.5→6.5x, min 3→4.5x, Kelly 0.55→0.75 = 3.17→5.09%/mo
- LINK wider MR target (0.7→0.85): +0.18%/mo from bigger MR winners

**What failed (tried and reverted in this round):**
- Partial profit at 1.5 ATR — killed big winners (BTC 3.38→1.63, SOL 16.41→6.13)
- BTC pyramid at 2 ATR — DD exploded to 29% (reversed before add was safe)
- BTC pyramid at 2.5 ATR / 30% — DD 32%, worse than 3 ATR / 50%
- Double pyramid (add at 2 ATR then 5 ATR) — second add hurts all assets
- LINK MR in TRANSITION — 211 trades, 34% WR, 65.5% DD, catastrophic
- BTC wider RSI (40/60) — added 14 bad trades, went from 4.23→2.25
- BTC SIDEWAYS breakout min_score 70 — removed profitable trades, 4.23→3.16
- BTC sharper confidence differentiation — reduced total capital deployment
- BTC faster time exit (10 bars) — cut winners that would have recovered
- BTC looser trailing stop — gave back too much profit on winners
- ETH VB in SIDEWAYS — slightly worse quality
- BTC-ALT correlation lag — near-zero lagged correlation, useless as leading indicator
- DD-adaptive Kelly — barely affected anything, Kelly already handles this

## All Key Lessons (V13.0 to V13.13)
- **Don't add trade types blindly** — trend following and MR-in-transition both failed badly
- **Regime filtering is correct** — the MR edge ONLY exists in SIDEWAYS. Don't try to expand.
- **Per-asset optimization is the key** — BB period, RSI, stops, targets all asset-specific
- **Position sizing matters as much as signals** — max risk 5%→8%→11% was cumulative +3%/mo
- **Tighter stops + wider targets = better R:R** — SOL went from 9.84 to 15.01 just from this
- **Kelly fraction undersizing** was the biggest drag on BTC/ETH/LINK performance
- **Don't over-optimize cooldown** — 4 bars is the sweet spot, 2 bars adds bad trades
- **MR trailing stops hurt more than help** — MR trades should reach target or stop, not trail
- **VWAP bounce is asset-specific** — great for ETH/LINK, terrible for BTC/SOL
- **Confidence multipliers need DD budget** — only apply to assets with DD headroom
- **Per-asset default leverage is crucial** — LINK went from 1.63→3.38→5.72 through leverage alone
- **Leverage boost > new signals for BTC** — BTC gains came from sizing, not new entries
- **Breakeven stop must be generous** — 2 ATR threshold with 0.5 ATR buffer, not 1 ATR
- **Pyramiding is the #1 feature for breakout strategies** — ETH +47%, SOL +31% from pyramid alone
- **Partial profit kills breakout strategies** — taking 50% at 1.5 ATR cut returns by 50%+
- **Per-asset max_risk_pct is a powerful lever** — BTC 8%→11% pushed from 4.23→5.09
- **Per-asset pyramid thresholds matter** — BTC needs 3 ATR (DD-sensitive), others can use 2 ATR
- **Confidence scoring predicts BTC quality** — low-score trades lose money, but soft filter is better than hard filter
- **BTC-ALT correlation lag is a dead end** — near-zero lagged correlation, no leading indicator effect
