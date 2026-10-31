"""
analysis/scan_oct19_25_setups.py
--------------------------------
Scans for all setups received between Oct 19 - 25, 2026 under current specifications:
  - Default profile: forex_com (synthetic open continuity)
  - Disrespect cap: max_disrespects = 4, ocl_max_disrespects = 4
  - Trend required: Candidate (c) evaluated at alert time (breakout time)
  - Counting rule: unique setup per weekday by (symbol, direction, breakout_date)
  - Breakout type: External only
  - External reference point: first_touch
  - Invalidation enabled: True
"""
import sys
import yaml
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.data.candles import apply_open_continuity
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import evaluate_setup_lifecycle, detect_ltf_levels, BreakoutType
from slk_bot.strategy.models import TriggerDirection, TriggerMethod, KeyLevel
from slk_bot.strategy.levels import track_key_levels
from analysis.evaluate_trend_three_moments import compute_trend_b_c

cache = CandleCache()
INST_PATH = ROOT / "slk_bot" / "config" / "instruments.yaml"
with open(INST_PATH, "r", encoding="utf-8") as f:
    INST_DATA = yaml.safe_load(f)

# Scan window: Oct 19, 2026 to Oct 25, 2026
WINDOW_START = pd.Timestamp("2026-10-19 00:00:00", tz="UTC")
WINDOW_END   = pd.Timestamp("2026-10-25 23:59:59", tz="UTC")
TRIG_CUTOFF  = pd.Timestamp("2026-08-01 00:00:00", tz="UTC")

ref_df = pd.read_csv(ROOT / "reference_setups.csv")
core_18_syms = set(ref_df["symbol"].unique())

instruments = [inst for inst in INST_DATA["instruments"] if not inst.get("exclude_from_triggers")]

print(f"Total active instruments to scan: {len(instruments)}")
print(f"Window: {WINDOW_START} to {WINDOW_END}")

# 1. Preload candles & trend cache
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

# 2. Run engine across all instruments (Daily -> H4, and USOIL Weekly -> Daily)
all_alerts = []

for inst in instruments:
    sym = inst["name"]
    ac = inst.get("asset_class", "forex")
    df_d, df_h4, df_w = candles_by_sym[sym]

    # D1 -> H4
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
                    thist = trend_cache.get((sym, "W"), [])
                    priors = [h for h in thist if h[0] <= asof]
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

print(f"Total raw external trend-aligned alerts in Oct 19-25: {len(all_alerts)}")

# 3. Apply user counting rule: unique (symbol, direction, breakout_date)
unique_setups = {}
for item in all_alerts:
    a = item["alert"]
    sdir = "BUY" if a.direction == TriggerDirection.BULLISH else "SELL"
    k = (a.symbol, sdir, a.breakout_candle_time.strftime("%Y-%m-%d"))
    if k not in unique_setups:
        unique_setups[k] = item

print(f"Total unique setups under counting rule: {len(unique_setups)}")

# Helpers for formatting metrics
def compute_adr14(df_d: pd.DataFrame, asof_time: pd.Timestamp) -> float:
    priors = df_d[df_d.index <= asof_time]
    if len(priors) < 15:
        priors = df_d
    dr = priors["high"].iloc[-14:] - priors["low"].iloc[-14:]
    val = float(dr.mean())
    return val if val > 0 else float(dr.iloc[-1])

def format_wat_time(ts: pd.Timestamp) -> str:
    if pd.isna(ts): return ""
    ts_wat = ts + pd.Timedelta(hours=1)
    day_name = ts_wat.strftime("%a")
    day_num = ts_wat.day
    mon_name = ts_wat.strftime("%b")
    time_str = ts_wat.strftime("%H:%M")
    return f"{day_name} {day_num} {mon_name}, {time_str}"

def format_candle_formation(kl: KeyLevel) -> tuple[str, str]:
    if kl is None: return "", ""
    t1_wat = kl.formation_time + pd.Timedelta(hours=1)
    t2_wat = kl.formation_confirmed_time + pd.Timedelta(hours=1)
    kind_val = kl.kind.value if hasattr(kl.kind, "value") else str(kl.kind)
    c1_col, c2_col = ("bullish", "bearish") if kind_val in ("A", "bearish_OCL") else ("bearish", "bullish")
    return f"{t1_wat.strftime('%a %d %b %Y %H:%M')} ({c1_col})", f"{t2_wat.strftime('%a %d %b %Y %H:%M')} ({c2_col})"

rows = []
for k, item in unique_setups.items():
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
    disp_count = a.htf_level.disrespect_count if a.htf_level else (t.primary_level.disrespect_count if t.primary_level else None)
    
    m_name = {
        TriggerMethod.METHOD_1: "Method 1 (Rejection)",
        TriggerMethod.METHOD_2: "Method 2 (Sweep)",
        TriggerMethod.METHOD_3: "Method 3 (Combined)",
    }.get(a.method, f"Method {int(a.method)}")

    adr14 = compute_adr14(df_d, rej_time)
    rej_candle = htf_df.loc[rej_time] if rej_time in htf_df.index else htf_df[htf_df.index <= rej_time].iloc[-1]
    c_open, c_high, c_low, c_close = float(rej_candle["open"]), float(rej_candle["high"]), float(rej_candle["low"]), float(rej_candle["close"])
    wick_pen = max(0.0, c_high - kl_price) if rdir == "SELL" else max(0.0, kl_price - c_low)
    wick_pen_pct = (wick_pen / kl_price) * 100
    wick_pen_adr = wick_pen / adr14 if adr14 > 0 else 0.0
    close_dist = abs(c_close - kl_price)
    close_dist_pct = (close_dist / kl_price) * 100
    close_dist_adr = close_dist / adr14 if adr14 > 0 else 0.0
    c_size = c_high - c_low
    c_size_pct = (c_size / kl_price) * 100
    c_size_adr = c_size / adr14 if adr14 > 0 else 0.0
    body_dir = "BULLISH" if c_close > c_open else ("BEARISH" if c_close < c_open else "DOJI")

    priors = htf_df[htf_df.index < rej_time]
    swept = "N"
    if not priors.empty:
        prev = priors.iloc[-1]
        if rdir == "SELL" and c_high > float(prev["high"]) and c_close < float(prev["high"]): swept = "Y"
        elif rdir == "BUY" and c_low < float(prev["low"]) and c_close > float(prev["low"]): swept = "Y"

    fc1_str, fc2_str = format_candle_formation(a.htf_level)
    is_w = (htf_df is candles_by_sym[sym][2])
    rej_close_time = rej_time + (pd.Timedelta(days=7) if is_w else pd.Timedelta(days=1))
    inside_rej = "Y" if bo_time < rej_close_time else "N"

    rows.append({
        "instrument": sym,
        "direction": rdir,
        "method": m_name,
        "htf_candle_open_wat": format_wat_time(rej_time),
        "key_level_price": round(kl_price, 5),
        "key_level_kind": kl_kind,
        "key_level_freshness": kl_fresh,
        "disrespect_count": disp_count,
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
        "trend_state": trend,
        "breakout_level": round(bo_price, 5),
        "breakout_candle_open_wat": format_wat_time(bo_time),
        "breakout_inside_rejection": inside_rej,
        "is_core18": item["is_core18"],
        "bo_time_utc": bo_time,
    })

res_df = pd.DataFrame(rows)
res_df.sort_values(by=["bo_time_utc", "instrument"], inplace=True)
res_df.drop(columns=["bo_time_utc"], inplace=True)

out_csv = ROOT / "analysis" / "output" / "oct19_25_setups.csv"
res_df.to_csv(out_csv, index=False)
print(f"Saved {out_csv} with {len(res_df)} setups.")

print("\nSETUPS BREAKDOWN BY DAY:")
res_df['day'] = res_df['breakout_candle_open_wat'].apply(lambda s: s.split()[0] if s else '')
print(res_df.groupby('day').size())

print(f"\nCore 18 reference symbols: {len(res_df[res_df['is_core18']])} setups")
print(f"Other symbols: {len(res_df[~res_df['is_core18']])} setups")
