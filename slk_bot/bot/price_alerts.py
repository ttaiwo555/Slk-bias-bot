"""
slk_bot/bot/price_alerts.py
---------------------------
Price alert monitoring and management engine (Section 7b).
- 1-minute touch checks against high/low candles.
- 48-hour expiration skipping market-closed hours for forex, indices, and metals.
- Crypto runs continuously (24/7).
- Persistent in SQLite via CandleCache.
"""

from __future__ import annotations

import uuid
from typing import Optional, Any
from pathlib import Path
import pandas as pd
import pytz

from slk_bot.storage.cache import CandleCache
from slk_bot.bot.formatter import format_price, get_display_scale

# 48 active hours in seconds
EXPIRY_ACTIVE_SECONDS = 48 * 3600


def is_market_open(timestamp: pd.Timestamp, asset_class: str) -> bool:
    """
    Check if market is currently open for the given asset class.
    - Crypto: always open (24/7).
    - Forex / Metals / Indices: closed from Friday 17:00 NY to Sunday 17:00 NY.
    """
    if asset_class == "crypto":
        return True

    # Convert to America/New_York
    ny_tz = pytz.timezone("America/New_York")
    ny_dt = timestamp.tz_convert(ny_tz)

    weekday = ny_dt.weekday()  # Monday=0 ... Sunday=6
    hour = ny_dt.hour
    minute = ny_dt.minute

    # Friday after 17:00 NY -> closed
    if weekday == 4 and (hour > 17 or (hour == 17 and minute > 0)):
        return False
    # All of Saturday -> closed
    if weekday == 5:
        return False
    # Sunday before 17:00 NY -> closed
    if weekday == 6 and hour < 17:
        return False

    return True


def calculate_active_elapsed_seconds(
    start_time: pd.Timestamp, end_time: pd.Timestamp, asset_class: str
) -> float:
    """
    Calculate the number of active market seconds elapsed between start_time and end_time.
    For crypto: straight difference in seconds.
    For non-crypto: skips Friday 17:00 NY to Sunday 17:00 NY.
    """
    if asset_class == "crypto":
        return max(0.0, (end_time - start_time).total_seconds())

    # Fast calculation by 1-hour increments or direct weekend subtraction
    cur = start_time
    total_active_seconds = 0.0
    step = pd.Timedelta(hours=1)

    while cur < end_time:
        next_step = min(cur + step, end_time)
        mid_time = cur + (next_step - cur) / 2
        if is_market_open(mid_time, asset_class):
            total_active_seconds += (next_step - cur).total_seconds()
        cur = next_step

    return total_active_seconds


class PriceAlertManager:
    """Manages creation, evaluation, touch detection, and expiry of price alerts."""

    def __init__(self, cache: Optional[CandleCache] = None) -> None:
        self.cache = cache or CandleCache()

    def create_alert(
        self,
        symbol: str,
        target_price: float,
        current_price: float,
        asset_class: str = "forex",
        created_at: Optional[pd.Timestamp] = None,
    ) -> dict[str, Any]:
        """
        Create a new pending price alert.
        Direction:
          - 'ABOVE' if target_price > current_price (watching for upward cross/touch)
          - 'BELOW' if target_price < current_price (watching for downward cross/touch)
        """
        if created_at is None:
            created_at = pd.Timestamp.now("UTC")

        direction = "ABOVE" if target_price >= current_price else "BELOW"
        alert_id = str(uuid.uuid4())[:8]

        # Stored initial expiry timestamp string (48h active)
        # For display, approximate 48h clock time if open, or adjusted
        expires_approx = created_at + pd.Timedelta(hours=48)
        if asset_class != "crypto":
            # If weekend falls within 48h, add 48h weekend
            expires_approx = created_at + pd.Timedelta(hours=96)

        alert_data = {
            "alert_id": alert_id,
            "symbol": symbol,
            "target_price": target_price,
            "direction": direction,
            "created_at": created_at.isoformat(),
            "expires_at": expires_approx.isoformat(),
            "status": "active",
        }

        self.cache.save_price_alert(
            alert_id=alert_id,
            symbol=symbol,
            target_price=target_price,
            direction=direction,
            created_at=created_at.isoformat(),
            expires_at=expires_approx.isoformat(),
            status="active",
        )

        return alert_data

    def check_price_alerts(
        self,
        symbol: str,
        m1_high: float,
        m1_low: float,
        current_time: pd.Timestamp,
        asset_class: str = "forex",
    ) -> list[dict[str, Any]]:
        """
        Check if any active alerts for this symbol are touched or expired by this 1-minute candle.
        Returns a list of event dicts: {"event": "TRIGGERED" | "EXPIRED", "alert": alert_dict, "touch_price": ...}
        """
        active_alerts = self.cache.get_active_price_alerts(symbol=symbol)
        events = []

        for alert in active_alerts:
            alert_id = alert["alert_id"]
            target = float(alert["target_price"])
            direction = alert["direction"]
            created_at = pd.Timestamp(alert["created_at"])

            # 1. Check Expiry (48 active market hours)
            elapsed_sec = calculate_active_elapsed_seconds(created_at, current_time, asset_class)
            if elapsed_sec >= EXPIRY_ACTIVE_SECONDS:
                self.cache.update_price_alert_status(alert_id, "expired")
                events.append({
                    "event": "EXPIRED",
                    "alert": alert,
                    "time": current_time,
                })
                continue

            # 2. Check Touch (m1_low <= target <= m1_high)
            # A touch occurs if target price falls inside the high-low range of the minute
            if m1_low <= target <= m1_high:
                self.cache.update_price_alert_status(alert_id, "triggered")
                events.append({
                    "event": "TRIGGERED",
                    "alert": alert,
                    "touch_price": target,
                    "time": current_time,
                })

        return events

    def cancel_alert(self, alert_id: str) -> bool:
        """Cancel a pending price alert."""
        return self.cache.update_price_alert_status(alert_id, "cancelled")
