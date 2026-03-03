#!/usr/bin/env python3
"""
Trading Bot Scanner — Hourly SPX/VIX data collector + breakout detection.
Runs as a persistent pm2 process. Self-healing with retry + alerting.
"""

import time
import signal
import sys
import logging
import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
import traceback

import yfinance as yf
import psycopg2
from psycopg2.extras import execute_values

# --- Config ---
SUPABASE_CONFIG = {
    "host": "aws-0-us-west-2.pooler.supabase.com",
    "port": 6543,
    "dbname": "postgres",
    "user": "postgres.iioghvvidsvwqqonlesn",
    "password": "MdDSpVFXnQJlx8xz",
}
POLL_INTERVAL_SECONDS = 300  # 5 min between checks (only acts on hour boundaries)
MARKET_TZ = ZoneInfo("America/New_York")
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
MAX_RETRIES = 3
RETRY_BACKOFF = [5, 15, 45]  # seconds

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/Users/elio/Projects/trading-bot/scanner.log"),
    ],
)
log = logging.getLogger("scanner")

# --- State ---
last_hour_processed = None
consecutive_failures = 0
running = True
alerted_signals = set()  # track hour_start strings we've already alerted on

# Signals worth alerting (skip INSIDE — no directional move)
ALERT_SIGNALS = {"BREAKOUT_UP", "BREAKDOWN", "REJECTION_HIGH", "REJECTION_LOW_BOUNCE"}

SIGNAL_EMOJI = {
    "BREAKOUT_UP": "🟢",
    "BREAKDOWN": "🔴",
    "REJECTION_HIGH": "🔶",
    "REJECTION_LOW_BOUNCE": "🔷",
}


def signal_handler(sig, frame):
    global running
    log.info("Shutdown signal received, exiting gracefully...")
    running = False


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def is_market_hours():
    """Check if we're in US market hours (9:30 AM - 4:30 PM ET, weekdays)."""
    now = datetime.now(MARKET_TZ)
    if now.weekday() >= 5:  # Saturday/Sunday
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def current_market_hour():
    """Get current hour boundary in market time."""
    now = datetime.now(MARKET_TZ)
    return now.replace(minute=0, second=0, microsecond=0)


def get_db_connection():
    """Get database connection with retry."""
    for attempt in range(MAX_RETRIES):
        try:
            conn = psycopg2.connect(**SUPABASE_CONFIG, connect_timeout=10)
            conn.autocommit = True
            return conn
        except Exception as e:
            log.warning(f"DB connection attempt {attempt+1} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF[attempt])
    raise Exception("Failed to connect to database after all retries")


def fetch_hourly_data():
    """Fetch latest hourly candles for SPX and VIX."""
    spx = yf.Ticker("^GSPC")
    spx_hist = spx.history(period="1d", interval="1h")

    vix = yf.Ticker("^VIX")
    vix_hist = vix.history(period="1d", interval="1h")

    return spx_hist, vix_hist


def detect_breakout(curr, prev):
    """Detect breakout/rejection signal."""
    broke_high = float(curr["High"]) > float(prev["High"])
    broke_low = float(curr["Low"]) < float(prev["Low"])
    bullish = float(curr["Close"]) > float(curr["Open"])

    if broke_high and float(curr["Close"]) > float(prev["High"]):
        return "BREAKOUT_UP"
    elif broke_high and float(curr["Close"]) < float(prev["High"]):
        return "REJECTION_HIGH"
    elif broke_low and float(curr["Close"]) < float(prev["Low"]):
        return "BREAKDOWN"
    elif broke_low and float(curr["Close"]) > float(prev["Low"]):
        return "REJECTION_LOW_BOUNCE"
    else:
        return "INSIDE"


def analyze_signal_context(signal_type, curr_close, prev_high, prev_low, hour_start_iso):
    """Pull DB context to enrich the alert with analysis."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        today = datetime.now(MARKET_TZ).strftime("%Y-%m-%d")

        # VIX trend — last 3 hours
        cur.execute("""
            SELECT hour_start, close FROM vix_hourly
            WHERE date = %s ORDER BY hour_start DESC LIMIT 4
        """, (today,))
        vix_rows = cur.fetchall()
        vix_trend = None
        vix_now = None
        if len(vix_rows) >= 2:
            vix_now = float(vix_rows[0][1])
            vix_prev = float(vix_rows[-1][1])
            delta = vix_now - vix_prev
            vix_trend = f"↑{delta:.1f}" if delta > 0.5 else (f"↓{abs(delta):.1f}" if delta < -0.5 else "flat")

        # Prior signals today — look for momentum
        cur.execute("""
            SELECT signal_type, hour_start, curr_close FROM breakout_signals
            WHERE date = %s ORDER BY hour_start DESC LIMIT 5
        """, (today,))
        prior_signals = cur.fetchall()

        # Count directional momentum
        bullish_signals = sum(1 for s in prior_signals if s[0] in ("BREAKOUT_UP", "REJECTION_LOW_BOUNCE"))
        bearish_signals = sum(1 for s in prior_signals if s[0] in ("BREAKDOWN", "REJECTION_HIGH"))

        # SPX candles today — intraday high/low + opening candle
        cur.execute("""
            SELECT MIN(low), MAX(high) FROM hourly_candles
            WHERE ticker='SPX' AND date=%s
        """, (today,))
        row = cur.fetchone()
        day_low = float(row[0]) if row and row[0] else None
        day_high = float(row[1]) if row and row[1] else None

        cur.execute("""
            SELECT open FROM hourly_candles
            WHERE ticker='SPX' AND date=%s ORDER BY hour_start ASC LIMIT 1
        """, (today,))
        open_row = cur.fetchone()
        day_open = float(open_row[0]) if open_row else None

        conn.close()

        return {
            "vix_now": vix_now,
            "vix_trend": vix_trend,
            "bullish_count": bullish_signals,
            "bearish_count": bearish_signals,
            "prior_signals": [(s[0], s[2]) for s in prior_signals[:3]],
            "day_low": day_low,
            "day_high": day_high,
            "day_open": day_open,
        }
    except Exception as e:
        log.warning(f"Context analysis failed: {e}")
        return {}


def compute_playbook_score(signal_type, ctx):
    """Quick playbook confidence score (0-100%) based on available data."""
    score = 0
    max_score = 0
    factors = []

    vix = ctx.get("vix_now")
    vix_trend = ctx.get("vix_trend", "")
    bullish_signal = signal_type in ("BREAKOUT_UP", "REJECTION_LOW_BOUNCE")

    # VIX regime (25 pts)
    max_score += 25
    if vix:
        if bullish_signal:
            if vix < 20:
                score += 25; factors.append("VIX low (bullish regime)")
            elif vix < 25:
                score += 18; factors.append("VIX transitional")
            elif vix < 30:
                score += 8; factors.append("VIX elevated (headwind)")
            else:
                score += 0; factors.append("VIX stress zone (contra-trend risk)")
        else:  # bearish signal
            if vix > 25:
                score += 25; factors.append("VIX elevated (confirms bearish)")
            elif vix > 20:
                score += 15; factors.append("VIX moderate")
            else:
                score += 5; factors.append("VIX low (weak bearish signal)")

    # VIX direction confirming (15 pts)
    max_score += 15
    if vix_trend:
        if bullish_signal and "↓" in vix_trend:
            score += 15; factors.append("VIX falling (confirms)")
        elif not bullish_signal and "↑" in vix_trend:
            score += 15; factors.append("VIX rising (confirms)")
        elif "flat" in vix_trend:
            score += 8; factors.append("VIX flat (neutral)")
        else:
            score += 0; factors.append("VIX diverging (headwind)")

    # Momentum alignment (20 pts)
    max_score += 20
    b = ctx.get("bullish_count", 0)
    br = ctx.get("bearish_count", 0)
    if bullish_signal:
        if b > br:
            score += 20; factors.append(f"momentum aligned ({b}B/{br}b)")
        elif b == br:
            score += 10; factors.append("momentum mixed")
        else:
            score += 0; factors.append(f"momentum contra ({br}B/{b}b)")
    else:
        if br > b:
            score += 20; factors.append(f"momentum aligned ({br}B/{b}b)")
        elif br == b:
            score += 10; factors.append("momentum mixed")
        else:
            score += 0; factors.append(f"momentum contra ({b}B/{br}b)")

    # Price vs open (20 pts)
    max_score += 20
    day_open = ctx.get("day_open")
    curr_close = ctx.get("curr_close")
    if day_open and curr_close:
        pct = ((curr_close - day_open) / day_open) * 100
        if bullish_signal:
            if pct > 0:
                score += 20; factors.append(f"above open ({pct:+.1f}%)")
            elif pct > -0.5:
                score += 12; factors.append(f"near open ({pct:+.1f}%)")
            else:
                score += 5; factors.append(f"below open ({pct:+.1f}%, recovery needed)")
        else:
            if pct < 0:
                score += 20; factors.append(f"below open ({pct:+.1f}%)")
            elif pct < 0.5:
                score += 12; factors.append(f"near open ({pct:+.1f}%)")
            else:
                score += 5; factors.append(f"above open ({pct:+.1f}%, reversal needed)")

    # Signal strength / range (20 pts)
    max_score += 20
    # Bigger range = stronger breakout
    # We don't have range directly here but we can infer from prior_signals pattern
    prior = ctx.get("prior_signals", [])
    if len(prior) >= 2:
        # Clean breakout after consolidation (INSIDE → BREAKOUT) = strong
        prior_types = [s[0] for s in prior[:3]]
        if "INSIDE" in prior_types:
            score += 20; factors.append("compression→breakout pattern")
        else:
            score += 10; factors.append("direct breakout (no compression)")
    else:
        score += 10

    pct_score = int((score / max_score) * 100) if max_score > 0 else 50
    return pct_score, factors


def build_alert_message(signal_type, hour_start, curr_close, prev_high, prev_low, ctx):
    """Build enriched alert message from signal + DB context."""
    emoji = SIGNAL_EMOJI.get(signal_type, "📊")
    hour_str = hour_start.strftime("%H:%M ET") if hasattr(hour_start, "strftime") else str(hour_start)

    # Add curr_close to ctx for scoring
    ctx["curr_close"] = curr_close

    # Compute playbook score
    confidence, factors = compute_playbook_score(signal_type, ctx)

    lines = [f"{emoji} SPX {signal_type} @ {hour_str} — close {curr_close:.0f}"]

    # VIX context
    if ctx.get("vix_now"):
        vix_str = f"VIX {ctx['vix_now']:.1f}"
        if ctx.get("vix_trend"):
            vix_str += f" ({ctx['vix_trend']} last 3h)"
        lines.append(vix_str)

    # Intraday context
    if ctx.get("day_open") and ctx.get("day_low") and ctx.get("day_high"):
        pct_from_open = ((curr_close - ctx["day_open"]) / ctx["day_open"]) * 100
        sign = "+" if pct_from_open >= 0 else ""
        lines.append(f"vs open: {sign}{pct_from_open:.1f}% | day range {ctx['day_low']:.0f}–{ctx['day_high']:.0f}")

    # Momentum read
    b, br = ctx.get("bullish_count", 0), ctx.get("bearish_count", 0)
    if b > br:
        momentum = f"momentum: {b} bull vs {br} bear today"
    elif br > b:
        momentum = f"momentum: {br} bear vs {b} bull today"
    else:
        momentum = "momentum: mixed"
    lines.append(momentum)

    # Prior signals
    if ctx.get("prior_signals"):
        prior_str = " → ".join(s[0].replace("_", " ") for s in ctx["prior_signals"][:3])
        lines.append(f"prior: {prior_str}")

    # Playbook confidence
    lines.append(f"playbook confidence: {confidence}%")
    if confidence >= 70:
        lines.append("read: REAL SIGNAL — worth sizing")
    elif confidence >= 50:
        lines.append("read: MODERATE — small size, watch for confirmation")
    else:
        lines.append("read: WEAK — likely noise, wait for more confirmation")

    return "\n".join(lines)


def get_paper_balance():
    """Get current paper trading balance."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT balance FROM public.paper_account ORDER BY updated_at DESC LIMIT 1")
        row = cur.fetchone()
        conn.close()
        return float(row[0]) if row else 1000.0
    except Exception as e:
        log.warning(f"Could not fetch paper balance: {e}")
        return 1000.0


def enter_paper_trade(signal_type, entry_time, entry_price, confidence, ctx):
    """Enter a paper trade based on signal + confidence."""
    try:
        balance = get_paper_balance()
        bullish = signal_type in ("BREAKOUT_UP", "REJECTION_LOW_BOUNCE")
        direction = "LONG" if bullish else "SHORT"

        # Position sizing: confidence-based % of capital
        if confidence >= 70:
            risk_pct = 0.10  # 10% of capital
        elif confidence >= 50:
            risk_pct = 0.05  # 5% of capital
        else:
            return None  # No trade below 50%

        position_size = round(balance * risk_pct, 2)

        # Stop and target based on day range
        day_range = (ctx.get("day_high", entry_price) - ctx.get("day_low", entry_price)) or 30
        stop_distance = day_range * 0.25  # 25% of day range
        target_distance = day_range * 0.50  # 50% of day range (2:1 R)

        if bullish:
            stop_price = entry_price - stop_distance
            target_price = entry_price + target_distance
        else:
            stop_price = entry_price + stop_distance
            target_price = entry_price - target_distance

        conn = get_db_connection()
        cur = conn.cursor()
        today = datetime.now(MARKET_TZ).strftime("%Y-%m-%d")
        cur.execute("""
            INSERT INTO public.paper_trades
              (date, signal_type, direction, entry_time, entry_price, stop_price,
               target_price, confidence_pct, position_size, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (today, signal_type, direction, entry_time, entry_price,
              round(stop_price, 2), round(target_price, 2),
              confidence, position_size,
              f"VIX:{ctx.get('vix_now','?')} bal:${balance}"))
        trade_id = cur.fetchone()[0]
        conn.close()

        log.info(f"📝 Paper trade #{trade_id}: {direction} SPX @ {entry_price:.0f} | size:${position_size} | conf:{confidence}% | stop:{stop_price:.0f} | target:{target_price:.0f}")
        return {"id": trade_id, "direction": direction, "entry": entry_price,
                "stop": stop_price, "target": target_price, "size": position_size, "conf": confidence}
    except Exception as e:
        log.warning(f"Paper trade entry failed: {e}")
        return None


def check_and_exit_open_trades(current_price, current_time, signal_type=None):
    """Check open paper trades and exit if stop/target/reversal hit."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, direction, entry_price, stop_price, target_price,
                   position_size, confidence_pct
            FROM public.paper_trades
            WHERE exit_time IS NULL
            ORDER BY entry_time ASC
        """)
        open_trades = cur.fetchall()

        for trade in open_trades:
            tid, direction, entry, stop, target, size, conf = trade
            exit_reason = None
            exit_price = None

            if direction == "LONG":
                if current_price <= float(stop):
                    exit_reason, exit_price = "STOP_HIT", float(stop)
                elif current_price >= float(target):
                    exit_reason, exit_price = "TARGET_HIT", float(target)
                elif signal_type in ("BREAKDOWN", "REJECTION_HIGH"):
                    exit_reason, exit_price = "REVERSAL_SIGNAL", current_price
            else:  # SHORT
                if current_price >= float(stop):
                    exit_reason, exit_price = "STOP_HIT", float(stop)
                elif current_price <= float(target):
                    exit_reason, exit_price = "TARGET_HIT", float(target)
                elif signal_type in ("BREAKOUT_UP", "REJECTION_LOW_BOUNCE"):
                    exit_reason, exit_price = "REVERSAL_SIGNAL", current_price

            # EOD exit (after 3:45 PM ET)
            if not exit_reason and current_time.hour >= 15 and current_time.minute >= 45:
                exit_reason, exit_price = "EOD", current_price

            if exit_reason:
                pnl_pts = (exit_price - float(entry)) if direction == "LONG" else (float(entry) - exit_price)
                # P&L in dollars: position_size / entry_price * pnl_pts
                pts_per_dollar = float(size) / float(entry)
                pnl_usd = round(pnl_pts * pts_per_dollar, 2)

                # Update balance
                balance = get_paper_balance()
                new_balance = round(balance + pnl_usd, 2)
                cur.execute("""
                    UPDATE public.paper_trades
                    SET exit_time=%s, exit_price=%s, exit_reason=%s,
                        pnl_pts=%s, pnl_usd=%s, capital_after=%s
                    WHERE id=%s
                """, (current_time, exit_price, exit_reason,
                      round(pnl_pts, 2), pnl_usd, new_balance, tid))
                cur.execute("INSERT INTO public.paper_account (balance, note) VALUES (%s, %s)",
                            (new_balance, f"Trade #{tid} {exit_reason}"))

                icon = "✅" if pnl_usd > 0 else "❌"
                log.info(f"{icon} Paper trade #{tid} closed: {exit_reason} | P&L: {pnl_pts:+.0f}pts / ${pnl_usd:+.2f} | balance: ${new_balance}")

                # Alert group
                pnl_icon = "✅" if pnl_usd > 0 else "❌"
                msg = (f"{pnl_icon} Paper trade closed: {direction} SPX\n"
                       f"entry {float(entry):.0f} → exit {exit_price:.0f} ({exit_reason})\n"
                       f"P&L: {pnl_pts:+.0f}pts / ${pnl_usd:+.2f} | balance: ${new_balance}")
                try:
                    subprocess.run(["/opt/homebrew/bin/imsg", "send", "--chat-id", "6", "--text", msg],
                                   timeout=10, check=True, capture_output=True)
                except Exception:
                    pass

        conn.close()
    except Exception as e:
        log.warning(f"Exit check failed: {e}")


def send_group_alert(signal_type, hour_start, curr_close, prev_high, prev_low, vix_close=None):
    """Send enriched signal alert to Tự Kỷ group chat."""
    hour_key = hour_start.isoformat() if hasattr(hour_start, "isoformat") else str(hour_start)
    ctx = analyze_signal_context(signal_type, curr_close, prev_high, prev_low, hour_key)
    msg = build_alert_message(signal_type, hour_start, curr_close, prev_high, prev_low, ctx)
    try:
        subprocess.run(
            ["/opt/homebrew/bin/imsg", "send", "--chat-id", "6", "--text", msg],
            timeout=10, check=True, capture_output=True
        )
        log.info(f"📣 Group alert sent: {signal_type} @ {hour_key}")
    except Exception as e:
        log.warning(f"Failed to send group alert: {e}")


def store_data(spx_hist, vix_hist):
    """Store candle data + breakout signals in Supabase."""
    conn = get_db_connection()
    cur = conn.cursor()
    today = datetime.now(MARKET_TZ).strftime("%Y-%m-%d")
    inserted = {"candles": 0, "vix": 0, "signals": 0}

    try:
        # SPX candles
        for idx, row in spx_hist.iterrows():
            cur.execute(
                """INSERT INTO hourly_candles (ticker, date, hour_start, open, high, low, close, volume)
                   VALUES ('SPX', %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (ticker, hour_start) DO UPDATE SET
                     open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                     close=EXCLUDED.close, volume=EXCLUDED.volume""",
                (today, idx.isoformat(), float(row["Open"]), float(row["High"]),
                 float(row["Low"]), float(row["Close"]), int(row["Volume"])),
            )
            inserted["candles"] += 1

        # VIX candles
        for idx, row in vix_hist.iterrows():
            cur.execute(
                """INSERT INTO vix_hourly (date, hour_start, open, high, low, close)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (date, hour_start) DO UPDATE SET
                     open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                     close=EXCLUDED.close""",
                (today, idx.isoformat(), float(row["Open"]), float(row["High"]),
                 float(row["Low"]), float(row["Close"])),
            )
            inserted["vix"] += 1

        # Breakout signals
        # Get latest VIX close for alert context
        latest_vix = float(vix_hist.iloc[-1]["Close"]) if not vix_hist.empty else None

        candles = list(spx_hist.iterrows())
        for i in range(1, len(candles)):
            idx, row = candles[i]
            _, prev = candles[i - 1]
            signal_type = detect_breakout(row, prev)
            rng = float(row["High"]) - float(row["Low"])
            bullish = float(row["Close"]) > float(row["Open"])
            hour_key = idx.isoformat()

            rows_affected = cur.execute(
                """INSERT INTO breakout_signals (ticker, date, hour_start, signal_type,
                     prev_high, prev_low, curr_close, range_pts, bullish)
                   VALUES ('SPX', %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (today, idx.isoformat(), signal_type, float(prev["High"]),
                 float(prev["Low"]), float(row["Close"]), rng, bullish),
            )
            inserted["signals"] += 1

            # Alert group + paper trade on new meaningful signals
            if (signal_type in ALERT_SIGNALS
                    and hour_key not in alerted_signals
                    and cur.rowcount > 0):  # only on fresh insert
                alerted_signals.add(hour_key)
                send_group_alert(
                    signal_type, idx, float(row["Close"]),
                    float(prev["High"]), float(prev["Low"]), latest_vix
                )
                # Auto paper trade based on confidence
                alert_ctx = analyze_signal_context(signal_type, float(row["Close"]),
                                                   float(prev["High"]), float(prev["Low"]),
                                                   hour_key)
                alert_ctx["curr_close"] = float(row["Close"])
                conf, _ = compute_playbook_score(signal_type, alert_ctx)
                if conf >= 50:
                    entry_time = datetime.now(MARKET_TZ)
                    enter_paper_trade(signal_type, entry_time, float(row["Close"]), conf, alert_ctx)

    finally:
        cur.close()
        conn.close()

    return inserted


def run_cycle():
    """Run one data collection cycle."""
    global last_hour_processed, consecutive_failures

    current_hour = current_market_hour()

    # Skip if already processed this hour
    if last_hour_processed == current_hour:
        return None

    log.info(f"Running cycle for {current_hour.strftime('%Y-%m-%d %H:%M')} ET")

    for attempt in range(MAX_RETRIES):
        try:
            spx_hist, vix_hist = fetch_hourly_data()
            if spx_hist.empty:
                log.warning("No SPX data returned, skipping")
                return None

            result = store_data(spx_hist, vix_hist)
            last_hour_processed = current_hour
            consecutive_failures = 0
            log.info(f"✅ Stored: {result['candles']} SPX, {result['vix']} VIX, {result['signals']} signals")

            # Check open paper trades for exits on every cycle
            if not spx_hist.empty:
                current_price = float(spx_hist.iloc[-1]["Close"])
                check_and_exit_open_trades(current_price, datetime.now(MARKET_TZ))

            return result

        except Exception as e:
            log.error(f"Attempt {attempt+1} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF[attempt])
            else:
                consecutive_failures += 1
                log.error(f"All retries exhausted. Consecutive failures: {consecutive_failures}")
                if consecutive_failures >= 3:
                    log.critical("🚨 3+ consecutive failures — needs attention!")
                raise

    return None


def main():
    log.info("🚀 Trading Bot Scanner started")
    log.info(f"Config: poll every {POLL_INTERVAL_SECONDS}s, market hours only")

    while running:
        try:
            now_et = datetime.now(MARKET_TZ)
            now_local = datetime.now(LOCAL_TZ)

            if is_market_hours():
                result = run_cycle()
                if result:
                    log.info(f"Cycle complete at {now_local.strftime('%H:%M PST')}")
            else:
                # Log once per hour when market is closed
                if now_et.minute < 6:
                    log.info(f"Market closed ({now_et.strftime('%H:%M ET %A')}). Sleeping.")

        except Exception as e:
            log.error(f"Cycle error: {e}\n{traceback.format_exc()}")

        # Sleep between polls
        time.sleep(POLL_INTERVAL_SECONDS)

    log.info("Scanner stopped.")


if __name__ == "__main__":
    main()
