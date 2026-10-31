"""
slk_bot/strategy/setups.py
--------------------------
Phase 3: Setup Detection, Confirmation, Lifecycle, and Alerts (Sections 5.6 to 5.9).

Pure functions and dataclasses for:
  - LTF mitigation candle identification
  - External breakout level (LX) and Internal breakout levels
  - Setup lifecycle: WATCHING, INTERNAL_CONFIRMED, EXTERNAL_CONFIRMED, INVALIDATED, EXPIRED
  - Alert generation and timing rules
"""

from __future__ import annotations

import copy
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
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
from slk_bot.strategy.levels import detect_formation


class SetupState(str, Enum):
    WATCHING = "WATCHING"
    INTERNAL_CONFIRMED = "INTERNAL_CONFIRMED"
    EXTERNAL_CONFIRMED = "EXTERNAL_CONFIRMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class BreakoutType(str, Enum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


@dataclass
class SetupTimeline:
    """Timeline milestones of an SLK setup."""
    trigger_time: pd.Timestamp           # UTC open_time of HTF trigger candle
    trigger_close_time: pd.Timestamp     # UTC close_time of HTF trigger candle
    extreme_candle_time: pd.Timestamp    # UTC open_time of LTF candle holding X
    extreme_candle_index: int
    extreme_price_x: float
    lx_level: Optional[KeyLevel] = None  # External level
    internal_levels: list[KeyLevel] = field(default_factory=list)
    internal_breakout_candle_time: Optional[pd.Timestamp] = None
    internal_broken_level: Optional[KeyLevel] = None
    internal_alert_time: Optional[pd.Timestamp] = None
    external_breakout_candle_time: Optional[pd.Timestamp] = None
    external_broken_level: Optional[KeyLevel] = None
    external_alert_time: Optional[pd.Timestamp] = None
    invalidation_time: Optional[pd.Timestamp] = None
    expiry_time: Optional[pd.Timestamp] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_time": self.trigger_time.strftime("%Y-%m-%d %H:%M UTC"),
            "trigger_close_time": self.trigger_close_time.strftime("%Y-%m-%d %H:%M UTC"),
            "extreme_candle_time": self.extreme_candle_time.strftime("%Y-%m-%d %H:%M UTC"),
            "extreme_candle_index": self.extreme_candle_index,
            "extreme_price_x": self.extreme_price_x,
            "lx_level": self.lx_level.to_dict() if self.lx_level else None,
            "internal_levels": [l.to_dict() for l in self.internal_levels],
            "internal_breakout_candle_time": self.internal_breakout_candle_time.strftime("%Y-%m-%d %H:%M UTC") if self.internal_breakout_candle_time else None,
            "internal_broken_level": self.internal_broken_level.to_dict() if self.internal_broken_level else None,
            "internal_alert_time": self.internal_alert_time.strftime("%Y-%m-%d %H:%M UTC") if self.internal_alert_time else None,
            "external_breakout_candle_time": self.external_breakout_candle_time.strftime("%Y-%m-%d %H:%M UTC") if self.external_breakout_candle_time else None,
            "external_broken_level": self.external_broken_level.to_dict() if self.external_broken_level else None,
            "external_alert_time": self.external_alert_time.strftime("%Y-%m-%d %H:%M UTC") if self.external_alert_time else None,
            "invalidation_time": self.invalidation_time.strftime("%Y-%m-%d %H:%M UTC") if self.invalidation_time else None,
            "expiry_time": self.expiry_time.strftime("%Y-%m-%d %H:%M UTC") if self.expiry_time else None,
        }


@dataclass
class SLKAlert:
    """Alert generated upon setup confirmation."""
    dedupe_key: str
    symbol: str
    htf: str
    ltf: str
    direction: TriggerDirection
    method: TriggerMethod
    breakout_type: BreakoutType
    alert_time: pd.Timestamp
    breakout_candle_time: pd.Timestamp
    htf_level: Optional[KeyLevel]
    mitigation_extreme_x: float
    broken_level: KeyLevel
    is_low_probability: bool
    is_marginal: bool
    is_push_suppressed: bool = False
    next_day_rule: bool = False
    rejection_candle_time: Optional[pd.Timestamp] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dedupe_key": self.dedupe_key,
            "symbol": self.symbol,
            "htf": self.htf,
            "ltf": self.ltf,
            "direction": self.direction.value,
            "method": int(self.method),
            "breakout_type": self.breakout_type.value,
            "alert_time": self.alert_time.strftime("%Y-%m-%d %H:%M UTC"),
            "breakout_candle_time": self.breakout_candle_time.strftime("%Y-%m-%d %H:%M UTC"),
            "htf_level": self.htf_level.to_dict() if self.htf_level else None,
            "mitigation_extreme_x": self.mitigation_extreme_x,
            "broken_level": self.broken_level.to_dict(),
            "is_low_probability": self.is_low_probability,
            "is_marginal": self.is_marginal,
            "is_push_suppressed": self.is_push_suppressed,
            "next_day_rule": self.next_day_rule,
        }


@dataclass
class SLKSetup:
    """An SLK setup tracking through its lifecycle."""
    id: str
    symbol: str
    htf: str
    ltf: str
    trigger: HTFTrigger
    state: SetupState
    timeline: SetupTimeline
    alerts: list[SLKAlert] = field(default_factory=list)

    @property
    def is_confirmed(self) -> bool:
        return self.state in (SetupState.INTERNAL_CONFIRMED, SetupState.EXTERNAL_CONFIRMED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "htf": self.htf,
            "ltf": self.ltf,
            "trigger": self.trigger.to_dict(),
            "state": self.state.value,
            "timeline": self.timeline.to_dict(),
            "alerts": [a.to_dict() for a in self.alerts],
        }


def find_mitigation_candle(
    ltf_df: pd.DataFrame,
    trigger_start: pd.Timestamp,
    trigger_end: pd.Timestamp,
    direction: TriggerDirection,
    extreme_x: float,
) -> tuple[int, pd.Timestamp]:
    """
    Find the LTF candle inside the trigger window [trigger_start, trigger_end)
    holding the extreme X (earliest if tied).
    """
    sub = ltf_df[(ltf_df.index >= trigger_start) & (ltf_df.index < trigger_end)]
    if sub.empty:
        # Fallback to closest available candle
        sub = ltf_df[ltf_df.index >= trigger_start]
        if sub.empty:
            sub = ltf_df

    if direction == TriggerDirection.BULLISH:
        # Lowest low
        min_low = sub["low"].min()
        # Find earliest candle with low == min_low (or within float eps)
        matches = sub[abs(sub["low"] - min_low) < 1e-6]
        ext_time = matches.index[0]
    else:
        # Highest high
        max_high = sub["high"].max()
        matches = sub[abs(sub["high"] - max_high) < 1e-6]
        ext_time = matches.index[0]

    # Get integer location in full ltf_df
    loc = ltf_df.index.get_loc(ext_time)
    if isinstance(loc, slice):
        loc = loc.start
    return int(loc), ext_time


def detect_ltf_levels(
    ltf_df: pd.DataFrame,
    symbol: str,
    ltf_tf: str,
    doji_body_threshold_pct: float = 0.0,
) -> list[tuple[int, KeyLevel]]:
    """
    Detect all A-shapes and V-shapes on the LTF series.
    Returns list of (formation_confirmation_index, KeyLevel).
    """
    levels: list[tuple[int, KeyLevel]] = []
    opens = ltf_df["open"].values
    closes = ltf_df["close"].values
    times = ltf_df.index
    n = len(ltf_df)

    for k in range(1, n):
        formation = detect_formation(
            open_i=opens[k - 1],
            close_i=closes[k - 1],
            open_next=opens[k],
            close_next=closes[k],
            doji_body_threshold_pct=doji_body_threshold_pct,
        )
        if formation is not None:
            kind, role, side = formation
            # We only track A-shapes and V-shapes for breakouts
            if kind not in (LevelKind.A_SHAPE, LevelKind.V_SHAPE):
                continue
            lvl_price = float(closes[k - 1])
            t_form = times[k - 1]
            t_conf = times[k]
            lvl_id = f"{symbol}_{ltf_tf}_{kind.value}_{t_form.strftime('%Y%m%d%H%M')}_{lvl_price:.5f}"
            lvl = KeyLevel(
                id=lvl_id,
                symbol=symbol,
                timeframe=ltf_tf,
                kind=kind,
                price=lvl_price,
                formation_time=t_form,
                formation_confirmed_time=t_conf,
                formation_index=k - 1,
                current_role=role,
                disrespect_count=0,
                last_side=side,
            )
            # Confirmed at index k
            levels.append((k, lvl))
    return levels


def get_candle_close_time(t_open: pd.Timestamp, timeframe: str) -> pd.Timestamp:
    """
    Return the nominal close time for a candle.
    - Weekly: opens Sunday 17:00 NY, closes Friday 17:00 NY (5 days = 120 hours).
    - Daily: runs for 24 hours (closes 17:00 NY next day).
    - H4: runs for 4 hours.
    """
    if timeframe == "W":
        return t_open + pd.Timedelta(days=5)
    elif timeframe == "D":
        return t_open + pd.Timedelta(days=1)
    elif timeframe == "H4":
        return t_open + pd.Timedelta(hours=4)
    elif timeframe == "H1":
        return t_open + pd.Timedelta(hours=1)
    return t_open + pd.Timedelta(hours=4)


def evaluate_setup_lifecycle(
    trigger: HTFTrigger,
    htf_df: pd.DataFrame,
    ltf_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    symbol: str,
    htf_tf: str,
    ltf_tf: str,
    unfresh_alerts: bool = False,
    expiry_daily_trigger_candles: int = 3,
    expiry_weekly_trigger_candles: int = 14,
    doji_body_threshold_pct: float = 0.0,
    all_ltf_levels: Optional[list[tuple[int, KeyLevel]]] = None,
    external_ref_point: str = "first_touch",
    enable_invalidation: bool = True,
) -> SLKSetup:
    """
    Simulate the full lifecycle of a setup from its HTF trigger.
    """
    t_trig = trigger.trigger_candle_time
    t_trig_close = get_candle_close_time(t_trig, htf_tf)

    direction = trigger.direction
    extreme_x = trigger.mitigation_extreme

    # 1. Mitigation candle on LTF
    ext_idx, ext_time = find_mitigation_candle(
        ltf_df=ltf_df,
        trigger_start=t_trig,
        trigger_end=t_trig_close,
        direction=direction,
        extreme_x=extreme_x,
    )

    # Determine reference index for external turning points:
    # "first_touch": last LTF turning point formed strictly before the first LTF candle touching key level
    # "extreme": last LTF turning point formed before the extreme candle
    ref_idx = ext_idx
    touch_idx: Optional[int] = None
    if external_ref_point == "first_touch" and trigger.primary_level:
        kl_p = trigger.primary_level.price
        sub = ltf_df[(ltf_df.index >= t_trig) & (ltf_df.index < t_trig_close)]
        touches = sub[(sub["low"] <= kl_p + 1e-6) & (sub["high"] >= kl_p - 1e-6)]
        if not touches.empty:
            loc = ltf_df.index.get_loc(touches.index[0])
            if isinstance(loc, slice):
                loc = loc.start
            touch_idx = int(loc)
            ref_idx = touch_idx

    # 2. Detect all LTF levels (if not precomputed)
    if all_ltf_levels is None:
        all_ltf_levels = detect_ltf_levels(ltf_df, symbol, ltf_tf, doji_body_threshold_pct)

    # Pre-extract numpy arrays for fast vector slicing
    ltf_closes = ltf_df["close"].values
    ltf_highs = ltf_df["high"].values if "high" in ltf_df.columns else ltf_closes
    ltf_lows = ltf_df["low"].values if "low" in ltf_df.columns else ltf_closes
    ltf_times = ltf_df.index

    # 3. Find External Level LX
    # First-touch rule fixes:
    # (a) the last turning point must have been formed strictly BEFORE the first-touch candle (use <, not <=)
    # (b) check that the turning point was intact only through the candle before the first-touch candle,
    #     so the touch candle itself may be the breakout candle (reference #32 SPX500 has breakout 7710.6 on the touch candle).
    limit_idx = touch_idx if (external_ref_point == "first_touch" and touch_idx is not None) else ext_idx
    lx: Optional[KeyLevel] = None
    if direction == TriggerDirection.BULLISH:
        if external_ref_point == "first_touch" and touch_idx is not None:
            candidates = [
                lvl for conf_idx, lvl in all_ltf_levels
                if lvl.kind == LevelKind.A_SHAPE and lvl.formation_index < touch_idx and lvl.price > extreme_x
            ]
        else:
            candidates = [
                lvl for conf_idx, lvl in all_ltf_levels
                if lvl.kind == LevelKind.A_SHAPE and lvl.formation_index <= ext_idx and lvl.price > extreme_x
            ]
        valid_lx = []
        for c in candidates:
            c_conf_idx = c.formation_index + 1
            closes_between = ltf_closes[c_conf_idx : limit_idx]
            if not (closes_between > c.price).any():
                valid_lx.append(c)
        if valid_lx:
            valid_lx.sort(key=lambda l: l.formation_index)
            lx = valid_lx[-1]

    else:
        if external_ref_point == "first_touch" and touch_idx is not None:
            candidates = [
                lvl for conf_idx, lvl in all_ltf_levels
                if lvl.kind == LevelKind.V_SHAPE and lvl.formation_index < touch_idx and lvl.price < extreme_x
            ]
        else:
            candidates = [
                lvl for conf_idx, lvl in all_ltf_levels
                if lvl.kind == LevelKind.V_SHAPE and lvl.formation_index <= ext_idx and lvl.price < extreme_x
            ]
        valid_lx = []
        for c in candidates:
            c_conf_idx = c.formation_index + 1
            closes_between = ltf_closes[c_conf_idx : limit_idx]
            if not (closes_between < c.price).any():
                valid_lx.append(c)
        if valid_lx:
            valid_lx.sort(key=lambda l: l.formation_index)
            lx = valid_lx[-1]

    # 4. Compute Expiry Deadline
    # Daily trigger: 3 trading days after t_trig_close
    # Weekly trigger: 14 trading days after t_trig_close
    expiry_limit = expiry_weekly_trigger_candles if htf_tf == "W" else expiry_daily_trigger_candles
    post_daily = daily_df[daily_df.index >= t_trig_close]
    if len(post_daily) >= expiry_limit:
        t_expiry = post_daily.index[expiry_limit - 1] + pd.Timedelta(days=1)
    else:
        # Extrapolate
        t_expiry = t_trig_close + pd.Timedelta(days=expiry_limit * 1.5)

    timeline = SetupTimeline(
        trigger_time=t_trig,
        trigger_close_time=t_trig_close,
        extreme_candle_time=ext_time,
        extreme_candle_index=ext_idx,
        extreme_price_x=extreme_x,
        lx_level=copy.copy(lx) if lx else None,
        expiry_time=t_expiry,
    )

    setup_id = f"{symbol}_{htf_tf}_{direction.value}_{t_trig.strftime('%Y%m%d%H%M')}"
    state = SetupState.WATCHING
    alerts: list[SLKAlert] = []

    # 5. Walk forward LTF candles after the extreme candle
    n_ltf = len(ltf_df)
    internal_levels: list[KeyLevel] = []

    # Fast O(1) lookup of levels confirmed at candle k
    levels_by_conf: dict[int, list[KeyLevel]] = defaultdict(list)
    for conf_idx, lvl in all_ltf_levels:
        if conf_idx > ext_idx and lvl.formation_index >= ext_idx:
            levels_by_conf[conf_idx].append(lvl)

    start_k = touch_idx if (external_ref_point == "first_touch" and touch_idx is not None) else ext_idx + 1
    for k in range(start_k, n_ltf):
        t_k = ltf_times[k]
        c_k = float(ltf_closes[k])
        h_k = float(ltf_highs[k])
        l_k = float(ltf_lows[k])

        t_k_close = get_candle_close_time(t_k, ltf_tf)

        # Check Expiry
        if t_k >= t_expiry and state == SetupState.WATCHING:
            state = SetupState.EXPIRED
            break

        # Check Invalidation: close beyond X in opposite direction
        if enable_invalidation:
            if direction == TriggerDirection.BULLISH and c_k < extreme_x:
                state = SetupState.INVALIDATED
                timeline.invalidation_time = t_k
                break
            elif direction == TriggerDirection.BEARISH and c_k > extreme_x:
                state = SetupState.INVALIDATED
                timeline.invalidation_time = t_k
                break

        # Check new internal formations confirmed at candle k
        for lvl in levels_by_conf.get(k, []):
            if direction == TriggerDirection.BULLISH and lvl.kind == LevelKind.A_SHAPE and lvl.price > extreme_x:
                if lvl not in internal_levels:
                    internal_levels.append(lvl)
                    timeline.internal_levels.append(copy.copy(lvl))
            elif direction == TriggerDirection.BEARISH and lvl.kind == LevelKind.V_SHAPE and lvl.price < extreme_x:
                if lvl not in internal_levels:
                    internal_levels.append(lvl)
                    timeline.internal_levels.append(copy.copy(lvl))

        # Check Breakout Confirmation on candle k
        broke_lx = False
        if lx is not None:
            if direction == TriggerDirection.BULLISH and c_k > lx.price:
                broke_lx = True
            elif direction == TriggerDirection.BEARISH and c_k < lx.price:
                broke_lx = True

        broke_internal: Optional[KeyLevel] = None
        for ilvl in internal_levels:
            if direction == TriggerDirection.BULLISH and c_k > ilvl.price:
                broke_internal = ilvl
                break
            elif direction == TriggerDirection.BEARISH and c_k < ilvl.price:
                broke_internal = ilvl
                break

        # External breakout takes priority if both broken on same candle
        if broke_lx:
            # External Confirmed!
            timeline.external_breakout_candle_time = t_k
            timeline.external_broken_level = copy.copy(lx)
            # Alert time rule: max(t_k_close, t_trig_close)
            alert_time = max(t_k_close, t_trig_close)
            timeline.external_alert_time = alert_time

            is_suppressed = bool(trigger.is_low_probability and not unfresh_alerts)
            dedupe_key = f"{symbol}_{htf_tf}_{t_trig.strftime('%Y%m%d%H%M')}_{direction.value}_{int(trigger.method)}_EXTERNAL"

            alert = SLKAlert(
                dedupe_key=dedupe_key,
                symbol=symbol,
                htf=htf_tf,
                ltf=ltf_tf,
                direction=direction,
                method=trigger.method,
                breakout_type=BreakoutType.EXTERNAL,
                alert_time=alert_time,
                breakout_candle_time=t_k,
                htf_level=copy.copy(trigger.primary_level) if trigger.primary_level else None,
                mitigation_extreme_x=extreme_x,
                broken_level=copy.copy(lx),
                is_low_probability=trigger.is_low_probability,
                is_marginal=bool(trigger.margins and trigger.margins.is_marginal),
                is_push_suppressed=is_suppressed,
                next_day_rule=bool(t_k >= t_trig_close),
                rejection_candle_time=t_trig,
            )
            alerts.append(alert)
            state = SetupState.EXTERNAL_CONFIRMED
            # External confirmation is terminal
            break

        elif broke_internal is not None and state == SetupState.WATCHING:
            # Internal Confirmed!
            timeline.internal_breakout_candle_time = t_k
            timeline.internal_broken_level = copy.copy(broke_internal)
            alert_time = max(t_k_close, t_trig_close)
            timeline.internal_alert_time = alert_time

            is_suppressed = bool(trigger.is_low_probability and not unfresh_alerts)
            dedupe_key = f"{symbol}_{htf_tf}_{t_trig.strftime('%Y%m%d%H%M')}_{direction.value}_{int(trigger.method)}_INTERNAL"

            alert = SLKAlert(
                dedupe_key=dedupe_key,
                symbol=symbol,
                htf=htf_tf,
                ltf=ltf_tf,
                direction=direction,
                method=trigger.method,
                breakout_type=BreakoutType.INTERNAL,
                alert_time=alert_time,
                breakout_candle_time=t_k,
                htf_level=copy.copy(trigger.primary_level) if trigger.primary_level else None,
                mitigation_extreme_x=extreme_x,
                broken_level=copy.copy(broke_internal),
                is_low_probability=trigger.is_low_probability,
                is_marginal=bool(trigger.margins and trigger.margins.is_marginal),
                is_push_suppressed=is_suppressed,
                next_day_rule=bool(t_k >= t_trig_close),
                rejection_candle_time=t_trig,
            )
            alerts.append(alert)
            state = SetupState.INTERNAL_CONFIRMED
            # Can still upgrade to EXTERNAL on a later candle! Continue loop.

    return SLKSetup(
        id=setup_id,
        symbol=symbol,
        htf=htf_tf,
        ltf=ltf_tf,
        trigger=trigger,
        state=state,
        timeline=timeline,
        alerts=alerts,
    )
