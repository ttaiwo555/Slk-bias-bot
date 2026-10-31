"""
slk_bot/tests/test_cache.py
---------------------------
Unit tests for the local SQLite candle cache.
"""

import pandas as pd
import pytest

from slk_bot.storage.cache import CandleCache


def test_candle_cache_save_and_load(tmp_path):
    db_file = tmp_path / "test_candles.db"
    cache = CandleCache(db_file)

    times = pd.date_range("2026-10-01", periods=5, freq="D", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [1.1, 1.2, 1.3, 1.4, 1.5],
            "high": [1.15, 1.25, 1.35, 1.45, 1.55],
            "low": [1.05, 1.15, 1.25, 1.35, 1.45],
            "close": [1.12, 1.22, 1.32, 1.42, 1.52],
            "volume": [10, 20, 30, 40, 50],
        },
        index=times,
    )
    df.index.name = "open_time"

    saved = cache.save_candles("EURUSD", "D", df)
    assert saved == 5

    # Load all
    loaded = cache.load_candles("EURUSD", "D")
    assert len(loaded) == 5
    assert loaded.iloc[0]["open"] == 1.1
    assert loaded.iloc[-1]["close"] == 1.52

    # Load with limit
    loaded_tail = cache.load_candles("EURUSD", "D", count=3)
    assert len(loaded_tail) == 3
    assert loaded_tail.iloc[0]["open"] == 1.3

    # Check stats
    cnt, oldest, newest = cache.get_stats("EURUSD", "D")
    assert cnt == 5
    assert oldest == times[0]
    assert newest == times[-1]
