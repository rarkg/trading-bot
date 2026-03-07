# Trading Bot

Crypto trading system with backtested strategies and Kraken live infrastructure.

## Project Structure

```
strategies/
  squeeze_v15.py        # V15.5 squeeze strategy (incremental adaptive)
  candle_v2_3.py        # Candle V2.3 strategy (multi-timeframe confirmation)
backtest/
  engine.py             # Generic backtest engine
run_v15_cross_asset.py  # V15.5 backtest runner (BTC/ETH/SOL/LINK)
run_candle_v2_3_cross_asset.py  # Candle V2.3 backtest runner

live/                   # Kraken live trading infrastructure
  exchange/
    kraken.py           # Kraken API client (spot OHLCV + futures orders)
  feed.py               # LiveFeed: 1h/4h/1d OHLCV from Kraken REST
  executor.py           # KrakenExecutor: Kraken Futures order execution
  risk.py               # RiskManager: Kelly sizing, DD limits, position caps
  paper.py              # PaperTrader: demo-futures.kraken.com wrapper

scripts/
  paper_trader.py       # Paper trading engine
  live_data.py          # Binance.US hourly candle fetcher
  external_signals.py   # Fear & Greed, BTC dom, funding, OI
  paper_status.py       # CLI status tool

data/                   # Hourly OHLCV CSVs
scanner.py              # SPX/VIX hourly scanner
scanners/               # Individual scanners
playbooks/              # Trading playbooks
schemas/                # Supabase table definitions
archive/                # Old runners and strategies
```

## Live Trading

Install dependencies:
```bash
pip install -r requirements_live.txt
```

### Kraken API Endpoints
- Spot OHLCV: `https://api.kraken.com/0/public/OHLC`
- Futures demo: `https://demo-futures.kraken.com/derivatives/api/v3/`
- Futures live: `https://futures.kraken.com/derivatives/api/v3/`
- Perp symbols: `PF_XBTUSD`, `PF_ETHUSD`, `PF_SOLUSD`, `PF_LINKUSD`

### Usage

```python
from live.feed import LiveFeed
from live.executor import KrakenExecutor
from live.paper import PaperTrader
from live.risk import RiskManager

# Fetch candles
feed = LiveFeed()
df = feed.get_candles("BTC", interval="1h")

# Paper trading (demo)
paper = PaperTrader(api_key="...", api_secret="...")
paper.place_order("BTC", "buy", size=0.01)

# Risk sizing
rm = RiskManager()
size = rm.kelly_size(win_rate=0.6, avg_win=100, avg_loss=50, capital=10000)
```
