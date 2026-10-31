"""
slk_bot/strategy/models.py
--------------------------
Data structures for key levels and strategy state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional
import pandas as pd


class LevelKind(str, Enum):
    A_SHAPE = "A"
    V_SHAPE = "V"
    BULLISH_OCL = "bullish_OCL"
    BEARISH_OCL = "bearish_OCL"


class LevelRole(str, Enum):
    RESISTANCE = "resistance"
    SUPPORT = "support"
    RBS = "RBS"                  # Resistance Broken becomes Support (used for buys)
    SBR = "SBR"                  # Support Broken becomes Resistance (used for sells)
    QM = "QM"                    # Quasimodo (used for both buys and sells)
    FLIPPED_OCL = "flipped_OCL"  # OCL flipped to opposite role
    RETIRED = "retired"          # Disrespected > max_disrespects (default: > 3)


class Side(str, Enum):
    ABOVE = "above"
    BELOW = "below"


@dataclass
class KeyLevel:
    """
    Representation of a detected key level (Section 5.1 & 5.2).

    Attributes:
      id: Unique identifier, e.g. "EURUSD_D_A_20261014_1.12212"
      timeframe: Timeframe code ("W", "D", "H4")
      kind: LevelKind ("A", "V", "bullish_OCL", "bearish_OCL")
      price: Level price (close of candle i)
      formation_time: UTC open_time of candle i (the joint/close candle)
      formation_confirmed_time: UTC open_time of candle i+1 (the confirmation candle)
      formation_index: Integer position of candle i in the candle series
      disrespect_count: Number of times a candle has closed on the opposite side
      last_side: Side ("above" or "below") price was on after the last candle
      current_role: Current role (resistance, support, RBS, SBR, QM, flipped_OCL, retired)
      symbol: Optional symbol name, e.g. "EURUSD"
    """
    id: str
    timeframe: str
    kind: LevelKind
    price: float
    formation_time: pd.Timestamp
    formation_confirmed_time: pd.Timestamp
    formation_index: int
    disrespect_count: int
    last_side: Side
    current_role: LevelRole
    symbol: Optional[str] = None
    last_contact: str = "none"  # "none", "rejection", "disrespect"
    fresh: bool = True
    rejections_since_fresh: int = 0

    @property
    def is_unbroken(self) -> bool:
        """True if the level has not been breached by a candle close (disrespect_count == 0)."""
        return self.disrespect_count == 0

    @property
    def is_retired(self) -> bool:
        """True if the level has been retired."""
        return self.current_role == LevelRole.RETIRED

    @property
    def is_ocl(self) -> bool:
        """True if the level is a Bullish or Bearish OCL."""
        return self.kind in (LevelKind.BULLISH_OCL, LevelKind.BEARISH_OCL)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "kind": self.kind.value,
            "price": self.price,
            "formation_time": self.formation_time.strftime("%Y-%m-%d %H:%M UTC"),
            "formation_confirmed_time": self.formation_confirmed_time.strftime("%Y-%m-%d %H:%M UTC"),
            "disrespect_count": self.disrespect_count,
            "last_side": self.last_side.value,
            "current_role": self.current_role.value,
            "is_unbroken": self.is_unbroken,
            "is_retired": self.is_retired,
            "last_contact": self.last_contact,
            "fresh": self.fresh,
            "rejections_since_fresh": self.rejections_since_fresh,
        }


class TriggerDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class TriggerMethod(int, Enum):
    METHOD_1 = 1  # Rejection off key level only
    METHOD_2 = 2  # Liquidity sweep only
    METHOD_3 = 3  # Combined rejection + sweep


@dataclass
class SweepReference:
    """Details of the reference candle R swept by trigger candle c."""
    open_time: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    swept_level: float
    swept_side: str  # "high" or "low"

    def to_dict(self) -> dict[str, Any]:
        return {
            "open_time": self.open_time.strftime("%Y-%m-%d %H:%M UTC"),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "swept_level": self.swept_level,
            "swept_side": self.swept_side,
        }


@dataclass
class TriggerPassMargins:
    """
    Margins by which conditions were met (as % of price).
    Includes the MARGINAL flag evaluated against the asset class threshold.
    """
    rejection_wick_margin_pct: Optional[float] = None
    rejection_close_margin_pct: Optional[float] = None
    sweep_wick_margin_pct: Optional[float] = None
    sweep_close_margin_pct: Optional[float] = None
    min_margin_pct: float = 0.0
    threshold_pct: float = 0.0
    is_marginal: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "rejection_wick_margin_pct": self.rejection_wick_margin_pct,
            "rejection_close_margin_pct": self.rejection_close_margin_pct,
            "sweep_wick_margin_pct": self.sweep_wick_margin_pct,
            "sweep_close_margin_pct": self.sweep_close_margin_pct,
            "min_margin_pct": self.min_margin_pct,
            "threshold_pct": self.threshold_pct,
            "is_marginal": self.is_marginal,
        }


@dataclass
class HTFTrigger:
    """
    High Timeframe Trigger (Sections 5.3, 5.4, 5.5).
    Evaluated on closed candles as a pure function.
    """
    symbol: str
    timeframe: str  # "W" or "D"
    direction: TriggerDirection
    method: TriggerMethod
    trigger_candle_time: pd.Timestamp  # UTC open_time of candle c
    trigger_candle_open: float
    trigger_candle_high: float
    trigger_candle_low: float
    trigger_candle_close: float
    mitigation_extreme: float          # X: c.low for bullish, c.high for bearish
    primary_level: Optional[KeyLevel] = None
    all_rejected_levels: list[KeyLevel] = None
    sweep_reference: Optional[SweepReference] = None
    margins: Optional[TriggerPassMargins] = None
    is_low_probability: bool = False
    primary_level_fresh: Optional[bool] = None
    primary_level_last_contact: Optional[str] = None

    def __post_init__(self):
        if self.all_rejected_levels is None:
            self.all_rejected_levels = []
        if self.primary_level:
            if self.primary_level_fresh is None:
                self.primary_level_fresh = self.primary_level.fresh
            if self.primary_level_last_contact is None:
                self.primary_level_last_contact = self.primary_level.last_contact

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction.value,
            "method": int(self.method),
            "trigger_candle_time": self.trigger_candle_time.strftime("%Y-%m-%d %H:%M UTC"),
            "trigger_candle_open": self.trigger_candle_open,
            "trigger_candle_high": self.trigger_candle_high,
            "trigger_candle_low": self.trigger_candle_low,
            "trigger_candle_close": self.trigger_candle_close,
            "mitigation_extreme": self.mitigation_extreme,
            "is_low_probability": self.is_low_probability,
            "primary_level_fresh": self.primary_level_fresh,
            "primary_level_last_contact": self.primary_level_last_contact,
            "primary_level": self.primary_level.to_dict() if self.primary_level else None,
            "all_rejected_levels": [l.to_dict() for l in self.all_rejected_levels],
            "sweep_reference": self.sweep_reference.to_dict() if self.sweep_reference else None,
            "margins": self.margins.to_dict() if self.margins else None,
        }


