"""
analysis/fast_profile_comparison_item12.py
------------------------------------------
Fast profile comparison between reference_parity and forex_com for 12-16 Oct 2026.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.data.candles import apply_open_continuity
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import (
    evaluate_setup_lifecycle,
    detect_ltf_levels,
    BreakoutType,
    SLKAlert,
)
from slk_bot.strategy.models import TriggerDirection, TriggerMethod
from analysis.evaluate_trend_three_moments import compute_trend_b_c

cache = CandleCache()
INST_PATH = ROOT / "slk_bot" / "config" / "instruments.yaml"
with open(INST_PATH, "r", encoding="utf-8") as f:
    INST_DATA = yaml.safe_load(f)

ref_df = pd.read_csv(ROOT / "reference_setups.csv")
in_scope_syms = set(ref_df["symbol"].unique())
instruments = [inst for inst in INST_DATA.get("instruments", []) if inst["name"] in in_scope_syms]

WINDOW_START = pd.Timestamp("2026-10-12 00:00:00", tz="UTC")
WINDOW_END   = pd.Timestamp("2026-10-16 23:59:59", tz="UTC")
TRIG_CUTOFF  = pd.Timestamp("2026-08-01 00:00:00", tz="UTC")

def get_profile_candles(sym: str, profile_name: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df_d = cache.load_candles(sym, "D")
    df_h4 = cache.load_candles(sym, "H4")
    df_w = cache.load_candles(sym, "W")

    if profile_name == "forex_com":
        if not df_d.empty:
            df_d = apply_open_continuity(df_d.copy())
        if not df_h4.empty:
            df_h4 = apply_open_continuity(df_h4.copy())
        if not df_w.empty:
            df_w = apply_open_continuity(df_w.copy())

    return df_d, df_h4, df_w

def run_evaluation(profile_name: str) -> dict:
    alerts_by_sym = {}
    all_alerts = []
    trend_cache = {}

    for inst in instruments:
        sym = inst["name"]
        ac = inst.get("asset_class", "forex")
        df_d, df_h4, df_w = get_profile_candles(sym, profile_name)

        if not df_d.empty:
            thist_d = compute_trend_b_c(df_d, use_marginal=True, sym=sym)
            trend_cache[(sym, "D")] = [(h[0], h[1]) for h in thist_d]
        if not df_w.empty:
            thist_w = compute_trend_b_c(df_w, use_marginal=True, sym=sym)
            trend_cache[(sym, "W")] = [(h[0], h[1]) for h in thist_w]

        sym_alerts = []

        # D1 -> H4
        if not df_d.empty and not df_h4.empty:
            trigs_d = detect_triggers(
                df_d, "D", sym, ac,
                lookback=None, max_disrespects=None, ocl_max_disrespects=None,
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
                    if a.breakout_type == BreakoutType.EXTERNAL:
                        sym_alerts.append(a)

        # W1 -> D1 (USOIL only in scope)
        if sym == "USOIL" and not df_w.empty and not df_d.empty:
            trigs_w = detect_triggers(
                df_w, "W", sym, ac,
                lookback=None, max_disrespects=None, ocl_max_disrespects=None,
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
                    if a.breakout_type == BreakoutType.EXTERNAL:
                        sym_alerts.append(a)

        alerts_by_sym[sym] = sym_alerts
        all_alerts.extend(sym_alerts)

    # 1. Match against 35 reference setups
    match_rows = []
    same_bo_cnt = 0

    for _, ref_row in ref_df.iterrows():
        rid = ref_row["id"]
        sym = ref_row["symbol"]
        rdir = ref_row["dir"].upper()
        exp_dir = TriggerDirection.BEARISH if rdir == "SELL" else TriggerDirection.BULLISH
        htf_tf = "W" if "W1" in ref_row["pair"] else "D"
        ref_rej = pd.Timestamp(ref_row["rej_open_utc"], tz="UTC")
        ref_bo = pd.Timestamp(ref_row["bo_open_utc"], tz="UTC")
        ref_kl = float(ref_row["kl_price"])
        ref_bo_p = float(ref_row["bo_price"])

        cands = [a for a in alerts_by_sym.get(sym, []) if a.direction == exp_dir and a.htf == htf_tf]

        if not cands:
            match_rows.append({"id": rid, "status": "MISSING", "same_bo": False})
            continue

        scored = []
        for a in cands:
            same_bo = (a.breakout_candle_time == ref_bo)
            kl_diff = abs(a.htf_level.price - ref_kl) / ref_kl * 100.0 if a.htf_level else 999.0
            bo_diff = abs(a.broken_level.price - ref_bo_p) / ref_bo_p * 100.0
            time_diff_h = abs((a.breakout_candle_time - ref_bo).total_seconds()) / 3600.0
            is_match = same_bo and (kl_diff <= 0.20) and (bo_diff <= 0.20)
            scored.append({
                "alert": a,
                "is_match": is_match,
                "same_bo": same_bo,
                "time_diff_h": time_diff_h,
                "kl_diff": kl_diff,
                "bo_diff": bo_diff,
            })

        scored.sort(key=lambda x: (not x["is_match"], not x["same_bo"], x["time_diff_h"], x["kl_diff"]))
        best = scored[0]

        status = "MATCH" if best["is_match"] else "PARTIAL"
        if best["same_bo"]:
            same_bo_cnt += 1
        match_rows.append({"id": rid, "status": status, "same_bo": best["same_bo"]})

    df_res = pd.DataFrame(match_rows)
    vc = df_res["status"].value_counts().to_dict()
    status_summary = {
        "MATCH": vc.get("MATCH", 0),
        "PARTIAL": vc.get("PARTIAL", 0),
        "MISSING": vc.get("MISSING", 0),
        "SAME_BO_CANDLE": same_bo_cnt,
    }

    # 2. Weekday unique volume in 12-16 Oct
    window_alerts = [a for a in all_alerts if WINDOW_START <= a.breakout_candle_time <= WINDOW_END]

    trend_aligned_alerts = []
    for a in window_alerts:
        df_d, _, df_w = get_profile_candles(a.symbol, profile_name)
        htf_df = df_w if a.htf == "W" else df_d
        prior_htf = htf_df[htf_df.index < a.breakout_candle_time]
        asof = prior_htf.index[-1] if not prior_htf.empty else a.breakout_candle_time
        thist = trend_cache.get((a.symbol, a.htf), [])
        priors = [h for h in thist if h[0] <= asof]
        trend = priors[-1][1] if priors else "NONE"
        exp = "BULLISH" if a.direction == TriggerDirection.BULLISH else "BEARISH"
        if trend == exp:
            trend_aligned_alerts.append(a)

    days = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]

    vol_trend_off = {}
    vol_trend_req = {}

    for d_str, d_name in zip(days, day_names):
        setups_off = {(a.symbol, a.direction.value) for a in window_alerts if a.breakout_candle_time.strftime("%Y-%m-%d") == d_str}
        setups_req = {(a.symbol, a.direction.value) for a in trend_aligned_alerts if a.breakout_candle_time.strftime("%Y-%m-%d") == d_str}
        vol_trend_off[d_name] = len(setups_off)
        vol_trend_req[d_name] = len(setups_req)

    return {
        "status": status_summary,
        "vol_trend_off": vol_trend_off,
        "vol_trend_req": vol_trend_req,
        "total_vol_trend_off": sum(vol_trend_off.values()),
        "total_vol_trend_req": sum(vol_trend_req.values()),
    }

def main():
    print("=" * 80)
    print("PROFILE COMPARISON: reference_parity vs forex_com (12-16 Oct 2026)")
    print("=" * 80)

    for prof in ["reference_parity", "forex_com"]:
        res = run_evaluation(prof)
        st = res["status"]
        print(f"\n--- PROFILE: {prof} ---")
        print(f"Status Counter: MATCH: {st['MATCH']} | PARTIAL: {st['PARTIAL']} | MISSING: {st['MISSING']}")
        print(f"Same Breakout Candle: {st['SAME_BO_CANDLE']} / 35 ({st['SAME_BO_CANDLE']/35*100.0:.1f}%)")
        print("Unique Setups Per Weekday (Trend Required, Candidate c):")
        for day, cnt in res["vol_trend_req"].items():
            print(f"  {day}: {cnt}")
        print(f"  Total Week: {res['total_vol_trend_req']}")
        print("Unique Setups Per Weekday (Trend Off):")
        for day, cnt in res["vol_trend_off"].items():
            print(f"  {day}: {cnt}")
        print(f"  Total Week: {res['total_vol_trend_off']}")

if __name__ == "__main__":
    main()
