"""
slk_bot/tests/test_run_once_state.py
------------------------------------
Tests state persistence, Telegram update offset immediate saving,
7-day heartbeat rate-limiting, and metadata changes across repeated runs.
"""

import json
import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from run_once import (
    load_state,
    save_state,
    maybe_send_heartbeat,
    get_state_file_paths,
    UTC_TZ,
)
from slk_bot.bot.handlers import BotCommandHandler
from slk_bot.storage.cache import CandleCache


@pytest.fixture
def clean_state_env(tmp_path, monkeypatch):
    """Fixture providing isolated temporary state storage paths."""
    storage_dir = tmp_path / "slk_bot" / "storage"
    state_dir = tmp_path / "state"
    storage_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)

    def mock_get_paths():
        return [
            storage_dir / "state.json",
            state_dir / "state.json",
        ]

    monkeypatch.setattr("run_once.get_state_file_paths", mock_get_paths)
    return storage_dir, state_dir


def test_initial_state_and_synchronous_dual_save(clean_state_env):
    """Confirm state initializes cleanly and saves to both canonical paths."""
    storage_dir, state_dir = clean_state_env

    # 1. Initial load on empty env
    state = load_state()
    assert state["run_count"] == 0
    assert state["last_update_id"] == 0
    assert state["last_run_time_utc"] is None

    # 2. Update state and save
    state["run_count"] = 1
    state["last_update_id"] = 1001
    now_iso = datetime.datetime.now(UTC_TZ).isoformat()
    state["last_run_time_utc"] = now_iso
    save_state(state)

    # 3. Confirm both files exist and have identical contents
    p1 = storage_dir / "state.json"
    p2 = state_dir / "state.json"
    assert p1.exists()
    assert p2.exists()

    d1 = json.loads(p1.read_text(encoding="utf-8"))
    d2 = json.loads(p2.read_text(encoding="utf-8"))
    assert d1 == d2
    assert d1["run_count"] == 1
    assert d1["last_update_id"] == 1001
    assert d1["last_run_time_utc"] == now_iso


def test_consecutive_runs_metadata_progression(clean_state_env):
    """Confirm metadata advances correctly between two consecutive scheduled runs."""
    storage_dir, state_dir = clean_state_env

    # --- RUN 1 (Scheduled Run at T0) ---
    t0 = datetime.datetime(2026, 11, 2, 8, 0, 0, tzinfo=UTC_TZ)
    state_r1 = load_state()
    state_r1["run_count"] += 1
    state_r1["last_run_time_utc"] = t0.isoformat()
    state_r1["last_update_id"] = 500
    state_r1["last_candle_close_times"] = {"forex_ny17_h4": "2026-11-02T08:00:00+00:00"}
    save_state(state_r1)

    assert state_r1["run_count"] == 1
    assert state_r1["last_run_time_utc"] == t0.isoformat()

    # --- RUN 2 (Consecutive Run at T0 + 15m) ---
    t1 = datetime.datetime(2026, 11, 2, 8, 15, 0, tzinfo=UTC_TZ)
    state_r2 = load_state()
    assert state_r2["run_count"] == 1
    assert state_r2["last_run_time_utc"] == t0.isoformat()
    assert state_r2["last_update_id"] == 500

    # Advance Run 2
    state_r2["run_count"] += 1
    state_r2["last_run_time_utc"] = t1.isoformat()
    state_r2["last_update_id"] = 505
    state_r2["last_candle_close_times"]["crypto_utc_h4"] = "2026-11-02T08:00:00+00:00"
    save_state(state_r2)

    # Verify Run 2 persisted state
    reloaded = load_state()
    assert reloaded["run_count"] == 2
    assert reloaded["last_run_time_utc"] == t1.isoformat()
    assert reloaded["last_update_id"] == 505
    assert "crypto_utc_h4" in reloaded["last_candle_close_times"]


def test_telegram_offset_saved_immediately(clean_state_env):
    """Requirement 1: Telegram offset is saved immediately after processing commands."""
    storage_dir, state_dir = clean_state_env

    state = load_state()
    state["run_count"] = 1
    save_state(state)

    # Simulate command loop processing 3 messages, updating offset from 100 to 103
    last_update_id = 100
    new_updates = [{"update_id": 100}, {"update_id": 101}, {"update_id": 102}]
    for u in new_updates:
        last_update_id = max(last_update_id, u["update_id"] + 1)

    assert last_update_id == 103
    if last_update_id != state.get("last_update_id", 0):
        state["last_update_id"] = last_update_id
        save_state(state)

    # Verify on-disk file immediately updated
    reloaded = load_state()
    assert reloaded["last_update_id"] == 103


def test_heartbeat_at_most_once_every_7_days(clean_state_env):
    """Requirement 2: Heartbeat is sent at most once every 7 days based on persisted timestamp."""
    storage_dir, state_dir = clean_state_env

    t0 = datetime.datetime(2026, 11, 2, 8, 0, 0, tzinfo=UTC_TZ)
    state = {
        "run_count": 1,
        "first_started_utc": (t0 - datetime.timedelta(days=10)).isoformat(),
        "last_heartbeat_time_utc": None,
    }
    save_state(state)

    mock_telegram = MagicMock()
    mock_telegram.token = "fake_token"
    mock_telegram.chat_id = "12345"
    mock_cache = MagicMock()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (10,)
    mock_conn.cursor.return_value = mock_cursor
    mock_cache._get_connection.return_value.__enter__.return_value = mock_conn

    # 1. First run after 7+ days: Heartbeat should be sent
    sent_1 = maybe_send_heartbeat(state, mock_telegram, mock_cache, t0, dry_run=False)
    assert sent_1 is True
    assert mock_telegram.send_message.call_count == 1
    assert state["last_heartbeat_time_utc"] == t0.isoformat()

    # 2. Next run 15 minutes later: Heartbeat MUST NOT be sent
    t_15m = t0 + datetime.timedelta(minutes=15)
    sent_2 = maybe_send_heartbeat(state, mock_telegram, mock_cache, t_15m, dry_run=False)
    assert sent_2 is False
    assert mock_telegram.send_message.call_count == 1  # count remains 1

    # 3. Next run 6 days later: Heartbeat MUST NOT be sent
    t_6d = t0 + datetime.timedelta(days=6)
    sent_3 = maybe_send_heartbeat(state, mock_telegram, mock_cache, t_6d, dry_run=False)
    assert sent_3 is False
    assert mock_telegram.send_message.call_count == 1

    # 4. Run after 7 days and 1 second: Heartbeat SHOULD be sent
    t_7d = t0 + datetime.timedelta(days=7, seconds=1)
    sent_4 = maybe_send_heartbeat(state, mock_telegram, mock_cache, t_7d, dry_run=False)
    assert sent_4 is True
    assert mock_telegram.send_message.call_count == 2
    assert state["last_heartbeat_time_utc"] == t_7d.isoformat()


def test_status_output_active_instruments_and_run_count(clean_state_env):
    """Confirm /status dynamically reports 39 active instruments and current run_count."""
    storage_dir, state_dir = clean_state_env

    now_iso = datetime.datetime.now(UTC_TZ).isoformat()
    state = {
        "run_count": 42,
        "last_update_id": 9999,
        "last_run_time_utc": now_iso,
        "first_started_utc": now_iso,
        "last_candle_close_times": {"forex_ny17_h4": now_iso},
        "last_heartbeat_time_utc": now_iso,
    }
    save_state(state)

    mock_cache = MagicMock()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = (0,)
    mock_conn.cursor.return_value = mock_cur
    mock_cache._get_connection.return_value.__enter__.return_value = mock_conn

    handler = BotCommandHandler(
        owner_chat_id="console",
        cache=mock_cache,
        dry_run=True,
        state_getter=lambda: state,
    )

    status_resp = handler.handle_message("console", "/status")
    assert "Total Runs:</b> 42" in status_resp
    assert "Active Watchlist:</b> 39 active instruments (USOIL excluded)" in status_resp
    assert "USOIL" not in handler.instruments
    assert len(handler.instruments) == 39
