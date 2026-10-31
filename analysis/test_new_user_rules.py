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

instruments = [inst for inst in INST_DATA["instruments"] if not inst.get("exclude_from_triggers")]

print(f"Testing new specifications across {len(instruments)} instruments...")

# Implementation of updated apply_candle_to_level tracking rejections_since_fresh
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
    if level.is_retired or level.last_side != Side.ABOVE:
        return False
    return level.current_role in (LevelRole.SUPPORT, LevelRole.RBS, LevelRole.QM, LevelRole.FLIPPED_OCL)

def is_sell_role(level: KeyLevel) -> bool:
    if level.is_retired or level.last_side != Side.BELOW:
        return False
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
        if level.is_retired:
            continue
        if level.timeframe != timeframe:
            continue

        # Age rule: daily setups only
        if timeframe == "D":
            if level.formation_time < six_months_ago:
                continue
            if level.disrespect_count == 3 and level.formation_time < four_months_ago:
                continue

        # Unfresh rule:
        # If level is unfresh, it can only be used if rejected exactly once before
        # If rejected 2 or more times before, it isn't used.
        rejs = getattr(level, "rejections_since_fresh", 0 if level.fresh else 1)
        if not level.fresh:
            if rejs >= 2:
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
    df: pd.DataFrame,
    timeframe: str,
    symbol: str,
    asset_class: str = "forex",
    max_disrespects: int = 3,
    ocl_max_disrespects: int = 1,
    primary_selection_rule: str = "fresh_first",
) -> list[HTFTrigger]:
    if len(df) < 2: return []
    triggers = []
    active_levels: list[KeyLevel] = []
    threshold_pct = get_marginal_threshold(asset_class)

    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    times = df.index

    for k in range(1, len(df)):
        t_k = times[k]
        o_k, h_k, l_k, c_k = float(opens[k]), float(highs[k]), float(lows[k]), float(closes[k])
        t_r = times[k - 1]
        o_r, h_r, l_r, c_r = float(opens[k-1]), float(highs[k-1]), float(lows[k-1]), float(closes[k-1])

        res_bull, res_bear = evaluate_rejection_multi_new(
            candle_time=t_k,
            candle_low=l_k,
            candle_high=h_k,
            candle_close=c_k,
            active_levels=[lvl for lvl in active_levels if not lvl.is_retired],
            timeframe=timeframe,
            primary_selection_rule=primary_selection_rule,
        )

        sweep_dir, sweep_ref, s_wick_m, s_close_m = evaluate_sweep(
            candle_time=t_k, candle_open=o_k, candle_high=h_k, candle_low=l_k, candle_close=c_k,
            ref_time=t_r, ref_open=o_r, ref_high=h_r, ref_low=l_r, ref_close=c_r,
        )

        # Bullish
        if res_bull is not None and sweep_dir == TriggerDirection.BULLISH:
            prim_lvl, all_lvls, r_wick_m, r_close_m = res_bull
            min_m = min([r_wick_m, r_close_m, s_wick_m, s_close_m])
            is_low_prob = bool(prim_lvl is not None and not prim_lvl.fresh)
            triggers.append(HTFTrigger(
                symbol=symbol, timeframe=timeframe, direction=TriggerDirection.BULLISH,
                method=TriggerMethod.METHOD_3, trigger_candle_time=t_k,
                trigger_candle_open=o_k, trigger_candle_high=h_k, trigger_candle_low=l_k, trigger_candle_close=c_k,
                mitigation_extreme=l_k, primary_level=copy.copy(prim_lvl), all_rejected_levels=[copy.copy(l) for l in all_lvls],
                sweep_reference=sweep_ref, margins=TriggerPassMargins(r_wick_m, r_close_m, s_wick_m, s_close_m, min_m, threshold_pct, min_m < threshold_pct),
                is_low_probability=is_low_prob,
            ))
        elif res_bull is not None:
            prim_lvl, all_lvls, r_wick_m, r_close_m = res_bull
            min_m = min([r_wick_m, r_close_m])
            is_low_prob = bool(prim_lvl is not None and not prim_lvl.fresh)
            triggers.append(HTFTrigger(
                symbol=symbol, timeframe=timeframe, direction=TriggerDirection.BULLISH,
                method=TriggerMethod.METHOD_1, trigger_candle_time=t_k,
                trigger_candle_open=o_k, trigger_candle_high=h_k, trigger_candle_low=l_k, trigger_candle_close=c_k,
                mitigation_extreme=l_k, primary_level=copy.copy(prim_lvl), all_rejected_levels=[copy.copy(l) for l in all_lvls],
                sweep_reference=None, margins=TriggerPassMargins(r_wick_m, r_close_m, 0.0, 0.0, min_m, threshold_pct, min_m < threshold_pct),
                is_low_probability=is_low_prob,
            ))

        # Bearish
        if res_bear is not None and sweep_dir == TriggerDirection.BEARISH:
            prim_lvl, all_lvls, r_wick_m, r_close_m = res_bear
            min_m = min([r_wick_m, r_close_m, s_wick_m, s_close_m])
            is_low_prob = bool(prim_lvl is not None and not prim_lvl.fresh)
            triggers.append(HTFTrigger(
                symbol=symbol, timeframe=timeframe, direction=TriggerDirection.BEARISH,
                method=TriggerMethod.METHOD_3, trigger_candle_time=t_k,
                trigger_candle_open=o_k, trigger_candle_high=h_k, trigger_candle_low=l_k, trigger_candle_close=c_k,
                mitigation_extreme=h_k, primary_level=copy.copy(prim_lvl), all_rejected_levels=[copy.copy(l) for l in all_lvls],
                sweep_reference=sweep_ref, margins=TriggerPassMargins(r_wick_m, r_close_m, s_wick_m, s_close_m, min_m, threshold_pct, min_m < threshold_pct),
                is_low_probability=is_low_prob,
            ))
        elif res_bear is not None:
            prim_lvl, all_lvls, r_wick_m, r_close_m = res_bear
            min_m = min([r_wick_m, r_close_m])
            is_low_prob = bool(prim_lvl is not None and not prim_lvl.fresh)
            triggers.append(HTFTrigger(
                symbol=symbol, timeframe=timeframe, direction=TriggerDirection.BEARISH,
                method=TriggerMethod.METHOD_1, trigger_candle_time=t_k,
                trigger_candle_open=o_k, trigger_candle_high=h_k, trigger_candle_low=l_k, trigger_candle_close=c_k,
                mitigation_extreme=h_k, primary_level=copy.copy(prim_lvl), all_rejected_levels=[copy.copy(l) for l in all_lvls],
                sweep_reference=None, margins=TriggerPassMargins(r_wick_m, r_close_m, 0.0, 0.0, min_m, threshold_pct, min_m < threshold_pct),
                is_low_probability=is_low_prob,
            ))

        # Update level states
        for lvl in active_levels:
            apply_candle_with_rejections(lvl, c_k, h_k, l_k, max_disrespects, ocl_max_disrespects)

        # New formations
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

print("Testing engine across 40 instruments...")
# Now let's run scan
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
                        all_alerts.append({"alert": a, "trigger": t, "htf_df": df_d, "trend": trend, "htf_tf": "D"})

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
                        all_alerts.append({"alert": a, "trigger": t, "htf_df": df_w, "trend": trend, "htf_tf": "W"})

# Deduplicate unique setups
unique_setups = {}
for item in all_alerts:
    a = item["alert"]
    sdir = "BUY" if a.direction == TriggerDirection.BULLISH else "SELL"
    k = (a.symbol, sdir, a.breakout_candle_time.strftime("%Y-%m-%d"))
    if k not in unique_setups:
        unique_setups[k] = item

print(f"Total unique setups under new rules: {len(unique_setups)}")
