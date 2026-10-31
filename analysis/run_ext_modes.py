"""
analysis/test_ext_modes.py
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import (
    find_mitigation_candle,
    detect_ltf_levels,
    get_candle_close_time,
    BreakoutType,
    SLKAlert,
    SLKSetup,
    SetupTimeline,
    SetupState,
)
from slk_bot.strategy.models import (
    TriggerDirection,
    TriggerMethod,
    LevelKind,
    KeyLevel,
)

cache = CandleCache()
ref_df = pd.read_csv(ROOT / "reference_setups.csv")

def get_tolerance(symbol: str, price: float) -> float:
    if symbol in ("XAGUSD", "XAUUSD"):
        return price * 0.0015
    elif symbol in ("NAS100", "SPX500", "US30", "GER40", "UK100"):
        return price * 0.0020
    return price * 0.0005

def eval_setup_custom(trigger, htf_df, ltf_df, daily_df, symbol, htf_tf, ltf_tf, all_ltf_levels, mode="extreme", strict_less=False):
    t_trig = trigger.trigger_candle_time
    t_trig_close = get_candle_close_time(t_trig, htf_tf)
    direction = trigger.direction
    extreme_x = trigger.mitigation_extreme
    
    ext_idx, ext_time = find_mitigation_candle(ltf_df, t_trig, t_trig_close, direction, extreme_x)
    
    ref_idx = ext_idx
    if mode == "first_touch" and trigger.primary_level:
        kl_p = trigger.primary_level.price
        sub = ltf_df[(ltf_df.index >= t_trig) & (ltf_df.index < t_trig_close)]
        touches = sub[(sub["low"] <= kl_p + 1e-6) & (sub["high"] >= kl_p - 1e-6)]
        if not touches.empty:
            loc = ltf_df.index.get_loc(touches.index[0])
            if isinstance(loc, slice):
                loc = loc.start
            ref_idx = int(loc)
            
    ltf_closes = ltf_df["close"].values
    ltf_times = ltf_df.index
    
    lx = None
    if direction == TriggerDirection.BULLISH:
        if strict_less:
            candidates = [lvl for conf_idx, lvl in all_ltf_levels if lvl.kind == LevelKind.A_SHAPE and lvl.formation_index < ref_idx and lvl.price > extreme_x]
        else:
            candidates = [lvl for conf_idx, lvl in all_ltf_levels if lvl.kind == LevelKind.A_SHAPE and lvl.formation_index <= ref_idx and lvl.price > extreme_x]
        valid_lx = []
        for c in candidates:
            c_conf_idx = c.formation_index + 1
            closes_between = ltf_closes[c_conf_idx : ext_idx + 1]
            if not (closes_between > c.price).any():
                valid_lx.append(c)
        if valid_lx:
            valid_lx.sort(key=lambda l: l.formation_index)
            lx = valid_lx[-1]
    else:
        if strict_less:
            candidates = [lvl for conf_idx, lvl in all_ltf_levels if lvl.kind == LevelKind.V_SHAPE and lvl.formation_index < ref_idx and lvl.price < extreme_x]
        else:
            candidates = [lvl for conf_idx, lvl in all_ltf_levels if lvl.kind == LevelKind.V_SHAPE and lvl.formation_index <= ref_idx and lvl.price < extreme_x]
        valid_lx = []
        for c in candidates:
            c_conf_idx = c.formation_index + 1
            closes_between = ltf_closes[c_conf_idx : ext_idx + 1]
            if not (closes_between < c.price).any():
                valid_lx.append(c)
        if valid_lx:
            valid_lx.sort(key=lambda l: l.formation_index)
            lx = valid_lx[-1]
            
    # Check external breakout
    alerts = []
    if lx:
        # Check subsequent candles
        n = len(ltf_df)
        for k in range(ext_idx, n):
            c_close = ltf_closes[k]
            t_k = ltf_times[k]
            is_bo = (c_close > lx.price) if direction == TriggerDirection.BULLISH else (c_close < lx.price)
            if is_bo:
                # Breakout!
                alerts.append(SLKAlert(
                    dedupe_key=f"{symbol}_{t_k}",
                    symbol=symbol,
                    htf=htf_tf,
                    ltf=ltf_tf,
                    direction=direction,
                    method=trigger.method,
                    breakout_type=BreakoutType.EXTERNAL,
                    alert_time=t_k,
                    breakout_candle_time=t_k,
                    htf_level=trigger.primary_level,
                    mitigation_extreme_x=extreme_x,
                    broken_level=lx,
                    is_low_probability=False,
                    is_marginal=False,
                ))
                break
                
    tl = SetupTimeline(
        trigger_time=t_trig,
        trigger_close_time=t_trig_close,
        extreme_candle_time=ltf_times[ext_idx],
        extreme_candle_index=ext_idx,
        extreme_price_x=extreme_x,
        lx_level=lx,
    )
    return SLKSetup(
        id=f"{symbol}_{t_trig}",
        symbol=symbol,
        htf=htf_tf,
        ltf=ltf_tf,
        trigger=trigger,
        state=SetupState.EXTERNAL_CONFIRMED if alerts else SetupState.WATCHING,
        timeline=tl,
        alerts=alerts,
    )

def test_config(mode, strict_less):
    with open(ROOT / "slk_bot" / "config" / "instruments.yaml", "r", encoding="utf-8") as f:
        inst_data = yaml.safe_load(f)
    in_scope_symbols = set(ref_df["symbol"].unique())
    instruments = [inst for inst in inst_data.get("instruments", []) if inst["name"] in in_scope_symbols]
    
    engine_setups = []
    for inst in instruments:
        sym = inst["name"]
        ac = inst.get("asset_class", "forex")
        df_d = cache.load_candles(sym, "D")
        df_h4 = cache.load_candles(sym, "H4")
        if not df_d.empty and not df_h4.empty:
            trigs = detect_triggers(df_d, "D", sym, ac, lookback=250, max_disrespects=3, ocl_max_disrespects=3)
            h4_levels = detect_ltf_levels(df_h4, sym, "H4", 0.0)
            for t in trigs:
                if t.method == TriggerMethod.METHOD_2:
                    continue
                if t.trigger_candle_time < pd.Timestamp("2026-09-20", tz="UTC"):
                    continue
                s = eval_setup_custom(t, df_d, df_h4, df_d, sym, "D", "H4", h4_levels, mode=mode, strict_less=strict_less)
                engine_setups.append(s)
                
        df_w = cache.load_candles(sym, "W")
        if not df_w.empty and not df_d.empty:
            trigs_w = detect_triggers(df_w, "W", sym, ac, lookback=104, max_disrespects=3, ocl_max_disrespects=3)
            d_levels = detect_ltf_levels(df_d, sym, "D", 0.0)
            for t in trigs_w:
                if t.method == TriggerMethod.METHOD_2:
                    continue
                if t.trigger_candle_time < pd.Timestamp("2026-08-01", tz="UTC"):
                    continue
                s = eval_setup_custom(t, df_w, df_d, df_d, sym, "W", "D", d_levels, mode=mode, strict_less=strict_less)
                engine_setups.append(s)
                
    exact_matches = 0
    bo_price_matches = 0
    bo_time_matches = 0
    
    for idx, r in ref_df.iterrows():
        sym = r["symbol"]
        rdir = r["dir"]
        exp_dir = TriggerDirection.BEARISH if rdir == "SELL" else TriggerDirection.BULLISH
        ref_rej_time = pd.Timestamp(r["rej_open_utc"], tz="UTC")
        ref_bo_time = pd.Timestamp(r["bo_open_utc"], tz="UTC")
        ref_kl_p = float(r["kl_price"])
        ref_bo_p = float(r["bo_price"])
        
        cands = [s for s in engine_setups if s.symbol == sym and s.trigger.direction == exp_dir and s.timeline.trigger_time == ref_rej_time]
        if not cands:
            continue
            
        best_s = None
        for s in cands:
            if s.trigger.primary_level and abs(s.trigger.primary_level.price - ref_kl_p) <= get_tolerance(sym, ref_kl_p):
                best_s = s
                break
        if not best_s:
            best_s = cands[0]
            
        ext_alerts = [a for a in best_s.alerts if a.breakout_type == BreakoutType.EXTERNAL]
        if not ext_alerts:
            continue
            
        best_a = ext_alerts[0]
        p_m = abs(best_a.broken_level.price - ref_bo_p) <= get_tolerance(sym, ref_bo_p)
        t_m = (best_a.breakout_candle_time == ref_bo_time)
        kl_m = best_s.trigger.primary_level and abs(best_s.trigger.primary_level.price - ref_kl_p) <= get_tolerance(sym, ref_kl_p)
        
        if p_m:
            bo_price_matches += 1
        if t_m:
            bo_time_matches += 1
        if kl_m and p_m and t_m:
            exact_matches += 1
            
    print(f"Mode={mode}, strict_less={strict_less}:")
    print(f"  Exact Matches (KL + BO price + BO time): {exact_matches} / 35")
    print(f"  BO Price Matches: {bo_price_matches} / 35")
    print(f"  BO Time Matches:  {bo_time_matches} / 35")

print("Running comparisons...")
test_config("extreme", False)
test_config("extreme", True)
test_config("first_touch", False)
test_config("first_touch", True)
