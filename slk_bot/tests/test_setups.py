"""
slk_bot/tests/test_setups.py
----------------------------
Comprehensive unit tests for Phase 3:
  - No alert before the trigger candle closes (alert time = trigger candle close)
  - Wick-only break does not count (close beyond is mandatory)
  - Breakout before extreme candle does not count
  - Invalidation: close beyond X in opposite direction
  - Expiry in trading days (3 days for D, 14 days for W)
  - Upgrade internal -> external (generates second alert)
  - Dedupe across restarts using the database cache
  - Candle that breaks both levels gives External only
"""

import pytest
import pandas as pd
from slk_bot.strategy.models import (
    KeyLevel,
    LevelKind,
    LevelRole,
    Side,
    TriggerDirection,
    TriggerMethod,
    HTFTrigger,
)
from slk_bot.strategy.setups import (
    SetupState,
    BreakoutType,
    evaluate_setup_lifecycle,
    SLKAlert,
)
from slk_bot.storage.cache import CandleCache


def _make_trigger(
    direction: TriggerDirection = TriggerDirection.BULLISH,
    method: TriggerMethod = TriggerMethod.METHOD_1,
    trig_time: str = "2026-10-01 21:00:00+00:00",
    extreme_x: float = 100.0,
    is_low_prob: bool = False,
) -> HTFTrigger:
    t_open = pd.to_datetime(trig_time, utc=True)
    prim = KeyLevel(
        id="prim_1",
        symbol="EURUSD",
        timeframe="D",
        kind=LevelKind.V_SHAPE,
        price=100.5,
        formation_time=t_open - pd.Timedelta(days=5),
        formation_confirmed_time=t_open - pd.Timedelta(days=4),
        formation_index=0,
        current_role=LevelRole.SUPPORT,
        disrespect_count=0,
        last_side=Side.ABOVE,
        fresh=not is_low_prob,
    )
    return HTFTrigger(
        symbol="EURUSD",
        timeframe="D",
        direction=direction,
        method=method,
        trigger_candle_time=t_open,
        trigger_candle_open=102.0,
        trigger_candle_high=103.0,
        trigger_candle_low=extreme_x,
        trigger_candle_close=101.5,
        mitigation_extreme=extreme_x,
        primary_level=prim,
        all_rejected_levels=[prim],
        is_low_probability=is_low_prob,
    )


def test_no_alert_before_trigger_candle_closes():
    """
    If the LTF breakout occurs inside the forming HTF trigger candle,
    the alert time MUST be the trigger candle's close time.
    """
    trigger = _make_trigger(
        direction=TriggerDirection.BULLISH,
        trig_time="2026-10-01 21:00:00+00:00",
        extreme_x=100.0,
    )
    # HTF daily candle: 2026-10-01 21:00 closes at 2026-10-02 21:00 UTC
    htf_df = pd.DataFrame(
        {"open": [102.0], "high": [103.0], "low": [100.0], "close": [101.5]},
        index=pd.DatetimeIndex(["2026-10-01 21:00:00+00:00"]),
    )

    # LTF H4 candles inside the daily bar:
    # 21:00, 01:00 (forms LX), 05:00, 09:00 (extreme X=100.0), 13:00 (closes above LX!)
    h4_times = pd.date_range("2026-10-01 17:00", "2026-10-02 17:00", freq="4h", tz="UTC")
    # Form an LX A-shape before extreme:
    # 17:00: open 101.0, close 102.0 (bullish)
    # 21:00: open 102.0, close 101.5 (bearish) -> A-shape at 102.0 formed at 17:00, confirmed at 21:00
    # 01:00: open 101.5, low 100.0 (extreme X), close 100.5
    # 05:00: open 100.5, high 103.0, close 102.5 (CLOSES ABOVE LX 102.0!)
    opens =  [101.0, 102.0, 101.5, 100.5, 102.5, 102.0, 102.0]
    highs =  [102.0, 102.5, 102.0, 103.0, 103.0, 102.5, 102.5]
    lows =   [100.8, 101.2, 100.0, 100.2, 101.8, 101.5, 101.5]
    closes = [102.0, 101.5, 100.5, 102.5, 102.0, 102.0, 102.0]

    ltf_df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes},
        index=h4_times[:len(opens)],
    )

    setup = evaluate_setup_lifecycle(
        trigger=trigger,
        htf_df=htf_df,
        ltf_df=ltf_df,
        daily_df=htf_df,
        symbol="EURUSD",
        htf_tf="D",
        ltf_tf="H4",
    )

    assert setup.state == SetupState.EXTERNAL_CONFIRMED
    assert len(setup.alerts) == 1
    alert = setup.alerts[0]
    # Breakout candle is 05:00 UTC (closing at 09:00 UTC), which is BEFORE daily candle close (21:00 UTC)
    # Alert time must be delayed to the daily candle's close (2026-10-02 21:00 UTC)!
    assert alert.alert_time == pd.to_datetime("2026-10-02 21:00:00+00:00")


def test_wick_only_break_does_not_count():
    """A candle whose wick breaks beyond LX but close stays inside does NOT confirm breakout."""
    trigger = _make_trigger(extreme_x=100.0)
    htf_df = pd.DataFrame(
        {"open": [102.0], "high": [103.0], "low": [100.0], "close": [101.5]},
        index=pd.DatetimeIndex(["2026-10-01 21:00:00+00:00"]),
    )
    h4_times = pd.date_range("2026-10-01 17:00", periods=5, freq="4h", tz="UTC")
    # LX formed at 102.0
    # Extreme candle at 01:00 (low 100.0)
    # Candle at 05:00 has high 103.5 (wick above 102.0) but CLOSE 101.8 (below 102.0!)
    opens =  [101.0, 102.0, 101.5, 100.5, 101.8]
    highs =  [102.0, 102.5, 102.0, 103.5, 102.0]
    lows =   [100.8, 101.2, 100.0, 100.2, 101.0]
    closes = [102.0, 101.5, 100.5, 101.8, 101.5]

    ltf_df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes},
        index=h4_times,
    )

    setup = evaluate_setup_lifecycle(
        trigger=trigger,
        htf_df=htf_df,
        ltf_df=ltf_df,
        daily_df=htf_df,
        symbol="EURUSD",
        htf_tf="D",
        ltf_tf="H4",
    )
    # Still watching, not confirmed!
    assert setup.state == SetupState.WATCHING
    assert len(setup.alerts) == 0


def test_breakout_before_extreme_candle_does_not_count():
    """An LTF candle closing above LX before the extreme candle X occurs is ignored."""
    trigger = _make_trigger(extreme_x=100.0)
    htf_df = pd.DataFrame(
        {"open": [102.0], "high": [103.0], "low": [100.0], "close": [101.5]},
        index=pd.DatetimeIndex(["2026-10-01 21:00:00+00:00"]),
    )
    h4_times = pd.date_range("2026-10-01 13:00", periods=6, freq="4h", tz="UTC")
    # 13:00 forms A-shape at 102.0
    # 17:00 closes at 102.5 (above 102.0!)
    # But extreme candle X occurs at 21:00 (low 100.0)!
    # Breakout before 21:00 does not count.
    opens =  [101.0, 102.0, 101.5, 102.5, 101.0, 101.0]
    highs =  [102.0, 102.5, 102.8, 102.8, 101.5, 101.5]
    lows =   [100.8, 101.2, 101.0, 100.0, 100.5, 100.5]
    closes = [102.0, 101.5, 102.5, 100.5, 101.0, 101.0]

    ltf_df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes},
        index=h4_times,
    )

    setup = evaluate_setup_lifecycle(
        trigger=trigger,
        htf_df=htf_df,
        ltf_df=ltf_df,
        daily_df=htf_df,
        symbol="EURUSD",
        htf_tf="D",
        ltf_tf="H4",
    )
    # Setup did not trigger external confirmation from the pre-extreme bar
    assert setup.timeline.external_breakout_candle_time is None


def test_invalidation_on_close_beyond_extreme():
    """An LTF candle closing beyond extreme X in the opposite direction invalidates the setup."""
    trigger = _make_trigger(
        direction=TriggerDirection.BULLISH,
        extreme_x=100.0,
    )
    htf_df = pd.DataFrame(
        {"open": [102.0], "high": [103.0], "low": [100.0], "close": [101.5]},
        index=pd.DatetimeIndex(["2026-10-01 21:00:00+00:00"]),
    )
    # Trigger window: 2026-10-01 21:00 to 2026-10-02 21:00
    # Inside trigger window, lowest low is 100.0 at 01:00
    # On 2026-10-02 21:00 (after trigger candle closes), candle closes at 99.5 (< 100.0) -> INVALIDATED!
    times = pd.DatetimeIndex([
        "2026-10-01 17:00:00+00:00",
        "2026-10-01 21:00:00+00:00",
        "2026-10-02 01:00:00+00:00",  # Extreme candle X = 100.0
        "2026-10-02 05:00:00+00:00",
        "2026-10-02 21:00:00+00:00",  # Closes at 99.5 (< 100.0)
    ])
    opens =  [101.0, 102.0, 101.5, 100.5, 100.2]
    highs =  [102.0, 102.5, 102.0, 101.0, 100.5]
    lows =   [100.8, 101.2, 100.0, 100.2, 99.0]
    closes = [102.0, 101.5, 100.5, 100.8, 99.5]

    ltf_df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes},
        index=times,
    )

    setup = evaluate_setup_lifecycle(
        trigger=trigger,
        htf_df=htf_df,
        ltf_df=ltf_df,
        daily_df=htf_df,
        symbol="EURUSD",
        htf_tf="D",
        ltf_tf="H4",
    )
    assert setup.state == SetupState.INVALIDATED
    assert setup.timeline.invalidation_time == times[4]


def test_expiry_in_trading_days():
    """A setup expires if no breakout occurs within the configured daily candles window."""
    trigger = _make_trigger(extreme_x=100.0)
    htf_df = pd.DataFrame(
        {"open": [102.0], "high": [103.0], "low": [100.0], "close": [101.5]},
        index=pd.DatetimeIndex(["2026-10-01 21:00:00+00:00"]),
    )
    # 5 daily candles after trigger close
    daily_times = pd.date_range("2026-10-02 21:00", periods=6, freq="1D", tz="UTC")
    daily_df = pd.DataFrame(
        {"open": 101.0, "high": 101.8, "low": 100.2, "close": 101.5},
        index=daily_times,
    )
    # H4 candles extending past 3 trading days without breaking LX (102.0)
    h4_times = pd.date_range("2026-10-01 17:00", periods=30, freq="4h", tz="UTC")
    ltf_df = pd.DataFrame(
        {"open": 101.0, "high": 101.5, "low": 100.2, "close": 101.2},
        index=h4_times,
    )
    # inject LX formation and extreme
    ltf_df.iloc[0] = [101.0, 102.0, 100.8, 102.0]
    ltf_df.iloc[1] = [102.0, 102.5, 101.2, 101.5]
    ltf_df.iloc[2] = [101.5, 102.0, 100.0, 100.5]  # extreme X=100.0

    setup = evaluate_setup_lifecycle(
        trigger=trigger,
        htf_df=htf_df,
        ltf_df=ltf_df,
        daily_df=daily_df,
        symbol="EURUSD",
        htf_tf="D",
        ltf_tf="H4",
        expiry_daily_trigger_candles=3,
    )
    assert setup.state == SetupState.EXPIRED


def test_upgrade_internal_to_external():
    """An internal confirmation upgrades to external confirmation when LX breaks later."""
    trigger = _make_trigger(extreme_x=100.0)
    htf_df = pd.DataFrame(
        {"open": [102.0], "high": [103.0], "low": [100.0], "close": [101.5]},
        index=pd.DatetimeIndex(["2026-10-01 21:00:00+00:00"]),
    )
    h4_times = pd.date_range("2026-10-01 17:00", periods=8, freq="4h", tz="UTC")
    # LX = 103.0 formed at 17:00
    # Extreme X = 100.0 at 01:00 (index 2)
    # Candle 3 (05:00) & Candle 4 (09:00): form internal A-shape at 101.8
    # Candle 5 (13:00): closes at 102.2 (> 101.8, breaks internal!) -> INTERNAL_CONFIRMED
    # Candle 6 (17:00): closes at 103.5 (> 103.0, breaks LX!) -> EXTERNAL_CONFIRMED (Upgrade!)
    opens =  [101.0, 103.0, 102.0, 100.5, 101.8, 101.2, 102.2, 103.5]
    highs =  [103.0, 103.2, 102.0, 102.0, 102.0, 102.5, 103.8, 104.0]
    lows =   [100.8, 101.5, 100.0, 100.2, 101.0, 101.0, 102.0, 103.0]
    closes = [103.0, 102.0, 100.5, 101.8, 101.2, 102.2, 103.5, 103.8]

    ltf_df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes},
        index=h4_times,
    )

    setup = evaluate_setup_lifecycle(
        trigger=trigger,
        htf_df=htf_df,
        ltf_df=ltf_df,
        daily_df=htf_df,
        symbol="EURUSD",
        htf_tf="D",
        ltf_tf="H4",
    )

    assert setup.state == SetupState.EXTERNAL_CONFIRMED
    assert len(setup.alerts) == 2
    assert setup.alerts[0].breakout_type == BreakoutType.INTERNAL
    assert setup.alerts[1].breakout_type == BreakoutType.EXTERNAL


def test_candle_breaking_both_levels_gives_external_only():
    """If one candle closes beyond both an internal level and LX, record it as External only."""
    trigger = _make_trigger(extreme_x=100.0)
    htf_df = pd.DataFrame(
        {"open": [102.0], "high": [103.0], "low": [100.0], "close": [101.5]},
        index=pd.DatetimeIndex(["2026-10-01 21:00:00+00:00"]),
    )
    h4_times = pd.date_range("2026-10-01 17:00", periods=7, freq="4h", tz="UTC")
    # LX = 102.5
    # Extreme X = 100.0 at 01:00
    # Internal level formed at 101.5
    # Next candle closes at 103.0 (above both 101.5 and 102.5!)
    opens =  [101.0, 102.5, 102.0, 100.5, 101.5, 101.0, 103.0]
    highs =  [102.5, 102.8, 102.0, 101.8, 101.8, 103.5, 103.5]
    lows =   [100.8, 101.5, 100.0, 100.2, 101.0, 100.8, 102.5]
    closes = [102.5, 102.0, 100.5, 101.5, 101.0, 103.0, 103.2]

    ltf_df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes},
        index=h4_times,
    )

    setup = evaluate_setup_lifecycle(
        trigger=trigger,
        htf_df=htf_df,
        ltf_df=ltf_df,
        daily_df=htf_df,
        symbol="EURUSD",
        htf_tf="D",
        ltf_tf="H4",
    )

    assert setup.state == SetupState.EXTERNAL_CONFIRMED
    # Only 1 alert, and it's External
    assert len(setup.alerts) == 1
    assert setup.alerts[0].breakout_type == BreakoutType.EXTERNAL


def test_dedupe_across_restarts(tmp_path):
    """Saving duplicate alerts in the database returns False and prevents duplicate push alerts."""
    db_file = tmp_path / "test_candles.db"
    cache = CandleCache(db_path=db_file)

    dedupe_key = "EURUSD_D_202610012100_bullish_1_EXTERNAL"
    # First save: returns True
    res1 = cache.save_alert(
        dedupe_key=dedupe_key,
        setup_id="setup_1",
        symbol="EURUSD",
        htf="D",
        ltf="H4",
        direction="bullish",
        method=1,
        breakout_type="EXTERNAL",
        alert_time="2026-10-02 21:00 UTC",
        is_low_prob=False,
        is_marginal=False,
        is_suppressed=False,
        payload_json="{}",
    )
    assert res1 is True

    # Second save (e.g. after bot restart): returns False!
    res2 = cache.save_alert(
        dedupe_key=dedupe_key,
        setup_id="setup_1",
        symbol="EURUSD",
        htf="D",
        ltf="H4",
        direction="bullish",
        method=1,
        breakout_type="EXTERNAL",
        alert_time="2026-10-02 21:00 UTC",
        is_low_prob=False,
        is_marginal=False,
        is_suppressed=False,
        payload_json="{}",
    )
    assert res2 is False

    # Check database holds exactly 1 alert
    alerts = cache.get_alerts("EURUSD")
    assert len(alerts) == 1
    assert alerts[0]["dedupe_key"] == dedupe_key
