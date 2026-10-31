"""
slk_bot/data/candles.py
------------------------
Candle resampling and alignment.

Plain-language explanation
--------------------------
TradingView draws daily and weekly bars starting at specific times
(17:00 New York for forex, 00:00 UTC for crypto, etc.). If we use OANDA's
native daily bars they might start at a different time, and then the
A-shapes, V-shapes and rejections we detect won't match your charts.

So instead, this module takes the fine-grain 1-hour candles from OANDA
and "stacks" them into bigger bars using the exact same open times that
TradingView uses. That way the high of our H4 bar and the high of your
TradingView H4 bar should be identical.

Key concept: "anchor"
---------------------
An "anchor" is the reference start time for the first bar of each session.
For forex, the anchor is Sunday 17:00 New York. Pandas uses "offset aliases"
(like "17h" offsets) to group 1-hour candles into properly-aligned H4 / D / W bars.

DST handling
------------
The US changes clocks on 1 November 2026. We use the pytz / zoneinfo library
to convert "17:00 America/New_York" to UTC so the offset shifts automatically.
The result: in summer (EDT, UTC-4) the forex day opens at 21:00 UTC;
in winter (EST, UTC-5) it opens at 22:00 UTC.

Functions
---------
resample(h1_df, timeframe, alignment_profile) → DataFrame
    Takes a DataFrame of 1-hour candles and returns H4 / D / W candles
    aligned to the given profile.

drop_forming(df) → DataFrame
    Removes any candle that has not yet closed (should never be in the final
    strategy input, but we double-check here).
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import pytz

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Alignment profiles
# Each profile tells us:
#   - daily_open: local time string e.g. "17:00"
#   - tz:         timezone name for the above time, e.g. "America/New_York"
#   - weekly_anchor_day: 0=Monday … 6=Sunday (the day the week bar starts)
# ---------------------------------------------------------------------------
_PROFILES: dict[str, dict[str, Any]] = {
    "forex": {
        "daily_open": "17:00",
        "tz": "America/New_York",
        "weekly_anchor_day": 6,   # Sunday
        "open_continuity": True,
    },
    "crypto": {
        "daily_open": "00:00",
        "tz": "UTC",
        "weekly_anchor_day": 0,   # Monday
        "open_continuity": False,
    },
    "us_index": {
        "daily_open": "17:00",
        "tz": "America/New_York",
        "weekly_anchor_day": 6,
        "open_continuity": True,
    },
    "eu_index": {
        "daily_open": "17:00",
        "tz": "America/New_York",
        "weekly_anchor_day": 6,
        "open_continuity": True,
    },
    "uk_index": {
        "daily_open": "17:00",
        "tz": "America/New_York",
        "weekly_anchor_day": 6,
        "open_continuity": True,
    },
    "commodity": {
        "daily_open": "17:00",
        "tz": "America/New_York",
        "weekly_anchor_day": 6,
        "h4_anchor": "17:00",
        "h4_timezone": "America/New_York",
        "open_continuity": True,
    },
    "metal": {
        "daily_open": "17:00",
        "tz": "America/New_York",
        "weekly_anchor_day": 6,
        "h4_anchor": "17:00",
        "h4_timezone": "America/New_York",
        "open_continuity": True,
    },
    "metal_ny18": {
        "daily_open": "17:00",
        "tz": "America/New_York",
        "weekly_anchor_day": 6,
        "h4_anchor": "18:00",
        "h4_timezone": "America/New_York",
        "open_continuity": True,
    },
    "crypto_ny17": {
        "daily_open": "17:00",
        "tz": "America/New_York",
        "weekly_anchor_day": 6,
        "h4_anchor": "17:00",
        "h4_timezone": "America/New_York",
        "open_continuity": False,
    },
    "oil": {
        "daily_open": "17:00",
        "tz": "America/New_York",
        "weekly_anchor_day": 6,
        "h4_anchor": "00:00",
        "h4_timezone": "UTC",
        "h4_grid": "usoil",
        "open_continuity": True,
    },
}


def get_profile(alignment: str | dict[str, Any]) -> dict[str, Any]:
    """Return the alignment profile dict for the given asset class."""
    if isinstance(alignment, dict):
        prof = dict(alignment)
        prof.setdefault("name", "custom")
        return prof
    try:
        prof = dict(_PROFILES[alignment])
        prof["name"] = alignment
        return prof
    except KeyError:
        raise ValueError(
            f"Unknown alignment profile '{alignment}'. "
            f"Valid options: {list(_PROFILES)}"
        )


def _daily_open_utc(date: pd.Timestamp, profile: dict[str, Any]) -> pd.Timestamp:
    """
    Return the UTC time of the daily open for a given calendar date,
    respecting DST in the profile timezone.

    Example: "17:00 America/New_York" on 2026-10-20 (EDT, UTC-4) → 21:00 UTC
             "17:00 America/New_York" on 2026-11-15 (EST, UTC-5) → 22:00 UTC
    """
    tz = pytz.timezone(profile["tz"])
    h, m = (int(x) for x in profile["daily_open"].split(":"))
    local = tz.localize(date.replace(hour=h, minute=m, second=0, microsecond=0,
                                     tzinfo=None))
    return pd.Timestamp(local).tz_convert("UTC")


def resample(
    h1_df: pd.DataFrame,
    timeframe: str,
    alignment: str,
    h4_anchor: Optional[str] = None,
    h4_timezone: Optional[str] = None,
) -> pd.DataFrame:
    """
    Resample a 1-hour candle DataFrame into H4, D (daily) or W (weekly) bars
    using session-aligned boundaries.
    """
    if h1_df.empty:
        log.warning("resample called with empty H1 DataFrame.")
        return h1_df.copy()

    tf = timeframe.upper()
    profile = get_profile(alignment)

    if h4_anchor:
        profile["h4_anchor"] = h4_anchor
    if h4_timezone:
        profile["h4_timezone"] = h4_timezone

    if tf == "H4":
        out = _resample_h4(h1_df, profile)
    elif tf == "D":
        out = _resample_daily(h1_df, profile)
    elif tf == "W":
        out = _resample_weekly(h1_df, profile)
    else:
        raise ValueError(f"Unsupported resampling target '{timeframe}'. Use H4, D, or W.")

    if profile.get("open_continuity", False):
        out = apply_open_continuity(out)

    return out


def apply_open_continuity(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply TradingView-style open continuity:
      open(i) = close(i-1) for all i > 0, including across weekends.
      high(i) = max(high(i), open(i))
      low(i)  = min(low(i), open(i))
    """
    if len(df) < 2:
        return df.copy()
    out = df.copy()
    new_open = out["close"].shift(1)
    new_open.iloc[0] = out["open"].iloc[0]
    out["open"] = new_open
    out["high"] = out[["high", "open"]].max(axis=1)
    out["low"] = out[["low", "open"]].min(axis=1)
    return out


# ---------------------------------------------------------------------------
# H4 resampling
# ---------------------------------------------------------------------------

def _resample_h4(h1_df: pd.DataFrame, profile: dict) -> pd.DataFrame:
    """
    Group H1 bars into 4-hour bars anchored to the specified H4 anchor.
    - Forex / Indices: anchored at 17:00 New York (17, 21, 01, 05, 09, 13 NY).
    - Metals (XAUUSD, XAGUSD): anchored at 18:00 New York (14:00 and 18:00 UTC candles).
    - USOIL: anchored at 00:00 UTC (00, 04, 08, 12, 16, 20 UTC) with partial bars
      on Friday (20:00 to 21:00 UTC) and Sunday (22:00 to 00:00 UTC).
    """
    df = h1_df.copy().sort_index()

    # Check for USOIL special session edges (fixed 00/04/08/12/16/20 UTC grid, NY DST edges)
    if profile.get("h4_grid") == "usoil" or profile.get("name") in ("oil", "usoil"):
        tz_ny = pytz.timezone("America/New_York")

        def usoil_h4_open(ts: pd.Timestamp) -> pd.Timestamp:
            ts_ny = ts.tz_convert(tz_ny)
            # Sunday reopen: 18:00 New York (22:00 UTC in summer, 23:00 UTC in winter)
            if ts_ny.dayofweek == 6 and ts_ny.hour >= 18:
                reopen_local = ts_ny.replace(hour=18, minute=0, second=0, microsecond=0)
                reopen_utc = reopen_local.tz_convert("UTC")
                next_day_utc = reopen_utc.floor("D") + pd.Timedelta(days=1)
                if ts < next_day_utc:
                    return reopen_utc
            # Otherwise standard 4-hour slots in UTC: 00, 04, 08, 12, 16, 20
            # On Friday in winter (EST), 20:00 and 21:00 UTC both map to 20:00 (closes 22:00 UTC = 17:00 NY)
            slot_h = (ts.hour // 4) * 4
            return ts.floor("D") + pd.Timedelta(hours=slot_h)

        df_local = df.copy()
        df_local["bar_open_utc"] = [usoil_h4_open(ts) for ts in df_local.index]
        return _aggregate(df_local, "bar_open_utc", "H4", profile.get("name", "oil"))

    h4_tz_str = profile.get("h4_timezone", profile.get("tz", "America/New_York"))
    h4_anchor_str = profile.get("h4_anchor", profile.get("daily_open", "17:00"))

    tz = pytz.timezone(h4_tz_str)
    h_open, m_open = (int(x) for x in h4_anchor_str.split(":"))

    # Convert index to local time so we can compute session-day boundaries
    df_local = df.copy()
    df_local.index = df.index.tz_convert(tz)

    # Session day: the calendar date of the H4 anchor this hour belongs to.
    def session_day(ts: pd.Timestamp) -> pd.Timestamp:
        shifted = ts - pd.Timedelta(hours=h_open, minutes=m_open)
        return shifted.normalize()   # floor to calendar day

    df_local["session_day"] = [session_day(ts) for ts in df_local.index]

    # H4 slot within the session day (0=first, 1=second, ...)
    def h4_slot(ts: pd.Timestamp, sd: pd.Timestamp) -> int:
        minutes_since_open = int(
            (ts - (sd + pd.Timedelta(hours=h_open, minutes=m_open))).total_seconds() / 60
        )
        return minutes_since_open // 240   # 240 min = 4 hours

    df_local["h4_slot"] = [
        h4_slot(ts, sd)
        for ts, sd in zip(df_local.index, df_local["session_day"])
    ]

    # The open_time of the H4 bar (in local time, then convert to UTC)
    df_local["bar_open_local"] = (
        df_local["session_day"]
        + pd.to_timedelta([h_open * 60 + m_open + s * 240 for s in df_local["h4_slot"]], unit="min")
    )
    # Localize and convert to UTC
    bar_opens_utc = [
        tz.localize(t.to_pydatetime().replace(tzinfo=None)).astimezone(pytz.utc)
        for t in df_local["bar_open_local"]
    ]
    df_local["bar_open_utc"] = pd.DatetimeIndex(bar_opens_utc, tz="UTC")

    return _aggregate(df_local, "bar_open_utc", "H4", profile.get("name", "forex"))


# ---------------------------------------------------------------------------
# Daily resampling
# ---------------------------------------------------------------------------

def _resample_daily(h1_df: pd.DataFrame, profile: dict) -> pd.DataFrame:
    """Group H1 bars into session-aligned daily bars."""
    df = h1_df.copy().sort_index()
    tz = pytz.timezone(profile["tz"])
    h_open, m_open = (int(x) for x in profile["daily_open"].split(":"))

    df_local = df.copy()
    df_local.index = df.index.tz_convert(tz)

    def session_day_open_utc(ts: pd.Timestamp) -> pd.Timestamp:
        shifted = ts - pd.Timedelta(hours=h_open, minutes=m_open)
        sd = shifted.normalize()
        local_open = tz.localize(
            sd.to_pydatetime().replace(hour=h_open, minute=m_open, second=0, microsecond=0, tzinfo=None)
        )
        return pd.Timestamp(local_open).tz_convert("UTC")

    df_local["bar_open_utc"] = [session_day_open_utc(ts) for ts in df_local.index]
    return _aggregate(df_local, "bar_open_utc", "D", profile.get("name", "forex"))


# ---------------------------------------------------------------------------
# Weekly resampling
# ---------------------------------------------------------------------------

def _resample_weekly(h1_df: pd.DataFrame, profile: dict) -> pd.DataFrame:
    """Group H1 bars into session-aligned weekly bars."""
    df = h1_df.copy().sort_index()
    tz = pytz.timezone(profile["tz"])
    h_open, m_open = (int(x) for x in profile["daily_open"].split(":"))
    anchor_weekday = profile["weekly_anchor_day"]   # 0=Mon, 6=Sun

    df_local = df.copy()
    df_local.index = df.index.tz_convert(tz)

    def week_open_utc(ts: pd.Timestamp) -> pd.Timestamp:
        # Find the most recent anchor_weekday at h_open:m_open <= ts
        shifted = ts - pd.Timedelta(hours=h_open, minutes=m_open)
        days_since_anchor = (shifted.weekday() - anchor_weekday) % 7
        anchor_date = (shifted - pd.Timedelta(days=days_since_anchor)).normalize()
        local_open = tz.localize(
            anchor_date.to_pydatetime().replace(hour=h_open, minute=m_open, second=0, microsecond=0, tzinfo=None)
        )
        return pd.Timestamp(local_open).tz_convert("UTC")

    df_local["bar_open_utc"] = [week_open_utc(ts) for ts in df_local.index]
    return _aggregate(df_local, "bar_open_utc", "W", profile.get("name", "forex"))


# ---------------------------------------------------------------------------
# Shared aggregation helper
# ---------------------------------------------------------------------------

def _aggregate(df_local: pd.DataFrame, group_col: str, timeframe: str = "H4", alignment: str = "forex") -> pd.DataFrame:
    """
    Group H1 rows by `group_col` (UTC Timestamp) and compute OHLCV.
    Returns a DataFrame indexed by the group open_time (UTC).
    Still-forming bars whose session close time is in the future are dropped.
    """
    agg = (
        df_local.groupby(group_col, sort=True)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
    )
    agg.index.name = "open_time"

    # Drop any bar that has not yet reached its nominal closing time
    if len(agg) > 0:
        now_utc = pd.Timestamp.now("UTC")
        last_open = agg.index[-1]
        try:
            close_t = candle_close_time(last_open, timeframe, alignment)
            if close_t > now_utc:
                agg = agg.iloc[:-1]
        except Exception:
            # Fallback if timeframe unknown
            if len(agg) > 1:
                dur = agg.index[-1] - agg.index[-2]
            else:
                dur = pd.Timedelta(hours=1)
            if last_open + dur > now_utc:
                agg = agg.iloc[:-1]

    return agg


def candle_close_time(
    open_time: pd.Timestamp,
    timeframe: str,
    alignment: str = "forex",
) -> pd.Timestamp:
    """
    Return the exact UTC nominal closing time of a candle based on its timeframe and session alignment.

    Rules:
      - Weekly (forex, commodity, index): opens Sunday 17:00 NY, trading closes Friday 17:00 NY.
        In EDT (UTC-4): Sunday 21:00 UTC -> closes Friday 21:00 UTC (120 trading hours).
        In EST (UTC-5): Sunday 22:00 UTC -> closes Friday 22:00 UTC (120 trading hours).
      - Weekly (crypto): opens Monday 00:00 UTC, closes Sunday 24:00 UTC (Monday 00:00 UTC, 168 hours).
      - Daily (forex, commodity, index): opens 17:00 NY, closes next day 17:00 NY (24 hours).
      - Daily (crypto): opens 00:00 UTC, closes 24:00 UTC (24 hours).
      - H4: closes open_time + 4 hours.
      - H1: closes open_time + 1 hour.
    """
    tf = timeframe.upper()
    profile = get_profile(alignment)
    tz = pytz.timezone(profile["tz"])

    if tf == "W":
        if alignment == "crypto":
            return open_time + pd.Timedelta(days=7)
        else:
            local_open = open_time.tz_convert(tz)
            local_close = local_open + pd.Timedelta(days=5)
            return pd.Timestamp(local_close).tz_convert("UTC")
    elif tf == "D":
        if alignment == "crypto":
            return open_time + pd.Timedelta(days=1)
        else:
            local_open = open_time.tz_convert(tz)
            local_close = local_open + pd.Timedelta(days=1)
            return pd.Timestamp(local_close).tz_convert("UTC")
    elif tf == "H4":
        return open_time + pd.Timedelta(hours=4)
    elif tf == "H1":
        return open_time + pd.Timedelta(hours=1)
    else:
        raise ValueError(f"Unsupported timeframe '{timeframe}'")


def drop_forming(
    df: pd.DataFrame,
    timeframe_or_duration: str | pd.Timedelta = "H4",
    alignment: str = "forex",
    as_of: Optional[pd.Timestamp | str] = None,
) -> pd.DataFrame:
    """
    Remove any candle whose nominal session close time is after `as_of` (or now UTC).
    Guarantees that a candle is NEVER used before it has formally closed.
    """
    if df.empty:
        return df.copy()

    cutoff = pd.to_datetime(as_of, utc=True) if as_of else pd.Timestamp.now("UTC")

    if isinstance(timeframe_or_duration, pd.Timedelta):
        return df[df.index + timeframe_or_duration <= cutoff].copy()

    tf = timeframe_or_duration.upper()
    valid_indices = [
        ts for ts in df.index if candle_close_time(ts, tf, alignment) <= cutoff
    ]
    return df.loc[valid_indices].copy()

