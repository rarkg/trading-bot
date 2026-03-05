#!/usr/bin/env python3
"""
Paper trading runner — main loop that runs every hour.

Fetches new candles, runs V13.13 strategy, logs everything.
Graceful shutdown with SIGTERM/SIGINT.
"""

import sys
import time
import signal
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.live_data import init_db as init_candles_db, fetch_and_store_all, get_latest_candles, SYMBOLS
from scripts.external_signals import init_db as init_signals_db, fetch_all_signals, store_signals
from scripts.paper_trader import PaperTrader, init_db as init_trader_db, ASSETS

DB_PATH = Path(__file__).parent.parent / "data" / "paper_trading.db"

running = True


def shutdown(signum, frame):
    global running
    print(f"\n[{datetime.now(timezone.utc).isoformat()}] Received signal {signum}, shutting down gracefully...")
    running = False


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)


def init_all_tables():
    """Initialize all DB tables."""
    conn = sqlite3.connect(str(DB_PATH))
    # Candles table
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
    # External signals table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS external_signals (
            timestamp TEXT PRIMARY KEY,
            fear_greed_index INTEGER,
            fear_greed_label TEXT,
            btc_dominance REAL,
            btc_funding_rate REAL,
            eth_funding_rate REAL,
            sol_funding_rate REAL,
            link_funding_rate REAL,
            btc_open_interest REAL,
            eth_open_interest REAL,
            sol_open_interest REAL,
            link_open_interest REAL,
            dxy_proxy REAL,
            notes TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_ts ON external_signals(timestamp)")
    conn.commit()
    return conn


def run_cycle(conn, trader):
    """Run one trading cycle: fetch data, run strategy, log results."""
    now = datetime.now(timezone.utc)
    print(f"\n{'='*70}")
    print(f"  PAPER TRADING CYCLE — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}")

    # 1. Fetch new candles
    print("\n[1] Fetching candles...")
    fetch_and_store_all(conn, limit=500)

    # 2. Fetch external signals
    print("\n[2] Fetching external signals...")
    try:
        signals = fetch_all_signals()
        store_signals(conn, signals)
    except Exception as e:
        print(f"  [WARN] External signals failed: {e}")

    # 3. Set up BTC data for alt strategies
    btc_data = get_latest_candles(conn, "BTC", limit=500)

    # 4. Run strategy for each asset
    print("\n[3] Running strategy...")
    for asset in ASSETS:
        data = get_latest_candles(conn, asset, limit=500)
        if data.empty:
            print(f"  {asset}: No data available, skipping")
            continue

        # Set BTC data for correlation
        if asset != "BTC" and not btc_data.empty:
            trader.strategies[asset].set_btc_data(btc_data)

        result = trader.process_bar(asset, data)
        action = result.get("action", "NONE")

        if action == "ENTRY":
            print(f"  {asset}: ENTRY {result['direction']} @ ${result['price']:.2f} "
                  f"size=${result['size']:.0f} lev={result['leverage']:.1f}x "
                  f"stop=${result['stop']:.2f} target=${result['target']:.2f}")
        elif action == "EXIT":
            print(f"  {asset}: EXIT ({result['exit_reason']}) P&L: ${result['pnl']:.2f}")
        elif action == "PYRAMID":
            print(f"  {asset}: PYRAMID added, new size: ${result['new_size']:.0f}")
        elif action == "HOLD":
            print(f"  {asset}: HOLD @ ${result['price']:.2f} "
                  f"unrealized: ${result.get('unrealized_pnl', 0):.2f}")
        else:
            print(f"  {asset}: {action} @ ${result.get('price', 0):.2f}")

    # 5. Print status summary
    print("\n[4] Status Summary:")
    status = trader.get_status()
    print(f"  Total equity: ${status['total_equity']:.2f} "
          f"(P&L: ${status['total_pnl']:+.2f})")
    for asset in ASSETS:
        a = status["assets"][asset]
        pos_str = ""
        if a["position"]:
            p = a["position"]
            pos_str = f" | {p['direction']} {p['leverage']:.1f}x ${p['size']:.0f}"
        print(f"    {asset}: ${a['capital']:.2f} ({a['pnl_pct']:+.2f}%) DD:{a['drawdown_pct']:.1f}%{pos_str}")

    return status


def seconds_until_next_hour():
    """Seconds until the next hour boundary + 30s buffer."""
    now = datetime.now(timezone.utc)
    next_hour = now.replace(minute=0, second=0, microsecond=0)
    from datetime import timedelta
    next_hour += timedelta(hours=1)
    wait = (next_hour - now).total_seconds() + 30  # 30s after hour for candle to close
    return max(wait, 10)


def main():
    print("Paper Trading System v1.0")
    print(f"Starting at {datetime.now(timezone.utc).isoformat()}")
    print(f"Capital: ${1000 * len(ASSETS)} ({len(ASSETS)} assets x $1000)")

    # Initialize DB
    conn = init_all_tables()
    init_trader_db()

    # Initial backfill
    print("\nBackfilling candle data...")
    from scripts.live_data import backfill
    backfill(conn, days=25)

    # Initialize trader
    trader = PaperTrader(conn)

    # Run initial cycle immediately
    run_cycle(conn, trader)

    # Main loop
    while running:
        wait = seconds_until_next_hour()
        print(f"\nNext cycle in {wait:.0f}s ({wait/60:.1f}min)")

        # Sleep in small chunks for graceful shutdown
        waited = 0
        while waited < wait and running:
            time.sleep(min(10, wait - waited))
            waited += 10

        if running:
            try:
                run_cycle(conn, trader)
            except Exception as e:
                print(f"\n[ERROR] Cycle failed: {e}")
                import traceback
                traceback.print_exc()
                print("Will retry next cycle...")

    print("\nPaper trading stopped gracefully.")
    conn.close()


if __name__ == "__main__":
    main()
