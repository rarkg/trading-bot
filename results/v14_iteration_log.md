# V14 Iteration Log

## Target: ALL assets 10%+/mo, max DD <25%

## V14.0 (Baseline — copy of V13.13)
- BTC: +5.09%/mo (24.8% DD, PF 1.56)
- ETH: +5.43%/mo (23.2% DD, PF 2.97)
- SOL: +21.44%/mo (23.4% DD, PF 2.65)
- LINK: +5.72%/mo (21.1% DD, PF 3.86)
- Avg: 9.42%/mo

## V14.1 — Current Best
Changes from V13.13:
- SOL TRANSITION breakout min_score: 65 → 55 (biggest single gain: +15%/mo for SOL)
- LINK TRANSITION breakout min_score: 65 → 55
- Cross-asset momentum factor: +12 to confidence for ETH/LINK when 3/4 assets trending same way
- ETH Kelly: 0.50 → 0.55, ETH min_lev: 2.5 → 3.0, ETH default_lev: 3.5 → 4.0
- LINK Kelly: 0.80 → 0.85, LINK min_lev: 6.0 → 7.0, LINK max_lev: 15 → 16
- LINK regime_mult: TRANSITION 2.5x, SIDEWAYS 2.5x (was 2.0x)
- Double pyramiding for ETH/SOL/LINK (75% add, 2nd at 2x threshold)
- Overextension MR: regime_mult 2.5x for ultra-extreme (was 2.0x)

Results:
- BTC: +5.09%/mo (24.8% DD, PF 1.56) — UNCHANGED, DD-constrained
- ETH: +7.16%/mo (24.4% DD, PF 3.24) — +1.73 from cross-mom + leverage
- SOL: +36.47%/mo (22.5% DD, PF 2.68) — +15.03 from TRANSITION min_score 55
- LINK: +6.98%/mo (24.9% DD, PF 3.92) — +1.26 from leverage + regime mult
- Avg: 13.93%/mo (+4.51 from V13.13)
- All OOS pass

## Failed experiments:
1. MR in TRANSITION regime: generates hundreds of bad trades (-1.40%/mo BTC)
2. BTC VWAP bounce: loses money (3.86%/mo vs 5.09%)
3. LINK bull_long enabled: catastrophic (2.66%/mo, 59.7% DD)
4. Volume exhaustion exit: kills breakout winners (-1.29%/mo BTC)
5. Moderate extreme RSI (15-20/80-85): adds noise in TRENDING/VOLATILE
6. Lower TRANSITION short filter (70→65): all extra BTC trades are losers
7. BTC min_leverage 5.5: pushes DD over 25% without proportional return
8. Cross-asset momentum penalty: hurts more than helps

## Findings:
- BTC is fundamentally DD-constrained at 25% with 41% win rate
- SOL TRANSITION breakout at min_score 55 is the single biggest V14 win
- Cross-asset momentum as boost-only (no penalty) helps ETH/LINK
- LINK is trade-count constrained (only 30 trades due to direction filters)
- Double pyramiding helps modestly but mostly for already-winning trades
