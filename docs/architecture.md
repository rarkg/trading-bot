# Trading Bot Architecture — Service Separation

## Overview

Split the monolithic `run_live.py` into 4 independent services communicating through
Postgres (state) and Postgres LISTEN/NOTIFY (real-time events). Each service has one
job and one external connection.

## Services

### 1. Market Data Service (`services/data_collector.py`)
**Job:** Collect and store all market data. Single source of truth for candle data.

**Connections:**
- Kraken public WebSocket (wss://futures.kraken.com/ws/v1) — OHLC stream
- Postgres — write candles

**Behavior:**
- On startup: gap-fill from REST (last DB timestamp → now, with pagination)
- Runtime: subscribe to OHLC channels for all assets × all timeframes (1h, 15m)
- On each completed candle: INSERT into `candles` table, emit `NOTIFY new_candle, '{asset}:{timeframe}'`
- On partial candle update: update in-memory state (for live price), do NOT write partial to DB
- Reconnection: auto-reconnect with exponential backoff, gap-fill on reconnect
- Health: log heartbeat every 60s, pm2 managed

**Does NOT:**
- Run strategies
- Place orders
- Know about positions

---

### 2. Trading Engine (`services/trade_engine.py`)
**Job:** Run strategies on new candles, generate signals. Pure logic, no I/O to exchange.

**Connections:**
- Postgres — read candles, write signals, LISTEN for new_candle events

**Behavior:**
- LISTEN on `new_candle` channel
- On notification: load candle history from Postgres for the asset
- Run all active strategies (candle_v2_6, squeeze_v15, future S/R filter)
- If signal generated: INSERT into `signals` table, emit `NOTIFY new_signal`
- Stateless — can restart at any time without data loss

**Signals table schema:**
```sql
CREATE TABLE signals (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    asset VARCHAR(10) NOT NULL,
    strategy VARCHAR(50) NOT NULL,
    direction VARCHAR(5) NOT NULL,  -- LONG / SHORT
    signal_name VARCHAR(100),       -- e.g. CDLENGULFING|s2.0
    entry_price DOUBLE PRECISION,
    stop_price DOUBLE PRECISION,
    target_price DOUBLE PRECISION,
    score DOUBLE PRECISION,
    metadata JSONB,                 -- strategy-specific data
    status VARCHAR(20) NOT NULL DEFAULT 'pending'  -- pending / accepted / rejected / expired
);
```

**Does NOT:**
- Fetch market data from exchange
- Place orders
- Track positions

---

### 3. Execution Service (`services/executor.py`)
**Job:** Execute signals, manage orders and positions. Source of truth for position state.

**Connections:**
- Kraken Futures API (REST) — place/cancel orders
- Kraken private WebSocket — real-time fill notifications
- Postgres — read signals, write positions/trades, LISTEN for new_signal events

**Behavior:**
- LISTEN on `new_signal` channel
- On notification: read signal from DB, validate (risk check, asset tradeable, no duplicate position)
- If valid: place order via REST, update signal status to 'accepted', create position record
- Place SL/TP orders on exchange
- Private WebSocket: subscribe to `executions` channel
- On fill event: update position state immediately, log trade, emit `NOTIFY position_update`
- Reconciliation: every 5 min, compare DB positions vs exchange positions (safety net)
- Dust sweep: clean up positions < $10 notional

**Positions table schema:**
```sql
CREATE TABLE positions (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    asset VARCHAR(10) NOT NULL,
    strategy VARCHAR(50) NOT NULL,
    direction VARCHAR(5) NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    size_usd DOUBLE PRECISION NOT NULL,
    stop_price DOUBLE PRECISION,
    target_price DOUBLE PRECISION,
    sl_order_id VARCHAR(100),
    tp_order_id VARCHAR(100),
    signal_id INTEGER REFERENCES signals(id),
    status VARCHAR(20) NOT NULL DEFAULT 'open',  -- open / closed
    exit_price DOUBLE PRECISION,
    exit_reason VARCHAR(50),        -- STOP / TARGET / TIME_EXIT / MANUAL
    exit_at TIMESTAMPTZ,
    pnl_usd DOUBLE PRECISION,
    pnl_pct DOUBLE PRECISION,
    metadata JSONB
);
```

**Does NOT:**
- Fetch candle data
- Run strategies
- Serve dashboard

---

### 4. Dashboard API (`beacon` — already exists)
**Job:** Read-only view of system state for the frontend.

**Connections:**
- Postgres — read candles, positions, signals, trades
- LISTEN on `position_update` for real-time push to WebSocket clients

**Behavior:**
- Serve WebSocket to frontend with live position data
- REST endpoints for historical trades, equity curve, signal history
- Pure read-only — never writes to DB, never touches exchange

---

## Communication Flow

```
Kraken WS (public)
       │
       ▼
  Data Collector ──write──▶ Postgres `candles`
       │                        │
       │ NOTIFY new_candle      │ LISTEN new_candle
       │                        ▼
       │                  Trade Engine ──write──▶ Postgres `signals`
       │                        │
       │                        │ NOTIFY new_signal
       │                        ▼
       │                  Executor ──write──▶ Postgres `positions`
       │                     │  ▲
       │                     │  │ fill events
       │                     ▼  │
       │              Kraken WS (private) + REST
       │
       │ LISTEN position_update
       ▼
    Dashboard (beacon) ──▶ Frontend WebSocket
```

## Postgres Channels (LISTEN/NOTIFY)
- `new_candle` — payload: `{asset}:{timeframe}` (e.g. `BTC:1h`)
- `new_signal` — payload: `{signal_id}` (e.g. `42`)
- `position_update` — payload: `{position_id}:{event}` (e.g. `7:filled`, `7:closed`)

## Shared Code (`live/` package)
- `live/config.py` — assets, capital, risk params (all services read)
- `live/data_provider.py` — Postgres candle read/write (data collector + engine)
- `live/exchange/kraken.py` — Kraken API clients (data collector + executor)
- `strategies/` — strategy implementations (engine only)

## pm2 Process Names
- `data-collector` — Market Data Service
- `trade-engine` — Trading Engine
- `executor` — Execution Service
- `beacon` — Dashboard (already exists)

## Migration from run_live.py
1. Create `services/` directory
2. Build data_collector.py first (WebSocket + Postgres) — test standalone
3. Build trade_engine.py (LISTEN + strategy) — test with data_collector running
4. Build executor.py (LISTEN + Kraken private WS) — test full pipeline
5. Update beacon to read from new tables
6. Retire run_live.py

## Edge Cases
- WebSocket disconnect: auto-reconnect + gap-fill from REST on reconnect
- Postgres down: all services retry connection with backoff, log alerts
- Duplicate signals: executor checks for existing open position on same asset before accepting
- Signal expiration: signals older than 1 candle period are marked 'expired'
- Service restart ordering: data_collector first, then engine, then executor (but each handles missing dependencies gracefully)
- Rate limits: only executor hits authenticated endpoints; data_collector uses public WS (no rate limit)
