"""
slk_bot/strategy/levels.py
--------------------------
Detection and state tracking for SLK key levels (Sections 5.1 and 5.2).

Key Rules:
  - Level price = close(i) of the joint candle.
  - Dojis (close == open) never form an A-shape, V-shape, or OCL.
  - A-shape: bullish candle i followed by bearish candle i+1.
  - V-shape: bearish candle i followed by bullish candle i+1.
  - Bullish OCL: two consecutive bullish candles.
  - Bearish OCL: two consecutive bearish candles.
  - Disrespect: counted ONLY when a later candle CLOSES on the opposite side.
    Wicks never count. A close exactly on the price changes nothing.
  - Role changes:
      A-shape: count 0 -> resistance, count 1 -> RBS, count 2+ -> QM
      V-shape: count 0 -> support,    count 1 -> SBR, count 2+ -> QM
      OCL:     count 0 -> initial,    count 1+ -> flipped_OCL
  - Lookback: keep only levels formed inside the lookback window.
"""

from __future__ import annotations

import copy
from typing import Optional
import pandas as pd

from slk_bot.strategy.models import KeyLevel, LevelKind, LevelRole, Side


def candle_direction(
    open_price: float,
    close_price: float,
    doji_body_threshold_pct: float = 0.0,
) -> int:
    """
    Return candle direction:
      1  = Bullish (close > open)
      -1 = Bearish (close < open)
      0  = Doji (close == open, or body < doji_body_threshold_pct of close)
    """
    if doji_body_threshold_pct > 0 and close_price > 0:
        body = abs(close_price - open_price)
        if (body / close_price) * 100 < doji_body_threshold_pct:
            return 0

    if close_price > open_price:
        return 1
    elif close_price < open_price:
        return -1
    else:
        return 0


def determine_role(
    kind: LevelKind,
    disrespect_count: int,
    max_disrespects: Optional[int] = 3,
    ocl_max_disrespects: Optional[int] = 1,
) -> LevelRole:
    """
    Calculate current role based on level kind and disrespect count.

    Rules:
      - Count 0: initial role (resistance / support)
      - Count 1: RBS (A-shape) / SBR (V-shape) / flipped_OCL (OCL)
      - Counts 2 and 3: QM (A/V shapes)
      - Count > max_disrespects (or > ocl_max_disrespects for OCL): RETIRED (if limit is set)
    """
    limit = ocl_max_disrespects if kind in (LevelKind.BULLISH_OCL, LevelKind.BEARISH_OCL) else max_disrespects
    if limit is not None and disrespect_count > limit:
        return LevelRole.RETIRED

    if kind == LevelKind.A_SHAPE:
        if disrespect_count == 0:
            return LevelRole.RESISTANCE
        elif disrespect_count == 1:
            return LevelRole.RBS
        else:
            return LevelRole.QM
    elif kind == LevelKind.V_SHAPE:
        if disrespect_count == 0:
            return LevelRole.SUPPORT
        elif disrespect_count == 1:
            return LevelRole.SBR
        else:
            return LevelRole.QM
    elif kind in (LevelKind.BULLISH_OCL, LevelKind.BEARISH_OCL):
        if disrespect_count == 0:
            return LevelRole.SUPPORT if kind == LevelKind.BULLISH_OCL else LevelRole.RESISTANCE
        else:
            return LevelRole.FLIPPED_OCL
    raise ValueError(f"Unknown LevelKind: {kind}")


def apply_candle_to_level(
    level: KeyLevel,
    candle_close: float,
    candle_high: Optional[float] = None,
    candle_low: Optional[float] = None,
    max_disrespects: Optional[int] = 3,
    ocl_max_disrespects: Optional[int] = 1,
) -> KeyLevel:
    """
    Apply a single candle to a key level (Section 5.2 and Freshness rule).

    Contact rules:
      - Disrespect: candle CLOSES on the opposite side.
        Increments disrespect_count, updates last_side and role,
        sets last_contact = "disrespect", restores fresh = True, and resets rejections_since_fresh = 0.
      - Rejection: candle WICK penetrates beyond the level, but CLOSE stays on original side.
        Sets last_contact = "rejection", sets fresh = False (unfresh), and increments rejections_since_fresh.
      - No contact: wick does not reach beyond level. last_contact and fresh remain unchanged.
    """
    if candle_high is None:
        candle_high = candle_close
    if candle_low is None:
        candle_low = candle_close

    if not hasattr(level, "rejections_since_fresh"):
        level.rejections_since_fresh = 0 if level.fresh else 1

    if level.last_side == Side.BELOW:
        if candle_close > level.price:
            # Disrespect contact: close broke above resistance
            level.disrespect_count += 1
            level.last_side = Side.ABOVE
            level.current_role = determine_role(level.kind, level.disrespect_count, max_disrespects, ocl_max_disrespects)
            level.last_contact = "disrespect"
            level.fresh = True
            level.rejections_since_fresh = 0
        elif candle_high > level.price:
            # Rejection contact: wick tested above, but close stayed below
            level.last_contact = "rejection"
            level.fresh = False
            level.rejections_since_fresh += 1

    elif level.last_side == Side.ABOVE:
        if candle_close < level.price:
            # Disrespect contact: close broke below support
            level.disrespect_count += 1
            level.last_side = Side.BELOW
            level.current_role = determine_role(level.kind, level.disrespect_count, max_disrespects, ocl_max_disrespects)
            level.last_contact = "disrespect"
            level.fresh = True
            level.rejections_since_fresh = 0
        elif candle_low < level.price:
            # Rejection contact: wick tested below, but close stayed above
            level.last_contact = "rejection"
            level.fresh = False
            level.rejections_since_fresh += 1

    return level



def detect_formation(
    open_i: float,
    close_i: float,
    open_next: float,
    close_next: float,
    doji_body_threshold_pct: float = 0.0,
) -> Optional[tuple[LevelKind, LevelRole, Side]]:
    """
    Evaluate two consecutive candles (i and i+1) to see if a key level forms.
    Returns (kind, initial_role, initial_last_side) or None.
    Dojis (close == open, or body < threshold) never form an A-shape, V-shape, or OCL.
    """
    d_i = candle_direction(open_i, close_i, doji_body_threshold_pct)
    d_next = candle_direction(open_next, close_next, doji_body_threshold_pct)

    # Doji never forms any level
    if d_i == 0 or d_next == 0:
        return None

    if d_i == 1 and d_next == -1:
        # A-shape: bullish followed by bearish -> resistance
        return LevelKind.A_SHAPE, LevelRole.RESISTANCE, Side.BELOW

    elif d_i == -1 and d_next == 1:
        # V-shape: bearish followed by bullish -> support
        return LevelKind.V_SHAPE, LevelRole.SUPPORT, Side.ABOVE

    elif d_i == 1 and d_next == 1:
        # Bullish OCL: two consecutive bullish candles -> support
        return LevelKind.BULLISH_OCL, LevelRole.SUPPORT, Side.ABOVE

    elif d_i == -1 and d_next == -1:
        # Bearish OCL: two consecutive bearish candles -> resistance
        return LevelKind.BEARISH_OCL, LevelRole.RESISTANCE, Side.BELOW

    return None


def track_key_levels(
    df: pd.DataFrame,
    timeframe: str,
    symbol: Optional[str] = None,
    lookback: Optional[int] = None,
    max_disrespects: int = 3,
    ocl_max_disrespects: int = 3,
    doji_body_threshold_pct: float = 0.0,
) -> list[KeyLevel]:
    """
    Detect and track all key levels across a DataFrame of closed candles.

    Parameters:
      df: DataFrame indexed by UTC open_time, containing 'open', 'high', 'low', 'close'.
      timeframe: Timeframe identifier ('W', 'D', 'H4').
      symbol: Optional symbol name.
      lookback: Number of recent candles to keep levels for.
      max_disrespects: Max disrespects before role becomes RETIRED (default 3).
      ocl_max_disrespects: Max disrespects before OCL role becomes RETIRED (default 3).
      doji_body_threshold_pct: Body percentage below which candle is treated as doji (default 0).

    Returns:
      List of KeyLevel objects formed within lookback, with their latest states.
    """
    if len(df) < 2:
        return []

    active_levels: list[KeyLevel] = []
    n = len(df)

    opens = df["open"].values
    highs = df["high"].values if "high" in df.columns else closes
    lows = df["low"].values if "low" in df.columns else closes
    closes = df["close"].values
    times = df.index

    # Candle-by-candle state progression
    for k in range(1, n):
        close_k = float(closes[k])
        high_k = float(highs[k])
        low_k = float(lows[k])

        # 1. Update all existing levels formed before candle k
        for level in active_levels:
            apply_candle_to_level(level, candle_close=close_k, candle_high=high_k, candle_low=low_k, max_disrespects=max_disrespects, ocl_max_disrespects=ocl_max_disrespects)

        # 2. Check if candle pair (k-1, k) completes a new formation
        formation = detect_formation(
            open_i=opens[k - 1],
            close_i=closes[k - 1],
            open_next=opens[k],
            close_next=closes[k],
            doji_body_threshold_pct=doji_body_threshold_pct,
        )

        if formation is not None:
            kind, role, side = formation
            level_price = float(closes[k - 1])  # Level price = close(i)
            t_form = times[k - 1]
            t_conf = times[k]

            sym_prefix = f"{symbol}_" if symbol else ""
            level_id = f"{sym_prefix}{timeframe}_{kind.value}_{t_form.strftime('%Y%m%d%H%M')}_{level_price:.5f}"

            new_level = KeyLevel(
                id=level_id,
                timeframe=timeframe,
                kind=kind,
                price=level_price,
                formation_time=t_form,
                formation_confirmed_time=t_conf,
                formation_index=k - 1,
                disrespect_count=0,
                last_side=side,
                current_role=role,
                symbol=symbol,
            )
            active_levels.append(new_level)

    # 3. Filter by lookback window (levels formed inside lookback)
    if lookback is not None and lookback > 0:
        cutoff_index = max(0, n - lookback)
        active_levels = [lvl for lvl in active_levels if lvl.formation_index >= cutoff_index]

    return active_levels
