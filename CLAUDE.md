# CLAUDE.md — Crypto Bot Development Rules

## ⛔ READ THIS BEFORE ANY WORK

### Version Management (MANDATORY)
1. **Version file = COMPLETE package.** `CandleV2_6()` with zero args must produce exact V2.6 behavior. All params baked into the class. No external config overrides.
2. **Frozen files are IMMUTABLE.** `strategies/candle_v2_3.py` through `candle_v2_6.py` — NEVER modify. Bug? Create `candle_v2_7.py`.
3. **Config is NOT separate from the version.** The version IS the config + algorithm together.

### Before Running Any Backtest
1. Instantiate the strategy: `s = CandleV2_6(); print(s.__dict__)` — verify ALL params match expected values.
2. Run against stress test: `python -m backtest.stress_periods` (17 periods: crashes, sideways, euphoria, yearly). ALL must pass.
3. Compare results against baseline: V2.4 = +$16.8M, 78.9% WR, 17.6% DD, PF 2.07.
4. If new engine gives different results from old engine on same strategy → it's a BUG. Investigate first.

### Before Deploying ANY Code to Live Bot
1. Run full backtest (4yr + YTD + stress test) with the EXACT code that will go live.
2. No untested features. No "should work" deploys. V2.6 deployed untested → 290 duplicate orders, 220 bot restarts.
3. Review every function that touches the exchange (order placement, SL/TP, reconciliation).

### Database Rules
- **Postgres = production ONLY** (live trading, exchange sync, candle collector)
- **SQLite = backtest ONLY** (results, comparisons)
- **NEVER mix.** Never write backtest results to Postgres. Never read live data from SQLite.

### Position Sizing
- `position_size = risk% × initial_capital` — NOT running equity
- Without this cap: early wins compound → 100x positions → unrealistic results
- Standard: $5K total, $500/asset, 10 assets

### Exchange Integration
- Kraken = source of truth for positions (not local DB)
- Orders tracked in DB ONLY after fill confirmed on exchange
- SL/TP orders placed on Kraken for every trade (not software-only)
- Reconcile with Kraken every cycle (not just startup)
- SHIB sometimes generates price=0 — guard: skip if price/stop/target ≤ 0

### Backtest Standards
- Fee: 0.15% (fee + slippage)
- Capital: $5K total ($500/asset), 10 assets
- Assets: BTC, ETH, SOL, LINK, ADA, AVAX, DOGE, XRP, ICP, SHIB
- ALWAYS run: full 4yr + YTD + stress_periods.py
- Hourly timeframe only (15m doesn't work — brutal drawdowns)

### Architecture (V3)
```
candle-collector → candles → Postgres
crypto-live (trader.py) → signals + orders
exchange-sync → balance + positions + reconciliation (60s)
trading-monitor → dashboard (port 3002, reads Postgres)
```

### Key Files
- `run_live.py` — main live trading loop
- `live/executor.py` — order execution + exchange interaction
- `live/pg_writer.py` — Postgres writes
- `live/exchange_adapter.py` — ExchangeAdapter ABC + KrakenAdapter
- `live/data_provider.py` — PostgresProvider + CsvProvider
- `live/config.py` — runtime config (strategy version, scan interval, etc.)
- `strategies/` — frozen strategy versions (NEVER modify existing files)
- `backtest/engine.py` — backtest engine
- `backtest/stress_periods.py` — 17-period stress test (MANDATORY)

### Current Live State
- Strategy: V2.6 (tiered sizing by score)
- Scan interval: 15 min
- Exchange: Kraken Futures demo
- pm2: crypto-live, exchange-sync, trading-monitor

### Git Conventions
- Commit messages: `feat:`, `fix:`, `refactor:`, `config:`, `chore:`
- New version = new frozen file + update run_live.py import
