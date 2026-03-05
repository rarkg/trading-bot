#!/usr/bin/env python3
"""
Paper trading status query — CLI tool to check current state.
"""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "paper_trading.db"

ASSETS = ["BTC", "ETH", "SOL", "LINK"]
CAPITAL_PER_ASSET = 1000.0


def get_status():
    if not DB_PATH.exists():
        print("No paper trading database found. Run paper_trading.py first.")
        return

    conn = sqlite3.connect(str(DB_PATH))

    print(f"\n{'='*70}")
    print(f"  PAPER TRADING STATUS — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}")

    # Capital & equity
    print(f"\n  {'Asset':<6} {'Capital':>10} {'P&L':>10} {'P&L%':>8} {'DD%':>8} {'Trades':>8}")
    print(f"  {'─'*52}")

    total_capital = 0
    total_trades = 0
    for asset in ASSETS:
        row = conn.execute(
            "SELECT capital, peak_capital FROM paper_state WHERE asset = ?", (asset,)
        ).fetchone()
        if not row:
            print(f"  {asset:<6} {'N/A':>10}")
            continue

        capital, peak = row
        pnl = capital - CAPITAL_PER_ASSET
        pnl_pct = pnl / CAPITAL_PER_ASSET * 100
        dd = (peak - capital) / peak * 100 if peak > 0 else 0
        n_trades = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE asset = ?", (asset,)
        ).fetchone()[0]
        total_capital += capital
        total_trades += n_trades

        print(f"  {asset:<6} ${capital:>9.2f} ${pnl:>+9.2f} {pnl_pct:>+7.2f}% {dd:>7.1f}% {n_trades:>8}")

    total_pnl = total_capital - CAPITAL_PER_ASSET * len(ASSETS)
    print(f"  {'─'*52}")
    print(f"  {'TOTAL':<6} ${total_capital:>9.2f} ${total_pnl:>+9.2f} "
          f"{total_pnl / (CAPITAL_PER_ASSET * len(ASSETS)) * 100:>+7.2f}% {'':>8} {total_trades:>8}")

    # Open positions
    positions = conn.execute(
        "SELECT asset, direction, entry_price, entry_time, size, leverage, "
        "stop_price, target_price, unrealized_pnl FROM positions"
    ).fetchall()

    if positions:
        print(f"\n  OPEN POSITIONS:")
        print(f"  {'Asset':<6} {'Dir':<6} {'Entry':>10} {'Size':>10} {'Lev':>6} "
              f"{'Stop':>10} {'Target':>10} {'Unreal':>10}")
        print(f"  {'─'*70}")
        for p in positions:
            print(f"  {p[0]:<6} {p[1]:<6} ${p[2]:>9.2f} ${p[4]:>9.0f} {p[5]:>5.1f}x "
                  f"${p[6]:>9.2f} ${p[7]:>9.2f} ${p[8]:>+9.2f}")
    else:
        print(f"\n  No open positions.")

    # Recent trades
    trades = conn.execute(
        "SELECT timestamp, asset, direction, entry_price, exit_price, pnl, pnl_pct, "
        "exit_reason, signal_type FROM trades ORDER BY timestamp DESC LIMIT 10"
    ).fetchall()

    if trades:
        print(f"\n  RECENT TRADES (last 10):")
        print(f"  {'Time':<20} {'Asset':<6} {'Dir':<6} {'Entry':>9} {'Exit':>9} "
              f"{'P&L':>9} {'P&L%':>7} {'Reason':<8}")
        print(f"  {'─'*75}")
        for t in trades:
            ts = t[0][:16] if t[0] else "?"
            print(f"  {ts:<20} {t[1]:<6} {t[2]:<6} ${t[3]:>8.2f} ${t[4]:>8.2f} "
                  f"${t[5]:>+8.2f} {t[6]:>+6.2f}% {t[7]:<8}")

    # Latest external signals
    sig = conn.execute(
        "SELECT timestamp, fear_greed_index, fear_greed_label, btc_dominance, "
        "btc_funding_rate, eth_funding_rate FROM external_signals "
        "ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()

    if sig:
        print(f"\n  LATEST SIGNALS ({sig[0][:16]}):")
        print(f"    Fear & Greed: {sig[1]} ({sig[2]})")
        print(f"    BTC Dominance: {sig[3]:.1f}%" if sig[3] else "    BTC Dominance: N/A")
        print(f"    Funding: BTC={sig[4]:.6f}" if sig[4] else "    Funding: N/A")

    print()
    conn.close()


if __name__ == "__main__":
    get_status()
