"""
slk_bot/tests/test_grids.py
---------------------------
Unit tests for grid candle close time calculations, DST awareness,
weekend market-closed freezing, and fast no-op detection.
"""

import datetime
import zoneinfo
import pytest

from slk_bot.bot.grids import (
    get_latest_forex_h4_close,
    get_latest_metal_h4_close,
    get_latest_forex_daily_close,
    get_latest_crypto_h4_close,
    get_all_latest_closed_candle_times,
    evaluate_new_candles_closed,
    NY_TZ,
    UTC_TZ,
)


def test_forex_h4_weekday_close():
    # Tuesday 15:30 NY (= 19:30 UTC in EDT)
    # The 09:00-13:00 NY candle closed at 13:00 NY
    dt_ny = datetime.datetime(2026, 10, 13, 15, 30, tzinfo=NY_TZ)
    dt_utc = dt_ny.astimezone(UTC_TZ)

    close_utc = get_latest_forex_h4_close(dt_utc)
    close_ny = close_utc.astimezone(NY_TZ)

    assert close_ny.hour == 13
    assert close_ny.minute == 0
    assert close_ny.day == 13


def test_forex_weekend_freeze():
    # Saturday 12:00 NY: Forex market is closed
    sat_ny = datetime.datetime(2026, 10, 31, 12, 0, tzinfo=NY_TZ)
    sat_utc = sat_ny.astimezone(UTC_TZ)

    close_utc = get_latest_forex_h4_close(sat_utc)
    close_ny = close_utc.astimezone(NY_TZ)

    # Must be Friday 17:00 NY
    assert close_ny.weekday() == 4  # Friday
    assert close_ny.hour == 17
    assert close_ny.minute == 0


def test_crypto_continuous_24_7():
    # Saturday 14:15 UTC
    # 08:00-12:00 UTC candle closed at 12:00 UTC
    dt_utc = datetime.datetime(2026, 10, 31, 14, 15, tzinfo=UTC_TZ)
    close_utc = get_latest_crypto_h4_close(dt_utc)

    assert close_utc.hour == 12
    assert close_utc.minute == 0
    assert close_utc.day == 31


def test_fast_noop_detection():
    # Run at 14:07 UTC
    t1 = datetime.datetime(2026, 10, 31, 14, 7, tzinfo=UTC_TZ)
    times_t1 = get_all_latest_closed_candle_times(t1)

    # 15 minutes later (14:22 UTC): No new candle has closed
    t2 = datetime.datetime(2026, 10, 31, 14, 22, tzinfo=UTC_TZ)
    times_t2 = get_all_latest_closed_candle_times(t2)

    has_new, new_grids = evaluate_new_candles_closed(times_t2, times_t1)
    assert has_new is False
    assert len(new_grids) == 0

    # At 16:07 UTC (after 16:00 UTC): Crypto H4 candle closed!
    t3 = datetime.datetime(2026, 10, 31, 16, 7, tzinfo=UTC_TZ)
    times_t3 = get_all_latest_closed_candle_times(t3)

    has_new_3, new_grids_3 = evaluate_new_candles_closed(times_t3, times_t1)
    assert has_new_3 is True
    assert "crypto_utc_h4" in new_grids_3
