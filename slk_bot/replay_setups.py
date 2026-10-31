"""
slk_bot/replay_setups.py
------------------------
Replay command for historical SLK setups and alerts (Phase 3 deliverable).

Usage:
  python -m slk_bot.replay_setups --symbol X --from DATE --to DATE
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional
import pandas as pd
import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import evaluate_setup_lifecycle, SLKSetup, SetupState
from slk_bot.replay import (
    load_instrument_config,
    load_settings_thresholds,
    load_doji_body_threshold_pct,
    load_unfresh_policy,
)

CONFIG_DIR = Path(__file__).resolve().parent / "config"


def load_strategy_settings() -> dict:
    """Load settings.yaml strategy dict."""
    settings_path = CONFIG_DIR / "settings.yaml"
    if not settings_path.exists():
        return {}
    with open(settings_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("strategy", {})


def run_setups_replay(
    symbol: str,
    from_date: str,
    to_date: str,
    htf_list: Optional[list[str]] = None,
    cache_db: Optional[str] = None,
) -> list[SLKSetup]:
    """Run setup detection across both Weekly and Daily timeframes for a symbol."""
    cache = CandleCache(db_path=cache_db)
    cfg = load_instrument_config(symbol)
    if cfg.get("exclude_from_triggers", False):
        print(f"Notice: Symbol '{symbol}' is excluded from triggers by configuration.")
        return []

    strat_cfg = load_strategy_settings()
    thresholds = load_settings_thresholds()
    doji_pct = load_doji_body_threshold_pct()
    unfresh_pol = load_unfresh_policy()
    unfresh_alerts = bool(strat_cfg.get("unfresh_alerts", False))
    exp_daily = int(strat_cfg.get("expiry_daily_trigger_candles", 3))
    exp_weekly = int(strat_cfg.get("expiry_weekly_trigger_candles", 14))

    asset_class = cfg.get("asset_class", "forex")
    if htf_list is None:
        htf_list = ["W", "D"]

    df_w = cache.load_candles(symbol, "W")
    df_d = cache.load_candles(symbol, "D")
    df_h4 = cache.load_candles(symbol, "H4")

    all_setups: list[SLKSetup] = []

    t_start = pd.to_datetime(from_date, utc=True)
    t_end = pd.to_datetime(to_date, utc=True)
    if len(to_date.strip()) <= 10:
        t_end = t_end + pd.Timedelta(hours=23, minutes=59, seconds=59)

    for htf in htf_list:
        if htf == "W":
            ltf = "D"
            htf_df = df_w
            ltf_df = df_d
            lookback = 104
        else:
            ltf = "H4"
            htf_df = df_d
            ltf_df = df_h4
            lookback = 250

        if htf_df.empty or ltf_df.empty:
            continue

        triggers = detect_triggers(
            df=htf_df,
            timeframe=htf,
            symbol=symbol,
            asset_class=asset_class,
            lookback=lookback,
            max_disrespects=3,
            doji_body_threshold_pct=doji_pct,
            marginal_thresholds=thresholds,
            unfresh_policy=unfresh_pol,
        )

        for tr in triggers:
            if not (t_start <= tr.trigger_candle_time <= t_end):
                continue

            setup = evaluate_setup_lifecycle(
                trigger=tr,
                htf_df=htf_df,
                ltf_df=ltf_df,
                daily_df=df_d,
                symbol=symbol,
                htf_tf=htf,
                ltf_tf=ltf,
                unfresh_alerts=unfresh_alerts,
                expiry_daily_trigger_candles=exp_daily,
                expiry_weekly_trigger_candles=exp_weekly,
                doji_body_threshold_pct=doji_pct,
            )
            all_setups.append(setup)

    all_setups.sort(key=lambda s: s.timeline.trigger_time)
    return all_setups


def print_setups_report(setups: list[SLKSetup], symbol: str, from_date: str, to_date: str) -> None:
    cfg = load_instrument_config(symbol)
    scale = cfg.get("display_scale", 1.0)
    prec = 1 if scale > 1.0 else 5

    print("=" * 115)
    print(f" SLK SETUPS & BREAKOUT REPLAY | Symbol: {symbol} | Window: {from_date} to {to_date}")
    print(f" Total Setups Analyzed: {len(setups)}")
    print("=" * 115)

    if not setups:
        print("No setups found in the specified window.")
        return

    for idx, s in enumerate(setups, 1):
        tr = s.trigger
        tl = s.timeline
        dir_str = tr.direction.value.upper()
        prob_str = " [LOW PROBABILITY]" if tr.is_low_probability else ""
        marg_str = " [MARGINAL]" if (tr.margins and tr.margins.is_marginal) else ""
        print(f"\n[{idx:02d}] {s.htf}->{s.ltf} {dir_str} SETUP (Method {tr.method.value}){prob_str}{marg_str}")
        print(f"     Status               : {s.state.value}")
        print(f"     HTF Trigger Candle   : {tl.trigger_time.strftime('%Y-%m-%d %H:%M UTC')} (closes {tl.trigger_close_time.strftime('%Y-%m-%d %H:%M UTC')})")
        if tr.primary_level:
            p = tr.primary_level
            fresh_str = "FRESH" if tr.primary_level_fresh else f"UNFRESH ({tr.primary_level_last_contact})"
            print(f"     HTF Level (Primary)  : {p.kind.value} @ {p.price * scale:.{prec}f} ({p.current_role.value}, {fresh_str})")
        print(f"     LTF Extreme X        : {tl.extreme_price_x * scale:.{prec}f} on candle {tl.extreme_candle_time.strftime('%Y-%m-%d %H:%M UTC')}")
        if tl.lx_level:
            print(f"     External Level LX    : {tl.lx_level.kind.value} @ {tl.lx_level.price * scale:.{prec}f} formed {tl.lx_level.formation_time.strftime('%Y-%m-%d %H:%M UTC')}")
        else:
            print(f"     External Level LX    : None")

        if tl.internal_levels:
            il_strs = [f"{l.kind.value}@{l.price * scale:.{prec}f}({l.formation_time.strftime('%m-%d %H:%M')})" for l in tl.internal_levels]
            print(f"     Internal Level(s)    : {', '.join(il_strs)}")

        # Milestones
        if tl.internal_breakout_candle_time:
            print(f"     Internal Breakout    : Candle {tl.internal_breakout_candle_time.strftime('%Y-%m-%d %H:%M UTC')} | Broken: {tl.internal_broken_level.price * scale:.{prec}f} | Alert Time: {tl.internal_alert_time.strftime('%Y-%m-%d %H:%M UTC')}")
        if tl.external_breakout_candle_time:
            print(f"     External Breakout    : Candle {tl.external_breakout_candle_time.strftime('%Y-%m-%d %H:%M UTC')} | Broken: {tl.external_broken_level.price * scale:.{prec}f} | Alert Time: {tl.external_alert_time.strftime('%Y-%m-%d %H:%M UTC')}")
        if tl.invalidation_time:
            print(f"     Invalidation Time    : {tl.invalidation_time.strftime('%Y-%m-%d %H:%M UTC')}")
        if s.state == SetupState.EXPIRED:
            print(f"     Expiry Deadline      : {tl.expiry_time.strftime('%Y-%m-%d %H:%M UTC')}")

        if s.alerts:
            print(f"     Alerts Generated ({len(s.alerts)}):")
            for a in s.alerts:
                supp_str = " (PUSH SUPPRESSED - Low Prob)" if a.is_push_suppressed else ""
                print(f"       -> [{a.breakout_type.value}] Alert Time: {a.alert_time.strftime('%Y-%m-%d %H:%M UTC')} | Broken: {a.broken_level.kind.value}@{a.broken_level.price * scale:.{prec}f}{supp_str}")
        print("-" * 115)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay SLK Setups & Breakouts over a historical date range.")
    parser.add_argument("--symbol", type=str, required=True, help="Instrument symbol (e.g. AUDCAD, XAGUSD, EURUSD)")
    parser.add_argument("--from", dest="from_date", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", type=str, required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--tf", dest="timeframe", type=str, choices=["W", "D"], default=None, help="Filter by HTF timeframe")

    args = parser.parse_args()
    htfs = [args.timeframe] if args.timeframe else None
    setups = run_setups_replay(args.symbol, args.from_date, args.to_date, htf_list=htfs)
    print_setups_report(setups, args.symbol, args.from_date, args.to_date)


if __name__ == "__main__":
    main()
