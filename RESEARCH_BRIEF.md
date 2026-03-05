# V13 Research Brief — Deep Research Findings

## 1. Kelly Criterion for Crypto Trading

### The Formula
```
Kelly% = W - (1-W)/R
Where:
  W = win probability (from last N trades)
  R = avg_win / avg_loss ratio
```

### Implementation for Crypto Bot
- Use **fractional Kelly** (0.25x-0.5x of full Kelly) — full Kelly is mathematically optimal but too volatile for real trading
- Compute Kelly **per-asset, per-regime, per-direction** using rolling window of last 40-60 trades
- Kelly naturally solves the cross-asset problem: assets with poor edge (BTC, LINK) get tiny positions, assets with strong edge (SOL) get larger ones
- Kelly% maps directly to leverage: if Kelly says 15% of capital, and your margin is 40% of capital, leverage = 0.15/0.40 = 0.375x. If Kelly says 200%, leverage = 5x.
- **Cap Kelly leverage at 6x max** regardless of what the formula says (black swan protection)
- **Floor Kelly at 0** — negative Kelly means DON'T TRADE (expected value is negative)

### Rolling Kelly Implementation
```python
def compute_kelly(trades, fraction=0.3):
    """Fractional Kelly from recent trades."""
    if len(trades) < 20:
        return 0.5  # default conservative until enough data
    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]
    if not losses or not wins:
        return 0.5
    W = len(wins) / len(trades)
    R = abs(np.mean([t.pnl_pct for t in wins])) / abs(np.mean([t.pnl_pct for t in losses]))
    kelly = W - (1 - W) / R
    return max(0, kelly * fraction)  # fractional Kelly, floor at 0
```

### Key Insight from QuantStart
- Kelly assumes returns are normally distributed (they're NOT in crypto — fat tails)
- This means full Kelly WILL blow up in crypto. Fractional Kelly (0.25-0.3x) is essential
- Kelly also assumes parameters don't change over time — use rolling window to adapt

## 2. Regime Detection — The Critical Missing Piece

### Why Current Strategy Fails on BTC/LINK
- BTC spends ~60% of time in sideways/consolidation, ~25% trending, ~15% in sharp moves
- SOL spends ~40% sideways, ~35% trending, ~25% in sharp moves
- LINK is even choppier than BTC
- A breakout-only strategy applied to a mostly-sideways market = death by a thousand cuts (many small losses from false breakouts)

### Regime Classification Methods

#### Method 1: ADX + EMA Slope (Simple, Effective)
```
TRENDING:   ADX > 25 AND abs(EMA_slope) > threshold
SIDEWAYS:   ADX < 20 OR (ADX < 25 AND abs(EMA_slope) < threshold)  
VOLATILE:   ATR_percentile > 80th (regardless of direction)
TRANSITION: Everything else
```

#### Method 2: Bollinger Band Width Regime
```
SQUEEZE:    BB_width < 65% of avg (consolidation → potential breakout)
EXPANSION:  BB_width > 150% of avg (trending, already moving)
NORMAL:     Everything between
```

#### Method 3: Combined (Recommended)
```python
def detect_regime(adx, ema_slope, bb_width_ratio, atr_percentile):
    if atr_percentile > 85:
        return "VOLATILE"  # reduce all positions
    if adx > 25 and abs(ema_slope) > 0.3:
        return "TRENDING"  # breakout strategy
    if adx < 20 and bb_width_ratio < 0.8:
        return "SIDEWAYS"  # mean reversion strategy
    return "TRANSITION"    # small positions only
```

### Per-Regime Strategy

**TRENDING regime:**
- Use existing squeeze breakout strategy (V10 base)
- Full Kelly position sizing
- Trail stops with trend

**SIDEWAYS regime (THE BIG OPPORTUNITY):**
- Mean reversion: buy at lower Bollinger Band, sell at upper
- Or: buy at support (recent lows), sell at resistance (recent highs)
- Tighter stops (1.5 ATR instead of 2.5)
- Faster exits (target = middle band or SMA20, not 12 ATR)
- Lower leverage (0.5x Kelly since edge is smaller but more consistent)
- Higher win rate expected (60-70%) but smaller gains per trade
- This should dramatically improve BTC and LINK which spend most time here

**VOLATILE regime:**
- Reduce position sizes by 50-70%
- Wider stops (3.5 ATR)
- Only trade on very high confidence signals
- This protects during crashes and flash events

**TRANSITION regime:**
- Half-Kelly sizing
- Only trade if confidence > 65 (higher threshold)
- Expect false signals, so tighter risk management

## 3. Symmetric Long/Short with Regime Context

### Current Problem
- V10/V12 has asymmetric filters favoring longs in bull markets
- Crypto drops 2-3x faster than it rises (panic > FOMO)
- Short trades during bear regimes should be the PRIMARY profit driver
- In sideways: both directions equally (mean reversion by definition is bi-directional)

### Implementation
```python
# Per-direction Kelly
kelly_long = compute_kelly(recent_long_trades, fraction=0.3)
kelly_short = compute_kelly(recent_short_trades, fraction=0.3)

# Regime adjustment
if regime == "TRENDING" and trend == "UP":
    kelly_long *= 1.2   # slight boost
    kelly_short *= 0.6  # reduce counter-trend
elif regime == "TRENDING" and trend == "DOWN":
    kelly_long *= 0.6
    kelly_short *= 1.2
elif regime == "SIDEWAYS":
    # Mean reversion - equal both directions
    kelly_long = kelly_short = min(kelly_long, kelly_short)
```

## 4. Mean Reversion Sub-Strategy (for Sideways Regime)

### Signal Generation
```python
def mean_reversion_signal(close, bb_lower, bb_upper, bb_mid, rsi, regime):
    if regime != "SIDEWAYS":
        return None
    
    # Buy at lower band when oversold
    if close <= bb_lower * 1.01 and rsi < 35:
        return {"action": "LONG", "target": bb_mid, "stop": close - atr * 1.5}
    
    # Sell at upper band when overbought  
    if close >= bb_upper * 0.99 and rsi > 65:
        return {"action": "SHORT", "target": bb_mid, "stop": close + atr * 1.5}
    
    return None
```

### Key Parameters
- Entry: within 1% of Bollinger Band edge
- Target: middle band (SMA20) — NOT the opposite band (that's greedy)
- Stop: 1.5 ATR (tighter than breakout stops)
- RSI confirmation: < 35 for longs, > 65 for shorts
- Average trade duration: 5-15 bars (much shorter than breakout trades)
- Expected win rate: 60-70% (vs 40-46% for breakout)
- Expected avg win: smaller (1-2% vs 4%) but more frequent

## 5. Multi-Strategy Portfolio Effect

When you combine breakout + mean reversion:
- Breakout profits during trending periods
- Mean reversion profits during sideways periods
- The strategies are NEGATIVELY CORRELATED — when one loses, the other tends to win
- This smooths the equity curve and reduces drawdown
- Combined Sharpe ratio should be significantly higher than either alone

### Capital Allocation Between Sub-Strategies
- Use regime detection to weight: in trending, allocate 80% to breakout / 20% to mean reversion
- In sideways: flip to 20% breakout / 80% mean reversion
- In volatile: reduce both, hold more cash
- In transition: 50/50

## 6. Additional Edge: Funding Rate Arbitrage (Crypto-Specific)

Not implementable in backtesting but worth noting for live trading:
- Hyperliquid charges/pays funding rates every 8 hours
- When funding is very positive (longs pay shorts), there's a bias toward short
- When funding is very negative, bias toward long
- This is free alpha in crypto that doesn't exist in equities
- Can be layered on top of the regime-based strategy in live trading

## 7. Recommended V13 Architecture

```
V13Strategy:
├── RegimeDetector
│   ├── detect_regime(adx, ema_slope, bb_width, atr_pctile) → TRENDING|SIDEWAYS|VOLATILE|TRANSITION
│   └── get_trend_direction(ema_stack) → UP|DOWN|FLAT
├── BreakoutStrategy (existing V10 logic)
│   ├── Active in: TRENDING, TRANSITION (reduced)
│   └── Uses: squeeze detection, trend filters, confidence scoring
├── MeanReversionStrategy (NEW)
│   ├── Active in: SIDEWAYS, TRANSITION (reduced)
│   └── Uses: Bollinger bands, RSI, support/resistance levels
├── KellyPositionSizer
│   ├── Per-asset rolling Kelly (40-trade window)
│   ├── Per-direction (long/short) Kelly
│   ├── Regime-adjusted multipliers
│   └── Max leverage cap (6x)
└── ExitManager
    ├── Breakout exits: trailing stop, trend flip, time exit
    └── Mean reversion exits: target (mid-band), tight stop, time exit (faster)
```
