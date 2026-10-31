"""
slk_bot/tests/test_candles.py
-----------------------------
Unit tests for candle resampling, open continuity, and alignment.
"""

import pandas as pd
import pytest

from slk_bot.data.candles import (
    apply_open_continuity,
    get_profile,
    resample,
)


def test_apply_open_continuity():
    """Verify open(i) = close(i-1) and high/low widening."""
    df = pd.DataFrame(
        {
            "open": [1.1000, 1.1050, 1.0950],
            "high": [1.1100, 1.1080, 1.1020],
            "low": [1.0980, 1.1010, 1.0920],
            "close": [1.1020, 1.0980, 1.1000],
            "volume": [100.0, 100.0, 100.0],
        },
        index=pd.date_range("2026-10-18", periods=3, freq="4h", tz="UTC"),
    )
    
    out = apply_open_continuity(df)
    
    # Row 0: unchanged
    assert out.iloc[0]["open"] == 1.1000
    
    # Row 1: open becomes close(0) = 1.1020
    assert out.iloc[1]["open"] == 1.1020
    assert out.iloc[1]["high"] == 1.1080  # max(1.1080, 1.1020)
    assert out.iloc[1]["low"] == 1.1010   # min(1.1010, 1.1020)
    
    # Row 2: open becomes close(1) = 1.0980
    assert out.iloc[2]["open"] == 1.0980
    assert out.iloc[2]["high"] == 1.1020  # max(1.1020, 1.0980)
    assert out.iloc[2]["low"] == 1.0920   # min(1.0920, 1.0980)


def test_open_continuity_widens_high_and_low():
    """Verify that if previous close exceeds high or is below low, high/low are widened."""
    df = pd.DataFrame(
        {
            "open": [1.1000, 1.0800],
            "high": [1.1000, 1.0850],  # lower than previous close 1.1000
            "low": [1.1000, 1.0750],
            "close": [1.1000, 1.0820],
            "volume": [10.0, 10.0],
        },
        index=pd.date_range("2026-10-18", periods=2, freq="4h", tz="UTC"),
    )
    
    out = apply_open_continuity(df)
    assert out.iloc[1]["open"] == 1.1000
    # High widened from 1.0850 to 1.1000 to contain the open
    assert out.iloc[1]["high"] == 1.1000
    assert out.iloc[1]["low"] == 1.0750


def test_candle_close_time():
    """Verify nominal period end calculation for weekly forex/indices vs crypto."""
    from slk_bot.data.candles import candle_close_time

    # Forex weekly: opens Sunday 21:00 UTC (17:00 NY in EDT), closes Friday 21:00 UTC (17:00 NY)
    w_open_forex = pd.Timestamp("2026-10-11 21:00:00", tz="UTC")
    w_close_forex = candle_close_time(w_open_forex, "W", "forex")
    assert w_close_forex == pd.Timestamp("2026-10-16 21:00:00", tz="UTC")

    # Index weekly: same as forex (Friday 17:00 NY)
    w_close_index = candle_close_time(w_open_forex, "W", "us_index")
    assert w_close_index == pd.Timestamp("2026-10-16 21:00:00", tz="UTC")

    # Crypto weekly: opens Monday 00:00 UTC, closes Monday 00:00 UTC (7 days)
    w_open_crypto = pd.Timestamp("2026-10-12 00:00:00", tz="UTC")
    w_close_crypto = candle_close_time(w_open_crypto, "W", "crypto")
    assert w_close_crypto == pd.Timestamp("2026-10-19 00:00:00", tz="UTC")


def test_partial_week_never_used_before_close():
    """
    Verify that a partial week candle (e.g. evaluated mid-week on Thursday or Friday afternoon)
    is strictly discarded by drop_forming and never used before its nominal session close.
    """
    from slk_bot.data.candles import drop_forming

    # Two weekly candles:
    # Week 1: 2026-10-04 21:00 UTC (closes 2026-10-09 21:00 UTC)
    # Week 2: 2026-10-11 21:00 UTC (closes 2026-10-16 21:00 UTC)
    times = [
        pd.Timestamp("2026-10-04 21:00:00", tz="UTC"),
        pd.Timestamp("2026-10-11 21:00:00", tz="UTC"),
    ]
    df = pd.DataFrame(
        {
            "open": [1.1252, 1.1201],
            "high": [1.1276, 1.1269],
            "low": [1.1161, 1.1112],
            "close": [1.1201, 1.1238],
            "volume": [1000.0, 1000.0],
        },
        index=times,
    )

    # 1. As of Thursday Oct 15 12:00 UTC -> Week 2 is forming and MUST be dropped
    asof_thu = pd.Timestamp("2026-10-15 12:00:00", tz="UTC")
    df_thu = drop_forming(df, timeframe_or_duration="W", alignment="forex", as_of=asof_thu)
    assert len(df_thu) == 1
    assert df_thu.index[0] == pd.Timestamp("2026-10-04 21:00:00", tz="UTC")

    # 2. As of Friday Oct 16 20:59:00 UTC (1 minute before close) -> Week 2 MUST be dropped
    asof_fri_pre = pd.Timestamp("2026-10-16 20:59:00", tz="UTC")
    df_fri_pre = drop_forming(df, timeframe_or_duration="W", alignment="forex", as_of=asof_fri_pre)
    assert len(df_fri_pre) == 1

    # 3. As of Friday Oct 16 21:00:00 UTC (exact close) -> Week 2 is closed and kept
    asof_fri_close = pd.Timestamp("2026-10-16 21:00:00", tz="UTC")
    df_fri_close = drop_forming(df, timeframe_or_duration="W", alignment="forex", as_of=asof_fri_close)
    assert len(df_fri_close) == 2

    # 4. As of Saturday Oct 17 10:00:00 UTC -> Week 2 is closed and kept
    asof_sat = pd.Timestamp("2026-10-17 10:00:00", tz="UTC")
    df_sat = drop_forming(df, timeframe_or_duration="W", alignment="forex", as_of=asof_sat)
    assert len(df_sat) == 2

