"""Trading bot configuration — Config 2 (3:2 R:R)."""

# Account
INITIAL_CAPITAL = 5_000.0  # USD total

# Assets
ASSETS = ["BTC", "ETH", "SOL", "LINK", "ADA", "AVAX", "DOGE", "XRP", "ICP", "SHIB"]
CAPITAL_PER_ASSET = INITIAL_CAPITAL / len(ASSETS)  # $500 per asset

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
