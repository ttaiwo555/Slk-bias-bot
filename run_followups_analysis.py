"""
run_followups_analysis.py
-------------------------
Comprehensive script addressing Follow-ups 1, 2, 3, 4:
1. Reconcile Daily & Weekly trigger counts (Phase 2 vs Phase 3 vs deep history).
2. Count ALERTS (not just final states): internal alerts, external alerts, internal -> external upgrades.
3. Break down alerts (fresh only) by method (1, 2, 3), HTF (W, D), and breakout type.
4. Measure impact of OCL retirement rule change (ocl_max_disrespects = 3 vs retired on count 2).
"""

import sys
import os
import time
from pathlib import Path
from collections import defaultdict
import pandas as pd
import yaml
from dotenv import load_dotenv

if sys.platform == "win32":
    os.system("chcp 65001 > nul")

load_dotenv()

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import (
    evaluate_setup_lifecycle,
    detect_ltf_levels,
    SetupState,
    BreakoutType,
)
from slk_bot.strategy.models import TriggerDirection, TriggerMethod, KeyLevel, LevelKind
from slk_bot.replay import load_settings_thresholds, load_doji_body_threshold_pct

ROOT = Path(__file__).resolve().parent
INST_PATH = ROOT / "slk_bot" / "config" / "instruments.yaml"
SETTINGS_PATH = ROOT / "slk_bot" / "config" / "settings.yaml"

with open(INST_PATH, "r", encoding="utf-8") as f:
    INST_DATA = yaml.safe_load(f)

with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
    SETTINGS_DATA = yaml.safe_load(f)

cache = CandleCache()
thresholds = load_settings_thresholds()
doji_pct = load_doji_body_threshold_pct()
lookbacks = SETTINGS_DATA.get("lookback", {})
lookback_w = lookbacks.get("lookback_weekly", 104)
lookback_d = lookbacks.get("lookback_daily", 250)
lookback_h4 = lookbacks.get("lookback_h4", 300)

instruments = [
    inst for inst in INST_DATA.get("instruments", [])
    if not inst.get("exclude_from_triggers") and inst["name"] != "USOIL"
]

print(f"Loaded {len(instruments)} active watchlist instruments.")

# Windows
END_DATE = pd.Timestamp("2026-10-19 23:59:59", tz="UTC")
START_D_12M = pd.Timestamp("2025-10-19 00:00:00", tz="UTC")
START_W_24M = pd.Timestamp("2024-10-19 00:00:00", tz="UTC")

MONTHS_D = 12.0
MONTHS_W = 24.0

def run_analysis():
    # -------------------------------------------------------------
    # 1. Evaluate Daily Triggers -> H4 Setups (12-Month Window)
    # -------------------------------------------------------------
    daily_triggers_all = []
    daily_triggers_ocl2 = []  # with OCL retiring on count 2
    daily_setups = []

    # Alerts counters
    d_alerts_fresh = []
    d_alerts_all = []
    d_internal_then_external_fresh = 0
    d_internal_then_external_all = 0
    d_no_lx_count = 0

    print("Evaluating Daily -> H4 setups across all 37 instruments...")
    t0 = time.time()

    for idx, inst in enumerate(instruments, 1):
        name = inst["name"]
        asset_class = inst.get("asset_class", "forex")

        df_d = cache.load_candles(name, "D")
        df_h4 = cache.load_candles(name, "H4")

        if df_d.empty or df_h4.empty:
            print(f"  Warning: Missing data for {name} (D={len(df_d)}, H4={len(df_h4)})")
            continue

        # Detect Daily triggers with ocl_max_disrespects = 3 (current / restored)
        trigs_d = detect_triggers(
            df=df_d,
            timeframe="D",
            symbol=name,
            asset_class=asset_class,
            lookback=lookback_d,
            max_disrespects=3,
            ocl_max_disrespects=3,
            doji_body_threshold_pct=doji_pct,
            marginal_thresholds=thresholds,
        )
        trigs_d_12m = [t for t in trigs_d if START_D_12M <= t.trigger_candle_time <= END_DATE]
        daily_triggers_all.extend(trigs_d_12m)
        print(f"[{idx}/{len(instruments)}] {name}: {len(trigs_d_12m)} Daily triggers", flush=True)

        # Detect Daily triggers with ocl_max_disrespects = 1 (meaning retired on count >= 2, Phase 2 test)
        trigs_d_ocl2 = detect_triggers(
            df=df_d,
            timeframe="D",
            symbol=name,
            asset_class=asset_class,
            lookback=lookback_d,
            max_disrespects=3,
            ocl_max_disrespects=1,
            doji_body_threshold_pct=doji_pct,
            marginal_thresholds=thresholds,
        )
        trigs_d_ocl2_12m = [t for t in trigs_d_ocl2 if START_D_12M <= t.trigger_candle_time <= END_DATE]
        daily_triggers_ocl2.extend(trigs_d_ocl2_12m)

        # Precompute H4 levels for setup lifecycle simulation
        h4_levels = detect_ltf_levels(df_h4, name, "H4", doji_pct)

        for trig in trigs_d_12m:
            setup = evaluate_setup_lifecycle(
                trigger=trig,
                htf_df=df_d,
                symbol=name,
                htf_tf="D",
                ltf_tf="H4",
                ltf_df=df_h4,
                daily_df=df_d,
                unfresh_alerts=True,
                all_ltf_levels=h4_levels,
            )
            daily_setups.append(setup)

            if setup.timeline.lx_level is None:
                d_no_lx_count += 1

            # Track alerts
            is_fresh = not trig.is_low_probability
            has_internal = setup.timeline.internal_breakout_candle_time is not None
            has_external = setup.timeline.external_breakout_candle_time is not None

            if has_internal and has_external:
                d_internal_then_external_all += 1
                if is_fresh:
                    d_internal_then_external_fresh += 1

            for alert in setup.alerts:
                d_alerts_all.append(alert)
                if is_fresh:
                    d_alerts_fresh.append(alert)

    print(f"Completed Daily analysis in {time.time() - t0:.2f}s.")

    # -------------------------------------------------------------
    # 2. Evaluate Weekly Triggers -> Daily Setups (24-Month Window)
    # -------------------------------------------------------------
    weekly_triggers_all = []
    weekly_triggers_ocl2 = []
    weekly_setups = []

    w_alerts_fresh = []
    w_alerts_all = []
    w_internal_then_external_fresh = 0
    w_internal_then_external_all = 0
    w_no_lx_count = 0

    print("Evaluating Weekly -> Daily setups across all 37 instruments...")
    t1 = time.time()

    for idx, inst in enumerate(instruments, 1):
        name = inst["name"]
        asset_class = inst.get("asset_class", "forex")

        df_w = cache.load_candles(name, "W")
        df_d = cache.load_candles(name, "D")

        if df_w.empty or df_d.empty:
            continue

        trigs_w = detect_triggers(
            df=df_w,
            timeframe="W",
            symbol=name,
            asset_class=asset_class,
            lookback=lookback_w,
            max_disrespects=3,
            ocl_max_disrespects=3,
            doji_body_threshold_pct=doji_pct,
            marginal_thresholds=thresholds,
        )
        trigs_w_24m = [t for t in trigs_w if START_W_24M <= t.trigger_candle_time <= END_DATE]
        weekly_triggers_all.extend(trigs_w_24m)

        trigs_w_ocl2 = detect_triggers(
            df=df_w,
            timeframe="W",
            symbol=name,
            asset_class=asset_class,
            lookback=lookback_w,
            max_disrespects=3,
            ocl_max_disrespects=1,
            doji_body_threshold_pct=doji_pct,
            marginal_thresholds=thresholds,
        )
        trigs_w_ocl2_24m = [t for t in trigs_w_ocl2 if START_W_24M <= t.trigger_candle_time <= END_DATE]
        weekly_triggers_ocl2.extend(trigs_w_ocl2_24m)

        d_levels = detect_ltf_levels(df_d, name, "D", doji_pct)

        for trig in trigs_w_24m:
            setup = evaluate_setup_lifecycle(
                trigger=trig,
                htf_df=df_w,
                symbol=name,
                htf_tf="W",
                ltf_tf="D",
                ltf_df=df_d,
                daily_df=df_d,
                unfresh_alerts=True,
                all_ltf_levels=d_levels,
            )
            weekly_setups.append(setup)

            if setup.timeline.lx_level is None:
                w_no_lx_count += 1

            is_fresh = not trig.is_low_probability
            has_internal = setup.timeline.internal_breakout_candle_time is not None
            has_external = setup.timeline.external_breakout_candle_time is not None

            if has_internal and has_external:
                w_internal_then_external_all += 1
                if is_fresh:
                    w_internal_then_external_fresh += 1

            for alert in setup.alerts:
                w_alerts_all.append(alert)
                if is_fresh:
                    w_alerts_fresh.append(alert)

    print(f"Completed Weekly analysis in {time.time() - t1:.2f}s.")

    # -------------------------------------------------------------
    # 3. Print Comprehensive Report
    # -------------------------------------------------------------
    print("\n" + "="*80)
    print("FOLLOW-UP 1: RECONCILIATION OF DAILY & WEEKLY TRIGGERS")
    print("="*80)
    print(f"Total Watchlist Instruments: {len(instruments)} (USOIL excluded)")
    print(f"\nA. DAILY TRIGGERS (12-Month Window: 2025-10-19 to 2026-10-19):")
    print(f"  - Phase 2 reported count (38 instruments, before decoupling fixes): 9,673")
    print(f"  - Phase 3 previous count (only 8 instruments had cached H4): 2,524")
    print(f"  - Current Full Watchlist (37 instruments, deep H4 & D history): {len(daily_triggers_all)}")
    print(f"    * Fresh Primary Level : {sum(1 for t in daily_triggers_all if not t.is_low_probability)} ({sum(1 for t in daily_triggers_all if not t.is_low_probability)/len(daily_triggers_all):.1%})")
    print(f"    * Low Probability     : {sum(1 for t in daily_triggers_all if t.is_low_probability)} ({sum(1 for t in daily_triggers_all if t.is_low_probability)/len(daily_triggers_all):.1%})")
    print(f"    * Average per month across watchlist: {len(daily_triggers_all)/MONTHS_D:.1f} / mo ({len(daily_triggers_all)/(MONTHS_D*len(instruments)):.2f} / inst / mo)")

    print(f"\nB. WEEKLY TRIGGERS (24-Month Window: 2024-10-19 to 2026-10-19):")
    print(f"  - Phase 2 reported count (38 instruments, coupled/suppressed): 3,728")
    print(f"  - Phase 3 count (37 instruments, decoupled independent triggers): {len(weekly_triggers_all)}")
    print(f"    * Fresh Primary Level : {sum(1 for t in weekly_triggers_all if not t.is_low_probability)} ({sum(1 for t in weekly_triggers_all if not t.is_low_probability)/len(weekly_triggers_all):.1%})")
    print(f"    * Low Probability     : {sum(1 for t in weekly_triggers_all if t.is_low_probability)} ({sum(1 for t in weekly_triggers_all if t.is_low_probability)/len(weekly_triggers_all):.1%})")

    print("\n" + "="*80)
    print("FOLLOW-UP 4: OCL RETIREMENT RULE CHANGE IMPACT")
    print("="*80)
    print(f"Daily Triggers with OCL retiring on count >= 2: {len(daily_triggers_ocl2)}")
    print(f"Daily Triggers with OCL active up to count 3 (restored rule): {len(daily_triggers_all)}")
    print(f"  Difference on Daily: +{len(daily_triggers_all) - len(daily_triggers_ocl2)} triggers (+{(len(daily_triggers_all) - len(daily_triggers_ocl2))/len(daily_triggers_ocl2):.2%})")
    print(f"Weekly Triggers with OCL retiring on count >= 2: {len(weekly_triggers_ocl2)}")
    print(f"Weekly Triggers with OCL active up to count 3 (restored rule): {len(weekly_triggers_all)}")
    print(f"  Difference on Weekly: +{len(weekly_triggers_all) - len(weekly_triggers_ocl2)} triggers (+{(len(weekly_triggers_all) - len(weekly_triggers_ocl2))/len(weekly_triggers_ocl2):.2%})")

    print("\n" + "="*80)
    print("FOLLOW-UP 2: ALERT COUNTS (NOT JUST FINAL SETUP STATES)")
    print("="*80)
    # Daily Setups
    d_confirmed_ext = [s for s in daily_setups if s.state == SetupState.EXTERNAL_CONFIRMED]
    d_confirmed_int = [s for s in daily_setups if s.state == SetupState.INTERNAL_CONFIRMED]
    d_invalid = [s for s in daily_setups if s.state == SetupState.INVALIDATED]
    d_expired = [s for s in daily_setups if s.state == SetupState.EXPIRED]

    print(f"1. DAILY -> H4 SETUPS (Total Triggers: {len(daily_setups)}):")
    print(f"  Setup States Breakdown:")
    print(f"    - Ended as EXTERNAL_CONFIRMED : {len(d_confirmed_ext)} ({len(d_confirmed_ext)/len(daily_setups):.1%})")
    print(f"    - Ended as INTERNAL_CONFIRMED : {len(d_confirmed_int)} ({len(d_confirmed_int)/len(daily_setups):.1%})")
    print(f"    - INVALIDATED                 : {len(d_invalid)} ({len(d_invalid)/len(daily_setups):.1%})")
    print(f"    - EXPIRED (no break in 3 days): {len(d_expired)} ({len(d_expired)/len(daily_setups):.1%})")
    print(f"    - Triggers without LX found   : {d_no_lx_count} ({d_no_lx_count/len(daily_setups):.1%})")

    print(f"\n  Alert Breakdown (Fresh Primary Levels Only, 12 Months):")
    d_int_alerts_fresh = [a for a in d_alerts_fresh if a.breakout_type == BreakoutType.INTERNAL]
    d_ext_alerts_fresh = [a for a in d_alerts_fresh if a.breakout_type == BreakoutType.EXTERNAL]
    print(f"    - Total Fresh Alerts Generated : {len(d_alerts_fresh)} ({len(d_alerts_fresh)/MONTHS_D:.1f} / month across watchlist, {len(d_alerts_fresh)/(MONTHS_D*len(instruments)):.2f} / inst / mo)")
    print(f"    - Internal Alerts              : {len(d_int_alerts_fresh)} ({len(d_int_alerts_fresh)/MONTHS_D:.1f} / month)")
    print(f"    - External Alerts              : {len(d_ext_alerts_fresh)} ({len(d_ext_alerts_fresh)/MONTHS_D:.1f} / month)")
    print(f"    - Setups with Internal -> External Upgrade : {d_internal_then_external_fresh} ({d_internal_then_external_fresh/MONTHS_D:.1f} / month)")

    # Weekly Setups
    w_confirmed_ext = [s for s in weekly_setups if s.state == SetupState.EXTERNAL_CONFIRMED]
    w_confirmed_int = [s for s in weekly_setups if s.state == SetupState.INTERNAL_CONFIRMED]
    w_invalid = [s for s in weekly_setups if s.state == SetupState.INVALIDATED]
    w_expired = [s for s in weekly_setups if s.state == SetupState.EXPIRED]

    print(f"\n2. WEEKLY -> DAILY SETUPS (Total Triggers: {len(weekly_setups)}):")
    print(f"  Setup States Breakdown:")
    print(f"    - Ended as EXTERNAL_CONFIRMED : {len(w_confirmed_ext)} ({len(w_confirmed_ext)/len(weekly_setups):.1%})")
    print(f"    - Ended as INTERNAL_CONFIRMED : {len(w_confirmed_int)} ({len(w_confirmed_int)/len(weekly_setups):.1%})")
    print(f"    - INVALIDATED                 : {len(w_invalid)} ({len(w_invalid)/len(weekly_setups):.1%})")
    print(f"    - EXPIRED (no break in 14 d)  : {len(w_expired)} ({len(w_expired)/len(weekly_setups):.1%})")
    print(f"    - Triggers without LX found   : {w_no_lx_count} ({w_no_lx_count/len(weekly_setups):.1%})")

    print(f"\n  Alert Breakdown (Fresh Primary Levels Only, 24 Months):")
    w_int_alerts_fresh = [a for a in w_alerts_fresh if a.breakout_type == BreakoutType.INTERNAL]
    w_ext_alerts_fresh = [a for a in w_alerts_fresh if a.breakout_type == BreakoutType.EXTERNAL]
    print(f"    - Total Fresh Alerts Generated : {len(w_alerts_fresh)} ({len(w_alerts_fresh)/MONTHS_W:.1f} / month across watchlist, {len(w_alerts_fresh)/(MONTHS_W*len(instruments)):.2f} / inst / mo)")
    print(f"    - Internal Alerts              : {len(w_int_alerts_fresh)} ({len(w_int_alerts_fresh)/MONTHS_W:.1f} / month)")
    print(f"    - External Alerts              : {len(w_ext_alerts_fresh)} ({len(w_ext_alerts_fresh)/MONTHS_W:.1f} / month)")
    print(f"    - Setups with Internal -> External Upgrade : {w_internal_then_external_fresh} ({w_internal_then_external_fresh/MONTHS_W:.1f} / month)")

    print("\n" + "="*80)
    print("FOLLOW-UP 3: WOULD-BE ALERT BREAKDOWN (FRESH ONLY)")
    print("="*80)
    print("A. BY HTF TIMEFRAME:")
    print(f"  - Daily -> H4 Alerts   : {len(d_alerts_fresh)} total ({len(d_alerts_fresh)/MONTHS_D:.1f} / month)")
    print(f"  - Weekly -> Daily Alerts: {len(w_alerts_fresh)} total ({len(w_alerts_fresh)/MONTHS_W:.1f} / month)")
    print(f"  - Total Watchlist Fresh Alerts : {len(d_alerts_fresh)/MONTHS_D + len(w_alerts_fresh)/MONTHS_W:.1f} alerts / month (~{(len(d_alerts_fresh)/MONTHS_D + len(w_alerts_fresh)/MONTHS_W)/len(instruments):.2f} per instrument / month)")

    print("\nB. BY BREAKOUT TYPE (Per Month):")
    print(f"  - Daily External Breakout  : {len(d_ext_alerts_fresh)/MONTHS_D:.1f} / month")
    print(f"  - Daily Internal Breakout  : {len(d_int_alerts_fresh)/MONTHS_D:.1f} / month")
    print(f"  - Weekly External Breakout : {len(w_ext_alerts_fresh)/MONTHS_W:.1f} / month")
    print(f"  - Weekly Internal Breakout : {len(w_int_alerts_fresh)/MONTHS_W:.1f} / month")
    print(f"  - Total External (W + D)   : {len(d_ext_alerts_fresh)/MONTHS_D + len(w_ext_alerts_fresh)/MONTHS_W:.1f} / month")
    print(f"  - Total Internal (W + D)   : {len(d_int_alerts_fresh)/MONTHS_D + len(w_int_alerts_fresh)/MONTHS_W:.1f} / month")

    print("\nC. BY TRIGGER METHOD (Fresh Only, Per Month):")
    for m in (1, 2, 3):
        m_d_ext = sum(1 for a in d_ext_alerts_fresh if int(a.method) == m) / MONTHS_D
        m_d_int = sum(1 for a in d_int_alerts_fresh if int(a.method) == m) / MONTHS_D
        m_w_ext = sum(1 for a in w_ext_alerts_fresh if int(a.method) == m) / MONTHS_W
        m_w_int = sum(1 for a in w_int_alerts_fresh if int(a.method) == m) / MONTHS_W
        m_tot = m_d_ext + m_d_int + m_w_ext + m_w_int
        m_name = {1: "Method 1 (Rejection)", 2: "Method 2 (Sweep)", 3: "Method 3 (Combined)"}[m]
        print(f"  - {m_name}:")
        print(f"      Total: {m_tot:.1f} / mo (Daily: {m_d_ext+m_d_int:.1f} [Ext: {m_d_ext:.1f}, Int: {m_d_int:.1f}] | Weekly: {m_w_ext+m_w_int:.1f} [Ext: {m_w_ext:.1f}, Int: {m_w_int:.1f}])")

if __name__ == "__main__":
    run_analysis()
