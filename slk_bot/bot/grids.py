"""
slk_bot/bot/grids.py
-------------------
Grid close time calculation and daylight saving time (DST) handling.
Calculates the latest closed candle timestamps across the four strategy grids:
1. Forex & Indices: 17:00 New York anchor (H4 + Daily). Closed Fri 17:00 NY to Sun 17:00 NY.
2. Metals (Gold/Silver): 18:00 New York anchor for H4, 17:00 NY for Daily.
3. Crypto: 00:00 UTC anchor (H4 + Daily), 24/7.
4. USOIL: 00:00 UTC grid (H4).

No UTC close times are hardcoded; New York local time is converted dynamically
via zoneinfo so DST transitions (Spring forward / Fall back) are handled automatically.
"""

from __future__ import annotations

import datetime
from typing import Optional
import zoneinfo

NY_TZ = zoneinfo.ZoneInfo("America/New_York")
UTC_TZ = datetime.timezone.utc


def get_latest_forex_h4_close(dt_utc: datetime.datetime) -> datetime.datetime:
    """
    Forex / Indices H4 candles on 17:00 NY anchor.
    Open hours in NY: [1, 5, 9, 13, 17, 21].
    Close hours in NY: [5, 9, 13, 17, 21, 1 (next day)].
    Forex session: Opens Sunday 17:00 NY, Closes Friday 17:00 NY.
    """
    dt_ny = dt_utc.astimezone(NY_TZ)
    weekday = dt_ny.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun

    # Weekend handling: Friday 17:00 NY through Sunday 17:00 NY
    if weekday == 5 or (weekday == 6 and dt_ny.hour < 17) or (weekday == 4 and dt_ny.hour >= 17):
        days_back = 1 if weekday == 5 else (2 if weekday == 6 else 0)
        fri_date = dt_ny.date() - datetime.timedelta(days=days_back)
        close_ny = datetime.datetime(fri_date.year, fri_date.month, fri_date.day, 17, 0, 0, tzinfo=NY_TZ)
        return close_ny.astimezone(UTC_TZ)

    # Sunday after 17:00 NY: first candle is 17:00-21:00 NY
    if weekday == 6 and dt_ny.hour >= 17:
        if dt_ny.hour < 21:
            # First candle hasn't closed yet; latest closed is Friday 17:00 NY
            fri_date = dt_ny.date() - datetime.timedelta(days=2)
            close_ny = datetime.datetime(fri_date.year, fri_date.month, fri_date.day, 17, 0, 0, tzinfo=NY_TZ)
            return close_ny.astimezone(UTC_TZ)
        else:
            # 17:00-21:00 NY candle closed
            close_ny = datetime.datetime(dt_ny.year, dt_ny.month, dt_ny.day, 21, 0, 0, tzinfo=NY_TZ)
            return close_ny.astimezone(UTC_TZ)

    # Weekday candidates (Mon-Fri)
    candidates: list[datetime.datetime] = []
    for d in [0, 1]:
        day = dt_ny.date() - datetime.timedelta(days=d)
        for h in [1, 5, 9, 13, 17, 21]:
            close_dt = datetime.datetime(day.year, day.month, day.day, h, 0, 0, tzinfo=NY_TZ) + datetime.timedelta(hours=4)
            if close_dt <= dt_ny:
                candidates.append(close_dt)

    latest_ny = max(candidates)
    return latest_ny.astimezone(UTC_TZ)


def get_latest_metal_h4_close(dt_utc: datetime.datetime) -> datetime.datetime:
    """
    Metals H4 candles on 18:00 NY anchor (XAUUSD, XAGUSD H4).
    Open hours in NY: [2, 6, 10, 14, 18, 22].
    Close hours in NY: [6, 10, 14, 18, 22, 2 (next day)].
    Metals session closes Friday 17:00 NY and opens Sunday 18:00 NY.
    """
    dt_ny = dt_utc.astimezone(NY_TZ)
    weekday = dt_ny.weekday()

    # Weekend handling: Friday 17:00 NY through Sunday 18:00 NY
    if weekday == 5 or (weekday == 6 and dt_ny.hour < 18) or (weekday == 4 and dt_ny.hour >= 17):
        days_back = 1 if weekday == 5 else (2 if weekday == 6 else 0)
        fri_date = dt_ny.date() - datetime.timedelta(days=days_back)
        close_ny = datetime.datetime(fri_date.year, fri_date.month, fri_date.day, 17, 0, 0, tzinfo=NY_TZ)
        return close_ny.astimezone(UTC_TZ)

    # Sunday after 18:00 NY: first candle is 18:00-22:00 NY
    if weekday == 6 and dt_ny.hour >= 18:
        if dt_ny.hour < 22:
            fri_date = dt_ny.date() - datetime.timedelta(days=2)
            close_ny = datetime.datetime(fri_date.year, fri_date.month, fri_date.day, 17, 0, 0, tzinfo=NY_TZ)
            return close_ny.astimezone(UTC_TZ)
        else:
            close_ny = datetime.datetime(dt_ny.year, dt_ny.month, dt_ny.day, 22, 0, 0, tzinfo=NY_TZ)
            return close_ny.astimezone(UTC_TZ)

    candidates: list[datetime.datetime] = []
    for d in [0, 1]:
        day = dt_ny.date() - datetime.timedelta(days=d)
        for h in [2, 6, 10, 14, 18, 22]:
            close_dt = datetime.datetime(day.year, day.month, day.day, h, 0, 0, tzinfo=NY_TZ) + datetime.timedelta(hours=4)
            if close_dt <= dt_ny:
                candidates.append(close_dt)

    latest_ny = max(candidates)
    return latest_ny.astimezone(UTC_TZ)


def get_latest_forex_daily_close(dt_utc: datetime.datetime) -> datetime.datetime:
    """
    Forex / Indices Daily candle closes at 17:00 NY Mon-Fri.
    """
    dt_ny = dt_utc.astimezone(NY_TZ)
    weekday = dt_ny.weekday()

    # If weekend (Sat, Sun before 17:00, or Fri >= 17:00), latest is Friday 17:00 NY
    if weekday == 5 or (weekday == 6 and dt_ny.hour < 17) or (weekday == 4 and dt_ny.hour >= 17):
        days_back = 1 if weekday == 5 else (2 if weekday == 6 else 0)
        fri_date = dt_ny.date() - datetime.timedelta(days=days_back)
        close_ny = datetime.datetime(fri_date.year, fri_date.month, fri_date.day, 17, 0, 0, tzinfo=NY_TZ)
        return close_ny.astimezone(UTC_TZ)

    # Weekdays: if before 17:00 NY, previous day's 17:00 NY closed; if >= 17:00 NY, today's 17:00 NY closed
    if dt_ny.hour >= 17:
        close_ny = datetime.datetime(dt_ny.year, dt_ny.month, dt_ny.day, 17, 0, 0, tzinfo=NY_TZ)
    else:
        # If Monday before 17:00 NY, previous close was Friday 17:00 NY (3 days ago)
        days_back = 3 if weekday == 0 else 1
        prev_date = dt_ny.date() - datetime.timedelta(days=days_back)
        close_ny = datetime.datetime(prev_date.year, prev_date.month, prev_date.day, 17, 0, 0, tzinfo=NY_TZ)

    return close_ny.astimezone(UTC_TZ)


def get_latest_crypto_h4_close(dt_utc: datetime.datetime) -> datetime.datetime:
    """
    Crypto H4 candles on 00:00 UTC anchor (24/7).
    Open hours in UTC: [0, 4, 8, 12, 16, 20].
    """
    dt_u = dt_utc.astimezone(UTC_TZ)
    closed_hour = (dt_u.hour // 4) * 4
    close_dt = dt_u.replace(hour=closed_hour, minute=0, second=0, microsecond=0)
    return close_dt


def get_latest_crypto_daily_close(dt_utc: datetime.datetime) -> datetime.datetime:
    """
    Crypto Daily candle on 00:00 UTC anchor (24/7).
    """
    dt_u = dt_utc.astimezone(UTC_TZ)
    close_dt = dt_u.replace(hour=0, minute=0, second=0, microsecond=0)
    return close_dt


def get_all_latest_closed_candle_times(dt_utc: Optional[datetime.datetime] = None) -> dict[str, str]:
    """
    Returns ISO 8601 strings in UTC for the latest closed candles across all strategy grids.
    """
    if dt_utc is None:
        dt_utc = datetime.datetime.now(UTC_TZ)

    return {
        "forex_ny17_h4": get_latest_forex_h4_close(dt_utc).isoformat(),
        "metal_ny18_h4": get_latest_metal_h4_close(dt_utc).isoformat(),
        "forex_ny17_d": get_latest_forex_daily_close(dt_utc).isoformat(),
        "crypto_utc_h4": get_latest_crypto_h4_close(dt_utc).isoformat(),
        "crypto_utc_d": get_latest_crypto_daily_close(dt_utc).isoformat(),
    }


def evaluate_new_candles_closed(
    current_times: dict[str, str], stored_times: dict[str, str]
) -> tuple[bool, list[str]]:
    """
    Compares current calculated close times against stored close times.
    Returns (True, [list of grids with new closed candle]) or (False, []).
    """
    if not stored_times:
        # First start: treat as new
        return True, list(current_times.keys())

    new_grids: list[str] = []
    for grid_name, curr_time_str in current_times.items():
        prev_time_str = stored_times.get(grid_name)
        if prev_time_str is None or curr_time_str > prev_time_str:
            new_grids.append(grid_name)

    return (len(new_grids) > 0), new_grids
