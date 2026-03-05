"""
Live data fetcher — pulls hourly candles from Binance public API.
No API key needed for klines endpoint.
"""

import time
import sqlite3
import requests
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "paper_trading.db"

SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "LINK": "LINKUSDT",
}

BINANCE_KLINES_URL = "https://api.binance.us/api/v3/klines"


def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hourly_candles (
            timestamp TEXT NOT NULL,
            asset TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            PRIMARY KEY (timestamp, asset)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candles_ts ON hourly_candles(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candles_asset ON hourly_candles(asset)")
    conn.commit()
    return conn


def fetch_klines(symbol, interval="1h", limit=500):
    """Fetch klines from Binance. Returns list of OHLCV dicts."""
    for attempt in range(3):
        try:
            resp = requests.get(BINANCE_KLINES_URL, params={
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
            }, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            candles = []
            for k in data:
                candles.append({
                    "timestamp": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).isoformat(),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                })
            return candles
        except Exception as e:
            print(f"  [WARN] fetch_klines {symbol} attempt {attempt+1} failed: {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
    return []


def store_candles(conn, asset, candles):
    """Insert candles into DB, skip duplicates."""
    inserted = 0
    for c in candles:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO hourly_candles (timestamp, asset, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (c["timestamp"], asset, c["open"], c["high"], c["low"], c["close"], c["volume"])
            )
            inserted += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    return inserted


def get_latest_candles(conn, asset, limit=500):
    """Get most recent candles for an asset as a DataFrame."""
    rows = conn.execute(
        "SELECT timestamp, open, high, low, close, volume FROM hourly_candles "
        "WHERE asset = ? ORDER BY timestamp DESC LIMIT ?",
        (asset, limit)
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["datetime", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").set_index("datetime")
    return df


def fetch_and_store_all(conn, limit=500):
    """Fetch latest candles for all assets and store in DB."""
    results = {}
    for asset, symbol in SYMBOLS.items():
        candles = fetch_klines(symbol, limit=limit)
        if candles:
            n = store_candles(conn, asset, candles)
            total = conn.execute(
                "SELECT COUNT(*) FROM hourly_candles WHERE asset = ?", (asset,)
            ).fetchone()[0]
            print(f"  {asset}: fetched {len(candles)} candles, {n} new, {total} total in DB")
            results[asset] = len(candles)
        else:
            print(f"  {asset}: FAILED to fetch candles")
            results[asset] = 0
    return results


def backfill(conn, days=30):
    """Backfill historical candles (up to 1000 per request)."""
    limit = min(days * 24, 1000)
    print(f"Backfilling {limit} candles per asset...")
    return fetch_and_store_all(conn, limit=limit)


if __name__ == "__main__":
    conn = init_db()
    print("Backfilling 30 days of hourly candles...")
    backfill(conn, days=30)
    conn.close()
    print("Done.")
