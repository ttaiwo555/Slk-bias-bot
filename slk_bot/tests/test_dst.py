"""
slk_bot/tests/test_dst.py
-------------------------
Unit tests for New York Daylight Saving Time (DST) handling:
1. March 8, 2026 (Spring forward: EST UTC-5 -> EDT UTC-4 at 02:00)
   17:00 NY shifts from 22:00 UTC to 21:00 UTC.
2. November 1, 2026 (Fall back: EDT UTC-4 -> EST UTC-5 at 02:00)
   17:00 NY shifts from 21:00 UTC to 22:00 UTC.
"""

from __future__ import annotations

import pandas as pd
import pytest

from slk_bot.data.candles import resample, _daily_open_utc, get_profile


def _make_dummy_h1(start: str, end: str) -> pd.DataFrame:
    """Generate dummy 1-hour candles with UTC timestamps."""
    index = pd.date_range(start=start, end=end, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {
            "open": 1.1000,
            "high": 1.1050,
            "low": 1.0950,
            "close": 1.1020,
            "volume": 1000.0,
        },
        index=index,
    )
    df.index.name = "open_time"
    return df


def test_daily_open_utc_dst_transitions():
    """Verify daily open UTC calculation for both 2026 clock changes."""
    prof = get_profile("forex")

    # Before March 8, 2026: EST (UTC-5) -> 17:00 NY is 22:00 UTC
    assert _daily_open_utc(pd.Timestamp("2026-03-07"), prof) == pd.Timestamp("2026-03-07 22:00:00", tz="UTC")

    # Sunday March 8, 2026: clocks sprang forward to EDT (UTC-4) at 02:00 -> 17:00 NY is 21:00 UTC
    assert _daily_open_utc(pd.Timestamp("2026-03-08"), prof) == pd.Timestamp("2026-03-08 21:00:00", tz="UTC")
    assert _daily_open_utc(pd.Timestamp("2026-03-09"), prof) == pd.Timestamp("2026-03-09 21:00:00", tz="UTC")

    # Before November 1, 2026: EDT (UTC-4) -> 17:00 NY is 21:00 UTC
    assert _daily_open_utc(pd.Timestamp("2026-10-31"), prof) == pd.Timestamp("2026-10-31 21:00:00", tz="UTC")

    # Sunday November 1, 2026: clocks fall back to EST (UTC-5) at 02:00 -> 17:00 NY is 22:00 UTC
    assert _daily_open_utc(pd.Timestamp("2026-11-01"), prof) == pd.Timestamp("2026-11-01 22:00:00", tz="UTC")
    assert _daily_open_utc(pd.Timestamp("2026-11-02"), prof) == pd.Timestamp("2026-11-02 22:00:00", tz="UTC")


def test_march_2026_resample_daily_dst():
    """Verify that daily resampling shifts from 22:00 UTC to 21:00 UTC across March 8, 2026."""
    h1 = _make_dummy_h1("2026-03-05 00:00", "2026-03-12 00:00")
    daily = resample(h1, "D", "forex")

    # March 5 session opens at 22:00 UTC
    mar5_bar = daily.loc["2026-03-05 22:00:00+00:00"]
    assert mar5_bar is not None

    # March 8 session (opens Sunday 17:00 NY after spring forward) -> 21:00 UTC
    mar8_bar = daily.loc["2026-03-08 21:00:00+00:00"]
    assert mar8_bar is not None

    # March 9 session opens at 21:00 UTC
    mar9_bar = daily.loc["2026-03-09 21:00:00+00:00"]
    assert mar9_bar is not None


def test_november_1_2026_resample_daily_dst():
    """Verify that daily resampling shifts from 21:00 UTC to 22:00 UTC across November 1, 2026."""
    h1 = _make_dummy_h1("2026-10-29 00:00", "2026-11-05 00:00")
    daily = resample(h1, "D", "forex")

    # October 30 session (before change, EDT) -> 21:00 UTC
    oct30_bar = daily.loc["2026-10-30 21:00:00+00:00"]
    assert oct30_bar is not None

    # November 1 session (Sunday 17:00 NY after clocks fall back at 02:00 to EST) -> 22:00 UTC
    nov1_bar = daily.loc["2026-11-01 22:00:00+00:00"]
    assert nov1_bar is not None

    # November 2 session -> 22:00 UTC
    nov2_bar = daily.loc["2026-11-02 22:00:00+00:00"]
    assert nov2_bar is not None


def test_weekly_resample_dst():
    """Verify that weekly bar anchor (Sunday 17:00 NY) shifts correctly between EDT and EST."""
    # Summer week (EDT): Sunday opens at 21:00 UTC (span 2 weeks so bar 1 is closed)
    h1_summer = _make_dummy_h1("2026-10-11 00:00", "2026-10-25 00:00")
    w_summer = resample(h1_summer, "W", "forex")
    assert pd.Timestamp("2026-10-11 21:00:00+00:00") in w_summer.index

    # Winter week (EST): Sunday opens at 22:00 UTC (span 2 weeks so bar 1 is closed)
    h1_winter = _make_dummy_h1("2026-11-08 00:00", "2026-11-22 00:00")
    w_winter = resample(h1_winter, "W", "forex")
    assert pd.Timestamp("2026-11-08 22:00:00+00:00") in w_winter.index
