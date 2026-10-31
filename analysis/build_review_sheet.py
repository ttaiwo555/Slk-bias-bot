"""
analysis/build_review_sheet.py
------------------------------
Builds a review sheet for 12-16 Oct 2026 under default settings:
  - Default profile: forex_com (synthetic open continuity on all TFs/instruments/crypto/weekends)
  - Trend required: Candidate c evaluated at alert time
  - User counting rule: unique setup per weekday identified by (symbol, direction, breakout_date)
  - External breakouts only

Includes:
  - 35 Reference setups
  - 20 Randomly chosen extra setups (not in the reference)

Columns:
  group, id, instrument, direction, method, htf_candle_open_wat, key_level_price,
  key_level_kind, key_level_freshness, formation_candle_1_wat, formation_candle_2_wat,
  wick_penetration_pct, wick_penetration_adr, close_distance_pct, close_distance_adr,
  htf_candle_size_pct, htf_candle_size_adr, htf_candle_body_dir, swept_prev_extreme,
  trend_state, breakout_level, breakout_candle_open_wat, breakout_inside_rejection

Outputs:
  - Saved to analysis/output/review_sheet.csv
  - Statistical comparison: median and spread for Reference vs Extras
  - Best separating feature analysis
"""
import sys
import random
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
from slk_bot.strategy.models import TriggerDirection, TriggerMethod, KeyLevel
from slk_bot.strategy.levels import track_key_levels
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

def get_profile_candles(sym: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df_d = cache.load_candles(sym, "D")
    df_h4 = cache.load_candles(sym, "H4")
    df_w = cache.load_candles(sym, "W")

    if not df_d.empty:
        df_d = apply_open_continuity(df_d.copy())
    if not df_h4.empty:
        df_h4 = apply_open_continuity(df_h4.copy())
    if not df_w.empty:
        df_w = apply_open_continuity(df_w.copy())

    return df_d, df_h4, df_w

def compute_adr14(df_d: pd.DataFrame, asof_time: pd.Timestamp) -> float:
    priors = df_d[df_d.index <= asof_time]
    if len(priors) < 15:
        # Fallback to whatever is available or entire df
        priors = df_d
    dr = priors["high"].iloc[-14:] - priors["low"].iloc[-14:]
    val = float(dr.mean())
    return val if val > 0 else float(dr.iloc[-1])

def format_wat_time(ts: pd.Timestamp) -> str:
    if pd.isna(ts):
        return ""
    ts_wat = ts + pd.Timedelta(hours=1)
    day_name = ts_wat.strftime("%a")
    day_num = ts_wat.day
    mon_name = ts_wat.strftime("%b")
    time_str = ts_wat.strftime("%H:%M")
    return f"{day_name} {day_num} {mon_name}, {time_str}"

def format_candle_formation(kl: KeyLevel) -> tuple[str, str]:
    if kl is None:
        return "", ""
    t1_wat = kl.formation_time + pd.Timedelta(hours=1)
    t2_wat = kl.formation_confirmed_time + pd.Timedelta(hours=1)
    t1_str = f"{t1_wat.strftime('%a')} {t1_wat.day} {t1_wat.strftime('%b %H:%M')}"
    t2_str = f"{t2_wat.strftime('%a')} {t2_wat.day} {t2_wat.strftime('%b %H:%M')}"
    kind_val = kl.kind.value if hasattr(kl.kind, "value") else str(kl.kind)
    if kind_val in ("A", "bearish_OCL"):
        c1_col, c2_col = "bullish", "bearish"
    else:
        c1_col, c2_col = "bearish", "bullish"
    return f"{t1_str} ({c1_col})", f"{t2_str} ({c2_col})"

def main():
    print("=" * 80)
    print("BUILDING REVIEW SHEET FOR 12-16 OCT (DEFAULT SETTINGS)")
    print("=" * 80)

    # 1. Preload candles and trend cache
    candles_by_sym = {}
    trend_cache = {}
    levels_cache = {}

    for inst in instruments:
        sym = inst["name"]
        df_d, df_h4, df_w = get_profile_candles(sym)
        candles_by_sym[sym] = (df_d, df_h4, df_w)

        if not df_d.empty:
            thist_d = compute_trend_b_c(df_d, use_marginal=True, sym=sym)
            trend_cache[(sym, "D")] = [(h[0], h[1]) for h in thist_d]
            # Track daily levels for lookup
            levels_cache[(sym, "D")] = track_key_levels(df_d, "D", sym, lookback=None, doji_body_threshold_pct=0.0)

        if not df_w.empty:
            thist_w = compute_trend_b_c(df_w, use_marginal=True, sym=sym)
            trend_cache[(sym, "W")] = [(h[0], h[1]) for h in thist_w]
            levels_cache[(sym, "W")] = track_key_levels(df_w, "W", sym, lookback=None, doji_body_threshold_pct=0.0)

    # 2. Run engine to gather all candidate alerts under default settings
    all_engine_alerts = []

    for inst in instruments:
        sym = inst["name"]
        ac = inst.get("asset_class", "forex")
        df_d, df_h4, df_w = candles_by_sym[sym]

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
                    if a.breakout_type == BreakoutType.EXTERNAL and WINDOW_START <= a.breakout_candle_time <= WINDOW_END:
                        # Check trend alignment (Candidate c)
                        prior_d = df_d[df_d.index < a.breakout_candle_time]
                        asof = prior_d.index[-1] if not prior_d.empty else a.breakout_candle_time
                        thist = trend_cache.get((sym, "D"), [])
                        priors = [h for h in thist if h[0] <= asof]
                        trend = priors[-1][1] if priors else "NONE"
                        exp = "BULLISH" if a.direction == TriggerDirection.BULLISH else "BEARISH"
                        if trend == exp:
                            all_engine_alerts.append({
                                "alert": a,
                                "trigger": t,
                                "htf_df": df_d,
                                "df_d": df_d,
                                "trend": trend,
                                "htf_tf": "D",
                            })

        # W1 -> D1 (USOIL)
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
                    if a.breakout_type == BreakoutType.EXTERNAL and WINDOW_START <= a.breakout_candle_time <= WINDOW_END:
                        prior_w = df_w[df_w.index < a.breakout_candle_time]
                        asof = prior_w.index[-1] if not prior_w.empty else a.breakout_candle_time
                        thist = trend_cache.get((sym, "W"), [])
                        priors = [h for h in thist if h[0] <= asof]
                        trend = priors[-1][1] if priors else "NONE"
                        exp = "BULLISH" if a.direction == TriggerDirection.BULLISH else "BEARISH"
                        if trend == exp:
                            all_engine_alerts.append({
                                "alert": a,
                                "trigger": t,
                                "htf_df": df_w,
                                "df_d": df_d,
                                "trend": trend,
                                "htf_tf": "W",
                            })

    print(f"Total trend-aligned external engine alerts in window: {len(all_engine_alerts)}")

    # 3. Apply user's counting rule: unique (symbol, direction, breakout_date)
    unique_engine_setups = {}
    for item in all_engine_alerts:
        a = item["alert"]
        sdir = "BUY" if a.direction == TriggerDirection.BULLISH else "SELL"
        k = (a.symbol, sdir, a.breakout_candle_time.strftime("%Y-%m-%d"))
        if k not in unique_engine_setups:
            unique_engine_setups[k] = item

    print(f"Unique engine setups under counting rule: {len(unique_engine_setups)}")

    # Helper function to compute row metrics
    def build_row_metrics(
        group_name: str,
        row_id: Any,
        sym: str,
        rdir: str,
        method_str: str,
        htf_rej_time: pd.Timestamp,
        bo_time: pd.Timestamp,
        kl_price: float,
        kl_kind: str,
        kl_freshness: str,
        bo_price: float,
        htf_df: pd.DataFrame,
        df_d: pd.DataFrame,
        kl_obj: KeyLevel = None,
        trend_str: str = "",
        swept_override: str = "",
    ) -> dict:
        adr14 = compute_adr14(df_d, htf_rej_time)

        # HTF Rejection candle
        if htf_rej_time in htf_df.index:
            rej_candle = htf_df.loc[htf_rej_time]
            c_open = float(rej_candle["open"])
            c_high = float(rej_candle["high"])
            c_low = float(rej_candle["low"])
            c_close = float(rej_candle["close"])
        else:
            # Nearest preceding candle
            priors = htf_df[htf_df.index <= htf_rej_time]
            rej_candle = priors.iloc[-1]
            c_open = float(rej_candle["open"])
            c_high = float(rej_candle["high"])
            c_low = float(rej_candle["low"])
            c_close = float(rej_candle["close"])

        # Wick penetration
        if rdir == "SELL":
            # Resistance level: wick penetration above level
            wick_pen = max(0.0, c_high - kl_price)
        else:
            # Support level: wick penetration below level
            wick_pen = max(0.0, kl_price - c_low)

        wick_pen_pct = (wick_pen / kl_price) * 100.0 if kl_price > 0 else 0.0
        wick_pen_adr = (wick_pen / adr14) if adr14 > 0 else 0.0

        # Close distance from level
        close_dist = abs(c_close - kl_price)
        close_dist_pct = (close_dist / kl_price) * 100.0 if kl_price > 0 else 0.0
        close_dist_adr = (close_dist / adr14) if adr14 > 0 else 0.0

        # HTF candle size
        c_size = c_high - c_low
        c_size_pct = (c_size / kl_price) * 100.0 if kl_price > 0 else 0.0
        c_size_adr = (c_size / adr14) if adr14 > 0 else 0.0

        # Candle body direction
        if c_close >= c_open:
            body_dir = "BULLISH"
        else:
            body_dir = "BEARISH"

        # Swept previous candle extreme
        idx_loc = htf_df.index.get_indexer([htf_rej_time])[0]
        swept = "N"
        if swept_override in ("Y", "N"):
            swept = swept_override
        elif idx_loc > 0:
            prev_candle = htf_df.iloc[idx_loc - 1]
            p_high = float(prev_candle["high"])
            p_low = float(prev_candle["low"])
            if rdir == "SELL":
                if c_high > p_high and c_close < p_high:
                    swept = "Y"
            else:
                if c_low < p_low and c_close > p_low:
                    swept = "Y"

        # Formation candles
        fc1_str, fc2_str = "", ""
        if kl_obj is not None:
            fc1_str, fc2_str = format_candle_formation(kl_obj)
        else:
            # Find matching level in levels_cache
            sym_lvls = levels_cache.get((sym, "W" if "W1" in str(method_str) or sym == "USOIL" and htf_df is candles_by_sym[sym][2] else "D"), [])
            cands = [l for l in sym_lvls if abs(l.price - kl_price) / kl_price < 0.005]
            if cands:
                cands.sort(key=lambda l: abs(l.price - kl_price))
                fc1_str, fc2_str = format_candle_formation(cands[0])
                if not kl_freshness:
                    kl_freshness = "FRESH" if cands[0].fresh else "UNFRESH"

        # Breakout inside rejection
        # HTF candle duration: W = 7 days, D = 1 day (or 24h)
        is_w = (htf_df is candles_by_sym[sym][2])
        rej_close_time = htf_rej_time + (pd.Timedelta(days=7) if is_w else pd.Timedelta(days=1))
        inside_rej = "Y" if bo_time < rej_close_time else "N"

        return {
            "group": group_name,
            "id": row_id,
            "instrument": sym,
            "direction": rdir,
            "method": method_str,
            "htf_candle_open_wat": format_wat_time(htf_rej_time),
            "key_level_price": round(kl_price, 5),
            "key_level_kind": kl_kind,
            "key_level_freshness": kl_freshness or "FRESH",
            "formation_candle_1_wat": fc1_str,
            "formation_candle_2_wat": fc2_str,
            "wick_penetration_pct": round(wick_pen_pct, 4),
            "wick_penetration_adr": round(wick_pen_adr, 4),
            "close_distance_pct": round(close_dist_pct, 4),
            "close_distance_adr": round(close_dist_adr, 4),
            "htf_candle_size_pct": round(c_size_pct, 4),
            "htf_candle_size_adr": round(c_size_adr, 4),
            "htf_candle_body_dir": body_dir,
            "swept_prev_extreme": swept,
            "trend_state": trend_str,
            "breakout_level": round(bo_price, 5),
            "breakout_candle_open_wat": format_wat_time(bo_time),
            "breakout_inside_rejection": inside_rej,
        }

    # 4. Process the 35 Reference Setups
    reference_rows = []
    matched_unique_keys = set()

    for _, r in ref_df.iterrows():
        rid = r["id"]
        sym = r["symbol"]
        rdir = r["dir"].upper()
        pair = r["pair"]
        kl_price = float(r["kl_price"])
        bo_price = float(r["bo_price"])
        kl_kind = str(r["kl_kind"]).strip()
        rej_time = pd.Timestamp(r["rej_open_utc"], tz="UTC")
        bo_time = pd.Timestamp(r["bo_open_utc"], tz="UTC")
        swept_lvl = r.get("swept_level")
        has_sweep = pd.notna(swept_lvl) and str(swept_lvl).strip() != ""
        method_str = "Method 3 (Combined)" if has_sweep else "Method 1 (Rejection)"
        swept_flag = "Y" if has_sweep else ""

        df_d, df_h4, df_w = candles_by_sym[sym]
        htf_df = df_w if "W1" in pair else df_d
        htf_tf = "W" if "W1" in pair else "D"

        # Trend state at alert time
        prior_htf = htf_df[htf_df.index < bo_time]
        asof = prior_htf.index[-1] if not prior_htf.empty else bo_time
        thist = trend_cache.get((sym, htf_tf), [])
        priors = [h for h in thist if h[0] <= asof]
        trend_str = priors[-1][1] if priors else "NONE"

        row = build_row_metrics(
            group_name="reference",
            row_id=rid,
            sym=sym,
            rdir=rdir,
            method_str=method_str,
            htf_rej_time=rej_time,
            bo_time=bo_time,
            kl_price=kl_price,
            kl_kind=kl_kind,
            kl_freshness="FRESH",
            bo_price=bo_price,
            htf_df=htf_df,
            df_d=df_d,
            trend_str=trend_str,
            swept_override=swept_flag,
        )
        reference_rows.append(row)

        # Track matched key to exclude from extras
        bo_date_str = bo_time.strftime("%Y-%m-%d")
        matched_unique_keys.add((sym, rdir, bo_date_str))

    print(f"Constructed {len(reference_rows)} reference rows.")

    # 5. Identify Extra Setups (engine unique setups NOT matching any reference setup)
    extra_candidates = []
    for (sym, sdir, bo_date_str), item in unique_engine_setups.items():
        if (sym, sdir, bo_date_str) in matched_unique_keys:
            continue
        extra_candidates.append(item)

    print(f"Total extra candidate setups available: {len(extra_candidates)}")

    # 6. Randomly choose exactly 20 extra setups (fixed seed for determinism)
    rng = random.Random(42)
    selected_extras = rng.sample(extra_candidates, min(20, len(extra_candidates)))
    selected_extras.sort(key=lambda x: (x["alert"].breakout_candle_time, x["alert"].symbol))

    extra_rows = []
    for idx, item in enumerate(selected_extras, start=1):
        a = item["alert"]
        t = item["trigger"]
        htf_df = item["htf_df"]
        df_d = item["df_d"]
        trend = item["trend"]

        sym = a.symbol
        rdir = "BUY" if a.direction == TriggerDirection.BULLISH else "SELL"
        rej_time = t.trigger_candle_time
        bo_time = a.breakout_candle_time
        kl_price = a.htf_level.price if a.htf_level else a.broken_level.price
        bo_price = a.broken_level.price

        raw_k = a.htf_level.kind.value if a.htf_level and hasattr(a.htf_level.kind, "value") else (str(a.htf_level.kind) if a.htf_level else "Turning Point")
        kl_kind = "OCL" if "OCL" in raw_k else ("V" if "V" in raw_k else ("A" if "A" in raw_k else raw_k))
        kl_fresh = "LOW PROBABILITY" if a.is_low_probability else "FRESH"

        m_name = {
            TriggerMethod.METHOD_1: "Method 1 (Rejection)",
            TriggerMethod.METHOD_2: "Method 2 (Sweep)",
            TriggerMethod.METHOD_3: "Method 3 (Combined)",
        }.get(a.method, f"Method {int(a.method)}")

        row = build_row_metrics(
            group_name="extra",
            row_id=f"X{idx:02d}",
            sym=sym,
            rdir=rdir,
            method_str=m_name,
            htf_rej_time=rej_time,
            bo_time=bo_time,
            kl_price=kl_price,
            kl_kind=kl_kind,
            kl_freshness=kl_fresh,
            bo_price=bo_price,
            htf_df=htf_df,
            df_d=df_d,
            kl_obj=a.htf_level,
            trend_str=trend,
        )
        extra_rows.append(row)

    print(f"Constructed {len(extra_rows)} extra rows.")

    # 7. Combine into review sheet
    all_rows = reference_rows + extra_rows
    review_df = pd.DataFrame(all_rows)

    out_csv = ROOT / "analysis" / "output" / "review_sheet.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    review_df.to_csv(out_csv, index=False)
    print(f"Saved review sheet to {out_csv} ({len(review_df)} rows).")

    # 8. Statistical Comparison: Median and Spread
    ref_sub = review_df[review_df["group"] == "reference"]
    ext_sub = review_df[review_df["group"] == "extra"]

    def compute_stats(series: pd.Series):
        s = pd.to_numeric(series, errors="coerce").dropna()
        if len(s) == 0:
            return 0.0, 0.0, 0.0, 0.0
        med = float(s.median())
        q25 = float(s.quantile(0.25))
        q75 = float(s.quantile(0.75))
        iqr = q75 - q25
        return med, q25, q75, iqr

    metrics = [
        ("Wick Penetration (% of price)", "wick_penetration_pct"),
        ("Wick Penetration (ADR multiples)", "wick_penetration_adr"),
        ("Close Distance (% of price)", "close_distance_pct"),
        ("Close Distance (ADR multiples)", "close_distance_adr"),
        ("HTF Candle Size (% of price)", "htf_candle_size_pct"),
        ("HTF Candle Size (ADR multiples)", "htf_candle_size_adr"),
    ]

    print("\n" + "=" * 105)
    print(f"{'Metric':<38} | {'Reference (N=35) Median [IQR]':>30} | {'Extras (N=20) Median [IQR]':>30}")
    print("-" * 105)

    separations = []

    for label, col in metrics:
        r_med, r_q25, r_q75, r_iqr = compute_stats(ref_sub[col])
        e_med, e_q25, e_q75, e_iqr = compute_stats(ext_sub[col])
        r_str = f"{r_med:.3f} [{r_q25:.3f}, {r_q75:.3f}] (IQR={r_iqr:.3f})"
        e_str = f"{e_med:.3f} [{e_q25:.3f}, {e_q75:.3f}] (IQR={e_iqr:.3f})"
        print(f"{label:<38} | {r_str:>30} | {e_str:>30}")

        # Compute Mann-Whitney rank sum / median diff ratio for separation
        s_ref = pd.to_numeric(ref_sub[col], errors="coerce").dropna()
        s_ext = pd.to_numeric(ext_sub[col], errors="coerce").dropna()
        # Effect size: Difference in medians normalized by pooled spread
        pooled_spread = 0.5 * (r_iqr + e_iqr) if (r_iqr + e_iqr) > 0 else 1.0
        separation_score = abs(r_med - e_med) / pooled_spread
        separations.append((label, col, separation_score, r_med, e_med))

    # Candle body direction comparison
    ref_body_counts = ref_sub["htf_candle_body_dir"].value_counts(normalize=True) * 100.0
    ext_body_counts = ext_sub["htf_candle_body_dir"].value_counts(normalize=True) * 100.0

    # Alignment with rejection direction
    # BUY rejection is aligned with BULLISH body, SELL rejection with BEARISH body
    ref_aligned_body = sum((ref_sub["direction"] == "BUY") & (ref_sub["htf_candle_body_dir"] == "BULLISH") |
                           (ref_sub["direction"] == "SELL") & (ref_sub["htf_candle_body_dir"] == "BEARISH")) / len(ref_sub) * 100.0
    ext_aligned_body = sum((ext_sub["direction"] == "BUY") & (ext_sub["htf_candle_body_dir"] == "BULLISH") |
                           (ext_sub["direction"] == "SELL") & (ext_sub["htf_candle_body_dir"] == "BEARISH")) / len(ext_sub) * 100.0

    print("-" * 105)
    print(f"{'Body Aligned with Rejection Direction':<38} | {ref_aligned_body:>28.1f}% | {ext_aligned_body:>28.1f}%")
    print(f"{'HTF Candle Body: Bullish':<38} | {ref_body_counts.get('BULLISH', 0.0):>28.1f}% | {ext_body_counts.get('BULLISH', 0.0):>28.1f}%")
    print(f"{'HTF Candle Body: Bearish':<38} | {ref_body_counts.get('BEARISH', 0.0):>28.1f}% | {ext_body_counts.get('BEARISH', 0.0):>28.1f}%")

    # Additional categorical features
    ref_swept_rate = (ref_sub["swept_prev_extreme"] == "Y").mean() * 100.0
    ext_swept_rate = (ext_sub["swept_prev_extreme"] == "Y").mean() * 100.0
    ref_inside_rate = (ref_sub["breakout_inside_rejection"] == "Y").mean() * 100.0
    ext_inside_rate = (ext_sub["breakout_inside_rejection"] == "Y").mean() * 100.0
    ref_fresh_rate = (ref_sub["key_level_freshness"] == "FRESH").mean() * 100.0
    ext_fresh_rate = (ext_sub["key_level_freshness"] == "FRESH").mean() * 100.0

    print("-" * 105)
    print(f"{'Swept Previous Extreme Rate':<38} | {ref_swept_rate:>28.1f}% | {ext_swept_rate:>28.1f}%")
    print(f"{'Breakout Inside Rejection Rate':<38} | {ref_inside_rate:>28.1f}% | {ext_inside_rate:>28.1f}%")
    print(f"{'Key Level Freshness (FRESH %)':<38} | {ref_fresh_rate:>28.1f}% | {ext_fresh_rate:>28.1f}%")
    print("=" * 105)

    # Rank best separating difference
    separations.sort(key=lambda x: x[2], reverse=True)
    best_sep = separations[0]
    print(f"\nBest separating continuous measured difference: {best_sep[0]}")
    print(f"  Reference Median: {best_sep[3]:.4f} vs Extras Median: {best_sep[4]:.4f} (Separation Score: {best_sep[2]:.2f})")

if __name__ == "__main__":
    main()
