"""
slk_bot/storage/cache.py
------------------------
Local SQLite candle cache for persisting full lookback histories.
Stores OHLCV candles indexed by (symbol, timeframe, open_time).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional, Any, Union
import pandas as pd

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "candles.db"


class CandleCache:
    """Local SQLite cache for storing and retrieving candle history."""

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS candles (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    open_time TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    PRIMARY KEY (symbol, timeframe, open_time)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sym_tf_time
                ON candles (symbol, timeframe, open_time)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS setups (
                    setup_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    htf TEXT NOT NULL,
                    ltf TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    method INTEGER NOT NULL,
                    trigger_time TEXT NOT NULL,
                    state TEXT NOT NULL,
                    extreme_x REAL NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    dedupe_key TEXT PRIMARY KEY,
                    setup_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    htf TEXT NOT NULL,
                    ltf TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    method INTEGER NOT NULL,
                    breakout_type TEXT NOT NULL,
                    alert_time TEXT NOT NULL,
                    is_low_prob INTEGER NOT NULL,
                    is_marginal INTEGER NOT NULL,
                    is_suppressed INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts_sent (
                    dedupe_key TEXT PRIMARY KEY,
                    sent_at TEXT NOT NULL,
                    message_text TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS price_alerts (
                    alert_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    target_price REAL NOT NULL,
                    direction TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def save_candles(self, symbol: str, timeframe: str, df: pd.DataFrame) -> int:
        """
        Insert or replace candles into the local cache.
        Returns the number of rows saved.
        """
        if df.empty:
            return 0

        rows = []
        for ts, row in df.iterrows():
            time_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            rows.append((
                symbol,
                timeframe.upper(),
                time_str,
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row.get("volume", 0.0)),
            ))

        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO candles
                (symbol, timeframe, open_time, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def load_candles(
        self, symbol: str, timeframe: str, count: Optional[int] = None
    ) -> pd.DataFrame:
        """
        Load cached candles sorted ascending by open_time.
        If count is specified, loads the most recent `count` candles.
        """
        tf = timeframe.upper()
        with self._get_connection() as conn:
            if count is not None:
                query = """
                    SELECT open_time, open, high, low, close, volume
                    FROM (
                        SELECT open_time, open, high, low, close, volume
                        FROM candles
                        WHERE symbol = ? AND timeframe = ?
                        ORDER BY open_time DESC
                        LIMIT ?
                    )
                    ORDER BY open_time ASC
                """
                df = pd.read_sql_query(query, conn, params=(symbol, tf, count))
            else:
                query = """
                    SELECT open_time, open, high, low, close, volume
                    FROM candles
                    WHERE symbol = ? AND timeframe = ?
                    ORDER BY open_time ASC
                """
                df = pd.read_sql_query(query, conn, params=(symbol, tf))

        if df.empty:
            return pd.DataFrame(
                columns=["open", "high", "low", "close", "volume"],
                index=pd.DatetimeIndex([], tz="UTC", name="open_time"),
            )

        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
        df.set_index("open_time", inplace=True)
        return df

    def get_stats(self, symbol: str, timeframe: str) -> tuple[int, Optional[pd.Timestamp], Optional[pd.Timestamp]]:
        """Return (count, oldest_open_time, newest_open_time) for a given symbol & timeframe."""
        tf = timeframe.upper()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*), MIN(open_time), MAX(open_time)
                FROM candles
                WHERE symbol = ? AND timeframe = ?
                """,
                (symbol, tf),
            )
            row = cursor.fetchone()

        if not row or row[0] == 0:
            return 0, None, None

        cnt, min_t, max_t = row
        oldest = pd.to_datetime(min_t, utc=True) if min_t else None
        newest = pd.to_datetime(max_t, utc=True) if max_t else None
        return cnt, oldest, newest

    def save_setup(
        self,
        setup_id: Any,
        symbol: str = "",
        htf: str = "",
        ltf: str = "",
        direction: str = "",
        method: int = 1,
        trigger_time: str = "",
        state: str = "",
        extreme_x: float = 0.0,
        payload_json: str = "{}",
    ) -> None:
        """
        Insert or replace a setup state in local storage.
        Can accept an SLKSetup object as first argument, or individual scalar arguments.
        """
        if hasattr(setup_id, "id") and hasattr(setup_id, "trigger"):
            setup_obj = setup_id
            _sid = setup_obj.id
            _sym = setup_obj.symbol
            _htf = setup_obj.htf
            _ltf = setup_obj.ltf
            _dir = setup_obj.trigger.direction.value if hasattr(setup_obj.trigger.direction, "value") else str(setup_obj.trigger.direction)
            _met = setup_obj.trigger.method.value if hasattr(setup_obj.trigger.method, "value") else int(setup_obj.trigger.method)
            _tt = setup_obj.timeline.trigger_time.isoformat() if hasattr(setup_obj.timeline.trigger_time, "isoformat") else str(setup_obj.timeline.trigger_time)
            _st = setup_obj.state.value if hasattr(setup_obj.state, "value") else str(setup_obj.state)
            _xx = setup_obj.timeline.extreme_price_x
            _pj = json.dumps(setup_obj.to_dict()) if hasattr(setup_obj, "to_dict") else "{}"
            return self.save_setup(_sid, _sym, _htf, _ltf, _dir, _met, _tt, _st, _xx, _pj)

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO setups (
                    setup_id, symbol, htf, ltf, direction, method, trigger_time, state, extreme_x, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (setup_id, symbol, htf, ltf, direction, method, trigger_time, state, extreme_x, payload_json),
            )

    def get_setups(self, symbol: Optional[str] = None) -> list[dict]:
        """Fetch all setups from database, optionally filtered by symbol."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if symbol:
                cursor.execute(
                    "SELECT setup_id, symbol, htf, ltf, direction, method, trigger_time, state, extreme_x, payload_json FROM setups WHERE symbol = ? ORDER BY trigger_time",
                    (symbol,),
                )
            else:
                cursor.execute(
                    "SELECT setup_id, symbol, htf, ltf, direction, method, trigger_time, state, extreme_x, payload_json FROM setups ORDER BY trigger_time"
                )
            rows = cursor.fetchall()

        out = []
        for r in rows:
            out.append({
                "setup_id": r[0],
                "symbol": r[1],
                "htf": r[2],
                "ltf": r[3],
                "direction": r[4],
                "method": r[5],
                "trigger_time": r[6],
                "state": r[7],
                "extreme_x": r[8],
                "payload_json": r[9],
            })
        return out

    def save_alert(
        self,
        dedupe_key: Any,
        setup_id: str = "",
        symbol: str = "",
        htf: str = "",
        ltf: str = "",
        direction: str = "",
        method: int = 1,
        breakout_type: str = "",
        alert_time: str = "",
        is_low_prob: bool = False,
        is_marginal: bool = False,
        is_suppressed: bool = False,
        payload_json: str = "{}",
    ) -> bool:
        """
        Save alert into local storage.
        Can accept an SLKAlert object as first argument, or individual arguments.
        Returns True if newly inserted, False if duplicate dedupe_key.
        """
        if hasattr(dedupe_key, "dedupe_key"):
            alert_obj = dedupe_key
            _key = alert_obj.dedupe_key
            _sid = setup_id or getattr(alert_obj, "setup_id", _key)
            _sym = alert_obj.symbol
            _htf = alert_obj.htf
            _ltf = alert_obj.ltf
            _dir = alert_obj.direction.value if hasattr(alert_obj.direction, "value") else str(alert_obj.direction)
            _met = alert_obj.method.value if hasattr(alert_obj.method, "value") else int(alert_obj.method)
            _bt = alert_obj.breakout_type.value if hasattr(alert_obj.breakout_type, "value") else str(alert_obj.breakout_type)
            _at = alert_obj.alert_time.isoformat() if hasattr(alert_obj.alert_time, "isoformat") else str(alert_obj.alert_time)
            _low = alert_obj.is_low_probability
            _marg = alert_obj.is_marginal
            _supp = getattr(alert_obj, "is_suppressed", False)
            _pjson = json.dumps(alert_obj.to_dict()) if hasattr(alert_obj, "to_dict") else "{}"
            return self.save_alert(
                _key, _sid, _sym, _htf, _ltf, _dir, _met, _bt, _at, _low, _marg, _supp, _pjson
            )

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM alerts WHERE dedupe_key = ?", (dedupe_key,))
            if cursor.fetchone():
                return False  # Already exists

            cursor.execute(
                """
                INSERT INTO alerts (
                    dedupe_key, setup_id, symbol, htf, ltf, direction, method,
                    breakout_type, alert_time, is_low_prob, is_marginal, is_suppressed, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dedupe_key,
                    setup_id,
                    symbol,
                    htf,
                    ltf,
                    direction,
                    method,
                    breakout_type,
                    alert_time,
                    1 if is_low_prob else 0,
                    1 if is_marginal else 0,
                    1 if is_suppressed else 0,
                    payload_json,
                ),
            )
            return True

    def get_alerts(self, symbol: Optional[str] = None) -> list[dict]:
        """Fetch all alerts from database, optionally filtered by symbol."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if symbol:
                cursor.execute(
                    "SELECT dedupe_key, setup_id, symbol, htf, ltf, direction, method, breakout_type, alert_time, is_low_prob, is_marginal, is_suppressed, payload_json FROM alerts WHERE symbol = ? ORDER BY alert_time",
                    (symbol,),
                )
            else:
                cursor.execute(
                    "SELECT dedupe_key, setup_id, symbol, htf, ltf, direction, method, breakout_type, alert_time, is_low_prob, is_marginal, is_suppressed, payload_json FROM alerts ORDER BY alert_time"
                )
            rows = cursor.fetchall()

        out = []
        for r in rows:
            out.append({
                "dedupe_key": r[0],
                "setup_id": r[1],
                "symbol": r[2],
                "htf": r[3],
                "ltf": r[4],
                "direction": r[5],
                "method": r[6],
                "breakout_type": r[7],
                "alert_time": r[8],
                "is_low_prob": bool(r[9]),
                "is_marginal": bool(r[10]),
                "is_suppressed": bool(r[11]),
                "payload_json": r[12],
            })
        return out

    # ── Phase 4 Persistence Helpers ──────────────────────────────────────────

    def is_alert_sent(self, dedupe_key: str) -> bool:
        """True if this dedupe_key has already been sent to Telegram."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM alerts_sent WHERE dedupe_key = ?", (dedupe_key,))
            return cursor.fetchone() is not None

    def mark_alert_sent(
        self, dedupe_key: str, sent_at: Optional[str] = None, message_text: str = ""
    ) -> None:
        """Record an alert as sent with timestamp and message text."""
        if not sent_at:
            sent_at = pd.Timestamp.now("UTC").isoformat()
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO alerts_sent (dedupe_key, sent_at, message_text) VALUES (?, ?, ?)",
                (dedupe_key, sent_at, message_text),
            )

    def save_price_alert(
        self,
        alert_id: str,
        symbol: str,
        target_price: float,
        direction: str,
        created_at: str,
        expires_at: str,
        status: str = "active",
    ) -> None:
        """Insert or replace a price alert."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO price_alerts
                (alert_id, symbol, target_price, direction, created_at, expires_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (alert_id, symbol, target_price, direction, created_at, expires_at, status),
            )

    def get_active_price_alerts(self, symbol: Optional[str] = None) -> list[dict]:
        """Fetch all active price alerts, optionally filtered by symbol."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if symbol:
                cursor.execute(
                    "SELECT alert_id, symbol, target_price, direction, created_at, expires_at, status FROM price_alerts WHERE status = 'active' AND symbol = ? ORDER BY created_at",
                    (symbol,),
                )
            else:
                cursor.execute(
                    "SELECT alert_id, symbol, target_price, direction, created_at, expires_at, status FROM price_alerts WHERE status = 'active' ORDER BY created_at"
                )
            rows = cursor.fetchall()

        return [
            {
                "alert_id": r[0],
                "symbol": r[1],
                "target_price": r[2],
                "direction": r[3],
                "created_at": r[4],
                "expires_at": r[5],
                "status": r[6],
            }
            for r in rows
        ]

    def update_price_alert_status(self, alert_id: str, status: str) -> bool:
        """Update the status of a price alert (e.g., 'triggered', 'expired', 'cancelled')."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE price_alerts SET status = ? WHERE alert_id = ?", (status, alert_id))
            return cursor.rowcount > 0

    def get_price_alert(self, alert_id: str) -> Optional[dict]:
        """Fetch a specific price alert by ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT alert_id, symbol, target_price, direction, created_at, expires_at, status FROM price_alerts WHERE alert_id = ?",
                (alert_id,),
            )
            r = cursor.fetchone()
        if not r:
            return None
        return {
            "alert_id": r[0],
            "symbol": r[1],
            "target_price": r[2],
            "direction": r[3],
            "created_at": r[4],
            "expires_at": r[5],
            "status": r[6],
        }

    def get_bot_state(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Retrieve a bot state value by key."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
            r = cursor.fetchone()
        return r[0] if r else default

    def set_bot_state(self, key: str, value: str) -> None:
        """Persist a bot state key-value pair."""
        with self._get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)", (key, value))
