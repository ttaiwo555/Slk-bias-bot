"""
slk_bot/tests/test_levels.py
----------------------------
Comprehensive unit tests for Sections 5.1 and 5.2 of the SLK brief:
1. Dojis ignored (close == open never forms A, V, or OCL).
2. Level price = close(i).
3. Wick-only break does not count as disrespect (closes only).
4. Close beyond level increments disrespect_count and updates last_side.
5. Close exactly on level price changes nothing.
6. A-shape role changes: count 0 (resistance) -> count 1 (RBS) -> count 2+ (QM).
7. V-shape role changes: count 0 (support) -> count 1 (SBR) -> count 2+ (QM).
8. OCL role flip: count 0 -> count 1+ (flipped_OCL).
9. Lookback window filtering.
"""

import pandas as pd
import pytest

from slk_bot.strategy.models import KeyLevel, LevelKind, LevelRole, Side
from slk_bot.strategy.levels import (
    candle_direction,
    detect_formation,
    apply_candle_to_level,
    determine_role,
    track_key_levels,
)


def _make_df(candles: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Helper to build a candle DataFrame from a list of (open, high, low, close)."""
    times = pd.date_range("2026-10-01", periods=len(candles), freq="4h", tz="UTC")
    df = pd.DataFrame(candles, columns=["open", "high", "low", "close"], index=times)
    df.index.name = "open_time"
    return df


def test_doji_never_forms_level():
    """Verify that a doji as either candle i or candle i+1 never forms any level."""
    # Bullish followed by doji
    assert detect_formation(open_i=1.1000, close_i=1.1050, open_next=1.1050, close_next=1.1050) is None

    # Doji followed by bearish
    assert detect_formation(open_i=1.1000, close_i=1.1000, open_next=1.1000, close_next=1.0950) is None

    # Doji followed by doji
    assert detect_formation(open_i=1.1000, close_i=1.1000, open_next=1.1000, close_next=1.1000) is None

    # Bearish followed by doji
    assert detect_formation(open_i=1.1050, close_i=1.1000, open_next=1.1000, close_next=1.1000) is None


def test_a_shape_formation_and_price():
    """Bullish followed by bearish forms an A-shape with price = close(i)."""
    # Candle 0: Bullish (open 1.1000, close 1.1080)
    # Candle 1: Bearish (open 1.1080, close 1.1020)
    df = _make_df([
        (1.1000, 1.1100, 1.0990, 1.1080),  # Bullish
        (1.1080, 1.1090, 1.1010, 1.1020),  # Bearish
    ])
    levels = track_key_levels(df, timeframe="D")
    assert len(levels) == 1
    lvl = levels[0]
    assert lvl.kind == LevelKind.A_SHAPE
    assert lvl.price == 1.1080             # Exactly close(i)
    assert lvl.current_role == LevelRole.RESISTANCE
    assert lvl.last_side == Side.BELOW
    assert lvl.disrespect_count == 0


def test_v_shape_formation_and_price():
    """Bearish followed by bullish forms a V-shape with price = close(i)."""
    # Candle 0: Bearish (open 1.1080, close 1.1000)
    # Candle 1: Bullish (open 1.1000, close 1.1060)
    df = _make_df([
        (1.1080, 1.1090, 1.0980, 1.1000),  # Bearish
        (1.1000, 1.1070, 1.0990, 1.1060),  # Bullish
    ])
    levels = track_key_levels(df, timeframe="D")
    assert len(levels) == 1
    lvl = levels[0]
    assert lvl.kind == LevelKind.V_SHAPE
    assert lvl.price == 1.1000             # Exactly close(i)
    assert lvl.current_role == LevelRole.SUPPORT
    assert lvl.last_side == Side.ABOVE
    assert lvl.disrespect_count == 0


def test_ocl_formations():
    """Two consecutive bullish form bullish OCL; two bearish form bearish OCL."""
    df_bull = _make_df([
        (1.1000, 1.1060, 1.0990, 1.1050),  # Bullish
        (1.1050, 1.1110, 1.1040, 1.1100),  # Bullish
    ])
    levels_bull = track_key_levels(df_bull, timeframe="D")
    assert len(levels_bull) == 1
    assert levels_bull[0].kind == LevelKind.BULLISH_OCL
    assert levels_bull[0].price == 1.1050
    assert levels_bull[0].current_role == LevelRole.SUPPORT

    df_bear = _make_df([
        (1.1100, 1.1110, 1.1040, 1.1050),  # Bearish
        (1.1050, 1.1060, 1.0990, 1.1000),  # Bearish
    ])
    levels_bear = track_key_levels(df_bear, timeframe="D")
    assert len(levels_bear) == 1
    assert levels_bear[0].kind == LevelKind.BEARISH_OCL
    assert levels_bear[0].price == 1.1050
    assert levels_bear[0].current_role == LevelRole.RESISTANCE


def test_wick_only_break_does_not_count_as_disrespect():
    """Wicks that cross the level NEVER count as disrespect; only closes count."""
    # Formation: A-shape at 1.1080
    # Candle 2 wicks up to 1.1150 (wick above level), but closes at 1.1050 (below level)
    df = _make_df([
        (1.1000, 1.1090, 1.0990, 1.1080),  # i: Bullish
        (1.1080, 1.1090, 1.1010, 1.1020),  # i+1: Bearish -> forms A at 1.1080
        (1.1020, 1.1150, 1.1010, 1.1050),  # wick crosses 1.1080, but close is 1.1050 < 1.1080
    ])
    levels = track_key_levels(df, timeframe="D")
    a_level = [l for l in levels if l.kind == LevelKind.A_SHAPE][0]
    assert a_level.disrespect_count == 0
    assert a_level.last_side == Side.BELOW
    assert a_level.current_role == LevelRole.RESISTANCE


def test_close_beyond_counts_as_disrespect():
    """A candle closing beyond the level increments disrespect_count and updates last_side."""
    # A-shape at 1.1080. Candle 2 closes at 1.1120 (> 1.1080)
    df = _make_df([
        (1.1000, 1.1090, 1.0990, 1.1080),  # Bullish
        (1.1080, 1.1090, 1.1010, 1.1020),  # Bearish -> forms A at 1.1080
        (1.1020, 1.1130, 1.1010, 1.1120),  # Close 1.1120 is ABOVE level
    ])
    levels = track_key_levels(df, timeframe="D")
    a_level = [l for l in levels if l.kind == LevelKind.A_SHAPE][0]
    assert a_level.disrespect_count == 1
    assert a_level.last_side == Side.ABOVE
    assert a_level.current_role == LevelRole.RBS


def test_close_exactly_on_price_changes_nothing():
    """A close exactly on the price changes nothing (no disrespect, same side)."""
    df = _make_df([
        (1.1000, 1.1090, 1.0990, 1.1080),
        (1.1080, 1.1090, 1.1010, 1.1020),  # forms A at 1.1080, last_side=BELOW
        (1.1020, 1.1100, 1.1010, 1.1080),  # closes exactly on 1.1080
    ])
    levels = track_key_levels(df, timeframe="D")
    a_level = [l for l in levels if l.kind == LevelKind.A_SHAPE][0]
    assert a_level.disrespect_count == 0
    assert a_level.last_side == Side.BELOW
    assert a_level.current_role == LevelRole.RESISTANCE


def test_a_shape_role_transitions():
    """A-shape: count 0 -> resistance; count 1 -> RBS; counts 2, 3 -> QM; count 4+ -> retired."""
    df = _make_df([
        (1.1000, 1.1090, 1.0990, 1.1080),  # i: Bullish
        (1.1080, 1.1090, 1.1010, 1.1020),  # i+1: Bearish -> forms A at 1.1080 (count 0, resistance)
        (1.1020, 1.1120, 1.1010, 1.1100),  # close 1.1100 > 1.1080 -> count 1 (RBS, last_side=ABOVE)
        (1.1100, 1.1120, 1.1030, 1.1040),  # close 1.1040 < 1.1080 -> count 2 (QM, last_side=BELOW)
        (1.1040, 1.1150, 1.1030, 1.1110),  # close 1.1110 > 1.1080 -> count 3 (QM, last_side=ABOVE)
        (1.1110, 1.1120, 1.1000, 1.1020),  # close 1.1020 < 1.1080 -> count 4 (RETIRED, last_side=BELOW)
    ])
    levels = track_key_levels(df, timeframe="D", max_disrespects=3)
    a_level = [l for l in levels if l.kind == LevelKind.A_SHAPE][0]
    assert a_level.disrespect_count == 4
    assert a_level.last_side == Side.BELOW
    assert a_level.current_role == LevelRole.RETIRED
    assert a_level.is_retired is True


def test_v_shape_role_transitions():
    """V-shape: count 0 -> support; count 1 -> SBR; count 2, 3 -> QM; count 4+ -> retired."""
    df = _make_df([
        (1.1080, 1.1090, 1.0990, 1.1000),  # i: Bearish
        (1.1000, 1.1070, 1.0990, 1.1060),  # i+1: Bullish -> forms V at 1.1000 (count 0, support)
        (1.1060, 1.1070, 1.0940, 1.0950),  # close 1.0950 < 1.1000 -> count 1 (SBR, last_side=BELOW)
        (1.0950, 1.1050, 1.0940, 1.1030),  # close 1.1030 > 1.1000 -> count 2 (QM, last_side=ABOVE)
        (1.1030, 1.1040, 1.0920, 1.0940),  # close 1.0940 < 1.1000 -> count 3 (QM, last_side=BELOW)
        (1.0940, 1.1050, 1.0930, 1.1020),  # close 1.1020 > 1.1000 -> count 4 (RETIRED, last_side=ABOVE)
    ])
    levels = track_key_levels(df, timeframe="D", max_disrespects=3)
    v_level = [l for l in levels if l.kind == LevelKind.V_SHAPE][0]
    assert v_level.disrespect_count == 4
    assert v_level.last_side == Side.ABOVE
    assert v_level.current_role == LevelRole.RETIRED
    assert v_level.is_retired is True


def test_ocl_role_flip():
    """OCL: count 0 -> initial role; count 1, 2, 3 -> flipped_OCL; count 4+ -> retired."""
    df = _make_df([
        (1.1000, 1.1060, 1.0990, 1.1050),  # Bullish
        (1.1050, 1.1110, 1.1040, 1.1100),  # Bullish -> forms bullish OCL at 1.1050 (support)
        (1.1100, 1.1110, 1.0990, 1.1010),  # close 1.1010 < 1.1050 -> count 1, flipped_OCL
        (1.1010, 1.1120, 1.1000, 1.1080),  # close 1.1080 > 1.1050 -> count 2, still flipped_OCL
        (1.1080, 1.1090, 1.0990, 1.1020),  # close 1.1020 < 1.1050 -> count 3, still flipped_OCL
        (1.1020, 1.1100, 1.1010, 1.1090),  # close 1.1090 > 1.1050 -> count 4, RETIRED
    ])
    levels = track_key_levels(df, timeframe="D", max_disrespects=3)
    ocl = [l for l in levels if l.kind == LevelKind.BULLISH_OCL][0]
    assert ocl.disrespect_count == 4
    assert ocl.current_role == LevelRole.RETIRED
    assert ocl.is_retired is True


def test_lookback_filtering():
    """Levels formed outside the lookback window are dropped."""
    # 10 candles total. A-shape formed at candle 0 and 1.
    candles = [
        (1.1000, 1.1090, 1.0990, 1.1080),
        (1.1080, 1.1090, 1.1010, 1.1020),
    ] + [(1.1020, 1.1050, 1.1010, 1.1030)] * 8
    df = _make_df(candles)

    # With lookback=5, candle 0 is outside (10 - 5 = cutoff index 5)
    levels_filtered = track_key_levels(df, timeframe="D", lookback=5)
    assert not any(l.formation_index == 0 for l in levels_filtered)

    # With lookback=10, candle 0 is kept
    levels_all = track_key_levels(df, timeframe="D", lookback=10)
    assert any(l.formation_index == 0 for l in levels_all)


def test_open_continuity_a_shape_is_local_peak_and_v_shape_local_trough():
    """
    Instruction 3:
    Verify that on open-continuity candles, every A-shape is strictly a local peak
    of the close series and every V-shape is strictly a local trough.
    """
    from slk_bot.data.candles import apply_open_continuity

    raw_candles = [
        (1.1000, 1.1060, 1.0990, 1.1050),  # 0
        (1.1050, 1.1120, 1.1030, 1.1100),  # 1: Bullish -> forms A at 1.1100
        (1.1080, 1.1100, 1.1010, 1.1020),  # 2: Bearish
        (1.1020, 1.1040, 1.0920, 1.0940),  # 3: Bearish -> forms V at 1.0940
        (1.0960, 1.1060, 1.0950, 1.1040),  # 4: Bullish
        (1.1040, 1.1150, 1.1020, 1.1120),  # 5: Bullish -> forms A at 1.1120
        (1.1120, 1.1130, 1.1060, 1.1070),  # 6: Bearish
        (1.1070, 1.1080, 1.0970, 1.0980),  # 7: Bearish -> forms V at 1.0980
        (1.0980, 1.1060, 1.0970, 1.1050),  # 8: Bullish
    ]
    df = _make_df(raw_candles)
    # Apply open continuity so open(k) = close(k-1)
    df = apply_open_continuity(df)

    levels = track_key_levels(df, timeframe="D")
    closes = df["close"].values

    assert len(levels) >= 4

    for lvl in levels:
        idx = lvl.formation_index
        assert lvl.price == closes[idx]
        if idx > 0 and idx < len(closes) - 1:
            if lvl.kind == LevelKind.A_SHAPE:
                # A-shape: Bullish candle followed by Bearish candle
                # Since open(idx) = close(idx-1) and close(idx) > open(idx) => close[idx] > close[idx-1]
                # Since open(idx+1) = close(idx) and close(idx+1) < open(idx+1) => close[idx] > close[idx+1]
                assert closes[idx] > closes[idx - 1], f"A-shape at {idx} must exceed previous close"
                assert closes[idx] > closes[idx + 1], f"A-shape at {idx} must exceed next close"
            elif lvl.kind == LevelKind.V_SHAPE:
                # V-shape: Bearish candle followed by Bullish candle
                # Since open(idx) = close(idx-1) and close(idx) < open(idx) => close[idx] < close[idx-1]
                # Since open(idx+1) = close(idx) and close(idx+1) > open(idx+1) => close[idx] < close[idx+1]
                assert closes[idx] < closes[idx - 1], f"V-shape at {idx} must be below previous close"
                assert closes[idx] < closes[idx + 1], f"V-shape at {idx} must be below next close"


def test_doji_body_threshold_pct():
    """Verify that candles with body smaller than threshold % of close are treated as doji."""
    # open 100.0, close 100.02 -> body is 0.02, which is 0.02% of close.
    # With threshold 0 (off): candle is bullish (direction = 1)
    assert candle_direction(100.0, 100.02, doji_body_threshold_pct=0.0) == 1
    # With threshold 0.05: candle body (0.02%) is < 0.05%, treated as doji (direction = 0)
    assert candle_direction(100.0, 100.02, doji_body_threshold_pct=0.05) == 0

    # With threshold 0.05, formation detection with this candle as i or i+1 returns None
    assert detect_formation(100.0, 100.02, 100.02, 99.0, doji_body_threshold_pct=0.05) is None
    # But with threshold 0.0, it forms an A-shape
    assert detect_formation(100.0, 100.02, 100.02, 99.0, doji_body_threshold_pct=0.0) is not None


