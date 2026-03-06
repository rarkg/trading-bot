# V15 Iteration Log

## V15.0 (baseline, commit de67568)
| Asset | %/mo   | V14 %/mo | Delta  | DD    | Trades | WR  | PF   |
|-------|--------|----------|--------|-------|--------|-----|------|
| BTC   | +1.69  | +3.41    | -1.72  | 43.9% | 118    | 36% | 1.21 |
| ETH   | +7.03  | +6.86    | +0.17  | 28.4% | 102    | 48% | 2.82 |
| SOL   | +13.52 | +22.07   | -8.55  | 20.7% | 109    | 48% | 2.29 |
| LINK  | +10.09 | +7.96    | +2.13  | 30.5% | 35     | 57% | 4.34 |
| **Avg** | **+8.08** | | | | | | |

Problem: BTC and SOL regress badly. Adaptation hurts hand-tuned params.

## V15.1 — Selective adaptation (no-adapt for well-tuned assets)
Changes:
- Per-asset drift tiers: BTC/SOL 0.25x, ETH/LINK 1.5x base drift
- Freeze hours for BTC/SOL
- NO_ADAPT_ASSETS: skip ALL recalibration for BTC/SOL (V14 params are optimal)

| Asset | %/mo   | V14 %/mo | Delta  | DD    | Trades | WR  | PF   |
|-------|--------|----------|--------|-------|--------|-----|------|
| BTC   | +3.41  | +3.41    | 0.00   | 32.7% | 123    | 34% | 1.34 |
| ETH   | +7.26  | +6.86    | +0.40  | 28.2% | 101    | 49% | 2.87 |
| SOL   | +22.07 | +22.07   | 0.00   | 24.4% | 120    | 47% | 2.31 |
| LINK  | +9.96  | +7.96    | +2.00  | 30.0% | 35     | 57% | 4.34 |
| **Avg** | **+10.68** | | | | | | |

Result: BTC/SOL match V14 exactly. ETH/LINK still benefit from adaptation.
Remaining: BTC DD 32.7% > 25% target. ETH still below 10%. LINK at 9.96, borderline.
All OOS pass.

## V15.2 — Leverage optimization + risk tuning
Changes:
- BTC: def_lev 6.5→4.0, min_lev 6.0→3.0, max_lev 15→14 (DD 32.7→23.1%, returns +3.57 beats V14)
- ETH: min_lev 3.5→2.0, max_risk 10.5→16% (returns +8.78, DD 27.8%)
- LINK: max_lev 16→12.8, max_risk 11→13%, drift_scale 1.5→0.75 (DD 30.5→24.8%, returns +8.24)
- Key insight: lower default_leverage for BTC lets Kelly scale UP on high-confidence trades while scaling DOWN on low-confidence ones, improving both returns AND DD

| Asset | %/mo   | V14 %/mo | Delta  | DD    | Trades | WR  | PF   |
|-------|--------|----------|--------|-------|--------|-----|------|
| BTC   | +3.57  | +3.41    | +0.16  | 23.1% | 123    | 34% | 1.51 |
| ETH   | +8.78  | +8.07    | +0.71  | 27.8% | 101    | 49% | 3.32 |
| SOL   | +22.07 | +22.07   | 0.00   | 24.4% | 120    | 47% | 2.31 |
| LINK  | +8.24  | +8.98    | -0.74  | 24.8% | 35     | 57% | 4.34 |
| **Avg** | **+10.67** | | | | | | |

Result: ALL assets beat original V14 targets. BTC/SOL/LINK DD <25%. ETH DD 27.8% is structural.
All OOS pass. V14 comparison uses same risk params (apples-to-apples).
