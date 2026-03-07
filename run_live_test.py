#!/usr/bin/env python3
"""Smoke test: connect to Kraken demo, fetch balance, fetch OHLCV, place+close tiny order."""

import os
import sys

from dotenv import load_dotenv

load_dotenv(".env.demo")

API_KEY = os.environ["KRAKEN_DEMO_API_KEY"]
API_SECRET = os.environ["KRAKEN_DEMO_API_SECRET"]

ASSETS = ["BTC", "ETH", "SOL", "LINK"]


def main():
    from live.executor import KrakenExecutor
    from live.feed import LiveFeed

    print("=" * 60)
    print("Kraken Demo Smoke Test")
    print("=" * 60)

    # --- 1. Connect + fetch balance ---
    print("\n[1] Connecting to Kraken Futures demo...")
    executor = KrakenExecutor(API_KEY, API_SECRET, demo=True)
    balance = executor.get_balance()
    print(f"    Balance: {balance}")

    # --- 2. Fetch 1h OHLCV for all assets ---
    print("\n[2] Fetching 1h OHLCV candles...")
    feed = LiveFeed()
    for asset in ASSETS:
        df = feed.get_candles(asset, "1h")
        last = df.iloc[-1] if len(df) > 0 else None
        if last is not None:
            print(f"    {asset}: {len(df)} candles, last close=${last['close']:.2f} at {df.index[-1]}")
        else:
            print(f"    {asset}: no data")

    # --- 3. Fetch open positions ---
    print("\n[3] Checking open positions...")
    positions = executor.get_positions()
    if positions:
        for p in positions:
            print(f"    {p['symbol']} {p['side']} size={p['size']} entry={p['entry_price']}")
    else:
        print("    No open positions")

    # --- 4. Place tiny test order (0.0001 BTC) then close ---
    print("\n[4] Placing test market buy order (0.0001 BTC)...")
    try:
        order = executor.place_order("BTC", "buy", 0.0001, "mkt")
        order_id = order.get("id", "unknown")
        print(f"    Order placed: id={order_id}")
        print(f"    Order details: status={order.get('status')}, filled={order.get('filled')}")

        # Close immediately
        print("    Closing test position...")
        close = executor.place_order("BTC", "sell", 0.0001, "mkt", reduce_only=True)
        print(f"    Close order: id={close.get('id', 'unknown')}, status={close.get('status')}")
    except Exception as e:
        print(f"    Order failed: {e}")
        print("    (This may be expected if minimum order size is larger on demo)")

    print("\n" + "=" * 60)
    print("Smoke test complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
