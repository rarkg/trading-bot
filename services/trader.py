#!/usr/bin/env python3
"""Trader Service — reads candles from Postgres, generates signals, executes on Kraken.

Reads candles from Postgres `candles` table (written by candle-collector).
Generates signals via CandleV2_3 strategy. Places market orders on Kraken.
Manages positions with PENDING -> OPEN -> CLOSED lifecycle.

NO candle fetching. NO dashboard API. Candle data comes from Postgres only.
"""

import os
import sys
import signal
import subprocess
import time
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import Trade
from live.data_provider import PostgresDataProvider, KrakenDataProvider
from live.executor import KrakenExecutor
from live.exchange_adapter import KrakenAdapter, MockAdapter
from live.risk import RiskManager
from live.pg_writer import PgWriter
from live import config
from live.exchange.kraken import CCXT_SYMBOLS
from live.adaptive_sizer import AdaptiveSizer
from live.regime import RegimeDetector

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("trader")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ASSETS = config.ASSETS
MIN_BARS = 200
HOURLY_LOOP_SEC = 60       # check every minute, act once per hour
FAST_LOOP_SEC = 60         # 60s fast loop for SL/TP checks
STALE_CANDLE_MIN = 90      # skip asset if candles older than this

# MODE: live | paper | backtest
MODE = os.environ.get("MODE", "live").lower()

# ---------------------------------------------------------------------------
# Position tracker
# ---------------------------------------------------------------------------
@dataclass
class LivePosition:
    trade_id: Optional[int]
    asset: str
    strategy: str
    trade: Trade
    order_id: Optional[str] = None
    sl_order_id: Optional[str] = None
    tp_order_id: Optional[str] = None


# ---------------------------------------------------------------------------
# iMessage alerts
# ---------------------------------------------------------------------------
IMSG_CHAT_ID = "dan.k.ngo@gmail.com"


def send_imsg(message):
    # type: (str) -> None
    try:
        subprocess.run(
            ["imsg", "send", "--chat-id", IMSG_CHAT_ID, "--text", message],
            timeout=10, capture_output=True,
        )
    except Exception:
        log.warning("Failed to send iMessage alert", exc_info=True)


# ---------------------------------------------------------------------------
# Strategy factory
# ---------------------------------------------------------------------------
STRATEGY_VERSION = os.environ.get("STRATEGY_VERSION", "v2.5").lower().replace(".", "_").replace("v", "")
# Normalize: "v2.5" -> "2_5", "v2_3" -> "2_3", "2.4" -> "2_4"

_STRATEGY_MAP = {
    "2_3": ("strategies.candle_v2_3", "CandleV2_3"),
    "2_4": ("strategies.candle_v2_4", "CandleV2_4"),
    "2_5": ("strategies.candle_v2_5", "CandleV2_5"),
}


def _load_strategy_class():
    """Dynamically load strategy class based on STRATEGY_VERSION env var."""
    key = STRATEGY_VERSION
    if key not in _STRATEGY_MAP:
        log.warning("Unknown STRATEGY_VERSION '%s', defaulting to v2.5", STRATEGY_VERSION)
        key = "2_5"
    module_path, class_name = _STRATEGY_MAP[key]
    import importlib
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    log.info("Loaded strategy %s from %s", class_name, module_path)
    return cls, class_name


def make_candle_strategies():
    # type: () -> tuple
    """Create one strategy instance per asset with V2.5 params.

    Returns (strats_dict, strategy_name).
    """
    cls, class_name = _load_strategy_class()
    strats = {}
    for asset in ASSETS:
        strats[asset] = cls(
            min_score=1,
            stop_atr=2.0,
            target_atr=4.0,
            use_mtf=True,
            mtf_require="both",
            use_rsi=True, use_stoch_rsi=True, use_williams_r=True,
            use_macd=True, use_cci=True, use_ema_alignment=True,
            use_adx=True, use_bb=True, use_atr_percentile=True,
            use_keltner=True, use_volume=True, use_mfi=True,
            use_obv_slope=True, use_range_position=True, use_hh_ll=True,
            pattern_set="top5",
            adx_max=50,
            cooldown=12,
            time_exit_bars=144,
            base_leverage=2.0,
            use_trailing_stop=True,
            trail_activation_atr=1.5,
            trail_distance_atr=0.3,
        )
    # Derive strategy name: "candle_v2_3", "candle_v2_4", etc.
    strat_name = "candle_v" + STRATEGY_VERSION.replace("_", "_")
    return strats, strat_name


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------
def size_position(sig, price, risk_mgr, capital):
    # type: (dict, float, RiskManager, float) -> float
    stop = sig["stop"]
    stop_dist = abs(price - stop) / price if price > 0 else 0
    if stop_dist <= 0:
        return 0.0
    max_size = risk_mgr.max_position_size(capital, stop_dist)
    leverage = sig.get("leverage", 1.0)
    return min(max_size * leverage, capital * leverage)


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------
class Trader:
    def __init__(self):
        self.mode = MODE
        load_dotenv(".env.demo")

        # Data + Execution depend on mode
        if self.mode == "backtest":
            from live.data_provider import CsvDataProvider
            self.data = CsvDataProvider()
            self.adapter = MockAdapter(initial_balance=config.INITIAL_CAPITAL)
            self.executor = None
            self.pg = None
            self.bot_id = None
        elif self.mode == "paper":
            kraken_fallback = KrakenDataProvider()
            self.data = PostgresDataProvider(fallback=kraken_fallback)
            self.adapter = MockAdapter(initial_balance=config.INITIAL_CAPITAL)
            self.executor = None
            self.pg = PgWriter()
            self.bot_id = self.pg.register_bot("crypto-paper", {
                "assets": config.ASSETS,
                "capital": config.INITIAL_CAPITAL,
                "demo": True,
                "strategies": [STRATEGY_VERSION],
                "version": "v3",
                "mode": "paper",
            })  # type: Optional[int]
        else:
            # live mode
            api_key = os.environ["KRAKEN_DEMO_API_KEY"]
            api_secret = os.environ["KRAKEN_DEMO_API_SECRET"]
            kraken_fallback = KrakenDataProvider()
            self.data = PostgresDataProvider(fallback=kraken_fallback)
            self.executor = KrakenExecutor(api_key, api_secret, demo=config.DEMO)
            self.adapter = KrakenAdapter(self.executor)
            self.pg = PgWriter()
            self.bot_id = self.pg.register_bot("crypto-live", {
                "assets": config.ASSETS,
                "capital": config.INITIAL_CAPITAL,
                "demo": config.DEMO,
                "strategies": [STRATEGY_VERSION],
                "version": "v3",
                "mode": "live",
            })  # type: Optional[int]

        self.risk_mgr = RiskManager(max_risk_per_trade=config.MAX_RISK_PER_TRADE)

        # Strategies (dynamic version loading)
        self.candle, self.strat_name = make_candle_strategies()

        # V2.5 adaptive sizing + regime detection
        self.adaptive_sizer = AdaptiveSizer(
            enabled=getattr(config, "USE_ADAPTIVE_SIZING", False),
        )
        self.regime_detector = RegimeDetector(
            enabled=getattr(config, "USE_REGIME_DETECTION", False),
        )

        # Open positions: key = (asset, strategy_name)
        self.positions = {}  # type: dict

        # Track last processed hour
        self.last_processed_hour = None  # type: Optional[datetime]

        # Tick counters
        self._tick_signals = 0
        self._tick_opened = 0
        self._tick_closed = 0

        self._shutdown = False

    def run(self):
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        log.info("=== Trader V3 starting (mode=%s, demo=%s, strategy=%s) ===",
                 self.mode, config.DEMO, self.strat_name)
        log.info("Assets: %s", ASSETS)

        # Sync positions with exchange on startup (live mode only)
        if self.mode == "live":
            self._sync_positions()

        log.info("Entering main loop (hourly at HH:02, fast loop every %ds)", FAST_LOOP_SEC)

        while not self._shutdown:
            try:
                now = datetime.now(timezone.utc)

                # Hourly tick at HH:02+
                if now.minute >= 2:
                    current_hour = now.replace(minute=0, second=0, microsecond=0)
                    if self.last_processed_hour is None or self.last_processed_hour < current_hour:
                        self._hourly_tick(current_hour)

                # Fast loop: check SL/TP on open positions
                if self.positions:
                    self._fast_tick()

            except Exception:
                log.exception("Error in main loop")

            time.sleep(FAST_LOOP_SEC)

        log.info("=== Trader V3 shutdown complete ===")
        if self.pg is not None:
            self.pg.close()

    def _handle_signal(self, signum, frame):
        log.info("Received signal %d, shutting down gracefully...", signum)
        self._shutdown = True

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------
    def _sync_positions(self):
        """Reconcile Postgres OPEN trades with Kraken exchange on startup.

        FIX: Only syncs OPEN trades, skips PENDING (they haven't been filled).
        FIX: Synced positions get stop/target set from ATR so hard stop logic works.
        """
        try:
            exchange_positions = self.adapter.get_positions()
        except Exception:
            log.exception("Failed to fetch exchange positions for sync")
            return

        # Build map: ccxt_symbol -> pos dict
        exchange_map = {}  # type: dict
        for ep in exchange_positions:
            exchange_map[ep["symbol"]] = ep

        # Get OPEN trades from Postgres (not PENDING — those haven't filled)
        db_open = self.pg.get_open_trades(self.bot_id) if self.bot_id is not None else []

        for row in db_open:
            trade_id = row["id"]
            asset = row["asset"]
            strategy = row["strategy"]
            direction = row["direction"]
            sig = row["signal"]
            entry = row["entry_price"]
            stop = row["stop_price"]
            target = row["target_price"]
            size_usd = row["size_usd"]
            leverage = row["leverage"]

            ccxt_sym = CCXT_SYMBOLS.get(asset.upper(), "")
            if ccxt_sym not in exchange_map:
                log.warning(
                    "SYNC: %s %s OPEN in DB but not on exchange — marking closed",
                    asset, strategy,
                )
                self.pg.close_trade_by_id(trade_id, "SYNC_CLOSED")
                continue

            # Exchange has this position — load into memory
            ep = exchange_map.pop(ccxt_sym)
            trade = Trade(
                entry_time=pd.Timestamp.now(tz="UTC"),
                entry_price=ep["entry_price"] if ep["entry_price"] > 0 else entry,
                direction=direction,
                signal=sig or strategy,
                stop_price=stop if stop else 0.0,
                target_price=target if target else 0.0,
                size_usd=size_usd,
                leverage=leverage if leverage else 1.0,
            )
            key = (asset, strategy)
            self.positions[key] = LivePosition(
                trade_id=trade_id,
                asset=asset,
                strategy=strategy,
                trade=trade,
            )
            log.info(
                "SYNC: Loaded %s %s %s (entry=%.2f stop=%.2f target=%.2f)",
                asset, strategy, direction, trade.entry_price,
                trade.stop_price, trade.target_price,
            )

        # Exchange positions not in DB — load with ATR-based stop/target
        for ccxt_sym, ep in exchange_map.items():
            asset_name = None
            for a, s in CCXT_SYMBOLS.items():
                if s == ccxt_sym:
                    asset_name = a
                    break
            if asset_name is None:
                log.warning("SYNC: Unknown exchange position %s — skipping", ccxt_sym)
                continue

            direction = "LONG" if ep["side"] == "long" else "SHORT"
            entry_price = ep["entry_price"]

            # FIX: compute stop/target from ATR so hard stop logic works
            stop_price = 0.0
            target_price = 0.0
            try:
                df = self.data.get_candles(asset_name, "1h", limit=50)
                if len(df) >= 14:
                    atr = self._compute_atr(df, 14)
                    if direction == "LONG":
                        stop_price = entry_price - atr * 2.0
                        target_price = entry_price + atr * 4.0
                    else:
                        stop_price = entry_price + atr * 2.0
                        target_price = entry_price - atr * 4.0
            except Exception:
                log.warning("Could not compute ATR for synced %s position", asset_name)

            trade = Trade(
                entry_time=pd.Timestamp.now(tz="UTC"),
                entry_price=entry_price,
                direction=direction,
                signal="synced",
                stop_price=stop_price,
                target_price=target_price,
                size_usd=ep["size"] * entry_price,
                leverage=1.0,
            )

            trade_id = None
            if self.bot_id is not None:
                trade_id = self.pg.log_trade_open(
                    self.bot_id, asset_name, "synced", direction, "synced",
                    entry_price, stop_price, target_price,
                    trade.size_usd, 1.0,
                )
                # Immediately confirm as OPEN (it's already on exchange)
                if trade_id is not None:
                    self.pg.confirm_trade_open(trade_id, entry_price)

            key = (asset_name, "synced")
            self.positions[key] = LivePosition(
                trade_id=trade_id,
                asset=asset_name,
                strategy="synced",
                trade=trade,
            )
            log.warning(
                "SYNC: Exchange position %s %s NOT in DB — loaded (stop=%.2f target=%.2f)",
                asset_name, direction, stop_price, target_price,
            )

        if self.positions:
            log.info("SYNC: %d active positions after reconciliation", len(self.positions))
        else:
            log.info("SYNC: No open positions")

    @staticmethod
    def _compute_atr(df, period=14):
        # type: (pd.DataFrame, int) -> float
        """Compute ATR from a DataFrame with high/low/close columns."""
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1]),
            ),
        )
        if len(tr) < period:
            return float(np.mean(tr)) if len(tr) > 0 else 0.0
        return float(np.mean(tr[-period:]))

    # ------------------------------------------------------------------
    # Hourly tick
    # ------------------------------------------------------------------
    def _hourly_tick(self, current_hour):
        # type: (datetime) -> None
        """Main hourly processing: read candles from Postgres, run strategies."""
        log.info("--- Hourly tick: %s ---", current_hour.isoformat())
        self.last_processed_hour = current_hour

        self._tick_signals = 0
        self._tick_opened = 0
        self._tick_closed = 0

        for asset in ASSETS:
            df = self.data.get_candles(asset, "1h", limit=720)

            if len(df) == 0:
                log.warning("  %s: no candles in Postgres", asset)
                continue

            # Check staleness
            last_ts = df.index[-1]
            age_min = (pd.Timestamp.now(tz="UTC") - last_ts).total_seconds() / 60
            if age_min > STALE_CANDLE_MIN:
                log.warning("  %s: candles stale (%.0f min old), skipping", asset, age_min)
                continue

            if len(df) < MIN_BARS:
                log.warning("  %s: only %d bars, need %d", asset, len(df), MIN_BARS)
                continue

            i = len(df) - 1
            price = float(df.iloc[i]["close"])

            # Check exits on open positions
            self._check_exits(asset, df, i, price)

            # Generate signals
            self._run_strategy(asset, self.strat_name, self.candle[asset], df, i, price)
            self._tick_signals += 1

        # FIX: Log real Kraken balance as equity
        total_equity = self._get_real_equity()

        # Per-asset equity
        if self.bot_id is not None:
            per_asset_equity = total_equity / len(ASSETS) if len(ASSETS) > 0 else 0
            for asset in ASSETS:
                open_count = sum(1 for pos in self.positions.values() if pos.asset == asset)
                self.pg.log_equity(self.bot_id, asset, per_asset_equity, 0.0, open_count)

        # Heartbeat
        if self.bot_id is not None:
            self.pg.log_heartbeat(
                self.bot_id, self._tick_signals,
                self._tick_opened, self._tick_closed, total_equity,
            )

    def _get_real_equity(self):
        # type: () -> float
        """Fetch account balance from exchange adapter. Falls back to config."""
        try:
            bal = self.adapter.get_balance()
            total = bal.get("USD_total", 0.0)
            if total > 0:
                return total
        except Exception:
            log.warning("Failed to fetch balance", exc_info=True)
        return config.INITIAL_CAPITAL

    # ------------------------------------------------------------------
    # Fast tick (SL/TP check)
    # ------------------------------------------------------------------
    def _fast_tick(self):
        """Check open positions for hard stop/target hits using latest Postgres candle price."""
        keys_to_close = []
        for key, pos in self.positions.items():
            trade = pos.trade
            price = self.data.get_latest_price(pos.asset)
            if price <= 0:
                continue

            exit_reason = None
            if trade.direction == "LONG":
                if trade.stop_price > 0 and price <= trade.stop_price:
                    exit_reason = "STOP"
                elif trade.target_price > 0 and price >= trade.target_price:
                    exit_reason = "TARGET"
            else:
                if trade.stop_price > 0 and price >= trade.stop_price:
                    exit_reason = "STOP"
                elif trade.target_price > 0 and price <= trade.target_price:
                    exit_reason = "TARGET"

            if exit_reason:
                self._close_position(key, pos, price, exit_reason)
                keys_to_close.append(key)

        for k in keys_to_close:
            del self.positions[k]

    # ------------------------------------------------------------------
    # Exits
    # ------------------------------------------------------------------
    def _check_exits(self, asset, df, i, price):
        # type: (str, pd.DataFrame, int, float) -> None
        """Check strategy exit signals + hard stop/target on open positions."""
        keys_to_close = []
        for key, pos in self.positions.items():
            if pos.asset != asset:
                continue
            trade = pos.trade

            # Strategy exit signal
            exit_reason = self.candle[asset].check_exit(df, i, trade)

            # Hard stop/target
            if exit_reason is None:
                if trade.direction == "LONG":
                    if trade.stop_price > 0 and price <= trade.stop_price:
                        exit_reason = "STOP"
                    elif trade.target_price > 0 and price >= trade.target_price:
                        exit_reason = "TARGET"
                else:
                    if trade.stop_price > 0 and price >= trade.stop_price:
                        exit_reason = "STOP"
                    elif trade.target_price > 0 and price <= trade.target_price:
                        exit_reason = "TARGET"

            if exit_reason and not exit_reason.startswith("PYRAMID"):
                self._close_position(key, pos, price, exit_reason)
                keys_to_close.append(key)

        for k in keys_to_close:
            del self.positions[k]

    def _close_position(self, key, pos, price, exit_reason):
        # type: (tuple, LivePosition, float, str) -> None
        """Close position: cancel SL/TP, place reduce-only order, log, alert."""
        trade = pos.trade
        close_side = "sell" if trade.direction == "LONG" else "buy"
        size_contracts = trade.size_usd / trade.entry_price if trade.entry_price > 0 else 0

        if trade.direction == "LONG":
            pnl_pct = (price - trade.entry_price) / trade.entry_price
        else:
            pnl_pct = (trade.entry_price - price) / trade.entry_price
        pnl_usd = pnl_pct * trade.size_usd * trade.leverage

        log.info(
            "CLOSE %s %s %s | exit=%s price=%.2f pnl=$%.2f (%.2f%%)",
            pos.asset, pos.strategy, trade.direction,
            exit_reason, price, pnl_usd, pnl_pct * 100,
        )

        # Cancel exchange SL/TP orders
        self._cancel_sl_tp(pos)

        # Place close order on exchange
        try:
            self.adapter.place_market_order(
                symbol=pos.asset,
                side=close_side,
                size=abs(size_contracts),
                reduce_only=True,
            )
        except Exception:
            log.exception("Failed to close %s %s on exchange", pos.asset, pos.strategy)

        # FIX: Mark CLOSED in Postgres immediately after close order
        if pos.trade_id is not None:
            self.pg.log_trade_close(pos.trade_id, price, exit_reason, pnl_usd, pnl_pct * 100)

        # iMessage alert
        sign = "+" if pnl_usd >= 0 else ""
        msg = (
            "\U0001f534 CLOSE %s %s @ $%.2f | %s$%.0f (%s%.1f%%) | reason: %s"
            % (pos.asset, trade.direction, price, sign, pnl_usd, sign, pnl_pct * 100, exit_reason)
        )
        send_imsg(msg)

        self._tick_closed += 1
        self.adaptive_sizer.record_trade(trade.direction, pnl_pct)

        if self.bot_id is not None:
            self.pg.log_decision(
                self.bot_id, pos.asset, pos.strategy, "CLOSE",
                "%s pnl=$%.2f" % (exit_reason, pnl_usd),
            )

    def _cancel_sl_tp(self, pos):
        # type: (LivePosition) -> None
        for label, oid in [("SL", pos.sl_order_id), ("TP", pos.tp_order_id)]:
            if oid is None:
                continue
            try:
                self.adapter.cancel_order(oid, pos.asset)
                log.info("Cancelled %s order %s for %s", label, oid, pos.asset)
            except Exception:
                log.warning("Failed to cancel %s order %s for %s", label, oid, pos.asset, exc_info=True)
        pos.sl_order_id = None
        pos.tp_order_id = None

    def _place_sl_tp(self, pos):
        # type: (LivePosition) -> None
        """Place exchange SL/TP orders for crash protection."""
        trade = pos.trade
        close_side = "sell" if trade.direction == "LONG" else "buy"
        size_contracts = trade.size_usd / trade.entry_price if trade.entry_price > 0 else 0
        if size_contracts <= 0:
            return

        if trade.stop_price and trade.stop_price > 0:
            try:
                sl_order = self.adapter.place_stop_order(
                    symbol=pos.asset, side=close_side,
                    size=abs(size_contracts), stop_price=trade.stop_price,
                )
                pos.sl_order_id = sl_order.get("id", "")
                log.info("Placed SL %s for %s @ %.2f", pos.sl_order_id, pos.asset, trade.stop_price)
            except Exception:
                log.warning("Failed to place SL for %s", pos.asset, exc_info=True)

        if trade.target_price and trade.target_price > 0:
            try:
                tp_order = self.adapter.place_take_profit_order(
                    symbol=pos.asset, side=close_side,
                    size=abs(size_contracts), tp_price=trade.target_price,
                )
                pos.tp_order_id = tp_order.get("id", "")
                log.info("Placed TP %s for %s @ %.2f", pos.tp_order_id, pos.asset, trade.target_price)
            except Exception:
                log.warning("Failed to place TP for %s", pos.asset, exc_info=True)

    # ------------------------------------------------------------------
    # Signal processing
    # ------------------------------------------------------------------
    def _run_strategy(self, asset, strat_name, strat, df, i, price):
        # type: (str, str, CandleV2_3, pd.DataFrame, int, float) -> None
        """Run strategy, open position if signal fires."""
        key = (asset, strat_name)

        if key in self.positions:
            return

        if not self.risk_mgr.check_drawdown(asset, config.CAPITAL_PER_ASSET):
            if self.bot_id is not None:
                self.pg.log_decision(self.bot_id, asset, strat_name, "SKIP", "DD limit breached")
            return

        # Regime detection
        regime_state = self.regime_detector.detect(df, i)
        regime_adj = None
        if self.regime_detector.enabled:
            regime_adj = lambda d: self.regime_detector.get_score_adjustment(regime_state, d)

        sig = strat.generate_signal(df, i, regime_score_adj=regime_adj)
        if sig is None:
            return

        direction = sig["action"]

        # Correlation guard
        if getattr(strat, 'use_correlation_guard', False):
            max_same = getattr(strat, 'max_same_direction', 3)
            same_dir_count = sum(
                1 for pos in self.positions.values()
                if pos.trade.direction == direction
            )
            if same_dir_count >= max_same:
                if self.bot_id is not None:
                    self.pg.log_decision(
                        self.bot_id, asset, strat_name, "SKIP",
                        "correlation_guard: %d %s already open" % (same_dir_count, direction),
                    )
                return

        stop = sig["stop"]
        target = sig["target"]
        leverage = sig.get("leverage", 1.0)
        signal_name = sig.get("signal", strat_name)

        # Size position with V2.5 multipliers
        base_capital = config.CAPITAL_PER_ASSET
        adaptive_mult = self.adaptive_sizer.get_multiplier(direction)
        regime_dir_mult = regime_state.direction_multiplier(direction)
        vol_mult = regime_state.volatility_multiplier
        combined_mult = adaptive_mult * regime_dir_mult * vol_mult
        combined_mult = max(0.2, min(2.5, combined_mult))
        adjusted_capital = base_capital * combined_mult

        size_usd = size_position(sig, price, self.risk_mgr, adjusted_capital)
        if size_usd <= 0:
            if self.bot_id is not None:
                self.pg.log_decision(self.bot_id, asset, strat_name, "SKIP", "Size=0 for %s" % signal_name)
            return

        trade = Trade(
            entry_time=df.index[i],
            entry_price=price,
            direction=direction,
            signal=signal_name,
            stop_price=stop,
            target_price=target,
            size_usd=size_usd,
            leverage=leverage,
        )

        order_side = "buy" if direction == "LONG" else "sell"
        size_contracts = size_usd / price if price > 0 else 0

        log.info(
            "OPEN %s %s %s | signal=%s price=%.2f stop=%.2f target=%.2f size=$%.0f lev=%.1fx",
            asset, strat_name, direction, signal_name,
            price, stop, target, size_usd, leverage,
        )

        # Log as PENDING before placing order
        trade_id = None  # type: Optional[int]
        if self.bot_id is not None:
            trade_id = self.pg.log_trade_open(
                self.bot_id, asset, strat_name, direction, signal_name,
                price, stop, target, size_usd, leverage,
            )

        try:
            order = self.adapter.place_market_order(
                symbol=asset, side=order_side,
                size=size_contracts,
            )
            order_id = order.get("id", "")
        except Exception:
            log.exception("Failed to place %s %s order", asset, strat_name)
            if trade_id and self.bot_id is not None:
                self.pg.cancel_pending_trade(trade_id, "Order placement failed: %s" % signal_name)
                self.pg.log_decision(self.bot_id, asset, strat_name, "ERROR", "Order failed for %s" % signal_name)
            return

        # Confirm fill
        actual_fill = price
        try:
            time.sleep(0.5)
            order_status = self.adapter.fetch_order(order_id, asset)
            if order_status.get("status") == "closed":
                actual_fill = float(order_status.get("average") or order_status.get("price") or price)
                log.info("Fill confirmed: %s %s @ $%.4f (signal $%.4f)", asset, direction, actual_fill, price)
            elif order_status.get("status") == "canceled":
                if trade_id and self.bot_id:
                    self.pg.cancel_pending_trade(trade_id, "Order cancelled by exchange")
                log.warning("Order cancelled by exchange for %s %s", asset, strat_name)
                return
        except Exception:
            log.warning("Could not confirm fill for %s — using signal price", asset)

        # PENDING -> OPEN
        if trade_id:
            self.pg.confirm_trade_open(trade_id, actual_fill)
            trade.entry_price = actual_fill

        pos = LivePosition(
            trade_id=trade_id,
            asset=asset,
            strategy=strat_name,
            trade=trade,
            order_id=order_id,
        )
        self.positions[key] = pos

        # Place exchange SL/TP
        self._place_sl_tp(pos)

        # iMessage alert
        msg = (
            "\U0001f7e2 OPEN %s %s @ $%.2f | stop $%.2f target $%.2f | $%.0f %.1fx"
            % (asset, direction, price, stop, target, size_usd, leverage)
        )
        send_imsg(msg)

        self._tick_opened += 1
        if self.bot_id is not None:
            self.pg.log_decision(self.bot_id, asset, strat_name, "OPEN", "%s size=$%.0f" % (signal_name, size_usd))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    trader = Trader()
    trader.run()
