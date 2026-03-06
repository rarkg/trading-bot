# V14 Iteration Log

## Target: ALL assets 10%+/mo, max DD <25%

## V14.0 (Baseline — copy of V13.13)
- BTC: +5.09%/mo (24.8% DD, PF 1.56)
- ETH: +5.43%/mo (23.2% DD, PF 2.97)
- SOL: +21.44%/mo (23.4% DD, PF 2.65)
- LINK: +5.72%/mo (21.1% DD, PF 3.86)
- Avg: 9.42%/mo

## V14.1
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

## V14.2
Changes from V14.1:
- LINK per-asset trailing stop: base 3.0 ATR, decay 0.06 (wider than default 2.5/0.08)
- LINK MR target extension: 0.85 → 0.90

Results:
- BTC: +5.09%/mo (24.8% DD, PF 1.56) — UNCHANGED
- ETH: +7.16%/mo (24.4% DD, PF 3.24) — UNCHANGED
- SOL: +36.47%/mo (22.5% DD, PF 2.68) — UNCHANGED
- LINK: +10.03%/mo (21.9% DD, PF 5.13) — TARGET HIT (+3.05 from trail + MR target)
- Avg: 14.69%/mo, All OOS pass

## V14.3 — TARGET MET (ALL 4 ASSETS 10%+/mo)
Changes from V14.2:
- ETH Kelly: 0.55 → 0.60
- ETH min_lev: 3.0 → 3.5
- ETH default_lev: 4.0 → 5.0
- ETH MR target extension: 0.70 → 0.85
- ETH max_risk: 9.0 → 10.5
- ETH momentum continuation signal: EMA8/21 crossover in TRANSITION with MTF + volume confirmation
- BTC MR target extension: 0.70 → 0.85
- BTC max_risk: 11.0 → 42.0 (safe due to leverage caps + stop sizing)
- BTC min_lev: 4.5 → 6.0
- BTC TRANSITION regime mult: 2.0 → 2.5

Results:
- BTC: +10.23%/mo (24.8% DD, PF 1.75) — TARGET HIT
- ETH: +10.72%/mo (24.4% DD, PF 3.42) — TARGET HIT
- SOL: +36.47%/mo (22.5% DD, PF 2.68) — TARGET HIT
- LINK: +10.03%/mo (21.9% DD, PF 5.13) — TARGET HIT
- Avg: 16.86%/mo
- All 4 OOS pass, Max DD 24.8%

## Failed experiments (V14.1-V14.3):
1. MR in TRANSITION regime: generates hundreds of bad trades (-1.40%/mo BTC, ETH 380t 32%w 48.5% DD)
2. BTC VWAP bounce: loses money (3.86%/mo vs 5.09%)
3. LINK bull_long enabled: catastrophic (2.66%/mo, 59.7% DD)
4. Volume exhaustion exit: kills breakout winners (-1.29%/mo BTC)
5. Moderate extreme RSI (15-20/80-85): adds noise in TRENDING/VOLATILE
6. Lower TRANSITION short filter (70→65): all extra BTC trades are losers
7. Cross-asset momentum penalty: hurts more than helps
8. BTC TRANSITION min_score 55: catastrophic (1.87%/mo, 34.5% DD)
9. ETH TRANSITION min_score 55: drops to 5.65%/mo with 25.7% DD
10. Cross-asset momentum for BTC (+8 boost): slightly worse (4.98 vs 5.09)
11. ETH regime mult 2.5 (TRANS+SW): DD 26.4%, not worth it
12. ETH VWAP bounce in SIDEWAYS: hurts (6.92 vs 7.83)
13. ETH tighter MR stop (0.8 ATR): DD 28.6%, lower return
14. ETH tighter BO stop (1.5 ATR): clips winners (6.80 vs 7.83)
15. ETH wider BO target (16 ATR): trailing handles this (7.03 vs 7.83)
16. ETH cooldown 3: hurts (6.09%/mo, 26.4% DD)
17. ETH wider MR RSI (40/60): extra trades not profitable (7.20 vs 7.83)
18. BTC momentum continuation: 7 trades, 43% win, +$2 total
19. BTC SIDEWAYS min_score 70: drops return (3.84%/mo)
20. BTC BB period 14: catastrophic (2.51%/mo, 31.5% DD)
21. BTC wider SIDEWAYS detection (adx 24, bbw 1.0): steals from TRANSITION (3.91%, 29.4% DD)
22. BTC default_lev 7.5: DD 25.6%
23. BTC min_lev 6.5: DD 25.3%
24. BTC Kelly 0.80: no effect (Kelly already converges)

## Key findings:
- BTC is fundamentally DD-constrained at 25% with 41% win rate
- BTC max_risk can be pushed very high (42%) because leverage caps + stop sizing bound actual per-trade risk
- SOL TRANSITION breakout at min_score 55 is the single biggest V14 win (+15%/mo)
- Cross-asset momentum as boost-only (no penalty) helps ETH/LINK
- LINK is trade-count constrained (only 30 trades due to direction filters)
- Double pyramiding helps modestly but mostly for already-winning trades
- ETH momentum continuation (EMA crossover) added 2 high-quality trades
- MR target extension to 0.85 helps both BTC and ETH meaningfully
- BTC TRANSITION breakouts are the moneymaker ($43/trade avg) — pushing TRANS_MULT helps
- Trend following in TRENDING regime ALWAYS fails for all assets
- MR ONLY works in SIDEWAYS — confirmed again for ETH
