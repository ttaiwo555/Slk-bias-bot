"""
slk_bot/strategy/triggers.py
----------------------------
High Timeframe (HTF) Triggers for Weekly and Daily candles (Sections 5.3, 5.4, 5.5).

Evaluated as pure functions on closed candles only.
Evaluated against level states from BEFORE the trigger candle's close is applied.
Only active levels (disrespect_count <= max_disrespects) are used.
"""

from __future__ import annotations

import copy
from typing import Any, Optional
import pandas as pd

from slk_bot.strategy.models import (
    HTFTrigger,
    KeyLevel,
    LevelKind,
    LevelRole,
    Side,
    SweepReference,
    TriggerDirection,
    TriggerMethod,
    TriggerPassMargins,
)
from slk_bot.strategy.levels import (
    apply_candle_to_level,
    detect_formation,
)

# Default marginal thresholds per asset class (% of price)
DEFAULT_MARGINAL_THRESHOLDS: dict[str, float] = {
    "forex": 0.02,
    "commodity": 0.03,
    "index": 0.20,
    "crypto": 0.15,
}


def get_marginal_threshold(asset_class: str, custom_thresholds: Optional[dict[str, float]] = None) -> float:
    """Return marginal threshold percentage for the given asset class."""
    if custom_thresholds and asset_class in custom_thresholds:
        return float(custom_thresholds[asset_class])
    norm = asset_class.lower()
    if "index" in norm:
        return DEFAULT_MARGINAL_THRESHOLDS["index"]
    return DEFAULT_MARGINAL_THRESHOLDS.get(norm, 0.02)


def is_buy_role(level: KeyLevel) -> bool:
    """
    True if level is eligible for bullish rejection (buys).
    Requirements:
      - Active level (disrespect_count <= max_disrespects, not retired)
      - last_side == Side.ABOVE (price was above the level)
      - Role is support or flipped support
    """
    if level.is_retired:
        return False
    if level.last_side != Side.ABOVE:
        return False
    if level.current_role in (LevelRole.SUPPORT, LevelRole.RBS, LevelRole.QM, LevelRole.FLIPPED_OCL):
        return True
    return False


def is_sell_role(level: KeyLevel) -> bool:
    """
    True if level is eligible for bearish rejection (sells).
    Requirements:
      - Active level (disrespect_count <= max_disrespects, not retired)
      - last_side == Side.BELOW (price was below the level)
      - Role is resistance or flipped resistance
    """
    if level.is_retired:
        return False
    if level.last_side != Side.BELOW:
        return False
    if level.current_role in (LevelRole.RESISTANCE, LevelRole.SBR, LevelRole.QM, LevelRole.FLIPPED_OCL):
        return True
    return False



def evaluate_rejection_multi(
    candle_low: float,
    candle_high: float,
    candle_close: float,
    active_levels: list[KeyLevel],
    timeframe: str,
    primary_selection_rule: str = "nearest_extreme",
    candle_time: Optional[pd.Timestamp] = None,
    age_limit_timeframes: Optional[list[str]] = None,
    max_age_months: Optional[int] = 6,
    max_age_months_disrespect_3: Optional[int] = 4,
    allow_unfresh: bool = True,
) -> tuple[
    Optional[tuple[KeyLevel, list[KeyLevel], float, float]],
    Optional[tuple[KeyLevel, list[KeyLevel], float, float]],
]:
    """
    Evaluate Section 5.3 Method 1 Rejection independently for Bullish and Bearish.
    Primary selection rules:
      - "nearest_extreme": rejected level nearest to mitigation extreme X (low for bull, high for bear).
        If two are equally near, prefers the fresh one.
      - "fresh_first": rejected level nearest to extreme among FRESH rejected levels,
        falling back to nearest overall if none is fresh.
    """
    matching_bullish: list[KeyLevel] = []
    matching_bearish: list[KeyLevel] = []

    if age_limit_timeframes is None:
        age_limit_timeframes = ["D"]

    apply_age_rules = (timeframe in age_limit_timeframes) and (candle_time is not None)
    max_age_dt = (candle_time - pd.DateOffset(months=max_age_months)) if (apply_age_rules and max_age_months) else None
    max_age_disp3_dt = (candle_time - pd.DateOffset(months=max_age_months_disrespect_3)) if (apply_age_rules and max_age_months_disrespect_3) else None

    for level in active_levels:
        if level.is_retired:
            continue
        if level.timeframe != timeframe:
            continue

        # Age rules: apply only to configured timeframes (e.g. Daily only)
        if apply_age_rules:
            if max_age_dt is not None and level.formation_time < max_age_dt:
                continue
            if level.disrespect_count == 3 and max_age_disp3_dt is not None and level.formation_time < max_age_disp3_dt:
                continue

        # Unfresh levels: if allow_unfresh is False (Owner's Rule), unfresh levels do not count
        if not allow_unfresh:
            if not level.fresh:
                continue
        else:
            rejs = getattr(level, "rejections_since_fresh", 0 if level.fresh else 1)
            if not level.fresh and rejs >= 2:
                continue
            if level.is_ocl and not level.fresh:
                continue

        if is_buy_role(level):
            if candle_low <= level.price + 1e-6 and candle_close > level.price:
                matching_bullish.append(level)

        if is_sell_role(level):
            if candle_high >= level.price - 1e-6 and candle_close < level.price:
                matching_bearish.append(level)

    res_bull = None
    if matching_bullish:
        if primary_selection_rule == "fresh_first":
            fresh_bull = [l for l in matching_bullish if l.fresh]
            pool_bull = fresh_bull if fresh_bull else matching_bullish
            primary_bull = min(pool_bull, key=lambda l: abs(l.price - candle_low))
        else:
            primary_bull = min(matching_bullish, key=lambda l: (abs(l.price - candle_low), 0 if l.fresh else 1))
        w_m = (primary_bull.price - candle_low) / primary_bull.price * 100.0
        c_m = (candle_close - primary_bull.price) / primary_bull.price * 100.0
        res_bull = (primary_bull, matching_bullish, w_m, c_m)

    res_bear = None
    if matching_bearish:
        if primary_selection_rule == "fresh_first":
            fresh_bear = [l for l in matching_bearish if l.fresh]
            pool_bear = fresh_bear if fresh_bear else matching_bearish
            primary_bear = min(pool_bear, key=lambda l: abs(l.price - candle_high))
        else:
            primary_bear = min(matching_bearish, key=lambda l: (abs(l.price - candle_high), 0 if l.fresh else 1))
        w_m = (candle_high - primary_bear.price) / primary_bear.price * 100.0
        c_m = (primary_bear.price - candle_close) / primary_bear.price * 100.0
        res_bear = (primary_bear, matching_bearish, w_m, c_m)

    return res_bull, res_bear


def evaluate_rejection(
    candle_low: float,
    candle_high: float,
    candle_close: float,
    active_levels: list[KeyLevel],
    timeframe: str,
) -> tuple[
    Optional[TriggerDirection],
    Optional[KeyLevel],
    list[KeyLevel],
    Optional[float],
    Optional[float],
]:
    """Single-direction wrapper for evaluate_rejection_multi."""
    res_bull, res_bear = evaluate_rejection_multi(candle_low, candle_high, candle_close, active_levels, timeframe)
    if res_bull and not res_bear:
        p, lvls, wm, cm = res_bull
        return TriggerDirection.BULLISH, p, lvls, wm, cm
    elif res_bear and not res_bull:
        p, lvls, wm, cm = res_bear
        return TriggerDirection.BEARISH, p, lvls, wm, cm
    elif res_bull and res_bear:
        p_bull, lvls_bull, wm_bull, cm_bull = res_bull
        p_bear, lvls_bear, wm_bear, cm_bear = res_bear
        if abs(p_bull.price - candle_low) <= abs(p_bear.price - candle_high):
            return TriggerDirection.BULLISH, p_bull, lvls_bull, wm_bull, cm_bull
        else:
            return TriggerDirection.BEARISH, p_bear, lvls_bear, wm_bear, cm_bear
    return None, None, [], None, None


def evaluate_sweep(
    candle_time: pd.Timestamp,
    candle_open: float,
    candle_high: float,
    candle_low: float,
    candle_close: float,
    ref_time: pd.Timestamp,
    ref_open: float,
    ref_high: float,
    ref_low: float,
    ref_close: float,
) -> tuple[
    Optional[TriggerDirection],
    Optional[SweepReference],
    Optional[float],
    Optional[float],
]:
    """
    Evaluate Section 5.4 Method 2 Liquidity Sweep on closed HTF candle c.

    Reference candle R is the immediately preceding candle on the same timeframe.
    Rules:
      - Color asymmetry: Only a bullish candle's high and a bearish candle's low count as liquidity.
        A bullish candle's low and a bearish candle's high are ignored.
        If R is a doji (close == open), it has no sweepable liquidity.
      - Bearish sweep:
          R is bullish (ref_close > ref_open),
          c.high > R.high,
          c.close < R.high (close back inside),
          c.low >= R.low (opposite side untouched).
          Swept level = R.high. Bias: bearish.
      - Bullish sweep:
          R is bearish (ref_close < ref_open),
          c.low < R.low,
          c.close > R.low (close back inside),
          c.high <= R.high (opposite side untouched).
          Swept level = R.low. Bias: bullish.
      - Close back inside is mandatory. Close beyond swept level is a break, not a sweep.

    Returns:
      (direction, sweep_reference, wick_margin_pct, close_margin_pct)
    """
    # 1. Bearish sweep check: R must be bullish
    if ref_close > ref_open:
        # Swept level is R.high
        if candle_high > ref_high and candle_close < ref_high and candle_low >= ref_low:
            wick_margin = (candle_high - ref_high) / ref_high * 100.0
            close_margin = (ref_high - candle_close) / ref_high * 100.0
            sweep_ref = SweepReference(
                open_time=ref_time,
                open=ref_open,
                high=ref_high,
                low=ref_low,
                close=ref_close,
                swept_level=ref_high,
                swept_side="high",
            )
            return TriggerDirection.BEARISH, sweep_ref, wick_margin, close_margin

    # 2. Bullish sweep check: R must be bearish
    elif ref_close < ref_open:
        # Swept level is R.low
        if candle_low < ref_low and candle_close > ref_low and candle_high <= ref_high:
            wick_margin = (ref_low - candle_low) / ref_low * 100.0
            close_margin = (candle_close - ref_low) / ref_low * 100.0
            sweep_ref = SweepReference(
                open_time=ref_time,
                open=ref_open,
                high=ref_high,
                low=ref_low,
                close=ref_close,
                swept_level=ref_low,
                swept_side="low",
            )
            return TriggerDirection.BULLISH, sweep_ref, wick_margin, close_margin

    return None, None, None, None


def detect_triggers(
    df: pd.DataFrame,
    timeframe: str,
    symbol: str,
    asset_class: str = "forex",
    lookback: Optional[int] = None,
    max_disrespects: int = 3,
    ocl_max_disrespects: int = 1,
    doji_body_threshold_pct: float = 0.0,
    marginal_thresholds: Optional[dict[str, float]] = None,
    unfresh_policy: str = "flag",
    primary_selection_rule: str = "nearest_extreme",
    age_limit_timeframes: Optional[list[str]] = None,
    max_age_months: Optional[int] = 6,
    max_age_months_disrespect_3: Optional[int] = 4,
    allow_unfresh: bool = True,
) -> list[HTFTrigger]:
    """
    Detect all HTF triggers across a series of closed candles as a pure function.

    Critical invariant:
      - When evaluating candle k, level states are evaluated as they were BEFORE
        candle k's close is applied.
      - Candle k's close is then applied to update existing levels, and new formations
        are detected.
      - Only active levels (disrespect_count <= max_disrespects) are used.
      - Retired levels are excluded.

    Returns:
      List of HTFTrigger objects detected in the candle series.
    """
    if len(df) < 2:
        return []

    triggers: list[HTFTrigger] = []
    active_levels: list[KeyLevel] = []
    n = len(df)

    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    times = df.index

    threshold_pct = get_marginal_threshold(asset_class, marginal_thresholds)

    for k in range(1, n):
        t_k = times[k]
        o_k = float(opens[k])
        h_k = float(highs[k])
        l_k = float(lows[k])
        c_k = float(closes[k])

        # Reference candle R is immediately preceding candle (k-1)
        t_r = times[k - 1]
        o_r = float(opens[k - 1])
        h_r = float(highs[k - 1])
        l_r = float(lows[k - 1])
        c_r = float(closes[k - 1])

        # Filter active levels formed before candle k within lookback
        if lookback is not None and lookback > 0:
            cutoff_idx = max(0, k - lookback)
            eval_levels = [lvl for lvl in active_levels if not lvl.is_retired and lvl.formation_index >= cutoff_idx]
        else:
            eval_levels = [lvl for lvl in active_levels if not lvl.is_retired]

        # ------------------------------------------------------------------
        # 1. Evaluate Rejection (Section 5.3) on pre-close active levels
        # ------------------------------------------------------------------
        res_bull, res_bear = evaluate_rejection_multi(
            candle_low=l_k,
            candle_high=h_k,
            candle_close=c_k,
            active_levels=eval_levels,
            timeframe=timeframe,
            primary_selection_rule=primary_selection_rule,
            candle_time=t_k,
            age_limit_timeframes=age_limit_timeframes,
            max_age_months=max_age_months,
            max_age_months_disrespect_3=max_age_months_disrespect_3,
            allow_unfresh=allow_unfresh,
        )

        # ------------------------------------------------------------------
        # 2. Evaluate Liquidity Sweep (Section 5.4)
        # ------------------------------------------------------------------
        sweep_dir, sweep_ref, s_wick_m, s_close_m = evaluate_sweep(
            candle_time=t_k,
            candle_open=o_k,
            candle_high=h_k,
            candle_low=l_k,
            candle_close=c_k,
            ref_time=t_r,
            ref_open=o_r,
            ref_high=h_r,
            ref_low=l_r,
            ref_close=c_r,
        )

        # ------------------------------------------------------------------
        # 3. Generate Bullish Triggers
        # ------------------------------------------------------------------
        if res_bull is not None and sweep_dir == TriggerDirection.BULLISH:
            prim_lvl, all_lvls, r_wick_m, r_close_m = res_bull
            extreme = l_k
            margins_list = [r_wick_m, r_close_m, s_wick_m, s_close_m]
            min_m = min(margins_list)
            is_marg = min_m < threshold_pct
            margins = TriggerPassMargins(
                rejection_wick_margin_pct=r_wick_m,
                rejection_close_margin_pct=r_close_m,
                sweep_wick_margin_pct=s_wick_m,
                sweep_close_margin_pct=s_close_m,
                min_margin_pct=min_m,
                threshold_pct=threshold_pct,
                is_marginal=is_marg,
            )
            is_low_prob = bool(prim_lvl is not None and not prim_lvl.fresh and unfresh_policy == "flag")
            triggers.append(HTFTrigger(
                symbol=symbol,
                timeframe=timeframe,
                direction=TriggerDirection.BULLISH,
                method=TriggerMethod.METHOD_3,
                trigger_candle_time=t_k,
                trigger_candle_open=o_k,
                trigger_candle_high=h_k,
                trigger_candle_low=l_k,
                trigger_candle_close=c_k,
                mitigation_extreme=extreme,
                primary_level=copy.copy(prim_lvl) if prim_lvl else None,
                all_rejected_levels=[copy.copy(l) for l in all_lvls],
                sweep_reference=sweep_ref,
                margins=margins,
                is_low_probability=is_low_prob,
            ))
        elif res_bull is not None:
            prim_lvl, all_lvls, r_wick_m, r_close_m = res_bull
            extreme = l_k
            margins_list = [r_wick_m, r_close_m]
            min_m = min(margins_list)
            is_marg = min_m < threshold_pct
            margins = TriggerPassMargins(
                rejection_wick_margin_pct=r_wick_m,
                rejection_close_margin_pct=r_close_m,
                min_margin_pct=min_m,
                threshold_pct=threshold_pct,
                is_marginal=is_marg,
            )
            is_low_prob = bool(prim_lvl is not None and not prim_lvl.fresh and unfresh_policy == "flag")
            triggers.append(HTFTrigger(
                symbol=symbol,
                timeframe=timeframe,
                direction=TriggerDirection.BULLISH,
                method=TriggerMethod.METHOD_1,
                trigger_candle_time=t_k,
                trigger_candle_open=o_k,
                trigger_candle_high=h_k,
                trigger_candle_low=l_k,
                trigger_candle_close=c_k,
                mitigation_extreme=extreme,
                primary_level=copy.copy(prim_lvl) if prim_lvl else None,
                all_rejected_levels=[copy.copy(l) for l in all_lvls],
                sweep_reference=None,
                margins=margins,
                is_low_probability=is_low_prob,
            ))
        elif sweep_dir == TriggerDirection.BULLISH:
            extreme = l_k
            margins_list = [s_wick_m, s_close_m]
            min_m = min(margins_list)
            is_marg = min_m < threshold_pct
            margins = TriggerPassMargins(
                sweep_wick_margin_pct=s_wick_m,
                sweep_close_margin_pct=s_close_m,
                min_margin_pct=min_m,
                threshold_pct=threshold_pct,
                is_marginal=is_marg,
            )
            triggers.append(HTFTrigger(
                symbol=symbol,
                timeframe=timeframe,
                direction=TriggerDirection.BULLISH,
                method=TriggerMethod.METHOD_2,
                trigger_candle_time=t_k,
                trigger_candle_open=o_k,
                trigger_candle_high=h_k,
                trigger_candle_low=l_k,
                trigger_candle_close=c_k,
                mitigation_extreme=extreme,
                primary_level=None,
                all_rejected_levels=[],
                sweep_reference=sweep_ref,
                margins=margins,
                is_low_probability=False,
            ))

        # ------------------------------------------------------------------
        # 4. Generate Bearish Triggers
        # ------------------------------------------------------------------
        if res_bear is not None and sweep_dir == TriggerDirection.BEARISH:
            prim_lvl, all_lvls, r_wick_m, r_close_m = res_bear
            extreme = h_k
            margins_list = [r_wick_m, r_close_m, s_wick_m, s_close_m]
            min_m = min(margins_list)
            is_marg = min_m < threshold_pct
            margins = TriggerPassMargins(
                rejection_wick_margin_pct=r_wick_m,
                rejection_close_margin_pct=r_close_m,
                sweep_wick_margin_pct=s_wick_m,
                sweep_close_margin_pct=s_close_m,
                min_margin_pct=min_m,
                threshold_pct=threshold_pct,
                is_marginal=is_marg,
            )
            is_low_prob = bool(prim_lvl is not None and not prim_lvl.fresh and unfresh_policy == "flag")
            triggers.append(HTFTrigger(
                symbol=symbol,
                timeframe=timeframe,
                direction=TriggerDirection.BEARISH,
                method=TriggerMethod.METHOD_3,
                trigger_candle_time=t_k,
                trigger_candle_open=o_k,
                trigger_candle_high=h_k,
                trigger_candle_low=l_k,
                trigger_candle_close=c_k,
                mitigation_extreme=extreme,
                primary_level=copy.copy(prim_lvl) if prim_lvl else None,
                all_rejected_levels=[copy.copy(l) for l in all_lvls],
                sweep_reference=sweep_ref,
                margins=margins,
                is_low_probability=is_low_prob,
            ))
        elif res_bear is not None:
            prim_lvl, all_lvls, r_wick_m, r_close_m = res_bear
            extreme = h_k
            margins_list = [r_wick_m, r_close_m]
            min_m = min(margins_list)
            is_marg = min_m < threshold_pct
            margins = TriggerPassMargins(
                rejection_wick_margin_pct=r_wick_m,
                rejection_close_margin_pct=r_close_m,
                min_margin_pct=min_m,
                threshold_pct=threshold_pct,
                is_marginal=is_marg,
            )
            is_low_prob = bool(prim_lvl is not None and not prim_lvl.fresh and unfresh_policy == "flag")
            triggers.append(HTFTrigger(
                symbol=symbol,
                timeframe=timeframe,
                direction=TriggerDirection.BEARISH,
                method=TriggerMethod.METHOD_1,
                trigger_candle_time=t_k,
                trigger_candle_open=o_k,
                trigger_candle_high=h_k,
                trigger_candle_low=l_k,
                trigger_candle_close=c_k,
                mitigation_extreme=extreme,
                primary_level=copy.copy(prim_lvl) if prim_lvl else None,
                all_rejected_levels=[copy.copy(l) for l in all_lvls],
                sweep_reference=None,
                margins=margins,
                is_low_probability=is_low_prob,
            ))
        elif sweep_dir == TriggerDirection.BEARISH:
            extreme = h_k
            margins_list = [s_wick_m, s_close_m]
            min_m = min(margins_list)
            is_marg = min_m < threshold_pct
            margins = TriggerPassMargins(
                sweep_wick_margin_pct=s_wick_m,
                sweep_close_margin_pct=s_close_m,
                min_margin_pct=min_m,
                threshold_pct=threshold_pct,
                is_marginal=is_marg,
            )
            triggers.append(HTFTrigger(
                symbol=symbol,
                timeframe=timeframe,
                direction=TriggerDirection.BEARISH,
                method=TriggerMethod.METHOD_2,
                trigger_candle_time=t_k,
                trigger_candle_open=o_k,
                trigger_candle_high=h_k,
                trigger_candle_low=l_k,
                trigger_candle_close=c_k,
                mitigation_extreme=extreme,
                primary_level=None,
                all_rejected_levels=[],
                sweep_reference=sweep_ref,
                margins=margins,
                is_low_probability=False,
            ))

        # ------------------------------------------------------------------
        # 4. Update Level States with candle k (high, low, close)
        # ------------------------------------------------------------------
        for level in active_levels:
            apply_candle_to_level(level, candle_close=c_k, candle_high=h_k, candle_low=l_k, max_disrespects=max_disrespects, ocl_max_disrespects=ocl_max_disrespects)

        # ------------------------------------------------------------------
        # 5. Detect new formation (k-1, k) completing at candle k
        # ------------------------------------------------------------------
        formation = detect_formation(
            open_i=o_r,
            close_i=c_r,
            open_next=o_k,
            close_next=c_k,
            doji_body_threshold_pct=doji_body_threshold_pct,
        )

        if formation is not None:
            kind, role, side = formation
            level_price = float(c_r)  # close(i)
            sym_prefix = f"{symbol}_" if symbol else ""
            level_id = f"{sym_prefix}{timeframe}_{kind.value}_{t_r.strftime('%Y%m%d%H%M')}_{level_price:.5f}"

            new_level = KeyLevel(
                id=level_id,
                timeframe=timeframe,
                kind=kind,
                price=level_price,
                formation_time=t_r,
                formation_confirmed_time=t_k,
                formation_index=k - 1,
                disrespect_count=0,
                last_side=side,
                current_role=role,
                symbol=symbol,
                last_contact="none",
                fresh=True,
            )
            active_levels.append(new_level)

    return triggers
