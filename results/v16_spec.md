# V16 Specification — Portfolio Intelligence Layer

## Goal
V16 adds a portfolio-level intelligence layer ON TOP of V15's per-asset adaptive strategy.
V15 optimizes each asset independently. V16 optimizes HOW MUCH capital goes to each asset and WHEN to pull back.

**Priority: Don't lose money > Make money.**

## Core Components

### 1. Risk Parity Allocation (Bridgewater-style)
- Don't allocate equal dollars — allocate equal RISK
- Measure each asset's trailing 30-day realized volatility
- Higher vol asset gets less capital, lower vol gets more
- Rebalance monthly
- Example: if SOL is 3x more volatile than BTC, SOL gets 1/3 the capital
- Each asset contributes equal risk to portfolio

### 2. Momentum Rebalancing (AQR/Renaissance-style)
- Rank all assets by trailing 3-month returns
- Top performer gets 35% allocation, second 30%, third 20%, worst 15%
- Blend with risk parity (50/50 weight)
- Rebalance monthly
- This rides the rotation — whoever is hot gets more capital

### 3. Drawdown Circuit Breaker (Citadel-style)
- Portfolio level: if total equity drops -10% from peak → cut ALL position sizes by 50%
- Per-asset level: if single asset hits -15% DD → STOP trading that asset until DD recovers to -8%
- Monthly loss limit: if any asset loses -5% in a calendar month → pause that asset for rest of month
- Re-entry: gradual scale-up (25% → 50% → 75% → 100%) over 4 winning trades after circuit break

### 4. Correlation Monitor
- Track rolling 30-day correlation between all asset pairs (6 pairs for 4 assets)
- If average pairwise correlation > 0.75 → reduce total portfolio exposure by 30%
- If BTC-ETH-SOL all correlated > 0.8 → max 2 concurrent positions (not 4)
- When correlation drops below 0.5 → resume normal allocation

### 5. Kelly with Drawdown Scaling
- Use half-Kelly as maximum (current Kelly fractions × 0.50)
- After any drawdown > 10%: drop to quarter-Kelly until 5 consecutive winners
- After drawdown recovery: gradual scale-up over 10 trades back to half-Kelly
- Never exceed half-Kelly regardless of win streak

## Implementation
- New file: `strategies/portfolio_manager_v16.py`
- Wraps V15 strategy instances — V16 doesn't change signal generation
- V16 controls: capital allocation, position sizing override, circuit breakers
- Runner: `run_v16_cross_asset.py`
- Must be compatible with both backtest and live paper trading

## Targets
- No asset should have a negative YEAR (2022, 2023, 2024, 2025 all positive)
- Max portfolio DD < 15% (down from 25% per-asset)
- Total portfolio return should still exceed 5%/mo average
- All OOS must pass

## Validation
- Run year-by-year comparison: V14 vs V15 vs V16
- Show correlation heatmap during high-correlation periods
- Show circuit breaker activations (when did it save us?)
- Show allocation changes over time

## Key Constraint
- fee_pct = 0.10 (worst-case, non-negotiable)
- Do NOT lower confidence min_score thresholds
