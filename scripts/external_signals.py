"""
External signal logger — fetches and stores market signals for correlation analysis.

Sources:
- Fear & Greed Index (alternative.me)
- BTC Dominance (CoinGecko)
- Funding rates (Binance Futures)
- Open interest (Binance Futures)
"""

import time
import sqlite3
import requests
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "paper_trading.db"

FUNDING_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT"]


def init_db():
    conn = sqlite3.connect(str(DB_PATH))
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


def _get_json(url, params=None, timeout=15):
    """GET JSON with retries."""
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"  [WARN] GET {url} attempt {attempt+1}: {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
    return None


def fetch_fear_greed():
    """Fetch Fear & Greed Index from alternative.me."""
    data = _get_json("https://api.alternative.me/fng/", params={"limit": "1"})
    if data and "data" in data and data["data"]:
        entry = data["data"][0]
        return int(entry["value"]), entry["value_classification"]
    return None, None


def fetch_btc_dominance():
    """Fetch BTC dominance from CoinGecko."""
    data = _get_json("https://api.coingecko.com/api/v3/global")
    if data and "data" in data:
        return data["data"].get("market_cap_percentage", {}).get("btc")
    return None


def fetch_derivatives_from_coingecko():
    """Fetch funding rates and OI from CoinGecko derivatives (no geo restriction)."""
    data = _get_json("https://api.coingecko.com/api/v3/derivatives")
    if not data:
        return {}, {}

    # Map CoinGecko index_id to our asset names
    asset_map = {"BTC": "btc", "ETH": "eth", "SOL": "sol", "LINK": "link"}
    funding = {}
    oi = {}

    for entry in data:
        idx = entry.get("index_id", "")
        if idx in asset_map and entry.get("contract_type") == "perpetual":
            key = asset_map[idx]
            # Take the first perpetual we find per asset (usually Binance)
            if f"{key}_funding_rate" not in funding:
                fr = entry.get("funding_rate")
                if fr is not None:
                    funding[f"{key}_funding_rate"] = float(fr) / 100  # CoinGecko gives %
                interest = entry.get("open_interest")
                if interest is not None:
                    oi[f"{key}_open_interest"] = float(interest)

    return funding, oi


def fetch_all_signals():
    """Fetch all external signals. Returns dict."""
    now = datetime.now(timezone.utc).isoformat()
    print(f"  Fetching external signals at {now}...")

    fg_val, fg_label = fetch_fear_greed()
    print(f"    Fear & Greed: {fg_val} ({fg_label})")

    btc_dom = fetch_btc_dominance()
    print(f"    BTC Dominance: {btc_dom}")

    funding, oi = fetch_derivatives_from_coingecko()
    for k, v in funding.items():
        print(f"    {k}: {v}")
    for k, v in oi.items():
        print(f"    {k}: {v}")

    return {
        "timestamp": now,
        "fear_greed_index": fg_val,
        "fear_greed_label": fg_label,
        "btc_dominance": btc_dom,
        "btc_funding_rate": funding.get("btc_funding_rate"),
        "eth_funding_rate": funding.get("eth_funding_rate"),
        "sol_funding_rate": funding.get("sol_funding_rate"),
        "link_funding_rate": funding.get("link_funding_rate"),
        "btc_open_interest": oi.get("btc_open_interest"),
        "eth_open_interest": oi.get("eth_open_interest"),
        "sol_open_interest": oi.get("sol_open_interest"),
        "link_open_interest": oi.get("link_open_interest"),
        "dxy_proxy": None,
        "notes": None,
    }


def store_signals(conn, signals):
    """Store external signals in DB."""
    conn.execute(
        "INSERT OR REPLACE INTO external_signals "
        "(timestamp, fear_greed_index, fear_greed_label, btc_dominance, "
        "btc_funding_rate, eth_funding_rate, sol_funding_rate, link_funding_rate, "
        "btc_open_interest, eth_open_interest, sol_open_interest, link_open_interest, "
        "dxy_proxy, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            signals["timestamp"], signals["fear_greed_index"], signals["fear_greed_label"],
            signals["btc_dominance"],
            signals["btc_funding_rate"], signals["eth_funding_rate"],
            signals["sol_funding_rate"], signals["link_funding_rate"],
            signals["btc_open_interest"], signals["eth_open_interest"],
            signals["sol_open_interest"], signals["link_open_interest"],
            signals["dxy_proxy"], signals["notes"],
        )
    )
    conn.commit()


if __name__ == "__main__":
    conn = init_db()
    signals = fetch_all_signals()
    store_signals(conn, signals)
    print("Signals stored.")
    conn.close()
