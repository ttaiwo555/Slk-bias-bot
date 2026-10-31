"""
slk_bot/bot/formatter.py
------------------------
Telegram HTML message formatting for alerts, summaries, price alerts, and digests.
Implements Section 6 template plus:
- Freshness label: [FRESH] or [LOW PROBABILITY]
- MARGINAL flag: [MARGINAL]
- Opposite confirmed setup conflict line
- UTC timestamps throughout
- Instrument display_scale formatting
"""

from __future__ import annotations

from typing import Optional, Any
from pathlib import Path
import pandas as pd
import yaml

from slk_bot.strategy.models import TriggerDirection, TriggerMethod, KeyLevel
from slk_bot.strategy.setups import SLKAlert, SLKSetup, BreakoutType

ROOT = Path(__file__).resolve().parent.parent
INST_PATH = ROOT / "config" / "instruments.yaml"

# Load instrument configs for precision and display_scale
_INST_MAP: dict[str, dict[str, Any]] = {}
if INST_PATH.exists():
    with open(INST_PATH, "r", encoding="utf-8") as f:
        _data = yaml.safe_load(f)
        for inst in _data.get("instruments", []):
            _INST_MAP[inst["name"]] = inst


def get_display_scale(symbol: str) -> float:
    """Return the display_scale multiplier for the given symbol (default 1.0)."""
    inst = _INST_MAP.get(symbol, {})
    return float(inst.get("display_scale", 1.0))


def format_price(price: float, symbol: str) -> str:
    """
    Format price according to instrument display_scale and asset class precision.
    """
    scale = get_display_scale(symbol)
    scaled_price = price * scale

    inst = _INST_MAP.get(symbol, {})
    asset_class = inst.get("asset_class", "forex")

    if asset_class == "forex":
        if "JPY" in symbol:
            return f"{scaled_price:.3f}"
        return f"{scaled_price:.5f}"
    elif asset_class == "commodity":
        if symbol == "XAUUSD":
            return f"{scaled_price:.2f}"
        elif symbol == "XAGUSD":
            return f"{scaled_price:.3f}"
        elif symbol == "USOIL":
            return f"{scaled_price:.1f}"
        return f"{scaled_price:.3f}"
    elif asset_class == "index":
        return f"{scaled_price:.1f}"
    elif asset_class == "crypto":
        return f"{scaled_price:.2f}"
    return f"{scaled_price:.4f}"


def format_alert_message(
    alert: SLKAlert,
    opposite_setup: Optional[SLKSetup] = None,
    repeat_count: int = 1,
) -> str:
    """
    Format an SLK alert into the Section 6 Telegram HTML message.
    """
    sym = alert.symbol
    direction_str = alert.direction.value.upper()
    dir_emoji = "🟢" if alert.direction == TriggerDirection.BULLISH else "🔴"

    # Header
    htf_label = "Daily" if alert.htf == "D" else "Weekly"
    ltf_label = "H4" if alert.ltf == "H4" else "Daily"
    header = f"{dir_emoji} <b>SLK Alert: {sym} – {direction_str} ({htf_label})</b>"

    # Method & Flags
    m_name = {
        TriggerMethod.METHOD_1: "Method 1 (Rejection)",
        TriggerMethod.METHOD_2: "Method 2 (Sweep)",
        TriggerMethod.METHOD_3: "Method 3 (Combined)",
    }.get(alert.method, f"Method {int(alert.method)}")

    flags = []
    if alert.is_low_probability:
        flags.append("[LOW PROBABILITY]")
    else:
        flags.append("[FRESH]")

    if alert.is_marginal:
        flags.append("[MARGINAL]")

    flags_str = " " + " ".join(flags) if flags else ""
    method_line = f"<b>Method:</b> {m_name}{flags_str}"

    # Breakout line
    b_type = alert.breakout_type.value.upper()
    breakout_line = f"<b>Breakout:</b> {b_type} ({ltf_label})"

def format_candle_display(ts: pd.Timestamp, timeframe: str) -> str:
    """
    Format a candle's date/time according to TradingView convention:
    - Daily: named by the date TradingView shows on the daily chart (the date of the candle's
      close in New York time; e.g. candle opening Thu 21:00 UTC closes Fri 17:00 NY -> Fri 16 Oct 2026).
    - Weekly: named by the Monday of its trading week (e.g. Mon 12 Oct 2026).
    - H4: actual open date and time in WAT (e.g. Mon 12 Oct 06:00 WAT).
    """
    if pd.isna(ts) or ts is None:
        return ""
    if not isinstance(ts, pd.Timestamp):
        ts = pd.Timestamp(ts)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")

    if timeframe == "D":
        ts_ny = ts.tz_convert("America/New_York")
        close_ny = ts_ny + pd.Timedelta(hours=24) - pd.Timedelta(seconds=1)
        return f"{close_ny.strftime('%a')} {close_ny.day} {close_ny.strftime('%b %Y')}"

    elif timeframe == "W":
        ts_ny = ts.tz_convert("America/New_York")
        days_to_mon = (0 - ts_ny.weekday()) % 7
        mon_dt = ts_ny + pd.Timedelta(days=days_to_mon)
        return f"{mon_dt.strftime('%a')} {mon_dt.day} {mon_dt.strftime('%b %Y')}"

    elif timeframe == "H4":
        ts_wat = ts + pd.Timedelta(hours=1)
        return f"{ts_wat.strftime('%a')} {ts_wat.day} {ts_wat.strftime('%b %H:%M WAT')}"

    else:
        ts_wat = ts + pd.Timedelta(hours=1)
        return f"{ts_wat.strftime('%a')} {ts_wat.day} {ts_wat.strftime('%b %H:%M WAT')}"


def format_kl_formation(level: Any) -> str:
    """Format key level formation showing dates and colours of the two candles on level's timeframe."""
    if level is None:
        return ""
    tf = getattr(level, "timeframe", "D")
    t1_str = format_candle_display(level.formation_time, tf)
    t2_str = format_candle_display(level.formation_confirmed_time, tf)
    kind_val = level.kind.value if hasattr(level.kind, "value") else str(level.kind)
    if kind_val in ("A", "bearish_OCL"):
        c1_col, c2_col = "bullish", "bearish"
    else:
        c1_col, c2_col = "bearish", "bullish"
    return f"formed by {t1_str} ({c1_col}) and {t2_str} ({c2_col})"


def format_alert_message(
    alert: SLKAlert,
    opposite_setup: Optional[SLKSetup] = None,
    repeat_count: int = 1,
) -> str:
    """
    Format an SLK alert into the Telegram HTML message with actual dates, formation colors, and WAT times.
    """
    sym = alert.symbol
    direction_str = alert.direction.value.upper()
    dir_emoji = "🟢" if alert.direction == TriggerDirection.BULLISH else "🔴"

    # Header
    htf_label = "Daily" if alert.htf == "D" else "Weekly"
    ltf_label = "H4" if alert.ltf == "H4" else "Daily"
    header = f"{dir_emoji} <b>SLK Alert: {sym} – {direction_str} ({htf_label})</b>"

    # Method & Flags
    m_name = {
        TriggerMethod.METHOD_1: "Method 1 (Rejection)",
        TriggerMethod.METHOD_2: "Method 2 (Sweep)",
        TriggerMethod.METHOD_3: "Method 3 (Combined)",
    }.get(alert.method, f"Method {int(alert.method)}")

    flags = []
    if alert.is_low_probability:
        flags.append("[LOW PROBABILITY]")
    else:
        flags.append("[FRESH]")

    if alert.is_marginal:
        flags.append("[MARGINAL]")

    flags_str = " " + " ".join(flags) if flags else ""
    method_line = f"<b>Method:</b> {m_name}{flags_str}"

    # Breakout line
    b_type = alert.breakout_type.value.upper()
    breakout_line = f"<b>Breakout:</b> {b_type} ({ltf_label})"

    # Broken level
    bl = alert.broken_level
    bl_price = format_price(bl.price, sym)
    bl_desc = format_kl_formation(bl)
    broken_line = f"<b>Level Broken:</b> {bl.kind.value} @ {bl_price} ({bl_desc})"

    # Trigger Details
    ext_price = format_price(alert.mitigation_extreme_x, sym)
    trigger_lines = [
        "<b>Trigger Details:</b>",
        f"• Mitigation Extreme (X): <code>{ext_price}</code>",
    ]

    if alert.htf_level is not None:
        hl_price = format_price(alert.htf_level.price, sym)
        hl_desc = format_kl_formation(alert.htf_level)
        trigger_lines.append(f"• Rejected Level: {alert.htf_level.kind.value} @ {hl_price} ({alert.htf_level.current_role.value})\n  ↳ {hl_desc}")

    # Rejection Date & Breakout Date
    if getattr(alert, "rejection_candle_time", None):
        rej_date_str = format_candle_display(alert.rejection_candle_time, alert.htf)
        trigger_lines.append(f"• Rejection Date: {rej_date_str}")

    bo_date_str = format_candle_display(alert.breakout_candle_time, alert.ltf)
    trigger_lines.append(f"• Breakout Date: {bo_date_str}")

    # Conflict line (if an opposite confirmed setup is active)
    conflict_line = ""
    if opposite_setup is not None:
        opp_dir = opposite_setup.trigger.direction.value.upper()
        opp_tf = "Daily" if opposite_setup.htf == "D" else "Weekly"
        conflict_line = f"\n⚠️ <b>Active Opposite Setup:</b> Confirmed {opp_dir} on {opp_tf} ({opposite_setup.symbol})\n"

    # Next Day Rule note
    ndr_line = "✅ <b>Next Day Rule:</b> Breakout candle opened after rejection candle closed" if getattr(alert, "next_day_rule", False) else ""

    # Timing in WAT
    created_wat = alert.alert_time + pd.Timedelta(hours=1)
    created_wat_str = f"{created_wat.strftime('%a')} {created_wat.day} {created_wat.strftime('%b %H:%M WAT')}"
    time_line = f"<b>Alert Created:</b> {created_wat_str}"

    # Feed disclaimer
    disclaimer_line = "⚠️ <i>Prices come from the OANDA feed and may differ from other platforms by a few pips.</i>"

    # Assemble
    parts = [
        header,
        "",
        method_line,
        breakout_line,
        broken_line,
        "",
        "\n".join(trigger_lines),
    ]

    if ndr_line:
        parts.extend(["", ndr_line])

    if conflict_line:
        parts.append(conflict_line)

    parts.extend([
        "",
        time_line,
        "",
        disclaimer_line,
    ])

    return "\n".join(parts)


def format_digest_message(overflow_alerts: list[SLKAlert]) -> str:
    """
    Format a digest message when more than 5 alerts are generated in one scan.
    """
    header = f"📋 <b>SLK Digest: {len(overflow_alerts)} Additional Confirmed Alerts</b>\n"
    lines = [header]

    for a in overflow_alerts:
        dir_emoji = "🟢" if a.direction == TriggerDirection.BULLISH else "🔴"
        sym = a.symbol
        dir_str = a.direction.value.upper()
        htf = "Daily" if a.htf == "D" else "Weekly"
        b_type = a.breakout_type.value.upper()
        prob_str = " [LOW PROB]" if a.is_low_probability else ""
        marg_str = " [MARGINAL]" if a.is_marginal else ""
        lines.append(
            f"• {dir_emoji} <b>{sym}</b> {dir_str} ({htf}) – {b_type} Breakout{prob_str}{marg_str}"
        )

    lines.append(f"\n<i>Check /active or /internal for individual setup details.</i>")
    return "\n".join(lines)


def format_internal_setup_message(setup: SLKSetup) -> str:
    """
    Format a setup that currently has an active internal breakout (/internal command).
    """
    sym = setup.symbol
    direction_str = setup.trigger.direction.value.upper()
    dir_emoji = "🟢" if setup.trigger.direction == TriggerDirection.BULLISH else "🔴"
    htf_label = "Daily" if setup.htf == "D" else "Weekly"
    ltf_label = "H4" if setup.ltf == "H4" else "Daily"

    header = f"{dir_emoji} <b>Active Internal Setup: {sym} – {direction_str} ({htf_label})</b>"
    
    # Internal breakout details
    ib_time = setup.timeline.internal_breakout_candle_time.strftime("%Y-%m-%d %H:%M UTC") if setup.timeline.internal_breakout_candle_time else "N/A"
    ib_level = setup.timeline.internal_broken_level
    ib_level_str = f"{ib_level.kind.value} @ {format_price(ib_level.price, sym)}" if ib_level else "N/A"

    # LX level waiting for external confirmation
    lx_level = setup.timeline.lx_level
    lx_str = f"{lx_level.kind.value} @ {format_price(lx_level.price, sym)}" if lx_level else "None"

    # Extreme X
    x_str = format_price(setup.timeline.extreme_price_x, sym)

    lines = [
        header,
        f"<b>Internal Breakout:</b> {ib_level_str} on {ib_time}",
        f"<b>Awaiting External (LX):</b> {lx_str}",
        f"<b>Mitigation Extreme (X):</b> {x_str} (invalidation level)",
        f"<b>Status:</b> INTERNAL_CONFIRMED (can upgrade to EXTERNAL on close beyond LX)",
    ]
    return "\n".join(lines)
