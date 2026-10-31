"""
slk_bot/tests/test_triggers.py
------------------------------
Unit tests for HTF triggers: rejection (5.3), sweep (5.4), combined (5.5).
"""

import pandas as pd
import pytest

from slk_bot.strategy.models import (
    KeyLevel,
    LevelKind,
    LevelRole,
    Side,
    TriggerDirection,
    TriggerMethod,
)
from slk_bot.strategy.triggers import (
    detect_triggers,
    evaluate_rejection,
    evaluate_rejection_multi,
    evaluate_sweep,
    get_marginal_threshold,
)


def _make_level(
    level_id: str,
    timeframe: str,
    kind: LevelKind,
    price: float,
    role: LevelRole,
    last_side: Side,
    disrespect_count: int = 0,
    formation_time: Optional[pd.Timestamp] = None,
) -> KeyLevel:
    t0 = formation_time or pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
    return KeyLevel(
        id=level_id,
        timeframe=timeframe,
        kind=kind,
        price=price,
        formation_time=t0,
        formation_confirmed_time=t0 + pd.Timedelta(days=1),
        formation_index=0,
        disrespect_count=disrespect_count,
        last_side=last_side,
        current_role=role,
    )


# -----------------------------------------------------------------------------
# 1. Rejection in both directions (bullish and bearish)
# -----------------------------------------------------------------------------
def test_rejection_bullish_and_bearish():
    # Bullish rejection off support (last_side == ABOVE)
    support_lvl = _make_level("L1", "D", LevelKind.V_SHAPE, 100.0, LevelRole.SUPPORT, Side.ABOVE)
    direction, prim, all_lvls, wick_m, close_m = evaluate_rejection(
        candle_low=98.0,
        candle_high=102.0,
        candle_close=101.0,
        active_levels=[support_lvl],
        timeframe="D",
    )
    assert direction == TriggerDirection.BULLISH
    assert prim == support_lvl
    assert len(all_lvls) == 1
    assert wick_m == pytest.approx((100.0 - 98.0) / 100.0 * 100.0)
    assert close_m == pytest.approx((101.0 - 100.0) / 100.0 * 100.0)

    # Bearish rejection off resistance (last_side == BELOW)
    resist_lvl = _make_level("L2", "D", LevelKind.A_SHAPE, 100.0, LevelRole.RESISTANCE, Side.BELOW)
    direction, prim, all_lvls, wick_m, close_m = evaluate_rejection(
        candle_low=97.0,
        candle_high=102.5,
        candle_close=99.0,
        active_levels=[resist_lvl],
        timeframe="D",
    )
    assert direction == TriggerDirection.BEARISH
    assert prim == resist_lvl
    assert len(all_lvls) == 1
    assert wick_m == pytest.approx((102.5 - 100.0) / 100.0 * 100.0)
    assert close_m == pytest.approx((100.0 - 99.0) / 100.0 * 100.0)


# -----------------------------------------------------------------------------
# 2. Level on wrong timeframe ignored
# -----------------------------------------------------------------------------
def test_rejection_wrong_timeframe_ignored():
    # Daily candle evaluating a Weekly level
    weekly_lvl = _make_level("L_W", "W", LevelKind.V_SHAPE, 100.0, LevelRole.SUPPORT, Side.ABOVE)
    direction, prim, all_lvls, _, _ = evaluate_rejection(
        candle_low=98.0,
        candle_high=102.0,
        candle_close=101.0,
        active_levels=[weekly_lvl],
        timeframe="D",  # Candle timeframe is Daily
    )
    assert direction is None
    assert prim is None
    assert len(all_lvls) == 0


# -----------------------------------------------------------------------------
# 3. Rejection needs last_side to match
# -----------------------------------------------------------------------------
def test_rejection_needs_last_side_to_match():
    # Level at 100.0 with last_side == BELOW cannot be a bullish bounce off support
    # (even if price dips to 98 and closes at 101, that is a close across/breach, not a rejection from above)
    lvl_below = _make_level("L3", "D", LevelKind.A_SHAPE, 100.0, LevelRole.RESISTANCE, Side.BELOW)
    direction, prim, _, _, _ = evaluate_rejection(
        candle_low=98.0,
        candle_high=102.0,
        candle_close=101.0,
        active_levels=[lvl_below],
        timeframe="D",
    )
    # Cannot be bullish rejection because last_side is BELOW
    assert direction != TriggerDirection.BULLISH

    # Level with last_side == ABOVE cannot be a bearish rejection
    lvl_above = _make_level("L4", "D", LevelKind.V_SHAPE, 100.0, LevelRole.SUPPORT, Side.ABOVE)
    direction, prim, _, _, _ = evaluate_rejection(
        candle_low=98.0,
        candle_high=102.0,
        candle_close=99.0,
        active_levels=[lvl_above],
        timeframe="D",
    )
    assert direction != TriggerDirection.BEARISH


# -----------------------------------------------------------------------------
# 4. Sweep valid (close back inside)
# -----------------------------------------------------------------------------
def test_sweep_valid():
    t_c = pd.Timestamp("2026-01-02 00:00:00", tz="UTC")
    t_r = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")

    # Bearish sweep: R is bullish, c.high > R.high, c.close < R.high, c.low >= R.low
    direction, ref, wick_m, close_m = evaluate_sweep(
        candle_time=t_c,
        candle_open=105.0,
        candle_high=108.0,
        candle_low=102.0,
        candle_close=104.0,
        ref_time=t_r,
        ref_open=100.0,
        ref_high=106.0,
        ref_low=99.0,
        ref_close=105.0,  # Bullish
    )
    assert direction == TriggerDirection.BEARISH
    assert ref is not None
    assert ref.swept_level == 106.0
    assert ref.swept_side == "high"
    assert wick_m == pytest.approx((108.0 - 106.0) / 106.0 * 100.0)
    assert close_m == pytest.approx((106.0 - 104.0) / 106.0 * 100.0)

    # Bullish sweep: R is bearish, c.low < R.low, c.close > R.low, c.high <= R.high
    direction, ref, wick_m, close_m = evaluate_sweep(
        candle_time=t_c,
        candle_open=100.0,
        candle_high=103.0,
        candle_low=95.0,
        candle_close=99.0,
        ref_time=t_r,
        ref_open=105.0,
        ref_high=106.0,
        ref_low=98.0,
        ref_close=100.0,  # Bearish
    )
    assert direction == TriggerDirection.BULLISH
    assert ref is not None
    assert ref.swept_level == 98.0
    assert ref.swept_side == "low"
    assert wick_m == pytest.approx((98.0 - 95.0) / 98.0 * 100.0)
    assert close_m == pytest.approx((99.0 - 98.0) / 98.0 * 100.0)


# -----------------------------------------------------------------------------
# 5. Sweep invalid (close beyond swept level is a break, not a sweep)
# -----------------------------------------------------------------------------
def test_sweep_invalid_close_beyond():
    t_c = pd.Timestamp("2026-01-02 00:00:00", tz="UTC")
    t_r = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")

    # Bearish attempt where candle closes ABOVE R.high (107.0 > 106.0)
    direction, ref, _, _ = evaluate_sweep(
        candle_time=t_c,
        candle_open=105.0,
        candle_high=108.0,
        candle_low=102.0,
        candle_close=107.0,  # Broke beyond R.high
        ref_time=t_r,
        ref_open=100.0,
        ref_high=106.0,
        ref_low=99.0,
        ref_close=105.0,
    )
    assert direction is None
    assert ref is None

    # Bullish attempt where candle closes BELOW R.low (97.0 < 98.0)
    direction, ref, _, _ = evaluate_sweep(
        candle_time=t_c,
        candle_open=100.0,
        candle_high=103.0,
        candle_low=95.0,
        candle_close=97.0,  # Broke beyond R.low
        ref_time=t_r,
        ref_open=105.0,
        ref_high=106.0,
        ref_low=98.0,
        ref_close=100.0,
    )
    assert direction is None
    assert ref is None


# -----------------------------------------------------------------------------
# 6. Sweep invalid when opposite side is touched
# -----------------------------------------------------------------------------
def test_sweep_invalid_opposite_side_touched():
    t_c = pd.Timestamp("2026-01-02 00:00:00", tz="UTC")
    t_r = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")

    # Bearish attempt where candle.low broke below R.low (98.0 < 99.0)
    direction, ref, _, _ = evaluate_sweep(
        candle_time=t_c,
        candle_open=105.0,
        candle_high=108.0,
        candle_low=98.0,  # Touched opposite side
        candle_close=104.0,
        ref_time=t_r,
        ref_open=100.0,
        ref_high=106.0,
        ref_low=99.0,
        ref_close=105.0,
    )
    assert direction is None
    assert ref is None

    # Bullish attempt where candle.high broke above R.high (107.0 > 106.0)
    direction, ref, _, _ = evaluate_sweep(
        candle_time=t_c,
        candle_open=100.0,
        candle_high=107.0,  # Touched opposite side
        candle_low=95.0,
        candle_close=99.0,
        ref_time=t_r,
        ref_open=105.0,
        ref_high=106.0,
        ref_low=98.0,
        ref_close=100.0,
    )
    assert direction is None
    assert ref is None


# -----------------------------------------------------------------------------
# 7. Color asymmetry (bullish candle low & bearish candle high ignored)
# -----------------------------------------------------------------------------
def test_sweep_color_asymmetry():
    t_c = pd.Timestamp("2026-01-02 00:00:00", tz="UTC")
    t_r = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")

    # R is BULLISH: its LOW cannot be swept for a bullish sweep!
    direction, ref, _, _ = evaluate_sweep(
        candle_time=t_c,
        candle_open=102.0,
        candle_high=104.0,
        candle_low=95.0,
        candle_close=100.0,
        ref_time=t_r,
        ref_open=99.0,
        ref_high=106.0,
        ref_low=98.0,
        ref_close=105.0,  # Bullish R
    )
    assert direction is None

    # R is BEARISH: its HIGH cannot be swept for a bearish sweep!
    direction, ref, _, _ = evaluate_sweep(
        candle_time=t_c,
        candle_open=103.0,
        candle_high=108.0,
        candle_low=101.0,
        candle_close=104.0,
        ref_time=t_r,
        ref_open=105.0,
        ref_high=106.0,
        ref_low=98.0,
        ref_close=100.0,  # Bearish R
    )
    assert direction is None


# -----------------------------------------------------------------------------
# 8. Method 3 needing both conditions in the same direction
# -----------------------------------------------------------------------------
def test_method_3_both_conditions_same_direction():
    # Build a candle DataFrame with:
    # 0: Bullish candle to form A-shape with candle 1
    # 1: Bearish candle to complete A-shape at 105.0
    # 2: Bullish candle (ref candle for sweep)
    # 3: Trigger candle that sweeps candle 2 high AND rejects A-shape at 105.0
    dates = pd.date_range("2026-01-01", periods=4, freq="D", tz="UTC")
    df = pd.DataFrame(
        [
            {"open": 100.0, "high": 106.0, "low": 99.0, "close": 105.0, "volume": 100},  # 0: bullish
            {"open": 105.0, "high": 105.5, "low": 98.0, "close": 100.0, "volume": 100},  # 1: bearish (A formed at 105.0)
            {"open": 100.0, "high": 104.0, "low": 99.0, "close": 103.0, "volume": 100},  # 2: bullish ref candle (H=104.0)
            {"open": 103.0, "high": 105.5, "low": 101.0, "close": 102.0, "volume": 100}, # 3: high=105.5 > 105.0 & > 104.0, close=102.0 < 105.0 & < 104.0
        ],
        index=dates,
    )

    triggers = detect_triggers(df, timeframe="D", symbol="TEST", asset_class="forex")
    # Candle 3 should be Method 3 Bearish!
    t3 = [t for t in triggers if t.trigger_candle_time == dates[3]]
    assert len(t3) == 1
    assert t3[0].method == TriggerMethod.METHOD_3
    assert t3[0].direction == TriggerDirection.BEARISH
    assert t3[0].primary_level is not None
    assert t3[0].primary_level.price == 105.0
    assert t3[0].sweep_reference is not None
    assert t3[0].sweep_reference.swept_level == 104.0
    assert t3[0].mitigation_extreme == 105.5


# -----------------------------------------------------------------------------
# 9. Marginal flag per asset class
# -----------------------------------------------------------------------------
def test_marginal_flag_per_asset_class():
    assert get_marginal_threshold("forex") == 0.02
    assert get_marginal_threshold("commodity") == 0.03
    assert get_marginal_threshold("index") == 0.20
    assert get_marginal_threshold("crypto") == 0.15

    # Test marginal threshold flagging on small pass margin
    # Level at 100.0. Close is 100.01 (0.01% pass margin).
    # Forex threshold is 0.02% -> 0.01% < 0.02% should be flagged MARGINAL!
    dates = pd.date_range("2026-01-01", periods=3, freq="D", tz="UTC")
    df = pd.DataFrame(
        [
            {"open": 105.0, "high": 105.0, "low": 99.0, "close": 100.0, "volume": 100},  # 0: bearish
            {"open": 100.0, "high": 106.0, "low": 100.0, "close": 105.0, "volume": 100}, # 1: bullish (V formed at 100.0)
            {"open": 105.0, "high": 105.0, "low": 99.5, "close": 100.01, "volume": 100}, # 2: low=99.5 < 100.0, close=100.01 > 100.0
        ],
        index=dates,
    )
    triggers = detect_triggers(df, timeframe="D", symbol="TEST", asset_class="forex")
    assert len(triggers) >= 1
    t = triggers[-1]
    assert t.margins.is_marginal is True


# -----------------------------------------------------------------------------
# 10. Freshness full cycle: A rejected -> unfresh -> disrespected -> fresh RBS
#     -> rejected -> unfresh -> disrespected -> fresh QM
# -----------------------------------------------------------------------------
def test_freshness_full_cycle():
    from slk_bot.strategy.levels import apply_candle_to_level

    # 1. Start with an A-shape at 100.0 (resistance, last_side = BELOW)
    lvl = _make_level("A1", "D", LevelKind.A_SHAPE, 100.0, LevelRole.RESISTANCE, Side.BELOW)
    assert lvl.fresh is True
    assert lvl.last_contact == "none"

    # 2. Rejection: wick high 102.0 > 100.0, close 98.0 <= 100.0 -> UNFRESH
    apply_candle_to_level(lvl, candle_close=98.0, candle_high=102.0, candle_low=97.0)
    assert lvl.fresh is False
    assert lvl.last_contact == "rejection"
    assert lvl.current_role == LevelRole.RESISTANCE

    # 3. Disrespect: close 105.0 > 100.0 -> FRESH RBS (count 1, last_side = ABOVE)
    apply_candle_to_level(lvl, candle_close=105.0, candle_high=106.0, candle_low=99.0)
    assert lvl.fresh is True
    assert lvl.last_contact == "disrespect"
    assert lvl.current_role == LevelRole.RBS
    assert lvl.last_side == Side.ABOVE
    assert lvl.disrespect_count == 1

    # 4. Rejection of RBS: wick low 98.0 < 100.0, close 102.0 >= 100.0 -> UNFRESH RBS
    apply_candle_to_level(lvl, candle_close=102.0, candle_high=103.0, candle_low=98.0)
    assert lvl.fresh is False
    assert lvl.last_contact == "rejection"
    assert lvl.current_role == LevelRole.RBS
    assert lvl.last_side == Side.ABOVE

    # 5. Disrespect: close 97.0 < 100.0 -> FRESH QM (count 2, last_side = BELOW)
    apply_candle_to_level(lvl, candle_close=97.0, candle_high=101.0, candle_low=96.0)
    assert lvl.fresh is True
    assert lvl.last_contact == "disrespect"
    assert lvl.current_role == LevelRole.QM
    assert lvl.last_side == Side.BELOW
    assert lvl.disrespect_count == 2


# -----------------------------------------------------------------------------
# 11. Unfresh OCL never used as trigger level
# -----------------------------------------------------------------------------
def test_unfresh_ocl_never_used_as_trigger():
    # Fresh Bullish OCL at 100.0
    ocl = _make_level("OCL1", "D", LevelKind.BULLISH_OCL, 100.0, LevelRole.SUPPORT, Side.ABOVE)
    assert ocl.fresh is True

    # 1. Fresh OCL can be rejected
    direction, prim, all_lvls, _, _ = evaluate_rejection(
        candle_low=98.0, candle_high=102.0, candle_close=101.0, active_levels=[ocl], timeframe="D"
    )
    assert direction == TriggerDirection.BULLISH
    assert prim == ocl

    # 2. Make OCL unfresh (e.g. prior rejection contact)
    ocl.fresh = False
    ocl.last_contact = "rejection"

    # 3. Unfresh OCL MUST NOT be used as trigger level
    direction, prim, all_lvls, _, _ = evaluate_rejection(
        candle_low=98.0, candle_high=102.0, candle_close=101.0, active_levels=[ocl], timeframe="D"
    )
    assert direction is None
    assert prim is None
    assert len(all_lvls) == 0


# -----------------------------------------------------------------------------
# 12. Unfresh non-OCL allowed as trigger and labeled LOW PROBABILITY
# -----------------------------------------------------------------------------
def test_unfresh_non_ocl_allowed_and_labeled_low_prob():
    # Build a candle DataFrame where an A-shape formed, gets rejected once (becomes unfresh),
    # and then gets rejected again as a trigger candle
    dates = pd.date_range("2026-01-01", periods=5, freq="D", tz="UTC")
    df = pd.DataFrame(
        [
            {"open": 100.0, "high": 106.0, "low": 99.0, "close": 105.0, "volume": 100},  # 0: bullish
            {"open": 105.0, "high": 105.5, "low": 98.0, "close": 100.0, "volume": 100},  # 1: bearish (A formed at 105.0)
            {"open": 100.0, "high": 105.5, "low": 99.0, "close": 101.0, "volume": 100},  # 2: wick tested 105.5 > 105.0, close 101.0 < 105.0 (Rejection! Level becomes UNFRESH)
            {"open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0, "volume": 100},  # 3: normal candle
            {"open": 102.0, "high": 105.5, "low": 101.5, "close": 103.0, "volume": 100},  # 4: Rejection trigger candle testing unfresh A-shape
        ],
        index=dates,
    )

    # With unfresh_policy="flag": trigger is generated and labeled LOW PROBABILITY
    triggers_flag = detect_triggers(df, timeframe="D", symbol="TEST", unfresh_policy="flag")
    t4 = [t for t in triggers_flag if t.trigger_candle_time == dates[4]]
    assert len(t4) == 1
    assert t4[0].is_low_probability is True
    assert t4[0].primary_level_fresh is False
    assert t4[0].primary_level_last_contact == "rejection"

    # With unfresh_policy="off": trigger is generated without LOW PROBABILITY label
    triggers_off = detect_triggers(df, timeframe="D", symbol="TEST", unfresh_policy="off")
    t4_off = [t for t in triggers_off if t.trigger_candle_time == dates[4]]
    assert len(t4_off) == 1
    assert t4_off[0].is_low_probability is False


# -----------------------------------------------------------------------------
# 13. Marginal flag computed only from primary level (secondary levels ignored)
# -----------------------------------------------------------------------------
def test_marginal_computed_only_from_primary_level():
    # Two support levels:
    # L1 (primary, nearest to close): 100.0. Candle close = 101.0 (pass margin 1.0% -> NOT marginal)
    # L2 (secondary): 100.99. Candle close = 101.0 (pass margin 0.01% -> would be marginal if considered)
    # Marginal flag MUST be based only on primary level L1 -> is_marginal MUST be False!
    l1 = _make_level("L1", "D", LevelKind.V_SHAPE, 100.0, LevelRole.SUPPORT, Side.ABOVE)
    l2 = _make_level("L2", "D", LevelKind.V_SHAPE, 100.99, LevelRole.SUPPORT, Side.ABOVE)

    # Candle dips to 98.0, closes at 101.0
    # Primary level is L2 if nearest to 101.0!
    # Wait, 100.99 is closer to 101.0 than 100.0.
    # So let's make L1 at 100.90 (distance 0.10, margin (101.0-100.9)/100.9 = 0.099% > 0.02% -> NOT marginal)
    # And L2 at 95.0 (far away, distance 6.0, margin 6.0%)
    # Or let's test where primary level passes by 1.0%, but secondary level was barely penetrated:
    # Primary is nearest to mitigation extreme X (candle_low = 98.0 for bullish):
    # L_PRIM at 98.50: dist to low = 0.50. Pass margins: wick = 0.507%, close = 2.53% (both > 0.02% -> NOT marginal)
    # L_SEC at 100.99: dist to low = 2.99. Close pass margin = 0.0099% (< 0.02% -> would be marginal if counted!)
    l_prim = _make_level("L_PRIM", "D", LevelKind.V_SHAPE, 98.50, LevelRole.SUPPORT, Side.ABOVE)
    l_sec = _make_level("L_SEC", "D", LevelKind.V_SHAPE, 100.99, LevelRole.SUPPORT, Side.ABOVE)

    direction, prim, all_lvls, wick_m, close_m = evaluate_rejection(
        candle_low=98.0,
        candle_high=102.0,
        candle_close=101.0,
        active_levels=[l_prim, l_sec],
        timeframe="D",
    )
    assert prim == l_prim  # Primary is L_PRIM (dist to low abs(98.50-98.0)=0.5 < abs(100.99-98.0)=2.99)
    assert len(all_lvls) == 2
    # Margins are computed ONLY from primary!
    assert wick_m == pytest.approx((98.50 - 98.0) / 98.50 * 100.0)
    assert close_m == pytest.approx((101.0 - 98.50) / 98.50 * 100.0)

    # Trigger margins check: marginal evaluated only against primary level
    threshold = 0.02
    min_m = min(wick_m, close_m)
    assert min_m > threshold
    assert (min_m < threshold) is False  # NOT marginal!


def test_owner_rules_allow_unfresh_false_and_age_limits():
    # 1. Test allow_unfresh=False rejects unfresh levels
    dates = pd.date_range("2026-01-01", periods=5, freq="D", tz="UTC")
    df = pd.DataFrame(
        [
            {"open": 100.0, "high": 106.0, "low": 99.0, "close": 105.0, "volume": 100},
            {"open": 105.0, "high": 105.5, "low": 98.0, "close": 100.0, "volume": 100},
            {"open": 100.0, "high": 105.5, "low": 99.0, "close": 101.0, "volume": 100},  # Rejection -> level becomes UNFRESH
            {"open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0, "volume": 100},
            {"open": 102.0, "high": 105.5, "low": 101.5, "close": 103.0, "volume": 100},  # 4: Rejection on unfresh level
        ],
        index=dates,
    )
    trigs_strict = detect_triggers(df, timeframe="D", symbol="TEST", allow_unfresh=False)
    t4_strict = [t for t in trigs_strict if t.trigger_candle_time == dates[4]]
    assert len(t4_strict) == 0  # Excluded under Owner's Rule 7a!

    # 2. Test Age limits on D1: > 6 months excluded, disp=3 and > 4 months excluded
    rej_time = pd.Timestamp("2026-10-15 21:00:00", tz="UTC")
    lvl_recent = _make_level("L_RECENT", "D", LevelKind.A_SHAPE, 105.0, LevelRole.RESISTANCE, Side.BELOW, formation_time=pd.Timestamp("2026-09-01 21:00:00", tz="UTC"))
    lvl_old = _make_level("L_OLD", "D", LevelKind.A_SHAPE, 105.0, LevelRole.RESISTANCE, Side.BELOW, formation_time=pd.Timestamp("2026-03-01 21:00:00", tz="UTC"))  # > 7 months old
    lvl_disp3_old = _make_level("L_DISP3_OLD", "D", LevelKind.A_SHAPE, 105.0, LevelRole.QM, Side.BELOW, disrespect_count=3, formation_time=pd.Timestamp("2026-05-15 21:00:00", tz="UTC"))  # 5 months old with disp=3

    # D1 evaluation (RESISTANCE level tested from below -> bearish rejection in index 1)
    _, res_recent = evaluate_rejection_multi(
        candle_low=102.0, candle_high=105.5, candle_close=103.0,
        active_levels=[lvl_recent], timeframe="D", candle_time=rej_time,
        age_limit_timeframes=["D"], max_age_months=6, max_age_months_disrespect_3=4,
    )
    assert res_recent is not None

    _, res_old = evaluate_rejection_multi(
        candle_low=102.0, candle_high=105.5, candle_close=103.0,
        active_levels=[lvl_old], timeframe="D", candle_time=rej_time,
        age_limit_timeframes=["D"], max_age_months=6, max_age_months_disrespect_3=4,
    )
    assert res_old is None  # Excluded because > 6 months

    _, res_disp3_old = evaluate_rejection_multi(
        candle_low=102.0, candle_high=105.5, candle_close=103.0,
        active_levels=[lvl_disp3_old], timeframe="D", candle_time=rej_time,
        age_limit_timeframes=["D"], max_age_months=6, max_age_months_disrespect_3=4,
    )
    assert res_disp3_old is None  # Excluded because disp=3 and > 4 months

    # Weekly timeframe: no age limit applies
    lvl_weekly_old = _make_level("L_W_OLD", "W", LevelKind.A_SHAPE, 105.0, LevelRole.RESISTANCE, Side.BELOW, formation_time=pd.Timestamp("2023-01-01 21:00:00", tz="UTC"))
    _, res_w = evaluate_rejection_multi(
        candle_low=102.0, candle_high=105.5, candle_close=103.0,
        active_levels=[lvl_weekly_old], timeframe="W", candle_time=rej_time,
        age_limit_timeframes=["D"], max_age_months=6, max_age_months_disrespect_3=4,
    )
    assert res_w is not None  # Weekly setups have no age limit!


