"""
Paper trading engine — runs V13.13 strategy against live data.

Mirrors the backtest engine logic (V13Engine.run_with_kelly_feedback)
but operates bar-by-bar as new candles arrive.
"""

import json
import sqlite3
import pandas as pd
import numpy as np
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from strategies.squeeze_v13 import SqueezeV13
from backtest.engine import Trade

DB_PATH = Path(__file__).parent.parent / "data" / "paper_trading.db"

ASSETS = ["BTC", "ETH", "SOL", "LINK"]

# Per-asset max risk (same as V13.13 runner)
ASSET_MAX_RISK = {"BTC": 11.0, "ETH": 8.0, "SOL": 8.0, "LINK": 10.0}

# Starting capital per asset
CAPITAL_PER_ASSET = 1000.0


def init_db():
    conn = sqlite3.connect(str(DB_PATH))

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            asset TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            size REAL NOT NULL,
            leverage REAL NOT NULL,
            pnl REAL,
            pnl_pct REAL,
            signal_type TEXT,
            regime TEXT,
            confidence_score INTEGER,
            indicators_json TEXT,
            exit_reason TEXT,
            entry_time TEXT NOT NULL,
            exit_time TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            asset TEXT PRIMARY KEY,
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            entry_time TEXT NOT NULL,
            size REAL NOT NULL,
            leverage REAL NOT NULL,
            stop_price REAL NOT NULL,
            target_price REAL NOT NULL,
            unrealized_pnl REAL DEFAULT 0,
            signal_type TEXT,
            regime TEXT,
            confidence_score INTEGER DEFAULT 0,
            bar_index INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS equity_curve (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            asset TEXT NOT NULL,
            equity REAL NOT NULL,
            drawdown_pct REAL NOT NULL,
            total_equity REAL NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_state (
            asset TEXT PRIMARY KEY,
            capital REAL NOT NULL,
            peak_capital REAL NOT NULL,
            last_bar_time TEXT,
            last_exit_bar INTEGER DEFAULT -12,
            trades_completed INTEGER DEFAULT 0
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_asset ON trades(asset)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity_curve(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_equity_asset ON equity_curve(asset)")
    conn.commit()
    return conn


class PaperTrader:
    def __init__(self, conn):
        self.conn = conn
        self.strategies = {}
        self.capitals = {}
        self.peak_capitals = {}
        self.open_positions = {}
        self.fee_pct = 0.045 / 100  # 0.045%
        self.max_risk_pct = {}

        self._load_state()
        self._init_strategies()

    def _load_state(self):
        """Load state from DB or initialize fresh."""
        for asset in ASSETS:
            row = self.conn.execute(
                "SELECT capital, peak_capital FROM paper_state WHERE asset = ?",
                (asset,)
            ).fetchone()
            if row:
                self.capitals[asset] = row[0]
                self.peak_capitals[asset] = row[1]
            else:
                self.capitals[asset] = CAPITAL_PER_ASSET
                self.peak_capitals[asset] = CAPITAL_PER_ASSET
                self.conn.execute(
                    "INSERT INTO paper_state (asset, capital, peak_capital) VALUES (?, ?, ?)",
                    (asset, CAPITAL_PER_ASSET, CAPITAL_PER_ASSET)
                )
            self.max_risk_pct[asset] = ASSET_MAX_RISK.get(asset, 8.0) / 100

            # Load open position if any
            pos = self.conn.execute(
                "SELECT direction, entry_price, entry_time, size, leverage, "
                "stop_price, target_price, signal_type, regime, confidence_score, bar_index "
                "FROM positions WHERE asset = ?", (asset,)
            ).fetchone()
            if pos:
                self.open_positions[asset] = Trade(
                    entry_time=pd.Timestamp(pos[2]),
                    entry_price=pos[1],
                    direction=pos[0],
                    signal=pos[7] or "",
                    stop_price=pos[5],
                    target_price=pos[6],
                    size_usd=pos[3],
                    confidence_score=pos[9] or 0,
                    leverage=pos[4],
                    market_regime=pos[8],
                )
                self.open_positions[asset]._bar_index = pos[10]

        self.conn.commit()

    def _init_strategies(self):
        """Initialize SqueezeV13 instances per asset."""
        for asset in ASSETS:
            self.strategies[asset] = SqueezeV13(asset_name=asset)

    def process_bar(self, asset, data):
        """
        Process one new bar for an asset.
        data: DataFrame with 500+ rows of OHLCV, newest bar is last row.
        Returns: dict with action taken (if any).
        """
        if len(data) < 100:
            return {"action": "SKIP", "reason": "insufficient data", "bars": len(data)}

        strategy = self.strategies[asset]

        # Force recompute indicators on full dataset
        strategy._ind = None
        strategy._precompute(data)

        i = len(data) - 1
        row = data.iloc[i]
        price = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        now = data.index[i].isoformat() if hasattr(data.index[i], 'isoformat') else str(data.index[i])

        result = {"asset": asset, "price": price, "time": now}

        # Check exit on open position
        if asset in self.open_positions:
            trade = self.open_positions[asset]
            exit_reason = None
            exit_price = None

            if trade.direction == "LONG":
                if low <= trade.stop_price:
                    exit_reason = "STOP"
                    exit_price = trade.stop_price
                elif high >= trade.target_price:
                    exit_reason = "TARGET"
                    exit_price = trade.target_price
            elif trade.direction == "SHORT":
                if high >= trade.stop_price:
                    exit_reason = "STOP"
                    exit_price = trade.stop_price
                elif low <= trade.target_price:
                    exit_reason = "TARGET"
                    exit_price = trade.target_price

            if not exit_reason:
                sig = strategy.check_exit(data, i, trade)
                if isinstance(sig, str) and sig.startswith("PYRAMID_"):
                    pct = int(sig.split("_")[1]) / 100.0
                    add_size = trade.size_usd * pct
                    max_add = self.capitals[asset] * self.max_risk_pct[asset] * 2
                    add_size = min(add_size, max_add)
                    trade.size_usd = round(trade.size_usd + add_size, 2)
                    atr_now = float(high - low)
                    if trade.direction == "LONG":
                        be_stop = trade.entry_price + atr_now * 0.5
                        if be_stop > trade.stop_price:
                            trade.stop_price = be_stop
                            strategy._trailing_stop = be_stop
                    else:
                        be_stop = trade.entry_price - atr_now * 0.5
                        if be_stop < trade.stop_price:
                            trade.stop_price = be_stop
                            strategy._trailing_stop = be_stop
                    self._save_position(asset, trade)
                    result["action"] = "PYRAMID"
                    result["new_size"] = trade.size_usd
                    return result
                elif sig:
                    exit_reason = sig
                    exit_price = price

            if exit_reason:
                self._close_position(asset, trade, exit_price, exit_reason, now)
                result["action"] = "EXIT"
                result["exit_reason"] = exit_reason
                result["pnl"] = trade.pnl_usd
                return result

            # Update unrealized P&L
            if trade.direction == "LONG":
                unreal = (price - trade.entry_price) / trade.entry_price
            else:
                unreal = (trade.entry_price - price) / trade.entry_price
            unreal_usd = trade.size_usd * unreal
            self.conn.execute(
                "UPDATE positions SET unrealized_pnl = ? WHERE asset = ?",
                (round(unreal_usd, 2), asset)
            )
            result["action"] = "HOLD"
            result["unrealized_pnl"] = round(unreal_usd, 2)

        # Check for new entry
        if asset not in self.open_positions:
            signal = strategy.generate_signal(data, i)

            if signal and signal.get("action") in ("LONG", "SHORT"):
                direction = signal["action"]
                stop = signal.get("stop", 0)
                target = signal.get("target", 0)
                sig_leverage = signal.get("leverage", 1.0)
                capital = self.capitals[asset]

                risk_per_unit = abs(price - stop) if stop else price * 0.02
                margin = min(capital * 0.4, capital)
                size = margin * sig_leverage
                max_loss = capital * self.max_risk_pct[asset]
                loss_per_unit = risk_per_unit * (size / price) if price > 0 else max_loss
                if loss_per_unit > max_loss and risk_per_unit > 0:
                    size = max_loss / (risk_per_unit / price)
                size = min(size, capital * sig_leverage)

                trade = Trade(
                    entry_time=data.index[i],
                    entry_price=price,
                    direction=direction,
                    signal=signal.get("signal", ""),
                    stop_price=stop,
                    target_price=target,
                    size_usd=round(size, 2),
                    confidence_score=signal.get("confidence_score", 0),
                    leverage=sig_leverage,
                    market_regime=signal.get("market_regime"),
                )
                trade._bar_index = i

                self.open_positions[asset] = trade
                self._save_position(asset, trade)

                indicators = {k: signal.get(k) for k in
                              ["rsi_at_entry", "atr_at_entry", "bb_width_at_entry",
                               "market_regime", "confidence_score"]
                              if signal.get(k) is not None}

                result["action"] = "ENTRY"
                result["direction"] = direction
                result["size"] = round(size, 2)
                result["leverage"] = sig_leverage
                result["stop"] = stop
                result["target"] = target
                result["signal"] = signal.get("signal", "")
                return result

            result["action"] = "NONE"

        # Record equity
        equity = self.capitals[asset]
        if asset in self.open_positions:
            trade = self.open_positions[asset]
            if trade.direction == "LONG":
                unreal = (price - trade.entry_price) / trade.entry_price
            else:
                unreal = (trade.entry_price - price) / trade.entry_price
            equity += trade.size_usd * unreal

        self.peak_capitals[asset] = max(self.peak_capitals[asset], equity)
        dd = (self.peak_capitals[asset] - equity) / self.peak_capitals[asset] if self.peak_capitals[asset] > 0 else 0

        total_equity = sum(self.capitals.values())
        self.conn.execute(
            "INSERT INTO equity_curve (timestamp, asset, equity, drawdown_pct, total_equity) "
            "VALUES (?, ?, ?, ?, ?)",
            (now, asset, round(equity, 2), round(dd * 100, 2), round(total_equity, 2))
        )
        self.conn.commit()

        return result

    def _close_position(self, asset, trade, exit_price, exit_reason, now):
        """Close a position, record trade, update capital."""
        trade.exit_time = pd.Timestamp(now)
        trade.exit_price = exit_price
        trade.exit_reason = exit_reason

        if trade.direction == "LONG":
            raw_pnl = (exit_price - trade.entry_price) / trade.entry_price
        else:
            raw_pnl = (trade.entry_price - exit_price) / trade.entry_price

        pnl_pct = raw_pnl - (self.fee_pct * 2)
        pnl_usd = trade.size_usd * pnl_pct
        trade.pnl_pct = round(pnl_pct * 100, 2)
        trade.pnl_usd = round(pnl_usd, 2)

        self.capitals[asset] += pnl_usd
        self.peak_capitals[asset] = max(self.peak_capitals[asset], self.capitals[asset])

        # Feed back to strategy for Kelly
        self.strategies[asset].record_trade(trade.direction, trade.pnl_pct)

        # Store trade in DB
        self.conn.execute(
            "INSERT INTO trades (timestamp, asset, direction, entry_price, exit_price, "
            "size, leverage, pnl, pnl_pct, signal_type, regime, confidence_score, "
            "exit_reason, entry_time, exit_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now, asset, trade.direction, trade.entry_price, exit_price,
             trade.size_usd, trade.leverage, trade.pnl_usd, trade.pnl_pct,
             trade.signal, trade.market_regime, trade.confidence_score,
             exit_reason, str(trade.entry_time), now)
        )

        # Remove position
        self.conn.execute("DELETE FROM positions WHERE asset = ?", (asset,))

        # Update state
        self.conn.execute(
            "UPDATE paper_state SET capital = ?, peak_capital = ?, last_bar_time = ? WHERE asset = ?",
            (round(self.capitals[asset], 2), round(self.peak_capitals[asset], 2), now, asset)
        )
        self.conn.commit()

        del self.open_positions[asset]

    def _save_position(self, asset, trade):
        """Save or update position in DB."""
        self.conn.execute(
            "INSERT OR REPLACE INTO positions "
            "(asset, direction, entry_price, entry_time, size, leverage, "
            "stop_price, target_price, signal_type, regime, confidence_score, bar_index) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (asset, trade.direction, trade.entry_price, str(trade.entry_time),
             trade.size_usd, trade.leverage, trade.stop_price, trade.target_price,
             trade.signal, trade.market_regime, trade.confidence_score,
             getattr(trade, '_bar_index', 0))
        )
        self.conn.commit()

    def get_status(self):
        """Get current status summary."""
        status = {
            "total_equity": 0,
            "total_pnl": 0,
            "assets": {},
        }
        for asset in ASSETS:
            capital = self.capitals[asset]
            pnl = capital - CAPITAL_PER_ASSET
            dd = (self.peak_capitals[asset] - capital) / self.peak_capitals[asset] * 100 if self.peak_capitals[asset] > 0 else 0
            pos = self.open_positions.get(asset)

            status["assets"][asset] = {
                "capital": round(capital, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl / CAPITAL_PER_ASSET * 100, 2),
                "drawdown_pct": round(dd, 2),
                "position": {
                    "direction": pos.direction,
                    "entry_price": pos.entry_price,
                    "size": pos.size_usd,
                    "leverage": pos.leverage,
                    "stop": pos.stop_price,
                    "target": pos.target_price,
                } if pos else None,
            }
            status["total_equity"] += capital

        status["total_pnl"] = round(status["total_equity"] - CAPITAL_PER_ASSET * len(ASSETS), 2)
        status["total_equity"] = round(status["total_equity"], 2)
        return status
