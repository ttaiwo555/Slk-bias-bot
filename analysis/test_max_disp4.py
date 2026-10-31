import sys
sys.path.insert(0, '.')
import yaml
from pathlib import Path
import pandas as pd

from slk_bot.storage.cache import CandleCache
from slk_bot.data.candles import apply_open_continuity
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import evaluate_setup_lifecycle, detect_ltf_levels, BreakoutType
from slk_bot.strategy.models import TriggerDirection, TriggerMethod, KeyLevel
from analysis.evaluate_trend_three_moments import compute_trend_b_c

cache = CandleCache()
INST_PATH = Path("slk_bot/config/instruments.yaml")
with open(INST_PATH, "r", encoding="utf-8") as f:
    INST_DATA = yaml.safe_load(f)

WINDOW_START = pd.Timestamp("2026-10-19 00:00:00", tz="UTC")
WINDOW_END   = pd.Timestamp("2026-10-25 23:59:59", tz="UTC")
TRIG_CUTOFF  = pd.Timestamp("2026-08-01 00:00:00", tz="UTC")

ref_df = pd.read_csv("reference_setups.csv")
core_18_syms = set(ref_df["symbol"].unique())
instruments = [inst for inst in INST_DATA["instruments"] if not inst.get("exclude_from_triggers")]

print(f"Scanning {len(instruments)} instruments with max_disrespects=4...")

candles_by_sym = {}
trend_cache = {}
for inst in instruments:
    sym = inst["name"]
    df_d = apply_open_continuity(cache.load_candles(sym, "D"))
    df_h4 = apply_open_continuity(cache.load_candles(sym, "H4"))
    df_w = apply_open_continuity(cache.load_candles(sym, "W"))
    candles_by_sym[sym] = (df_d, df_h4, df_w)

    if not df_d.empty:
        thist = compute_trend_b_c(df_d, use_marginal=True, sym=sym)
        trend_cache[(sym, "D")] = [(h[0], h[1]) for h in thist]
    if not df_w.empty:
        thist_w = compute_trend_b_c(df_w, use_marginal=True, sym=sym)
        trend_cache[(sym, "W")] = [(h[0], h[1]) for h in thist_w]

all_alerts = []
for inst in instruments:
    sym = inst["name"]
    ac = inst.get("asset_class", "forex")
    df_d, df_h4, df_w = candles_by_sym[sym]

    # D1 -> H4 with max_disrespects=4, ocl_max_disrespects=4
    if not df_d.empty and not df_h4.empty:
        trigs_d = detect_triggers(
            df_d, "D", sym, ac,
            lookback=None, max_disrespects=4, ocl_max_disrespects=4,
            primary_selection_rule="fresh_first",
        )
        h4_lvls = detect_ltf_levels(df_h4, sym, "H4", 0.0)
        for t in trigs_d:
            if t.method == TriggerMethod.METHOD_2 or t.trigger_candle_time < TRIG_CUTOFF:
                continue
            s = evaluate_setup_lifecycle(
                t, df_d, df_h4, df_d, sym, "D", "H4",
                unfresh_alerts=True, all_ltf_levels=h4_lvls,
                external_ref_point="first_touch",
                enable_invalidation=True,
            )
            for a in s.alerts:
                if a.breakout_type == BreakoutType.EXTERNAL and WINDOW_START <= a.breakout_candle_time <= WINDOW_END:
                    prior_d = df_d[df_d.index < a.breakout_candle_time]
                    asof = prior_d.index[-1] if not prior_d.empty else a.breakout_candle_time
                    thist = trend_cache.get((sym, "D"), [])
                    priors = [h for h in thist if h[0] <= asof]
                    trend = priors[-1][1] if priors else "NONE"
                    exp = "BULLISH" if a.direction == TriggerDirection.BULLISH else "BEARISH"
                    if trend == exp:
                        all_alerts.append({
                            "alert": a,
                            "trigger": t,
                            "htf_df": df_d,
                            "df_d": df_d,
                            "trend": trend,
                            "htf_tf": "D",
                            "is_core18": (sym in core_18_syms),
                        })

    # W1 -> D1 (USOIL)
    if sym == "USOIL" and not df_w.empty and not df_d.empty:
        trigs_w = detect_triggers(
            df_w, "W", sym, ac,
            lookback=None, max_disrespects=4, ocl_max_disrespects=4,
            primary_selection_rule="fresh_first",
        )
        d_lvls = detect_ltf_levels(df_d, sym, "D", 0.0)
        for t in trigs_w:
            if t.method == TriggerMethod.METHOD_2 or t.trigger_candle_time < TRIG_CUTOFF:
                continue
            s = evaluate_setup_lifecycle(
                t, df_w, df_d, df_d, sym, "W", "D",
                unfresh_alerts=True, all_ltf_levels=d_lvls,
                external_ref_point="first_touch",
                enable_invalidation=True,
            )
            for a in s.alerts:
                if a.breakout_type == BreakoutType.EXTERNAL and WINDOW_START <= a.breakout_candle_time <= WINDOW_END:
                    prior_w = df_w[df_w.index < a.breakout_candle_time]
                    asof = prior_w.index[-1] if not prior_w.empty else a.breakout_candle_time
                    thist_w = trend_cache.get((sym, "W"), [])
                    priors = [h for h in thist_w if h[0] <= asof]
                    trend = priors[-1][1] if priors else "NONE"
                    exp = "BULLISH" if a.direction == TriggerDirection.BULLISH else "BEARISH"
                    if trend == exp:
                        all_alerts.append({
                            "alert": a,
                            "trigger": t,
                            "htf_df": df_w,
                            "df_d": df_d,
                            "trend": trend,
                            "htf_tf": "W",
                            "is_core18": True,
                        })

# Unique setup per weekday
unique_setups = {}
for item in all_alerts:
    a = item["alert"]
    sdir = "BUY" if a.direction == TriggerDirection.BULLISH else "SELL"
    k = (a.symbol, sdir, a.breakout_candle_time.strftime("%Y-%m-%d"))
    if k not in unique_setups:
        unique_setups[k] = item

print(f"Total unique setups with max_disrespects=4: {len(unique_setups)}")
