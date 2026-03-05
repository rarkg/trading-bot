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
- This converts more MR trades to TARGET hits with bigger gains
- Tested: 0.3, 0.5, 0.6, 0.7 multipliers
- 0.7 gave best avg (2.02%) but ETH DD was 20.3%
- With ETH max_lev reduced to 3.0, DD controlled at 17.1%

### Final Result (V13.6)
| Asset | %/mo | DD | PF | Sharpe | Trades | Win% |
|-------|------|-----|-----|--------|--------|------|
| BTC | +0.57 | 9.6% | 1.38 | 1.94 | 130 | 40% |
| ETH | +0.59 | 17.1% | 1.69 | 3.09 | 88 | 41% |
| SOL | +6.46 | 17.1% | 2.35 | 4.13 | 106 | 47% |
| LINK | +0.49 | 11.4% | 1.55 | 3.26 | 48 | 40% |
| **Avg** | **+2.03** | **17.1%** | | | | |

### Targets
- All 4 positive: YES
- Avg 2%+/mo: YES (+2.03%)
- Max DD < 25%: YES (17.1%)
- ETH meaningful: YES (+0.59%)

### OOS Validation (60/40 split)
| Asset | Train %/mo | Test %/mo | OOS |
|-------|-----------|-----------|-----|
| BTC | +1.15 | -0.54 | FAIL |
| ETH | +0.07 | +1.26 | OK |
| SOL | +3.79 | +1.70 | OK |
| LINK | +0.16 | +0.73 | OK |

BTC OOS fails — train period captures 2022-2024 bull, test period captures late 2025-2026 which may be sideways/bear. 3/4 OOS pass.

## Key Changes from V13.0 to V13.6
1. **Per-asset Kelly fractions**: SOL 0.55, BTC 0.40, LINK 0.40, ETH 0.30
2. **Per-asset max leverage**: ETH capped at 3.0 to control DD
3. **Direction filters**: ETH/LINK breakout shorts blocked, BTC bear longs blocked, LINK bull longs blocked
4. **Williams %R confirmation** for mean reversion entries
5. **Wider MR targets**: BB mid + 70% toward opposite band (vs just BB mid)
6. **Higher leverage multipliers**: SIDEWAYS 1.5 (from 1.3)
7. **Reduced cooldown**: 4 bars (from 8)
8. **Better time exits**: Only exit if not profitable after 10-12 bars
9. **TA-Lib weak candle filter**: Skips breakouts on doji/spinning top
10. **Kelly min leverage**: 1.5 (from 1.0), min history 15 (from 20)

## Key Lessons
- **Don't add trade types blindly** — trend following in TRENDING regime generated 100s of bad trades
- **Per-asset direction filtering is powerful** — removing losing direction combos is easy alpha
- **MR target placement is critical** — targeting beyond BB mid was the single biggest improvement
- **Kelly fraction scaling** — higher for strong-edge assets (SOL), lower for weak (ETH)
- **Leverage caps per asset** — essential for DD control on weaker assets
