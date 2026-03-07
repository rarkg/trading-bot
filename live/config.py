"""Trading bot configuration — Config 2 (3:2 R:R)."""

# Account
INITIAL_CAPITAL = 5_000.0  # USD total

# Assets
ASSETS = ["BTC", "ETH", "SOL", "LINK", "ADA", "AVAX", "DOGE", "XRP", "ICP", "SHIB"]
CAPITAL_PER_ASSET = INITIAL_CAPITAL / len(ASSETS)  # $500 per asset (legacy fallback)

# Kraken symbol mapping
SYMBOL_MAP = {
    "BTC": "PF_XBTUSD",
    "ETH": "PF_ETHUSD",
    "SOL": "PF_SOLUSD",
    "LINK": "PF_LINKUSD",
    "ADA": "PF_ADAUSD",
    "AVAX": "PF_AVAXUSD",
    "DOGE": "PF_DOGEUSD",
    "XRP": "PF_XRPUSD",
    "ICP": "PF_ICPUSD",
    "SHIB": "PF_SHIBUSD",
}

# Risk
MAX_RISK_PER_TRADE = 0.02  # 2% of capital per trade
MAX_DRAWDOWN = 0.25  # 25% per asset — halt trading if breached

# Environment
DEMO = True  # Set False for live trading

# ─── V2.6 Feature Flags ───
USE_WICK_GUARD = True          # Wick-resistant stops: require 15m close below SL
USE_SMART_ENTRIES = True       # Limit orders at pullback (entry - 0.3*ATR)
USE_REGIME_SIZING = True       # ADX+EMA slope position size multiplier
SMART_ENTRY_PULLBACK_ATR = 0.3 # Pullback distance as fraction of ATR
SMART_ENTRY_EXPIRY_HOURS = 1   # Cancel unfilled limit orders after N hours
REGIME_SIZE_MIN = 0.5          # Min regime sizing multiplier
REGIME_SIZE_MAX = 1.5          # Max regime sizing multiplier

# ─── V2.7 Reliability Flags ───
# 15-minute signal evaluation
USE_15M_SIGNALS = True         # Evaluate signals every 15 min (MTF hourly confirmation)
SIGNAL_INTERVAL_MIN = 15       # Minutes between signal evaluations

# Percentage-based position sizing (replaces CAPITAL_PER_ASSET)
USE_PCT_SIZING = True          # Enable percentage-based sizing from Kraken balance
BASE_POSITION_PCT = 0.15       # 15% of equity per position
MAX_POSITION_PCT = 0.30        # 30% max single position
MIN_POSITION_PCT = 0.03        # 3% min (skip if below)
MAX_EXPOSURE_PCT = 2.00        # 200% total exposure cap
# Score multipliers for pct sizing
SCORE_MULT_LOW = 0.7           # score 1-2
SCORE_MULT_MID = 1.0           # score 3
SCORE_MULT_HIGH = 1.3          # score 4+
# Regime multipliers for pct sizing
REGIME_MULT_TREND = 1.2        # trending aligned
REGIME_MULT_RANGE = 0.8        # ranging
REGIME_MULT_VOLATILE = 0.7     # volatile

# Kraken balance as equity source
USE_KRAKEN_EQUITY = True       # Use fetch_balance() as true equity

# ─── Config 2 (Candle V2.3) ───
# R:R 3:2 — stop 3 ATR, target 2 ATR
CANDLE_V2_CONFIG = {
    "stop_atr": 3.0,
    "target_atr": 2.0,
    "min_score": 5.0,
    "use_mtf": True,
    "mtf_require": "both",       # hourly + 4H + daily all must agree
    "adx_max": 40,
    "cooldown": 12,              # bars between trades
    "time_exit_bars": 144,       # max bars in trade
    "pattern_set": "all",        # all 21 TA-Lib patterns
    "fee_pct": 0.15,             # fee + slippage
    # All 15 indicators ON
    "use_rsi": True,
    "use_ema_align": True,
    "use_bb": True,
    "use_macd": True,
    "use_obv": True,
    "use_stoch": True,
    "use_atr_filter": True,
    "use_vwap": True,
    "use_ichimoku": True,
    "use_cmf": True,
    "use_williams_r": True,
    "use_mfi": True,
    "use_cci": True,
    "use_roc": True,
    "use_tsi": True,
}

# ─── Config (Squeeze V15) ───
# Uses V15.4 static params + 3 adaptive params
SQUEEZE_V15_CONFIG = {
    "fee_pct": 0.10,             # fee per side
    # Adaptive params (V15.5)
    "adaptive_kelly": True,
    "adaptive_regime_mult": True,
    "adaptive_bo_stop_atr": True,
}
