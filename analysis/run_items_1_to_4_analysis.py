"""
analysis/run_items_1_to_4_analysis.py
-------------------------------------
Evaluates Items 1, 2, 3, and 4 from the prompt:
1 & 2: Alert counts under the exact counting rule:
   - Method 1: counts only when primary rejected level is fresh.
   - Method 2: always counts.
   - Method 3: counts even when level is unfresh; unfresh OCL never used.
   - Method 1 unfresh stored as /lowprob (no push).
   - Evaluated under both:
     (a) primary = nearest to extreme (current rule)
     (b) primary = nearest to extreme among fresh, fallback to nearest overall
   - Breakdowns: HTF, method (1,2,3), breakout type (ext, int), unfresh M3, weekday distribution, internal-without-external.
3. Breakout selectivity: triggers that count -> any breakout, external, invalidated, expired.
4. Outcome statistics: forward 5 daily closes (Daily->H4) / 10 daily closes (Weekly->Daily)
   - Win rate (close > entry in bias direction)
   - Avg move in % and ADR(14) multiples
   - Hit +1 ADR before -1 ADR
   - Baseline: random dates and directions.
   - Deduplicated at most 1 alert per instrument, direction, and day.
"""

import sys
import os
import time
import random
from collections import defaultdict
from pathlib import Path
import pandas as pd
import numpy as np
import yaml
from dotenv import load_dotenv

if sys.platform == "win32":
    os.system("chcp 65001 > nul")

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import (
    evaluate_setup_lifecycle,
    detect_ltf_levels,
    SetupState,
    BreakoutType,
    SLKAlert,
    SLKSetup,
)
from slk_bot.strategy.models import TriggerDirection, TriggerMethod, KeyLevel
from slk_bot.replay import load_settings_thresholds, load_doji_body_threshold_pct

ROOT = Path(__file__).resolve().parent.parent
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

instruments = [
    inst for inst in INST_DATA.get("instruments", [])
    if not inst.get("exclude_from_triggers") and inst["name"] != "USOIL"
]

END_DATE = pd.Timestamp("2026-10-19 23:59:59", tz="UTC")
START_D_12M = pd.Timestamp("2025-10-19 00:00:00", tz="UTC")
START_W_24M = pd.Timestamp("2024-10-19 00:00:00", tz="UTC")

MONTHS_D = 12.0
MONTHS_W = 24.0

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

def is_setup_counting(trig) -> bool:
    """
    Counting rule:
      - Method 1: counts only when primary level is fresh.
      - Method 2: always counts.
      - Method 3: counts even when level is unfresh (unfresh OCL never used in triggers).
    """
    if int(trig.method) == 1:  # Method 1
        return bool(trig.primary_level and trig.primary_level.fresh)
    elif int(trig.method) == 2:  # Method 2
        return True
    elif int(trig.method) == 3:  # Method 3
        return True
    return False


def run_pipeline(rule_name: str = "nearest_extreme"):
    print(f"\n=======================================================")
    print(f"RUNNING PIPELINE WITH RULE: {rule_name.upper()}")
    print(f"=======================================================")

    daily_triggers_all = []
    daily_setups_counting = []
    daily_alerts_counting = []

    weekly_triggers_all = []
    weekly_setups_counting = []
    weekly_alerts_counting = []

    # Cache pre-loaded data for faster execution
    data_store = {}
    for inst in instruments:
        name = inst["name"]
        data_store[name] = {
            "D": cache.load_candles(name, "D"),
            "W": cache.load_candles(name, "W"),
            "H4": cache.load_candles(name, "H4"),
            "asset_class": inst.get("asset_class", "forex"),
        }

    # 1. Daily -> H4
    t0 = time.time()
    for inst in instruments:
        name = inst["name"]
        df_d = data_store[name]["D"]
        df_h4 = data_store[name]["H4"]
        asset_class = data_store[name]["asset_class"]

        if df_d.empty or df_h4.empty:
            continue

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
            primary_selection_rule=rule_name,
        )
        trigs_d_12m = [t for t in trigs_d if START_D_12M <= t.trigger_candle_time <= END_DATE]
        daily_triggers_all.extend(trigs_d_12m)

        # Filter only counting triggers for setup lifecycle
        counting_trigs = [t for t in trigs_d_12m if is_setup_counting(t)]

        h4_levels = detect_ltf_levels(df_h4, name, "H4", doji_pct)

        for trig in counting_trigs:
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
            daily_setups_counting.append(setup)
            for alert in setup.alerts:
                daily_alerts_counting.append(alert)

    print(f"Daily pipeline completed in {time.time() - t0:.2f}s.")

    # 2. Weekly -> Daily
    t1 = time.time()
    for inst in instruments:
        name = inst["name"]
        df_w = data_store[name]["W"]
        df_d = data_store[name]["D"]
        asset_class = data_store[name]["asset_class"]

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
            primary_selection_rule=rule_name,
        )
        trigs_w_24m = [t for t in trigs_w if START_W_24M <= t.trigger_candle_time <= END_DATE]
        weekly_triggers_all.extend(trigs_w_24m)

        counting_trigs = [t for t in trigs_w_24m if is_setup_counting(t)]
        d_levels = detect_ltf_levels(df_d, name, "D", doji_pct)

        for trig in counting_trigs:
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
            weekly_setups_counting.append(setup)
            for alert in setup.alerts:
                weekly_alerts_counting.append(alert)

    print(f"Weekly pipeline completed in {time.time() - t1:.2f}s.")

    return {
        "daily_triggers_all": daily_triggers_all,
        "daily_setups": daily_setups_counting,
        "daily_alerts": daily_alerts_counting,
        "weekly_triggers_all": weekly_triggers_all,
        "weekly_setups": weekly_setups_counting,
        "weekly_alerts": weekly_alerts_counting,
        "data_store": data_store,
    }


def analyze_rule_results(res, rule_label: str):
    daily_trigs = res["daily_triggers_all"]
    daily_setups = res["daily_setups"]
    daily_alerts = res["daily_alerts"]

    weekly_trigs = res["weekly_triggers_all"]
    weekly_setups = res["weekly_setups"]
    weekly_alerts = res["weekly_alerts"]

    print("\n" + "="*80)
    print(f"ITEM 1 & 2: WOULD-BE ALERTS PER MONTH UNDER COUNTING RULE [{rule_label.upper()}]")
    print("="*80)

    # 1. Monthly Total & Split by HTF
    d_alerts_mo = len(daily_alerts) / MONTHS_D
    w_alerts_mo = len(weekly_alerts) / MONTHS_W
    tot_alerts_mo = d_alerts_mo + w_alerts_mo
    per_inst_mo = tot_alerts_mo / len(instruments)

    print(f"A. WATCHLIST TOTAL ALERTS PER MONTH:")
    print(f"  - Daily -> H4 Alerts   : {d_alerts_mo:.1f} / month ({len(daily_alerts)} total over 12m)")
    print(f"  - Weekly -> Daily Alerts: {w_alerts_mo:.1f} / month ({len(weekly_alerts)} total over 24m)")
    print(f"  - Combined Total Alerts : {tot_alerts_mo:.1f} / month ({per_inst_mo:.2f} per instrument / month)")

    # 2. Split by Breakout Type
    d_ext = [a for a in daily_alerts if a.breakout_type == BreakoutType.EXTERNAL]
    d_int = [a for a in daily_alerts if a.breakout_type == BreakoutType.INTERNAL]
    w_ext = [a for a in weekly_alerts if a.breakout_type == BreakoutType.EXTERNAL]
    w_int = [a for a in weekly_alerts if a.breakout_type == BreakoutType.INTERNAL]

    print(f"\nB. BY BREAKOUT TYPE (Per Month):")
    print(f"  - External Breakouts: {len(d_ext)/MONTHS_D + len(w_ext)/MONTHS_W:.1f} / mo (Daily: {len(d_ext)/MONTHS_D:.1f}, Weekly: {len(w_ext)/MONTHS_W:.1f})")
    print(f"  - Internal Breakouts: {len(d_int)/MONTHS_D + len(w_int)/MONTHS_W:.1f} / mo (Daily: {len(d_int)/MONTHS_D:.1f}, Weekly: {len(w_int)/MONTHS_W:.1f})")

    # 3. Split by Method
    print(f"\nC. BY METHOD (Per Month):")
    for m in (1, 2, 3):
        m_name = {1: "Method 1 (Rejection - Fresh)", 2: "Method 2 (Sweep)", 3: "Method 3 (Combined)"}[m]
        d_m = sum(1 for a in daily_alerts if int(a.method) == m) / MONTHS_D
        w_m = sum(1 for a in weekly_alerts if int(a.method) == m) / MONTHS_W
        print(f"  - {m_name}: {d_m + w_m:.1f} / mo (Daily: {d_m:.1f}, Weekly: {w_m:.1f})")

    # 4. Separately: Method 3 with Unfresh Level
    d_m3_unfresh = [a for a in daily_alerts if int(a.method) == 3 and a.is_low_probability]
    w_m3_unfresh = [a for a in weekly_alerts if int(a.method) == 3 and a.is_low_probability]
    print(f"\nD. METHOD 3 SETUPS WITH UNFRESH LEVEL (Allowed by rule):")
    print(f"  - Daily M3 Unfresh Alerts : {len(d_m3_unfresh)/MONTHS_D:.1f} / mo ({len(d_m3_unfresh)} total)")
    print(f"  - Weekly M3 Unfresh Alerts: {len(w_m3_unfresh)/MONTHS_W:.1f} / mo ({len(w_m3_unfresh)} total)")
    print(f"  - Total M3 Unfresh Alerts : {len(d_m3_unfresh)/MONTHS_D + len(w_m3_unfresh)/MONTHS_W:.1f} / mo")

    # 5. Internal alerts that had no later external breakout
    d_int_no_ext = sum(1 for s in daily_setups if s.timeline.internal_breakout_candle_time and not s.timeline.external_breakout_candle_time)
    w_int_no_ext = sum(1 for s in weekly_setups if s.timeline.internal_breakout_candle_time and not s.timeline.external_breakout_candle_time)
    d_int_with_ext = sum(1 for s in daily_setups if s.timeline.internal_breakout_candle_time and s.timeline.external_breakout_candle_time)
    w_int_with_ext = sum(1 for s in weekly_setups if s.timeline.internal_breakout_candle_time and s.timeline.external_breakout_candle_time)
    print(f"\nE. INTERNAL ALERTS UPGRADE VS NO LATER EXTERNAL:")
    print(f"  - Daily -> H4:")
    print(f"      Total Internal Alerts: {len(d_int)} ({len(d_int)/MONTHS_D:.1f} / mo)")
    print(f"      Upgraded to External : {d_int_with_ext} ({d_int_with_ext/MONTHS_D:.1f} / mo, {d_int_with_ext/max(1, len(d_int)):.1%})")
    print(f"      No Later External    : {d_int_no_ext} ({d_int_no_ext/MONTHS_D:.1f} / mo, {d_int_no_ext/max(1, len(d_int)):.1%})")
    print(f"  - Weekly -> Daily:")
    print(f"      Total Internal Alerts: {len(w_int)} ({len(w_int)/MONTHS_W:.1f} / mo)")
    print(f"      Upgraded to External : {w_int_with_ext} ({w_int_with_ext/MONTHS_W:.1f} / mo, {w_int_with_ext/max(1, len(w_int)):.1%})")
    print(f"      No Later External    : {w_int_no_ext} ({w_int_no_ext/MONTHS_W:.1f} / mo, {w_int_no_ext/max(1, len(w_int)):.1%})")

    # 6. Weekday Distribution of Alerts
    print(f"\nF. ALERTS PER WEEKDAY (Average per week across Watchlist):")
    # Group alerts by alert_time day of week (Monday=0 ... Sunday=6)
    d_day_counts = defaultdict(int)
    for a in daily_alerts:
        d_day_counts[a.alert_time.day_name()] += 1
    w_day_counts = defaultdict(int)
    for a in weekly_alerts:
        w_day_counts[a.alert_time.day_name()] += 1

    # Over 52 weeks (12m) for Daily, 104 weeks (24m) for Weekly
    weeks_d = 52.14
    weeks_w = 104.28
    print(f"  {'Weekday':<12} | {'Daily/wk':<10} | {'Weekly/wk':<10} | {'Combined/wk':<12}")
    print(f"  {'-'*50}")
    for day in WEEKDAYS:
        dw = d_day_counts[day] / weeks_d
        ww = w_day_counts[day] / weeks_w
        print(f"  {day:<12} | {dw:<10.2f} | {ww:<10.2f} | {dw + ww:<12.2f}")

    # -------------------------------------------------------------
    # ITEM 3: BREAKOUT SELECTIVITY FOR COUNTING SETUPS
    # -------------------------------------------------------------
    print("\n" + "="*80)
    print(f"ITEM 3: BREAKOUT SELECTIVITY FOR COUNTING SETUPS [{rule_label.upper()}]")
    print("="*80)

    for htf_label, setups, total_trigs_all in [
        ("Daily -> H4", daily_setups, daily_trigs),
        ("Weekly -> Daily", weekly_setups, weekly_trigs),
    ]:
        n_count = len(setups)
        n_any_breakout = sum(1 for s in setups if s.timeline.internal_breakout_candle_time or s.timeline.external_breakout_candle_time)
        n_ext = sum(1 for s in setups if s.timeline.external_breakout_candle_time)
        n_inval = sum(1 for s in setups if s.state == SetupState.INVALIDATED)
        n_exp = sum(1 for s in setups if s.state == SetupState.EXPIRED)

        print(f"\n{htf_label} (Counting Triggers: {n_count} out of {len(total_trigs_all)} total):")
        print(f"  - Reached ANY Breakout Alert (Int or Ext) : {n_any_breakout:>5} ({n_any_breakout/n_count:>6.1%})")
        print(f"  - Reached EXTERNAL Breakout              : {n_ext:>5} ({n_ext/n_count:>6.1%})")
        print(f"  - Ended INVALIDATED (closed beyond X)    : {n_inval:>5} ({n_inval/n_count:>6.1%})")
        print(f"  - Ended EXPIRED (no break in window)     : {n_exp:>5} ({n_exp/n_count:>6.1%})")

    return {
        "tot_alerts_mo": tot_alerts_mo,
        "d_alerts_mo": d_alerts_mo,
        "w_alerts_mo": w_alerts_mo,
        "d_ext": len(d_ext),
        "d_int": len(d_int),
        "w_ext": len(w_ext),
        "w_int": len(w_int),
    }


def compute_adrs(df_d: pd.DataFrame) -> pd.Series:
    """14-day average daily range (high - low)."""
    dr = df_d["high"] - df_d["low"]
    adr14 = dr.rolling(14).mean()
    return adr14


def run_outcome_statistics(res):
    print("\n" + "="*80)
    print("ITEM 4: OUTCOME STATISTICS & FORWARD PERFORMANCE ANALYSIS")
    print("="*80)

    daily_alerts = res["daily_alerts"]
    weekly_alerts = res["weekly_alerts"]
    data_store = res["data_store"]

    # Deduplicate alerts: at most one alert per instrument, direction, and day
    def dedupe_alerts(alerts: list[SLKAlert]) -> list[SLKAlert]:
        seen = set()
        deduped = []
        for a in alerts:
            day_str = a.alert_time.strftime("%Y-%m-%d")
            key = (a.symbol, a.direction.value, day_str)
            if key not in seen:
                seen.add(key)
                deduped.append(a)
        return deduped

    d_alerts_deduped = dedupe_alerts(daily_alerts)
    w_alerts_deduped = dedupe_alerts(weekly_alerts)

    print(f"Deduplicated Alerts for Outcome Evaluation (max 1/sym/dir/day):")
    print(f"  - Daily -> H4 Alerts   : {len(d_alerts_deduped)} (from {len(daily_alerts)})")
    print(f"  - Weekly -> Daily Alerts: {len(w_alerts_deduped)} (from {len(weekly_alerts)})")

    # Evaluate an alert's outcome
    # Daily -> H4: next 5 daily closes (N=5)
    # Weekly -> Daily: next 10 daily closes (N=10)
    def evaluate_alert_outcome(alert: SLKAlert, htf_tf: str, N: int):
        sym = alert.symbol
        df_d = data_store[sym]["D"]
        ltf_df = data_store[sym]["H4"] if htf_tf == "D" else df_d

        t_alert = alert.alert_time

        # Entry = close of the LTF candle at alert time
        # The LTF candle closing at alert_time has open_time < alert_time
        # For H4: if alert_time is 21:00 UTC, the candle opened at 17:00 UTC.
        # Find candle whose index <= alert_time
        ltf_sub = ltf_df[ltf_df.index <= t_alert]
        if ltf_sub.empty:
            return None
        entry = float(ltf_sub["close"].iloc[-1])

        # ADR14 as of the alert date
        d_sub = df_d[df_d.index <= t_alert]
        if len(d_sub) < 15:
            return None
        dr = d_sub["high"] - d_sub["low"]
        adr14 = float(dr.iloc[-14:].mean())
        if adr14 <= 0:
            return None

        # Next N daily candles closed strictly after t_alert
        post_d = df_d[df_d.index >= t_alert]
        if len(post_d) < N:
            return None
        eval_window = post_d.iloc[:N]

        close_N = float(eval_window["close"].iloc[-1])
        direction = alert.direction

        if direction == TriggerDirection.BULLISH:
            win = close_N > entry
            move_pct = (close_N - entry) / entry * 100.0
            move_adr = (close_N - entry) / adr14

            # Hit +1 ADR before -1 ADR
            target_hit = False
            for _, row in eval_window.iterrows():
                h = row["high"]
                l = row["low"]
                hit_pos = h >= (entry + adr14)
                hit_neg = l <= (entry - adr14)
                if hit_pos and not hit_neg:
                    target_hit = True
                    break
                elif hit_neg and not hit_pos:
                    target_hit = False
                    break
                elif hit_pos and hit_neg:
                    target_hit = False
                    break
        else:
            win = close_N < entry
            move_pct = (entry - close_N) / entry * 100.0
            move_adr = (entry - close_N) / adr14

            target_hit = False
            for _, row in eval_window.iterrows():
                h = row["high"]
                l = row["low"]
                hit_pos = l <= (entry - adr14)
                hit_neg = h >= (entry + adr14)
                if hit_pos and not hit_neg:
                    target_hit = True
                    break
                elif hit_neg and not hit_pos:
                    target_hit = False
                    break
                elif hit_pos and hit_neg:
                    target_hit = False
                    break

        return {
            "win": win,
            "move_pct": move_pct,
            "move_adr": move_adr,
            "target_hit": target_hit,
            "alert": alert,
        }

    # Generate random baseline outcomes
    random.seed(42)
    baseline_d = []
    baseline_w = []

    for sym in [inst["name"] for inst in instruments]:
        df_d = data_store[sym]["D"]
        if len(df_d) < 30:
            continue
        # Random dates
        for _ in range(50):
            idx = random.randint(15, len(df_d) - 12)
            t_rand = df_d.index[idx]
            entry = float(df_d["close"].iloc[idx])
            dr = df_d["high"].iloc[idx-14:idx] - df_d["low"].iloc[idx-14:idx]
            adr14 = float(dr.mean())
            if adr14 <= 0:
                continue
            rand_dir = random.choice([TriggerDirection.BULLISH, TriggerDirection.BEARISH])

            # N=5
            window_5 = df_d.iloc[idx:idx+5]
            if len(window_5) == 5:
                close_5 = float(window_5["close"].iloc[-1])
                if rand_dir == TriggerDirection.BULLISH:
                    win_5 = close_5 > entry
                    move_pct_5 = (close_5 - entry) / entry * 100.0
                    move_adr_5 = (close_5 - entry) / adr14
                    hit_5 = (window_5["high"].max() >= entry + adr14) and not (window_5["low"].min() <= entry - adr14)
                else:
                    win_5 = close_5 < entry
                    move_pct_5 = (entry - close_5) / entry * 100.0
                    move_adr_5 = (entry - close_5) / adr14
                    hit_5 = (window_5["low"].min() <= entry - adr14) and not (window_5["high"].max() >= entry + adr14)
                baseline_d.append({"win": win_5, "move_pct": move_pct_5, "move_adr": move_adr_5, "target_hit": hit_5})

            # N=10
            window_10 = df_d.iloc[idx:idx+10]
            if len(window_10) == 10:
                close_10 = float(window_10["close"].iloc[-1])
                if rand_dir == TriggerDirection.BULLISH:
                    win_10 = close_10 > entry
                    move_pct_10 = (close_10 - entry) / entry * 100.0
                    move_adr_10 = (close_10 - entry) / adr14
                    hit_10 = (window_10["high"].max() >= entry + adr14) and not (window_10["low"].min() <= entry - adr14)
                else:
                    win_10 = close_10 < entry
                    move_pct_10 = (entry - close_10) / entry * 100.0
                    move_adr_10 = (entry - close_10) / adr14
                    hit_10 = (window_10["low"].min() <= entry - adr14) and not (window_10["high"].max() >= entry + adr14)
                baseline_w.append({"win": win_10, "move_pct": move_pct_10, "move_adr": move_adr_10, "target_hit": hit_10})

    # Evaluate all alerts
    d_outcomes = []
    for a in d_alerts_deduped:
        res_o = evaluate_alert_outcome(a, "D", N=5)
        if res_o is not None:
            d_outcomes.append(res_o)

    w_outcomes = []
    for a in w_alerts_deduped:
        res_o = evaluate_alert_outcome(a, "W", N=10)
        if res_o is not None:
            w_outcomes.append(res_o)

    def print_group_stats(group_name: str, outcomes: list[dict]):
        if not outcomes:
            print(f"  {group_name:<30} | N/A")
            return
        n = len(outcomes)
        win_rate = sum(1 for o in outcomes if o["win"]) / n * 100.0
        avg_pct = np.mean([o["move_pct"] for o in outcomes])
        avg_adr = np.mean([o["move_adr"] for o in outcomes])
        target_hit = sum(1 for o in outcomes if o["target_hit"]) / n * 100.0
        print(f"  {group_name:<35} | {n:>5} | {win_rate:>6.1f}% | {avg_pct:>+7.2f}% | {avg_adr:>+6.2f} ADR | {target_hit:>6.1f}%")

    header = f"  {'Group':<35} | {'Count':>5} | {'Win Rate':>7} | {'Avg Move%':>9} | {'Avg Move ADR':>12} | {'+1/-1 ADR':>9}"
    print("\n" + header)
    print("  " + "-" * len(header))

    # Daily -> H4 Groups (N=5 Days)
    print("  --- DAILY -> H4 SETUPS (Horizon: 5 Daily Closes) ---")
    print_group_stats("Daily All Alerts", d_outcomes)
    print_group_stats("  Method 1 (Rejection)", [o for o in d_outcomes if int(o["alert"].method) == 1])
    print_group_stats("  Method 2 (Sweep)", [o for o in d_outcomes if int(o["alert"].method) == 2])
    print_group_stats("  Method 3 (Combined)", [o for o in d_outcomes if int(o["alert"].method) == 3])
    print_group_stats("  External Breakout", [o for o in d_outcomes if o["alert"].breakout_type == BreakoutType.EXTERNAL])
    print_group_stats("  Internal Breakout", [o for o in d_outcomes if o["alert"].breakout_type == BreakoutType.INTERNAL])
    print_group_stats("  Fresh Level", [o for o in d_outcomes if not o["alert"].is_low_probability])
    print_group_stats("  Unfresh Level (M3 only)", [o for o in d_outcomes if o["alert"].is_low_probability])
    print_group_stats("  Marginal", [o for o in d_outcomes if o["alert"].is_marginal])
    print_group_stats("  Non-Marginal", [o for o in d_outcomes if not o["alert"].is_marginal])
    print_group_stats("Random Baseline (N=5)", baseline_d)

    print("\n  --- WEEKLY -> DAILY SETUPS (Horizon: 10 Daily Closes) ---")
    print_group_stats("Weekly All Alerts", w_outcomes)
    print_group_stats("  Method 1 (Rejection)", [o for o in w_outcomes if int(o["alert"].method) == 1])
    print_group_stats("  Method 2 (Sweep)", [o for o in w_outcomes if int(o["alert"].method) == 2])
    print_group_stats("  Method 3 (Combined)", [o for o in w_outcomes if int(o["alert"].method) == 3])
    print_group_stats("  External Breakout", [o for o in w_outcomes if o["alert"].breakout_type == BreakoutType.EXTERNAL])
    print_group_stats("  Internal Breakout", [o for o in w_outcomes if o["alert"].breakout_type == BreakoutType.INTERNAL])
    print_group_stats("  Fresh Level", [o for o in w_outcomes if not o["alert"].is_low_probability])
    print_group_stats("  Unfresh Level (M3 only)", [o for o in w_outcomes if o["alert"].is_low_probability])
    print_group_stats("  Marginal", [o for o in w_outcomes if o["alert"].is_marginal])
    print_group_stats("  Non-Marginal", [o for o in w_outcomes if not o["alert"].is_marginal])
    print_group_stats("Random Baseline (N=10)", baseline_w)


if __name__ == "__main__":
    # Run Rule (a): nearest to extreme
    res_a = run_pipeline("nearest_extreme")
    stats_a = analyze_rule_results(res_a, "Rule (a): Nearest to Extreme")

    # Run Rule (b): nearest to extreme among FRESH
    res_b = run_pipeline("fresh_first")
    stats_b = analyze_rule_results(res_b, "Rule (b): Nearest Among Fresh, Fallback Nearest Overall")

    print("\n" + "="*80)
    print("COMPARISON: RULE (a) vs RULE (b)")
    print("="*80)
    print(f"Total Would-be Alerts per Month across Watchlist:")
    print(f"  - Rule (a) Nearest Extreme    : {stats_a['tot_alerts_mo']:.1f} / mo (Daily: {stats_a['d_alerts_mo']:.1f}, Weekly: {stats_a['w_alerts_mo']:.1f})")
    print(f"  - Rule (b) Fresh-First Fallback: {stats_b['tot_alerts_mo']:.1f} / mo (Daily: {stats_b['d_alerts_mo']:.1f}, Weekly: {stats_b['w_alerts_mo']:.1f})")
    diff_mo = stats_b['tot_alerts_mo'] - stats_a['tot_alerts_mo']
    diff_pct = diff_mo / stats_a['tot_alerts_mo'] * 100.0
    print(f"  - Difference                   : {diff_mo:+.1f} / mo ({diff_pct:+.2f}%)")

    # Run Outcome Statistics on Rule (a)
    run_outcome_statistics(res_a)
