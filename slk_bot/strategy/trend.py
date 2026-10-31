"""
slk_bot/strategy/trend.py
-------------------------
Implements market structure trend detection:
Direction set by the latest confirmed market structure break on that timeframe.
- The most recently broken A-shape (peak) sets the trend to BULLISH.
- The most recently broken V-shape (trough) sets the trend to BEARISH.
- Each key level (swing high/low) is broken once upon candle close beyond its level.
"""

from __future__ import annotations
from typing import Optional, Dict, Tuple, List
import pandas as pd

from slk_bot.strategy.models import TriggerDirection, LevelKind
from slk_bot.strategy.levels import detect_formation
from slk_bot.storage.cache import CandleCache

_STRUCTURE_CACHE: Dict[Tuple[str, str], List[Tuple[pd.Timestamp, TriggerDirection]]] = {}


def compute_structure_breaks(df: pd.DataFrame) -> List[Tuple[pd.Timestamp, TriggerDirection]]:
    """
    Detect all A-shape and V-shape levels and record the first candle close
    that breaks each level.
    """
    if df.empty or len(df) < 2:
        return []

    opens = df["open"].values
    closes = df["close"].values
    times = df.index
    n = len(df)

    # Detect levels: [conf_idx, kind, price, broken_flag]
    levels: list[list] = []
    for k in range(1, n):
        formation = detect_formation(opens[k - 1], closes[k - 1], opens[k], closes[k], 0.0)
        if formation:
            kind, role, side = formation
            if kind in (LevelKind.A_SHAPE, LevelKind.V_SHAPE):
                levels.append([k, kind, float(closes[k - 1]), False])

    breaks: List[Tuple[pd.Timestamp, TriggerDirection]] = []
    for k in range(1, n):
        c_close = closes[k]
        t = times[k]
        for lvl in levels:
            conf_k, kind, price, broken = lvl
            if conf_k < k and not broken:
                if kind == LevelKind.A_SHAPE and c_close > price:
                    breaks.append((t, TriggerDirection.BULLISH))
                    lvl[3] = True
                elif kind == LevelKind.V_SHAPE and c_close < price:
                    breaks.append((t, TriggerDirection.BEARISH))
                    lvl[3] = True

    return breaks


def get_trend(
    symbol: str,
    timeframe: str,
    asof: pd.Timestamp,
    cache: Optional[CandleCache] = None,
) -> Optional[TriggerDirection]:
    """
    Get the market structure trend for `symbol` on `timeframe` as of timestamp `asof`.
    Returns TriggerDirection.BULLISH, TriggerDirection.BEARISH, or None.
    """
    key = (symbol, timeframe)
    if key not in _STRUCTURE_CACHE:
        c = cache or CandleCache()
        df = c.load_candles(symbol, timeframe)
        _STRUCTURE_CACHE[key] = compute_structure_breaks(df)

    breaks = _STRUCTURE_CACHE[key]
    prior_breaks = [b for b in breaks if b[0] <= asof]
    if prior_breaks:
        return prior_breaks[-1][1]
    return None


def is_trend_aligned(
    symbol: str,
    htf: str,
    direction: TriggerDirection,
    asof: pd.Timestamp,
    cache: Optional[CandleCache] = None,
) -> bool:
    """
    Check if the proposed setup direction aligns with the HTF market structure trend.
    """
    trend = get_trend(symbol, htf, asof, cache)
    if trend is None:
        return False
    return trend == direction


def clear_trend_cache(symbol: Optional[str] = None, timeframe: Optional[str] = None) -> None:
    """Clear memory cache when new candles are ingested."""
    global _STRUCTURE_CACHE
    if symbol and timeframe:
        _STRUCTURE_CACHE.pop((symbol, timeframe), None)
    elif symbol:
        keys_to_remove = [k for k in _STRUCTURE_CACHE if k[0] == symbol]
        for k in keys_to_remove:
            _STRUCTURE_CACHE.pop(k, None)
    else:
        _STRUCTURE_CACHE.clear()
