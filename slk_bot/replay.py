"""
slk_bot/replay.py
-----------------
Replay command for historical HTF triggers.

Usage:
  python -m slk_bot.replay --symbol X --tf D --from DATE --to DATE
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
from typing import Optional
import pandas as pd
import yaml

# Force UTF-8 stdout on Windows terminals
if sys.stdout and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.models import HTFTrigger

CONFIG_DIR = Path(__file__).resolve().parent / "config"


def load_instrument_config(symbol: str) -> dict[str, Any]:
    """Return instrument configuration dict for a given symbol."""
    inst_path = CONFIG_DIR / "instruments.yaml"
    if not inst_path.exists():
        return {"name": symbol, "asset_class": "forex", "display_scale": 1.0, "exclude_from_triggers": False}
    with open(inst_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    for inst in data.get("instruments", []):
        if inst.get("name") == symbol:
            return {
                "name": symbol,
                "asset_class": inst.get("asset_class", inst.get("profile", "forex")),
                "oanda_symbol": inst.get("oanda_symbol", inst.get("symbol", symbol)),
                "display_scale": float(inst.get("display_scale", 1.0)),
                "exclude_from_triggers": bool(inst.get("exclude_from_triggers", False)),
            }
    return {"name": symbol, "asset_class": "forex", "display_scale": 1.0, "exclude_from_triggers": False}


def load_instrument_profile(symbol: str) -> tuple[str, str]:
    """Return (asset_class, broker_symbol) for a given symbol."""
    cfg = load_instrument_config(symbol)
    return cfg["asset_class"], cfg["oanda_symbol"]


def load_settings_thresholds() -> dict[str, float]:
    """Load marginal_threshold_pct from settings.yaml."""
    settings_path = CONFIG_DIR / "settings.yaml"
    if not settings_path.exists():
        return {}
    with open(settings_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    strategy = data.get("strategy", {})
    return strategy.get("marginal_threshold_pct", {})


def load_doji_body_threshold_pct() -> float:
    """Load doji_body_threshold_pct from settings.yaml."""
    settings_path = CONFIG_DIR / "settings.yaml"
    if not settings_path.exists():
        return 0.0
    with open(settings_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return float(data.get("strategy", {}).get("doji_body_threshold_pct", 0.0))


def load_unfresh_policy() -> str:
    """Load unfresh_policy from settings.yaml ('flag' or 'off')."""
    settings_path = CONFIG_DIR / "settings.yaml"
    if not settings_path.exists():
        return "flag"
    with open(settings_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return str(data.get("strategy", {}).get("unfresh_policy", "flag")).lower()


def run_replay(
    symbol: str,
    timeframe: str,
    from_date: str,
    to_date: str,
    cache_db: Optional[str] = None,
) -> list[HTFTrigger]:
    """Run replay over cached candles and return filtered triggers."""
    cache = CandleCache(db_path=cache_db)
    df = cache.load_candles(symbol, timeframe)
    if df.empty:
        print(f"Error: No cached candles found for symbol '{symbol}' on timeframe '{timeframe}'.")
        return []

    asset_class, _ = load_instrument_profile(symbol)
    thresholds = load_settings_thresholds()
    doji_pct = load_doji_body_threshold_pct()
    unfresh_pol = load_unfresh_policy()

    lookback = 104 if timeframe == "W" else (250 if timeframe == "D" else 300)

    all_triggers = detect_triggers(
        df=df,
        timeframe=timeframe,
        symbol=symbol,
        asset_class=asset_class,
        lookback=lookback,
        max_disrespects=3,
        doji_body_threshold_pct=doji_pct,
        marginal_thresholds=thresholds,
        unfresh_policy=unfresh_pol,
    )

    t_start = pd.to_datetime(from_date, utc=True)
    t_end = pd.to_datetime(to_date, utc=True)
    # If to_date had no time specified (e.g. 2026-10-18), extend to end of day
    if len(to_date.strip()) <= 10:
        t_end = t_end + pd.Timedelta(hours=23, minutes=59, seconds=59)

    filtered = [
        t for t in all_triggers
        if t_start <= t.trigger_candle_time <= t_end
    ]
    return filtered


def print_triggers(triggers: list[HTFTrigger], symbol: str, timeframe: str, from_date: str, to_date: str) -> None:
    print("=" * 105)
    print(f" SLK REPLAY REPORT | Symbol: {symbol} | Timeframe: {timeframe} | Period: {from_date} to {to_date}")
    print(f" Total triggers found: {len(triggers)}")
    print("=" * 105)

    cfg = load_instrument_config(symbol)

    if not triggers:
        print("No triggers found in the specified window.")
        return

    for idx, t in enumerate(triggers, 1):
        m_str = f"Method {t.method.value}"
        dir_str = t.direction.value.upper()
        prob_str = " [LOW PROBABILITY]" if t.is_low_probability else ""
        t_time = t.trigger_candle_time.strftime("%Y-%m-%d %H:%M UTC")

        scale = cfg.get("display_scale", 1.0)
        prec = 1 if scale > 1.0 else 5

        print(f"\n[{idx:02d}] {dir_str} TRIGGER ({m_str}){prob_str} at {t_time}")
        print(f"     Trigger Candle: O={t.trigger_candle_open * scale:.{prec}f}, H={t.trigger_candle_high * scale:.{prec}f}, L={t.trigger_candle_low * scale:.{prec}f}, C={t.trigger_candle_close * scale:.{prec}f}")
        print(f"     Mitigation Extreme X: {t.mitigation_extreme * scale:.{prec}f}")

        # Rejection details
        if t.primary_level:
            p = t.primary_level
            p_time = p.formation_time.strftime("%Y-%m-%d %H:%M UTC")
            fresh_str = "FRESH" if t.primary_level_fresh else f"UNFRESH (last contact: {t.primary_level_last_contact})"
            print(f"     Rejected Level (Primary): {p.kind.value} @ {p.price * scale:.{prec}f} | Role: {p.current_role.value} | Formed: {p_time}")
            print(f"     Level Freshness         : {fresh_str}")
            if len(t.all_rejected_levels) > 1:
                others = [
                    f"{l.kind.value}@{l.price * scale:.{prec}f}({l.current_role.value}, {'fresh' if l.fresh else 'unfresh'})"
                    for l in t.all_rejected_levels if l.id != p.id
                ]
                print(f"     Other Rejected Levels   : {', '.join(others)}")
            if t.margins:
                print(f"     Rejection Margins       : Wick: {t.margins.rejection_wick_margin_pct:.3f}%, Close: {t.margins.rejection_close_margin_pct:.3f}%")

        # Sweep details
        if t.sweep_reference:
            s = t.sweep_reference
            s_time = s.open_time.strftime("%Y-%m-%d %H:%M UTC")
            print(f"     Swept Level             : {s.swept_level * scale:.{prec}f} ({s.swept_side.upper()} of candle {s_time})")
            print(f"     Reference Candle R      : O={s.open * scale:.{prec}f}, H={s.high * scale:.{prec}f}, L={s.low * scale:.{prec}f}, C={s.close * scale:.{prec}f}")
            if t.margins:
                print(f"     Sweep Margins           : Wick: {t.margins.sweep_wick_margin_pct:.3f}%, Close: {t.margins.sweep_close_margin_pct:.3f}%")

        # Overall Margins and Marginal Flag
        if t.margins:
            marg_flag = f"YES [MARGINAL: verify on chart] (< {t.margins.threshold_pct:.2f}%)" if t.margins.is_marginal else f"NO (>= {t.margins.threshold_pct:.2f}%)"
            print(f"     Min Pass Margin         : {t.margins.min_margin_pct:.3f}% | Marginal Flag: {marg_flag}")
        print("-" * 105)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay SLK HTF Triggers over a historical date range.")
    parser.add_argument("--symbol", type=str, required=True, help="Instrument symbol (e.g. AUDCAD, XAGUSD, EURUSD)")
    parser.add_argument("--tf", type=str, required=True, choices=["W", "D"], help="Timeframe ('W' or 'D')")
    parser.add_argument("--from", dest="from_date", type=str, required=True, help="Start date (YYYY-MM-DD or YYYY-MM-DD HH:MM)")
    parser.add_argument("--to", dest="to_date", type=str, required=True, help="End date (YYYY-MM-DD or YYYY-MM-DD HH:MM)")

    args = parser.parse_args()

    triggers = run_replay(
        symbol=args.symbol,
        timeframe=args.tf,
        from_date=args.from_date,
        to_date=args.to_date,
    )

    print_triggers(triggers, args.symbol, args.tf, args.from_date, args.to_date)


if __name__ == "__main__":
    main()
