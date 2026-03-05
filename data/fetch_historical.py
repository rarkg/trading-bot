"""
Fetch historical OHLCV data from Kraken via ccxt.
Pulls 3-4 years of hourly candles for BTC and ETH.
Saves to local CSV + optionally to Postgres.
"""

import ccxt
import pandas as pd
import time
import os
from datetime import datetime, timezone

DATA_DIR = os.path.dirname(os.path.abspath(__file__))

def fetch_ohlcv_full(symbol="BTC/USD", timeframe="1h", 
                     since_date="2022-01-01", exchange_name="kraken"):
    """
    Fetch complete OHLCV history from exchange.
    ccxt returns max ~720 candles per call, so we paginate.
    """
    exchange = getattr(ccxt, exchange_name)({
        "enableRateLimit": True,
        "timeout": 30000,
    })
    
    since = int(datetime.strptime(since_date, "%Y-%m-%d").replace(
        tzinfo=timezone.utc).timestamp() * 1000)
    
    all_candles = []
    batch = 0
    
    print(f"Fetching {symbol} {timeframe} from {since_date}...")
    
    while True:
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=720)
            if not candles:
                break
            
            all_candles.extend(candles)
            batch += 1
            
            # Move since to after last candle
            since = candles[-1][0] + 1
            
            if batch % 10 == 0:
                print(f"  Batch {batch}: {len(all_candles)} candles so far "
                      f"(latest: {datetime.fromtimestamp(candles[-1][0]/1000, tz=timezone.utc).strftime('%Y-%m-%d')})")
            
            # If we got less than limit, we've reached the end
            if len(candles) < 720:
                break
            
            time.sleep(0.5)  # Rate limit buffer
            
        except Exception as e:
            print(f"  Error at batch {batch}: {e}")
            time.sleep(5)
            continue
    
    print(f"Done: {len(all_candles)} total candles")
    
    # Convert to DataFrame
    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("datetime")
    df = df.drop_duplicates()
    df = df.sort_index()
    
    return df


def save_csv(df, symbol, timeframe):
    """Save DataFrame to CSV."""
    clean_symbol = symbol.replace("/", "_")
    filename = f"{clean_symbol}_{timeframe}.csv"
    filepath = os.path.join(DATA_DIR, filename)
    df.to_csv(filepath)
    print(f"Saved {len(df)} rows to {filepath}")
    return filepath


if __name__ == "__main__":
    # Fetch BTC and ETH — 4 years of hourly data
    for symbol in ["BTC/USD", "ETH/USD"]:
        df = fetch_ohlcv_full(
            symbol=symbol,
            timeframe="1h",
            since_date="2022-01-01",
            exchange_name="kraken"
        )
        save_csv(df, symbol, "1h")
        print(f"\n{symbol}: {df.index[0]} to {df.index[-1]}")
        print(f"  Rows: {len(df)}")
        print(f"  Price range: ${df['close'].min():,.0f} - ${df['close'].max():,.0f}")
        print()
