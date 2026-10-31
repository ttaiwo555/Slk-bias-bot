"""
analysis/run_doji_sweep.py
--------------------------
Evaluates Item 3:
  doji_body_threshold_pct = 0, 0.01, 0.02, 0.03, 0.05
Under parity settings:
  lookback=None, max_disrespects=None, ocl_max_disrespects=None,
  primary_selection_rule="fresh_first", external_ref_point="first_touch",
  enable_invalidation=True (default stays on).

Reports:
  - Status counter (MATCH, PARTIAL, MISSING)
  - Rows gained or lost relative to baseline (0.0)
  - Weekday volume (12-16 Oct) under Trend Required and Trend Off
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
    SLKSetup,
)
from slk_bot.strategy.models import TriggerDirection, TriggerMethod
from analysis.evaluate_trend_three_moments import get_trend_at_time

START_WEEK = pd.Timestamp("2026-10-12 00:00:00", tz="UTC")
END_WEEK = pd.Timestamp("2026-10-16 23:59:59", tz="UTC")

def get_tolerance(symbol: str, price: float) -> float:
    if symbol in ("XAGUSD", "XAUUSD"):
        return price * 0.0015
    elif symbol in ("NAS100", "SPX500", "US30", "GER40", "UK100"):
        return price * 0.0020
    return price * 0.0005

def run_for_threshold(doji_pct: float):
    cache = CandleCache()
    ref_df = pd.read_csv(ROOT / "reference_setups.csv")

    with open(ROOT / "slk_bot" / "config" / "instruments.yaml", "r", encoding="utf-8") as f:
        inst_data = yaml.safe_load(f)

    in_scope_symbols = set(ref_df["symbol"].unique())
    instruments = [inst for inst in inst_data.get("instruments", []) if inst["name"] in in_scope_symbols]

    engine_setups: list[SLKSetup] = []
    all_external_alerts: list[SLKAlert] = []

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
                doji_body_threshold_pct=doji_pct,
                primary_selection_rule="fresh_first",
            )
            trigs_d_win = [t for t in trigs_d if t.trigger_candle_time >= pd.Timestamp("2026-09-20", tz="UTC")]
            h4_levels = detect_ltf_levels(df_h4, sym, "H4", doji_pct)
            for t in trigs_d_win:
                if t.method == TriggerMethod.METHOD_2:
                    continue
                s = evaluate_setup_lifecycle(
                    trigger=t,
                    htf_df=df_d,
                    ltf_df=df_h4,
                    daily_df=df_d,
                    symbol=sym,
                    htf_tf="D",
                    ltf_tf="H4",
                    unfresh_alerts=True,
                    doji_body_threshold_pct=doji_pct,
                    all_ltf_levels=h4_levels,
                    external_ref_point="first_touch",
                    enable_invalidation=True,
                )
                engine_setups.append(s)
                for a in s.alerts:
                    if a.breakout_type == BreakoutType.EXTERNAL and START_WEEK <= a.breakout_candle_time <= END_WEEK:
                        all_external_alerts.append(a)

        # Weekly -> Daily
        df_w = cache.load_candles(sym, "W")
        if not df_w.empty and not df_d.empty:
            trigs_w = detect_triggers(
                df_w, "W", sym, ac,
                lookback=None, max_disrespects=None, ocl_max_disrespects=None,
                doji_body_threshold_pct=doji_pct,
                primary_selection_rule="fresh_first",
            )
            trigs_w_win = [t for t in trigs_w if t.trigger_candle_time >= pd.Timestamp("2026-08-01", tz="UTC")]
            d_levels = detect_ltf_levels(df_d, sym, "D", doji_pct)
            for t in trigs_w_win:
                if t.method == TriggerMethod.METHOD_2:
                    continue
                s = evaluate_setup_lifecycle(
                    trigger=t,
                    htf_df=df_w,
                    ltf_df=df_d,
                    daily_df=df_d,
                    symbol=sym,
                    htf_tf="W",
                    ltf_tf="D",
                    unfresh_alerts=True,
                    doji_body_threshold_pct=doji_pct,
                    all_ltf_levels=d_levels,
                    external_ref_point="first_touch",
                    enable_invalidation=True,
                )
                engine_setups.append(s)
                for a in s.alerts:
                    if a.breakout_type == BreakoutType.EXTERNAL and START_WEEK <= a.breakout_candle_time <= END_WEEK:
                        all_external_alerts.append(a)

    # Parity match
    match_dict = {}
    for idx, r in ref_df.iterrows():
        ref_id = r["id"]
        sym = r["symbol"]
        rdir = r["dir"]
        pair = r["pair"]
        htf_tf = "W" if "W1" in pair else "D"
        exp_dir = TriggerDirection.BEARISH if rdir == "SELL" else TriggerDirection.BULLISH

        ref_rej_time = pd.Timestamp(r["rej_open_utc"], tz="UTC")
        ref_bo_time = pd.Timestamp(r["bo_open_utc"], tz="UTC")
        ref_kl_p = float(r["kl_price"])
        ref_bo_p = float(r["bo_price"])

        kl_tol = get_tolerance(sym, ref_kl_p)
        bo_tol = get_tolerance(sym, ref_bo_p)

        cands = [
            s for s in engine_setups
            if s.symbol == sym and s.trigger.direction == exp_dir and s.htf == htf_tf and s.timeline.trigger_time == ref_rej_time
        ]

        if not cands:
            match_dict[ref_id] = "MISSING"
            continue

        best_setup = None
        for s in cands:
            p_lvl = s.trigger.primary_level
            if p_lvl and abs(p_lvl.price - ref_kl_p) <= kl_tol:
                best_setup = s
                break
        if not best_setup:
            best_setup = cands[0]

        s = best_setup
        p_lvl = s.trigger.primary_level
        ext_alerts = [a for a in s.alerts if a.breakout_type == BreakoutType.EXTERNAL]
        if not ext_alerts:
            match_dict[ref_id] = "PARTIAL"
            continue

        best_a = None
        exact_a = None
        for a in ext_alerts:
            p_match = abs(a.broken_level.price - ref_bo_p) <= bo_tol
            t_match = (a.breakout_candle_time == ref_bo_time)
            kl_match = p_lvl is not None and abs(p_lvl.price - ref_kl_p) <= kl_tol
            if kl_match and p_match and t_match:
                exact_a = a
                break
            if p_match or t_match:
                best_a = a

        chosen_a = exact_a or best_a or ext_alerts[0]
        kl_match = p_lvl is not None and abs(p_lvl.price - ref_kl_p) <= kl_tol
        p_match = abs(chosen_a.broken_level.price - ref_bo_p) <= bo_tol
        t_match = (chosen_a.breakout_candle_time == ref_bo_time)

        status = "MATCH" if (kl_match and p_match and t_match) else "PARTIAL"
        match_dict[ref_id] = status

    # Volume: Trend Required
    trend_req_alerts = []
    for a in all_external_alerts:
        df_d = cache.load_candles(a.symbol, "D")
        prior_d = df_d[df_d.index <= a.breakout_candle_time]
        asof = prior_d.index[-1] if not prior_d.empty else a.breakout_candle_time
        tf_trend = "W" if a.htf == "W" else "D"
        trend, _ = get_trend_at_time(a.symbol, tf_trend, asof, "c")
        exp_trend = "BULLISH" if a.direction == TriggerDirection.BULLISH else "BEARISH"
        if trend == exp_trend:
            trend_req_alerts.append(a)

    df_tr = pd.DataFrame([
        {"symbol": a.symbol, "direction": a.direction.value, "weekday": a.breakout_candle_time.day_name()}
        for a in trend_req_alerts
    ])
    tr_unique = df_tr.drop_duplicates(subset=["symbol", "direction", "weekday"]) if not df_tr.empty else pd.DataFrame()
    tr_vol = len(tr_unique)

    df_all = pd.DataFrame([
        {"symbol": a.symbol, "direction": a.direction.value, "weekday": a.breakout_candle_time.day_name()}
        for a in all_external_alerts
    ])
    all_unique = df_all.drop_duplicates(subset=["symbol", "direction", "weekday"]) if not df_all.empty else pd.DataFrame()
    all_vol = len(all_unique)

    return match_dict, tr_vol, all_vol

if __name__ == "__main__":
    thresholds = [0.0, 0.01, 0.02, 0.03, 0.05]
    results = {}
    print("=" * 80)
    print("ITEM 3: DOJI THRESHOLD SWEEP (0, 0.01, 0.02, 0.03, 0.05)")
    print("=" * 80)

    for th in thresholds:
        print(f"Running doji_body_threshold_pct = {th}...")
        m_dict, tr_vol, all_vol = run_for_threshold(th)
        matches = sum(1 for v in m_dict.values() if v == "MATCH")
        partials = sum(1 for v in m_dict.values() if v == "PARTIAL")
        missings = sum(1 for v in m_dict.values() if v == "MISSING")
        results[th] = {
            "m_dict": m_dict,
            "counts": {"MATCH": matches, "PARTIAL": partials, "MISSING": missings},
            "tr_vol": tr_vol,
            "all_vol": all_vol,
        }
        print(f"  -> Threshold {th}: MATCH={matches}, PARTIAL={partials}, MISSING={missings} | Vol(TrendReq)={tr_vol}, Vol(TrendOff)={all_vol}")

    base_m_dict = results[0.0]["m_dict"]
    print("\n" + "=" * 80)
    print("DOJI THRESHOLD SWEEP SUMMARY")
    print("=" * 80)
    print(f"{'Threshold':<10} | {'Status Counter':<25} | {'Gained Rows':<20} | {'Lost Rows':<20} | {'Vol (Trend Req)':<16} | {'Vol (Trend Off)':<16}")
    print("-" * 115)
    for th in thresholds:
        c = results[th]["counts"]
        c_str = f"M={c['MATCH']} P={c['PARTIAL']} Miss={c['MISSING']}"
        cur_m_dict = results[th]["m_dict"]
        gained = [rid for rid in base_m_dict if base_m_dict[rid] != "MATCH" and cur_m_dict[rid] == "MATCH"]
        lost = [rid for rid in base_m_dict if base_m_dict[rid] == "MATCH" and cur_m_dict[rid] != "MATCH"]
        gained_str = f"{gained}" if gained else "None"
        lost_str = f"{lost}" if lost else "None"
        print(f"{th:<10} | {c_str:<25} | {gained_str:<20} | {lost_str:<20} | {results[th]['tr_vol']:<16} | {results[th]['all_vol']:<16}")
    print("=" * 115)
