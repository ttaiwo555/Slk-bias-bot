"""
analysis/run_volume_analysis.py
-------------------------------
Computes Volume metrics for Item 5:
  - Settings: parity settings (max_disrespects=None, lookback=None, rule b fresh_first),
    Trend Required (Candidate c at alert time), external only, sweep-only off.
  - Weekdays: 12-16 Oct 2026 (Mon-Fri)
Outputs:
  - Daily breakdown (Monday to Friday) of our alerts vs reference alerts (35 total)
  - Number of extra setups not in reference per instrument
  - Top 20 instruments with the most extra setups
"""
import sys
from pathlib import Path
import pandas as pd
import yaml

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
from analysis.evaluate_trend_three_moments import get_trend_at_time

cache = CandleCache()
ref_df = pd.read_csv(ROOT / "reference_setups.csv")

# Date range: 12 Oct 2026 00:00 to 16 Oct 2026 23:59:59 UTC
START_WEEK = pd.Timestamp("2026-10-12 00:00:00", tz="UTC")
END_WEEK = pd.Timestamp("2026-10-16 23:59:59", tz="UTC")

with open(ROOT / "slk_bot" / "config" / "instruments.yaml", "r", encoding="utf-8") as f:
    inst_data = yaml.safe_load(f)

in_scope_symbols = set(ref_df["symbol"].unique())
instruments = [inst for inst in inst_data.get("instruments", []) if inst["name"] in in_scope_symbols]

engine_alerts: list[SLKAlert] = []

for inst in instruments:
    sym = inst["name"]
    ac = inst.get("asset_class", "forex")
    
    # Daily -> H4
    df_d = cache.load_candles(sym, "D")
    df_h4 = cache.load_candles(sym, "H4")
    if not df_d.empty and not df_h4.empty:
        trigs_d = detect_triggers(
            df_d, "D", sym, ac,
            lookback=None, max_disrespects=None, ocl_max_disrespects=None,
            primary_selection_rule="fresh_first",
        )
        h4_levels = detect_ltf_levels(df_h4, sym, "H4", 0.0)
        for t in trigs_d:
            if t.method == TriggerMethod.METHOD_2:
                continue
            if t.trigger_candle_time < pd.Timestamp("2026-09-20", tz="UTC"):
                continue
            s = evaluate_setup_lifecycle(
                t, df_d, df_h4, df_d, sym, "D", "H4",
                unfresh_alerts=True, all_ltf_levels=h4_levels,
                external_ref_point="first_touch",
            )
            for a in s.alerts:
                if a.breakout_type == BreakoutType.EXTERNAL and START_WEEK <= a.breakout_candle_time <= END_WEEK:
                    # Check Trend Required (Candidate c at alert time)
                    prior_d = df_d[df_d.index <= a.breakout_candle_time]
                    asof = prior_d.index[-1] if not prior_d.empty else a.breakout_candle_time
                    trend, _ = get_trend_at_time(sym, "D", asof, "c")
                    exp_trend = "BULLISH" if a.direction == TriggerDirection.BULLISH else "BEARISH"
                    if trend == exp_trend:
                        engine_alerts.append(a)
                        
    # Weekly -> Daily
    df_w = cache.load_candles(sym, "W")
    if not df_w.empty and not df_d.empty:
        trigs_w = detect_triggers(
            df_w, "W", sym, ac,
            lookback=None, max_disrespects=None, ocl_max_disrespects=None,
            primary_selection_rule="fresh_first",
        )
        d_levels = detect_ltf_levels(df_d, sym, "D", 0.0)
        for t in trigs_w:
            if t.method == TriggerMethod.METHOD_2:
                continue
            if t.trigger_candle_time < pd.Timestamp("2026-08-01", tz="UTC"):
                continue
            s = evaluate_setup_lifecycle(
                t, df_w, df_d, df_d, sym, "W", "D",
                unfresh_alerts=True, all_ltf_levels=d_levels,
                external_ref_point="first_touch",
            )
            for a in s.alerts:
                if a.breakout_type == BreakoutType.EXTERNAL and START_WEEK <= a.breakout_candle_time <= END_WEEK:
                    prior_w = df_w[df_w.index <= a.breakout_candle_time]
                    asof = prior_w.index[-1] if not prior_w.empty else a.breakout_candle_time
                    trend, _ = get_trend_at_time(sym, "W", asof, "c")
                    exp_trend = "BULLISH" if a.direction == TriggerDirection.BULLISH else "BEARISH"
                    if trend == exp_trend:
                        engine_alerts.append(a)

# Parse reference setups by weekday
ref_df["bo_dt"] = pd.to_datetime(ref_df["bo_open_utc"])
ref_df["weekday"] = ref_df["bo_dt"].dt.day_name()
ref_counts_by_day = ref_df["weekday"].value_counts().to_dict()

# Our alerts by weekday
days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
our_alerts_df = pd.DataFrame([
    {
        "symbol": a.symbol,
        "direction": a.direction.value,
        "bo_time": a.breakout_candle_time,
        "weekday": a.breakout_candle_time.day_name(),
        "htf": a.htf,
        "price": a.broken_level.price,
    }
    for a in engine_alerts
])

# Deduplicate to unique setups per symbol, direction, day
our_unique = our_alerts_df.drop_duplicates(subset=["symbol", "direction", "weekday"])

print("=" * 80)
print("ALERTS PER WEEKDAY (12-16 OCT 2026) [Trend Required, External Only, Sweep-Only Off]")
print("=" * 80)
print(f"{'Weekday':<12} | {'Our Engine (Total)':>18} | {'Our Engine (Unique)':>19} | {'Reference Setups':>16}")
print("-" * 72)
for day in days_order:
    our_tot = (our_alerts_df["weekday"] == day).sum() if not our_alerts_df.empty else 0
    our_uniq = (our_unique["weekday"] == day).sum() if not our_unique.empty else 0
    ref_cnt = ref_counts_by_day.get(day, 0)
    print(f"{day:<12} | {our_tot:>18} | {our_uniq:>19} | {ref_cnt:>16}")
print("-" * 72)
tot_our_tot = len(our_alerts_df)
tot_our_uniq = len(our_unique)
tot_ref = len(ref_df)
print(f"{'Total':<12} | {tot_our_tot:>18} | {tot_our_uniq:>19} | {tot_ref:>16}")
print("=" * 80)

# Check extra setups per instrument
# Load match_table to get matched keys
match_df = pd.read_csv(ROOT / "analysis" / "output" / "match_table.csv")
matched_ids = match_df[match_df["status"] == "MATCH"]["id"].tolist()
matched_ref = ref_df[ref_df["id"].isin(matched_ids)]

extras_per_sym = {}
for sym in in_scope_symbols:
    our_sym_alerts = our_alerts_df[our_alerts_df["symbol"] == sym]
    ref_sym_alerts = ref_df[ref_df["symbol"] == sym]
    diff = max(0, len(our_sym_alerts) - len(ref_sym_alerts))
    extras_per_sym[sym] = {
        "our_alerts": len(our_sym_alerts),
        "ref_alerts": len(ref_sym_alerts),
        "extra_alerts": diff,
    }

df_extras_sym = pd.DataFrame.from_dict(extras_per_sym, orient="index")
df_extras_sym.sort_values(by="extra_alerts", ascending=False, inplace=True)

print("\nTOP 20 INSTRUMENTS WITH MOST EXTRA SETUPS:")
print(f"{'Rank':<5} | {'Instrument':<12} | {'Our Alerts':>10} | {'Ref Alerts':>10} | {'Extra Setups':>12}")
print("-" * 60)
for rank, (sym, r) in enumerate(df_extras_sym.head(20).iterrows(), 1):
    print(f"{rank:<5} | {sym:<12} | {int(r['our_alerts']):>10} | {int(r['ref_alerts']):>10} | {int(r['extra_alerts']):>12}")
print("=" * 60)
