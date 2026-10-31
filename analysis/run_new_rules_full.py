import sys
sys.path.insert(0, '.')
import yaml
from pathlib import Path
import pandas as pd
import copy

from slk_bot.storage.cache import CandleCache
from slk_bot.data.candles import apply_open_continuity
from slk_bot.strategy.models import KeyLevel, LevelKind, LevelRole, Side, TriggerDirection, TriggerMethod
from slk_bot.strategy.levels import determine_role, detect_formation
from slk_bot.strategy.setups import evaluate_setup_lifecycle, detect_ltf_levels, BreakoutType
from slk_bot.strategy.triggers import HTFTrigger, evaluate_sweep, get_marginal_threshold, TriggerPassMargins
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

def apply_candle_with_rejections(
    level: KeyLevel,
    candle_close: float,
    candle_high: float,
    candle_low: float,
    max_disrespects: int = 3,
    ocl_max_disrespects: int = 1,
) -> None:
    if not hasattr(level, "rejections_since_fresh"):
        level.rejections_since_fresh = 0 if level.fresh else 1

    if level.last_side == Side.BELOW:
        if candle_close > level.price:
            level.disrespect_count += 1
            level.last_side = Side.ABOVE
            level.current_role = determine_role(level.kind, level.disrespect_count, max_disrespects, ocl_max_disrespects)
            level.last_contact = "disrespect"
            level.fresh = True
            level.rejections_since_fresh = 0
        elif candle_high > level.price:
            level.last_contact = "rejection"
            level.fresh = False
            level.rejections_since_fresh += 1

    elif level.last_side == Side.ABOVE:
        if candle_close < level.price:
            level.disrespect_count += 1
            level.last_side = Side.BELOW
            level.current_role = determine_role(level.kind, level.disrespect_count, max_disrespects, ocl_max_disrespects)
            level.last_contact = "disrespect"
            level.fresh = True
            level.rejections_since_fresh = 0
        elif candle_low < level.price:
            level.last_contact = "rejection"
            level.fresh = False
            level.rejections_since_fresh += 1

def is_buy_role(level: KeyLevel) -> bool:
    if level.is_retired or level.last_side != Side.ABOVE: return False
    return level.current_role in (LevelRole.SUPPORT, LevelRole.RBS, LevelRole.QM, LevelRole.FLIPPED_OCL)

def is_sell_role(level: KeyLevel) -> bool:
    if level.is_retired or level.last_side != Side.BELOW: return False
    return level.current_role in (LevelRole.RESISTANCE, LevelRole.SBR, LevelRole.QM, LevelRole.FLIPPED_OCL)

def evaluate_rejection_multi_new(
    candle_time: pd.Timestamp,
    candle_low: float,
    candle_high: float,
    candle_close: float,
    active_levels: list[KeyLevel],
    timeframe: str,
    primary_selection_rule: str = "fresh_first",
):
    matching_bullish: list[KeyLevel] = []
    matching_bearish: list[KeyLevel] = []

    six_months_ago = candle_time - pd.DateOffset(months=6)
    four_months_ago = candle_time - pd.DateOffset(months=4)

    for level in active_levels:
        if level.is_retired: continue
        if level.timeframe != timeframe: continue

        # Age rule: daily setups only
        if timeframe == "D":
            if level.formation_time < six_months_ago: continue
            if level.disrespect_count == 3 and level.formation_time < four_months_ago: continue

        # Unfresh rule:
        rejs = getattr(level, "rejections_since_fresh", 0 if level.fresh else 1)
        if not level.fresh and rejs >= 2:
            continue

        if is_buy_role(level):
            if candle_low <= level.price + 1e-6 and candle_close > level.price:
                matching_bullish.append(level)

        if is_sell_role(level):
            if candle_high >= level.price - 1e-6 and candle_close < level.price:
                matching_bearish.append(level)

    res_bull = None
    if matching_bullish:
        if primary_selection_rule == "fresh_first":
            fresh_bull = [l for l in matching_bullish if l.fresh]
            pool_bull = fresh_bull if fresh_bull else matching_bullish
            primary_bull = min(pool_bull, key=lambda l: abs(l.price - candle_low))
        else:
            primary_bull = min(matching_bullish, key=lambda l: (abs(l.price - candle_low), 0 if l.fresh else 1))
        w_m = (primary_bull.price - candle_low) / primary_bull.price * 100.0
        c_m = (candle_close - primary_bull.price) / primary_bull.price * 100.0
        res_bull = (primary_bull, matching_bullish, w_m, c_m)

    res_bear = None
    if matching_bearish:
        if primary_selection_rule == "fresh_first":
            fresh_bear = [l for l in matching_bearish if l.fresh]
            pool_bear = fresh_bear if fresh_bear else matching_bearish
            primary_bear = min(pool_bear, key=lambda l: abs(l.price - candle_high))
        else:
            primary_bear = min(matching_bearish, key=lambda l: (abs(l.price - candle_high), 0 if l.fresh else 1))
        w_m = (candle_high - primary_bear.price) / primary_bear.price * 100.0
        c_m = (primary_bear.price - candle_close) / primary_bear.price * 100.0
        res_bear = (primary_bear, matching_bearish, w_m, c_m)

    return res_bull, res_bear

def detect_triggers_new(
    df: pd.DataFrame, timeframe: str, symbol: str, asset_class: str = "forex",
    max_disrespects: int = 3, ocl_max_disrespects: int = 1, primary_selection_rule: str = "fresh_first",
) -> list[HTFTrigger]:
    if len(df) < 2: return []
    triggers = []
    active_levels: list[KeyLevel] = []
    threshold_pct = get_marginal_threshold(asset_class)

    opens, highs, lows, closes, times = df["open"].values, df["high"].values, df["low"].values, df["close"].values, df.index

    for k in range(1, len(df)):
        t_k, o_k, h_k, l_k, c_k = times[k], float(opens[k]), float(highs[k]), float(lows[k]), float(closes[k])
        t_r, o_r, h_r, l_r, c_r = times[k-1], float(opens[k-1]), float(highs[k-1]), float(lows[k-1]), float(closes[k-1])

        res_bull, res_bear = evaluate_rejection_multi_new(
            candle_time=t_k, candle_low=l_k, candle_high=h_k, candle_close=c_k,
            active_levels=[lvl for lvl in active_levels if not lvl.is_retired],
            timeframe=timeframe, primary_selection_rule=primary_selection_rule,
        )

        sweep_dir, sweep_ref, s_wick_m, s_close_m = evaluate_sweep(
            candle_time=t_k, candle_open=o_k, candle_high=h_k, candle_low=l_k, candle_close=c_k,
            ref_time=t_r, ref_open=o_r, ref_high=h_r, ref_low=l_r, ref_close=c_r,
        )

        # Bullish
        if res_bull is not None and sweep_dir == TriggerDirection.BULLISH:
            prim_lvl, all_lvls, r_wick_m, r_close_m = res_bull
            min_m = min([r_wick_m, r_close_m, s_wick_m, s_close_m])
            triggers.append(HTFTrigger(
                symbol=symbol, timeframe=timeframe, direction=TriggerDirection.BULLISH,
                method=TriggerMethod.METHOD_3, trigger_candle_time=t_k,
                trigger_candle_open=o_k, trigger_candle_high=h_k, trigger_candle_low=l_k, trigger_candle_close=c_k,
                mitigation_extreme=l_k, primary_level=copy.copy(prim_lvl), all_rejected_levels=[copy.copy(l) for l in all_lvls],
                sweep_reference=sweep_ref, margins=TriggerPassMargins(r_wick_m, r_close_m, s_wick_m, s_close_m, min_m, threshold_pct, min_m < threshold_pct),
                is_low_probability=bool(prim_lvl is not None and not prim_lvl.fresh),
            ))
        elif res_bull is not None:
            prim_lvl, all_lvls, r_wick_m, r_close_m = res_bull
            min_m = min([r_wick_m, r_close_m])
            triggers.append(HTFTrigger(
                symbol=symbol, timeframe=timeframe, direction=TriggerDirection.BULLISH,
                method=TriggerMethod.METHOD_1, trigger_candle_time=t_k,
                trigger_candle_open=o_k, trigger_candle_high=h_k, trigger_candle_low=l_k, trigger_candle_close=c_k,
                mitigation_extreme=l_k, primary_level=copy.copy(prim_lvl), all_rejected_levels=[copy.copy(l) for l in all_lvls],
                sweep_reference=None, margins=TriggerPassMargins(r_wick_m, r_close_m, 0.0, 0.0, min_m, threshold_pct, min_m < threshold_pct),
                is_low_probability=bool(prim_lvl is not None and not prim_lvl.fresh),
            ))

        # Bearish
        if res_bear is not None and sweep_dir == TriggerDirection.BEARISH:
            prim_lvl, all_lvls, r_wick_m, r_close_m = res_bear
            min_m = min([r_wick_m, r_close_m, s_wick_m, s_close_m])
            triggers.append(HTFTrigger(
                symbol=symbol, timeframe=timeframe, direction=TriggerDirection.BEARISH,
                method=TriggerMethod.METHOD_3, trigger_candle_time=t_k,
                trigger_candle_open=o_k, trigger_candle_high=h_k, trigger_candle_low=l_k, trigger_candle_close=c_k,
                mitigation_extreme=h_k, primary_level=copy.copy(prim_lvl), all_rejected_levels=[copy.copy(l) for l in all_lvls],
                sweep_reference=sweep_ref, margins=TriggerPassMargins(r_wick_m, r_close_m, s_wick_m, s_close_m, min_m, threshold_pct, min_m < threshold_pct),
                is_low_probability=bool(prim_lvl is not None and not prim_lvl.fresh),
            ))
        elif res_bear is not None:
            prim_lvl, all_lvls, r_wick_m, r_close_m = res_bear
            min_m = min([r_wick_m, r_close_m])
            triggers.append(HTFTrigger(
                symbol=symbol, timeframe=timeframe, direction=TriggerDirection.BEARISH,
                method=TriggerMethod.METHOD_1, trigger_candle_time=t_k,
                trigger_candle_open=o_k, trigger_candle_high=h_k, trigger_candle_low=l_k, trigger_candle_close=c_k,
                mitigation_extreme=h_k, primary_level=copy.copy(prim_lvl), all_rejected_levels=[copy.copy(l) for l in all_lvls],
                sweep_reference=None, margins=TriggerPassMargins(r_wick_m, r_close_m, 0.0, 0.0, min_m, threshold_pct, min_m < threshold_pct),
                is_low_probability=bool(prim_lvl is not None and not prim_lvl.fresh),
            ))

        for lvl in active_levels:
            apply_candle_with_rejections(lvl, c_k, h_k, l_k, max_disrespects, ocl_max_disrespects)

        f_res = detect_formation(o_r, c_r, o_k, c_k)
        if f_res is not None:
            kind, role, side = f_res
            nl = KeyLevel(
                id=f"{symbol}_{timeframe}_{t_r.strftime('%Y%m%d%H%M')}_{kind.value}",
                timeframe=timeframe, kind=kind, price=c_r, formation_time=t_r, formation_confirmed_time=t_k,
                formation_index=k-1, disrespect_count=0, last_side=side, current_role=role, symbol=symbol,
                last_contact="none", fresh=True,
            )
            nl.rejections_since_fresh = 0
            active_levels.append(nl)

    return triggers

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

    # D1 -> H4
    if not df_d.empty and not df_h4.empty:
        trigs_d = detect_triggers_new(df_d, "D", sym, ac, max_disrespects=3, ocl_max_disrespects=1)
        h4_lvls = detect_ltf_levels(df_h4, sym, "H4", 0.0)
        for t in trigs_d:
            if t.method == TriggerMethod.METHOD_2 or t.trigger_candle_time < TRIG_CUTOFF:
                continue
            s = evaluate_setup_lifecycle(
                t, df_d, df_h4, df_d, sym, "D", "H4",
                unfresh_alerts=True, all_ltf_levels=h4_lvls,
                external_ref_point="first_touch", enable_invalidation=True,
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
                            "alert": a, "trigger": t, "htf_df": df_d, "df_d": df_d,
                            "trend": trend, "htf_tf": "D", "is_core18": (sym in core_18_syms),
                        })

    # W1 -> D1 (USOIL)
    if sym == "USOIL" and not df_w.empty and not df_d.empty:
        trigs_w = detect_triggers_new(df_w, "W", sym, ac, max_disrespects=3, ocl_max_disrespects=1)
        d_lvls = detect_ltf_levels(df_d, sym, "D", 0.0)
        for t in trigs_w:
            if t.method == TriggerMethod.METHOD_2 or t.trigger_candle_time < TRIG_CUTOFF:
                continue
            s = evaluate_setup_lifecycle(
                t, df_w, df_d, df_d, sym, "W", "D",
                unfresh_alerts=True, all_ltf_levels=d_lvls,
                external_ref_point="first_touch", enable_invalidation=True,
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
                            "alert": a, "trigger": t, "htf_df": df_w, "df_d": df_d,
                            "trend": trend, "htf_tf": "W", "is_core18": True,
                        })

unique_setups = {}
for item in all_alerts:
    a = item["alert"]
    sdir = "BUY" if a.direction == TriggerDirection.BULLISH else "SELL"
    k = (a.symbol, sdir, a.breakout_candle_time.strftime("%Y-%m-%d"))
    if k not in unique_setups:
        unique_setups[k] = item

print(f"Total unique setups: {len(unique_setups)}")

# Date formatting helpers
def format_daily_trading_day(ts_utc: pd.Timestamp) -> str:
    # If opens at 00:00 UTC (crypto), the trading day is that date
    if ts_utc.hour == 0:
        return ts_utc.strftime("%a %d %b %Y")
    # For session opening >= 17:00 NY / >= 20:00 UTC, trading day is next day
    ts_next = ts_utc + pd.Timedelta(days=1)
    return ts_next.strftime("%a %d %b %Y")

def format_weekly_monday(ts_utc: pd.Timestamp) -> str:
    # Sunday open -> Monday of that week
    ts_next = ts_utc + pd.Timedelta(days=1)
    return ts_next.strftime("%a %d %b %Y")

def format_h4_wat(ts_utc: pd.Timestamp) -> str:
    ts_wat = ts_utc + pd.Timedelta(hours=1)
    return ts_wat.strftime("%a %d %b, %H:%M WAT")

def compute_adr14(df_d: pd.DataFrame, asof_time: pd.Timestamp) -> float:
    priors = df_d[df_d.index <= asof_time]
    if len(priors) < 15: priors = df_d
    dr = priors["high"].iloc[-14:] - priors["low"].iloc[-14:]
    val = float(dr.mean())
    return val if val > 0 else float(dr.iloc[-1])

rows = []
for k, item in unique_setups.items():
    a = item["alert"]
    t = item["trigger"]
    htf_df = item["htf_df"]
    df_d = item["df_d"]
    trend = item["trend"]
    sym = a.symbol
    rdir = "BUY" if a.direction == TriggerDirection.BULLISH else "SELL"
    m_name = "Method 3 (Combined)" if a.method == TriggerMethod.METHOD_3 else "Method 1 (Rejection)"

    rej_time = t.trigger_candle_time
    bo_time = a.breakout_candle_time
    alert_time = a.alert_time

    # HTF candle metrics
    rej_candle = htf_df.loc[rej_time] if rej_time in htf_df.index else htf_df.iloc[-1]
    c_open = float(rej_candle["open"])
    c_high = float(rej_candle["high"])
    c_low = float(rej_candle["low"])
    c_close = float(rej_candle["close"])
    adr14 = compute_adr14(df_d, rej_time)

    kl = a.htf_level
    kl_price = float(kl.price) if kl else float(t.primary_level.price)
    kind_raw = kl.kind.value if kl and hasattr(kl.kind, "value") else (str(kl.kind) if kl else "KL")
    kl_kind = "OCL" if "OCL" in kind_raw else ("A" if "A" in kind_raw else ("V" if "V" in kind_raw else kind_raw))
    kl_fresh = "LOW PROBABILITY" if a.is_low_probability else "FRESH"
    disp_count = kl.disrespect_count if kl else 0

    bo_price = float(a.broken_level.price) if a.broken_level else 0.0

    # Penetration and distance
    if rdir == "BUY":
        wick_pen = max(0.0, kl_price - c_low)
        close_dist = max(0.0, c_close - kl_price)
    else:
        wick_pen = max(0.0, c_high - kl_price)
        close_dist = max(0.0, kl_price - c_close)

    wick_pen_pct = (wick_pen / kl_price) * 100.0 if kl_price > 0 else 0.0
    wick_pen_adr = (wick_pen / adr14) if adr14 > 0 else 0.0
    close_dist_pct = (close_dist / kl_price) * 100.0 if kl_price > 0 else 0.0
    close_dist_adr = (close_dist / adr14) if adr14 > 0 else 0.0

    c_size = c_high - c_low
    c_size_pct = (c_size / c_close) * 100.0 if c_close > 0 else 0.0
    c_size_adr = (c_size / adr14) if adr14 > 0 else 0.0
    body_dir = "BULLISH" if c_close > c_open else ("BEARISH" if c_close < c_open else "DOJI")

    priors = htf_df[htf_df.index < rej_time]
    swept = "N"
    if not priors.empty:
        prev = priors.iloc[-1]
        if rdir == "SELL" and c_high > float(prev["high"]) and c_close < float(prev["high"]): swept = "Y"
        elif rdir == "BUY" and c_low < float(prev["low"]) and c_close > float(prev["low"]): swept = "Y"

    # Formation candle strings
    t1_wat = kl.formation_time + pd.Timedelta(hours=1)
    t2_wat = kl.formation_confirmed_time + pd.Timedelta(hours=1)
    kind_val = kl.kind.value if hasattr(kl.kind, "value") else str(kl.kind)
    c1_col, c2_col = ("bullish", "bearish") if kind_val in ("A", "bearish_OCL") else ("bearish", "bullish")
    fc1_str = f"{t1_wat.strftime('%a %d %b %Y %H:%M')} ({c1_col})"
    fc2_str = f"{t2_wat.strftime('%a %d %b %Y %H:%M')} ({c2_col})"

    is_w = (item["htf_tf"] == "W")
    rej_date_str = format_weekly_monday(rej_time) if is_w else format_daily_trading_day(rej_time)

    rej_close_time = rej_time + (pd.Timedelta(days=7) if is_w else pd.Timedelta(days=1))
    inside_rej = "Y" if bo_time < rej_close_time else "N"

    rows.append({
        "instrument": sym,
        "direction": rdir,
        "method": m_name,
        "htf_rejection_date": rej_date_str,
        "key_level_price": round(kl_price, 5),
        "key_level_kind": kl_kind,
        "key_level_freshness": kl_fresh,
        "disrespect_count": disp_count,
        "formation_candle_1": fc1_str,
        "formation_candle_2": fc2_str,
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
        "breakout_candle_wat": format_h4_wat(bo_time),
        "alert_created_wat": format_h4_wat(alert_time),
        "breakout_inside_rejection": inside_rej,
        "is_core18": item["is_core18"],
        "bo_time_utc": bo_time,
    })

res_df = pd.DataFrame(rows)
res_df.sort_values(by=["bo_time_utc", "instrument"], inplace=True)
res_df.drop(columns=["bo_time_utc"], inplace=True)

out_csv = Path("analysis/output/oct19_25_setups_final.csv")
res_df.to_csv(out_csv, index=False)
print(f"Saved {out_csv} with {len(res_df)} rows.")

res_df['day'] = res_df['breakout_candle_wat'].apply(lambda s: s.split()[0] if s else '')
print("\nSETUPS BREAKDOWN BY DAY:")
print(res_df.groupby('day').size())
print(f"\nFresh vs Low Prob:")
print(res_df['key_level_freshness'].value_counts())
print(f"\nDisrespect Count Distribution:")
print(res_df['disrespect_count'].value_counts().sort_index())
print(f"\nCore 18 vs Other:")
print(f"Core 18: {len(res_df[res_df['is_core18']])}, Other: {len(res_df[~res_df['is_core18']])}")
