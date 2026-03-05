"""
Fetch hourly crypto data from CryptoCompare API.
Free tier: 2000 candles per request, no key needed.
Paginate backwards to get full history.
"""

import requests
import pandas as pd
import time
import os
from datetime import datetime, timezone

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_URL = "https://min-api.cryptocompare.com/data/v2/histohour"


def fetch_hourly(symbol="BTC", target="USD", start_date="2022-01-01", end_date="2026-03-05"):
    """Fetch complete hourly history by paginating backwards."""
    
    end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    
    all_candles = []
    current_ts = end_ts
    batch = 0
    
    print(f"Fetching {symbol}/USD hourly from {start_date} to {end_date}...")
    
    while current_ts > start_ts:
        url = f"{BASE_URL}?fsym={symbol}&tsym={target}&limit=2000&toTs={current_ts}"
        
        try:
            resp = requests.get(url, timeout=30)
            data = resp.json()
            
            if data["Response"] != "Success":
                print(f"  Error: {data.get('Message', 'unknown')}")
                break
            
            candles = data["Data"]["Data"]
            if not candles:
                break
            
            all_candles.extend(candles)
            batch += 1
            
            # Move backwards
            earliest = candles[0]["time"]
            current_ts = earliest - 1
            
            if batch % 5 == 0:
                dt = datetime.fromtimestamp(earliest, tz=timezone.utc)
                print(f"  Batch {batch}: {len(all_candles)} candles (back to {dt.strftime('%Y-%m-%d')})")
            
            time.sleep(0.3)  # Rate limit
            
        except Exception as e:
            print(f"  Error at batch {batch}: {e}")
            time.sleep(2)
            continue
    
    if not all_candles:
        print("No data fetched!")
        return None
    
    # Convert to DataFrame
    df = pd.DataFrame(all_candles)
    df["datetime"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.rename(columns={
        "open": "open", "high": "high", "low": "low", 
        "close": "close", "volumefrom": "volume"
    })
    df = df[["datetime", "open", "high", "low", "close", "volume"]]
    df = df.set_index("datetime")
    df = df.drop_duplicates()
    df = df.sort_index()
    
    # Filter to requested range
    df = df[df.index >= pd.Timestamp(start_date, tz="UTC")]
    
    # Remove zero-volume rows (bad data)
    df = df[df["volume"] > 0]
    
    print(f"Done: {len(df)} candles from {df.index[0]} to {df.index[-1]}")
    return df


def save(df, symbol):
    filepath = os.path.join(DATA_DIR, f"{symbol}_USD_hourly.csv")
    df.to_csv(filepath)
    print(f"Saved to {filepath}")
    return filepath


if __name__ == "__main__":
    for sym in ["BTC", "ETH", "SOL"]:
        df = fetch_hourly(symbol=sym, start_date="2022-01-01", end_date="2026-03-05")
        if df is not None:
            save(df, sym)
            print(f"  {sym}: {len(df)} hourly candles")
            print(f"  Price range: ${df['close'].min():,.0f} - ${df['close'].max():,.0f}")
            print()
