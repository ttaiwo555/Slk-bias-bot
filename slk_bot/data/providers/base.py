"""
slk_bot/data/providers/base.py
------------------------------
Abstract base class that every data provider adapter must implement.
This is the "swappable plug" described in Section 4 of the brief.

Plain-language explanation
--------------------------
Think of this like a power socket standard. Every adapter (OANDA, Binance,
Bybit, etc.) must have the same shape of plug:
  - get_candles()  → fetch historical candles
  - list_symbols() → tell us what instruments this source carries

The strategy code never talks to any data source directly; it only speaks
to whichever adapter is plugged in. That means we can swap OANDA for
something else without touching a single line of strategy code.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Optional
import pandas as pd


@dataclass
class Candle:
    """One OHLCV candle with its open-time and close-time (UTC-aware)."""
    open_time: pd.Timestamp   # UTC, candle start
    close_time: pd.Timestamp  # UTC, candle end (exclusive: next candle starts here)
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None  # not always available


class DataProvider(abc.ABC):
    """
    Abstract provider interface.

    Every concrete adapter must implement these two methods.
    All returned DataFrames use UTC timestamps and columns:
        open_time (DatetimeTZDtype UTC, index)
        open, high, low, close  (float64)
        volume                  (float64, nullable)
    Candle that is still forming (the last open candle) must NOT be included.
    """

    @abc.abstractmethod
    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[pd.Timestamp] = None,
        count: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Fetch closed OHLCV candles.

        Parameters
        ----------
        symbol:    provider-specific symbol string (e.g. "EUR_USD" for OANDA)
        timeframe: one of "H1", "H4", "D", "W"
        since:     only return candles whose open_time >= since (UTC).
                   If None, return as many as count allows.
        count:     maximum number of candles to return (provider page size).
                   If None, return all available within lookback limits.

        Returns
        -------
        DataFrame indexed by open_time (UTC), columns: open, high, low, close,
        volume (NaN if unavailable). Sorted ascending by open_time.
        Only CLOSED candles; the forming candle is dropped by the provider.
        """

    @abc.abstractmethod
    def list_symbols(self) -> list[str]:
        """
        Return the list of all tradable symbols this provider can serve.
        Used during Phase 0 coverage check.
        """
