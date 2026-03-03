-- Shared Supabase schema

CREATE TABLE IF NOT EXISTS hourly_candles (
    ticker VARCHAR(10), date DATE, hour_start TIMESTAMPTZ,
    open DECIMAL, high DECIMAL, low DECIMAL, close DECIMAL, volume BIGINT,
    CONSTRAINT hourly_candles_pkey PRIMARY KEY (ticker, hour_start)
);

CREATE TABLE IF NOT EXISTS vix_hourly (
    date DATE, hour_start TIMESTAMPTZ,
    open DECIMAL, high DECIMAL, low DECIMAL, close DECIMAL,
    CONSTRAINT vix_hourly_pkey PRIMARY KEY (date, hour_start)
);

CREATE TABLE IF NOT EXISTS breakout_signals (
    ticker VARCHAR(10), date DATE, hour_start TIMESTAMPTZ, signal_type VARCHAR(30),
    prev_high DECIMAL, prev_low DECIMAL, curr_close DECIMAL, range_pts DECIMAL, bullish BOOLEAN,
    CONSTRAINT breakout_signals_unique UNIQUE (ticker, date, hour_start)
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id SERIAL PRIMARY KEY, date DATE, signal_type VARCHAR(30), direction VARCHAR(5),
    entry_time TIMESTAMPTZ, entry_price DECIMAL, stop_price DECIMAL, target_price DECIMAL,
    confidence_pct INTEGER, position_size DECIMAL,
    exit_time TIMESTAMPTZ, exit_price DECIMAL, exit_reason VARCHAR(50),
    pnl_pts DECIMAL, pnl_usd DECIMAL, capital_after DECIMAL, notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS paper_account (
    id SERIAL PRIMARY KEY, balance DECIMAL, updated_at TIMESTAMPTZ DEFAULT NOW(), note TEXT
);
