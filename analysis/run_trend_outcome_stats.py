"""
analysis/run_trend_outcome_stats.py
-----------------------------------
Re-runs the forward outcome statistics (from Phase 3) split by:
  - All Alerts
  - Trend Aligned
  - Trend Unaligned (Against Trend)
  - Random Baseline

Horizon:
  - Daily -> H4 setups: 5 Daily Closes
  - Weekly -> Daily setups: 10 Daily Closes

Metrics:
  - Count
  - Win Rate (close > entry in bias direction)
  - Avg Move %
  - Avg Move in ADR(14) multiples
  - +1/-1 ADR Hit Rate (hit +1 ADR before -1 ADR)
"""

import sys
import os
import random
from pathlib import Path
import pandas as pd
import numpy as np
import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import (
    evaluate_setup_lifecycle,
    detect_ltf_levels,
    BreakoutType,
    SLKAlert,
)
from slk_bot.strategy.models import TriggerDirection, TriggerMethod
from slk_bot.strategy.trend import get_trend

cache = CandleCache()
INST_PATH = ROOT / "slk_bot" / "config" / "instruments.yaml"
with open(INST_PATH, "r", encoding="utf-8") as f:
    INST_DATA = yaml.safe_load(f)

ignored = {"EU50", "JPN225", "DOTUSDT"}
instruments = [
    inst for inst in INST_DATA.get("instruments", [])
    if not inst.get("exclude_from_triggers") and inst["name"] not in ignored
]

# Preload data
data_store = {}
for inst in instruments:
    sym = inst["name"]
    df_d = cache.load_candles(sym, "D")
    df_h4 = cache.load_candles(sym, "H4")
    df_w = cache.load_candles(sym, "W")
    data_store[sym] = {"D": df_d, "H4": df_h4, "W": df_w, "class": inst.get("asset_class", "forex")}

def evaluate_alert_outcome(alert: SLKAlert, htf: str, N: int = 5):
    sym = alert.symbol
    df_d = data_store[sym]["D"]
    if df_d.empty:
        return None

    t_alert = alert.alert_time
    ltf_df = data_store[sym]["H4"] if htf == "D" else df_d
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

    final_close = float(eval_window["close"].iloc[-1])

    if alert.direction == TriggerDirection.BULLISH:
        win = final_close > entry
        move_pct = (final_close - entry) / entry * 100.0
        move_adr = (final_close - entry) / adr14

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
        win = final_close < entry
        move_pct = (entry - final_close) / entry * 100.0
        move_adr = (entry - final_close) / adr14

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

def main():
    print("Collecting alerts and evaluating outcomes...")
    d_alerts = []
    w_alerts = []

    for inst in instruments:
        sym = inst["name"]
        asset_class = data_store[sym]["class"]
        df_d = data_store[sym]["D"]
        df_h4 = data_store[sym]["H4"]
        df_w = data_store[sym]["W"]

        if not df_d.empty and not df_h4.empty:
            trigs_d = detect_triggers(df_d, sym, "D", asset_class, lookback=250, max_disrespects=3, ocl_max_disrespects=3)
            h4_levels = detect_ltf_levels(df_h4, sym, "H4", 0.0)
            for t in trigs_d:
                # Counting rule: M1 fresh only, M2 always, M3 always
                if t.method == TriggerMethod.METHOD_1 and not (t.primary_level and t.primary_level.fresh):
                    continue
                s = evaluate_setup_lifecycle(t, df_d, df_h4, df_d, sym, "D", "H4", unfresh_alerts=False, all_ltf_levels=h4_levels)
                for a in s.alerts:
                    if a.breakout_type == BreakoutType.EXTERNAL:
                        d_alerts.append(a)

        if not df_w.empty and not df_d.empty:
            trigs_w = detect_triggers(df_w, sym, "W", asset_class, lookback=104, max_disrespects=3, ocl_max_disrespects=3)
            d_levels = detect_ltf_levels(df_d, sym, "D", 0.0)
            for t in trigs_w:
                if t.method == TriggerMethod.METHOD_1 and not (t.primary_level and t.primary_level.fresh):
                    continue
                s = evaluate_setup_lifecycle(t, df_w, df_d, df_d, sym, "W", "D", unfresh_alerts=False, all_ltf_levels=d_levels)
                for a in s.alerts:
                    if a.breakout_type == BreakoutType.EXTERNAL:
                        w_alerts.append(a)

    # Deduplicate: at most 1 alert per symbol, direction, and day
    def dedupe(alerts):
        seen = set()
        out = []
        for a in alerts:
            k = (a.symbol, a.direction, a.breakout_candle_time.date())
            if k not in seen:
                seen.add(k)
                out.append(a)
        return out

    d_deduped = dedupe(d_alerts)
    w_deduped = dedupe(w_alerts)

    print(f"Total Daily External Alerts (Deduped): {len(d_deduped)}")
    print(f"Total Weekly External Alerts (Deduped): {len(w_deduped)}")

    # Evaluate outcomes
    d_outcomes = []
    for a in d_deduped:
        res = evaluate_alert_outcome(a, "D", N=5)
        if res:
            trend = get_trend(a.symbol, "D", a.breakout_candle_time)
            res["aligned"] = (trend == a.direction)
            d_outcomes.append(res)

    w_outcomes = []
    for a in w_deduped:
        res = evaluate_alert_outcome(a, "W", N=10)
        if res:
            trend = get_trend(a.symbol, "W", a.breakout_candle_time)
            res["aligned"] = (trend == a.direction)
            w_outcomes.append(res)

    # Random baseline
    random.seed(42)
    baseline_d = []
    baseline_w = []
    for sym in [inst["name"] for inst in instruments]:
        df_d = data_store[sym]["D"]
        if len(df_d) < 35:
            continue
        for _ in range(30):
            idx = random.randint(15, len(df_d) - 12)
            entry = float(df_d["close"].iloc[idx])
            dr = df_d["high"].iloc[idx-14:idx] - df_d["low"].iloc[idx-14:idx]
            adr14 = float(dr.mean())
            if adr14 <= 0:
                continue
            rand_dir = random.choice([TriggerDirection.BULLISH, TriggerDirection.BEARISH])

            # N=5
            w5 = df_d.iloc[idx:idx+5]
            if len(w5) == 5:
                c5 = float(w5["close"].iloc[-1])
                if rand_dir == TriggerDirection.BULLISH:
                    win5 = c5 > entry
                    pct5 = (c5 - entry) / entry * 100.0
                    adr5 = (c5 - entry) / adr14
                    hit5 = (w5["high"].max() >= entry + adr14) and not (w5["low"].min() <= entry - adr14)
                else:
                    win5 = c5 < entry
                    pct5 = (entry - c5) / entry * 100.0
                    adr5 = (entry - c5) / adr14
                    hit5 = (w5["low"].min() <= entry - adr14) and not (w5["high"].max() >= entry + adr14)
                baseline_d.append({"win": win5, "move_pct": pct5, "move_adr": adr5, "target_hit": hit5})

            # N=10
            w10 = df_d.iloc[idx:idx+10]
            if len(w10) == 10:
                c10 = float(w10["close"].iloc[-1])
                if rand_dir == TriggerDirection.BULLISH:
                    win10 = c10 > entry
                    pct10 = (c10 - entry) / entry * 100.0
                    adr10 = (c10 - entry) / adr14
                    hit10 = (w10["high"].max() >= entry + adr14) and not (w10["low"].min() <= entry - adr14)
                else:
                    win10 = c10 < entry
                    pct10 = (entry - c10) / entry * 100.0
                    adr10 = (entry - c10) / adr14
                    hit10 = (w10["low"].min() <= entry - adr14) and not (w10["high"].max() >= entry + adr14)
                baseline_w.append({"win": win10, "move_pct": pct10, "move_adr": adr10, "target_hit": hit10})

    def print_group_stats(group_name: str, outcomes: list[dict]):
        if not outcomes:
            print(f"  {group_name:<35} | N/A")
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

    print("  --- DAILY -> H4 SETUPS (Horizon: 5 Daily Closes) ---")
    print_group_stats("Daily All External Alerts", d_outcomes)
    print_group_stats("  Trend Aligned (Required)", [o for o in d_outcomes if o.get("aligned")])
    print_group_stats("  Trend Unaligned (Against Trend)", [o for o in d_outcomes if not o.get("aligned")])
    print_group_stats("Random Baseline (N=5)", baseline_d)

    print("\n  --- WEEKLY -> DAILY SETUPS (Horizon: 10 Daily Closes) ---")
    print_group_stats("Weekly All External Alerts", w_outcomes)
    print_group_stats("  Trend Aligned (Required)", [o for o in w_outcomes if o.get("aligned")])
    print_group_stats("  Trend Unaligned (Against Trend)", [o for o in w_outcomes if not o.get("aligned")])
    print_group_stats("Random Baseline (N=10)", baseline_w)

if __name__ == "__main__":
    main()
