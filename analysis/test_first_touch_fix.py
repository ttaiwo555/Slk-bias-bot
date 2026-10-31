import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pandas as pd
import numpy as np
import copy
from collections import defaultdict
from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import detect_ltf_levels, find_mitigation_candle, get_candle_close_time, BreakoutType, SLKAlert, SLKSetup, SetupTimeline, SetupState
from slk_bot.strategy.models import TriggerDirection, TriggerMethod, LevelKind, KeyLevel
import yaml

def evaluate_lifecycle_fixed(
    trigger,
    htf_df,
    ltf_df,
    daily_df,
    symbol,
    htf_tf,
    ltf_tf,
    unfresh_alerts=True,
    all_ltf_levels=None,
    external_ref_point="first_touch",
    rule_fix=False,
):
    t_trig = trigger.trigger_candle_time
    t_trig_close = get_candle_close_time(t_trig, htf_tf)
    direction = trigger.direction
    extreme_x = trigger.mitigation_extreme

    ext_idx, ext_time = find_mitigation_candle(
        ltf_df=ltf_df,
        trigger_start=t_trig,
        trigger_end=t_trig_close,
        direction=direction,
        extreme_x=extreme_x,
    )

    ref_idx = ext_idx
    touch_idx = None
    if external_ref_point == "first_touch" and trigger.primary_level:
        kl_p = trigger.primary_level.price
        sub = ltf_df[(ltf_df.index >= t_trig) & (ltf_df.index < t_trig_close)]
        touches = sub[(sub["low"] <= kl_p + 1e-6) & (sub["high"] >= kl_p - 1e-6)]
        if not touches.empty:
            loc = ltf_df.index.get_loc(touches.index[0])
            if isinstance(loc, slice):
                loc = loc.start
            touch_idx = int(loc)
            ref_idx = touch_idx

    if all_ltf_levels is None:
        all_ltf_levels = detect_ltf_levels(ltf_df, symbol, ltf_tf, 0.0)

    ltf_closes = ltf_df["close"].values
    ltf_highs = ltf_df["high"].values if "high" in ltf_df.columns else ltf_closes
    ltf_lows = ltf_df["low"].values if "low" in ltf_df.columns else ltf_closes
    ltf_times = ltf_df.index

    lx = None
    if not rule_fix:
        # OLD LOGIC
        if direction == TriggerDirection.BULLISH:
            candidates = [
                lvl for conf_idx, lvl in all_ltf_levels
                if lvl.kind == LevelKind.A_SHAPE and lvl.formation_index <= ref_idx and lvl.price > extreme_x
            ]
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
            candidates = [
                lvl for conf_idx, lvl in all_ltf_levels
                if lvl.kind == LevelKind.V_SHAPE and lvl.formation_index <= ref_idx and lvl.price < extreme_x
            ]
            valid_lx = []
            for c in candidates:
                c_conf_idx = c.formation_index + 1
                closes_between = ltf_closes[c_conf_idx : ext_idx + 1]
                if not (closes_between < c.price).any():
                    valid_lx.append(c)
            if valid_lx:
                valid_lx.sort(key=lambda l: l.formation_index)
                lx = valid_lx[-1]
        start_k = ext_idx + 1
    else:
        # FIXED LOGIC
        # (a) formation strictly before first-touch candle (< touch_idx if touch_idx else <= ext_idx)
        # (b) intact only through candle before first-touch candle (ltf_closes[c_conf_idx : touch_idx])
        # breakout evaluation starts at touch_idx if touch_idx is not None else ext_idx + 1
        limit_idx = touch_idx if touch_idx is not None else ext_idx
        start_k = touch_idx if touch_idx is not None else ext_idx + 1

        if direction == TriggerDirection.BULLISH:
            if touch_idx is not None:
                candidates = [
                    lvl for conf_idx, lvl in all_ltf_levels
                    if lvl.kind == LevelKind.A_SHAPE and lvl.formation_index < touch_idx and lvl.price > extreme_x
                ]
            else:
                candidates = [
                    lvl for conf_idx, lvl in all_ltf_levels
                    if lvl.kind == LevelKind.A_SHAPE and lvl.formation_index <= ext_idx and lvl.price > extreme_x
                ]
            valid_lx = []
            for c in candidates:
                c_conf_idx = c.formation_index + 1
                # Intact through candle before first touch (i.e. up to touch_idx - 1)
                closes_between = ltf_closes[c_conf_idx : limit_idx]
                if not (closes_between > c.price).any():
                    valid_lx.append(c)
            if valid_lx:
                valid_lx.sort(key=lambda l: l.formation_index)
                lx = valid_lx[-1]
        else:
            if touch_idx is not None:
                candidates = [
                    lvl for conf_idx, lvl in all_ltf_levels
                    if lvl.kind == LevelKind.V_SHAPE and lvl.formation_index < touch_idx and lvl.price < extreme_x
                ]
            else:
                candidates = [
                    lvl for conf_idx, lvl in all_ltf_levels
                    if lvl.kind == LevelKind.V_SHAPE and lvl.formation_index <= ext_idx and lvl.price < extreme_x
                ]
            valid_lx = []
            for c in candidates:
                c_conf_idx = c.formation_index + 1
                closes_between = ltf_closes[c_conf_idx : limit_idx]
                if not (closes_between < c.price).any():
                    valid_lx.append(c)
            if valid_lx:
                valid_lx.sort(key=lambda l: l.formation_index)
                lx = valid_lx[-1]

    # Expiry
    post_daily = daily_df[daily_df.index >= t_trig_close]
    expiry_limit = 14 if htf_tf == "W" else 3
    if len(post_daily) >= expiry_limit:
        t_expiry = post_daily.index[expiry_limit - 1] + pd.Timedelta(days=1)
    else:
        t_expiry = t_trig_close + pd.Timedelta(days=expiry_limit * 1.5)

    timeline = SetupTimeline(
        trigger_time=t_trig,
        trigger_close_time=t_trig_close,
        extreme_candle_time=ext_time,
        extreme_candle_index=ext_idx,
        extreme_price_x=extreme_x,
        lx_level=copy.copy(lx) if lx else None,
        expiry_time=t_expiry,
    )

    setup_id = f"{symbol}_{htf_tf}_{direction.value}_{t_trig.strftime('%Y%m%d%H%M')}"
    state = SetupState.WATCHING
    alerts = []
    n_ltf = len(ltf_df)

    levels_by_conf = defaultdict(list)
    for conf_idx, lvl in all_ltf_levels:
        if conf_idx > ext_idx and lvl.formation_index >= ext_idx:
            levels_by_conf[conf_idx].append(lvl)

    internal_levels = []
    for k in range(start_k, n_ltf):
        t_k = ltf_times[k]
        c_k = float(ltf_closes[k])
        t_k_close = get_candle_close_time(t_k, ltf_tf)

        if t_k >= t_expiry and state == SetupState.WATCHING:
            state = SetupState.EXPIRED
            break

        if direction == TriggerDirection.BULLISH and c_k < extreme_x:
            state = SetupState.INVALIDATED
            timeline.invalidation_time = t_k
            break
        elif direction == TriggerDirection.BEARISH and c_k > extreme_x:
            state = SetupState.INVALIDATED
            timeline.invalidation_time = t_k
            break

        for lvl in levels_by_conf.get(k, []):
            if direction == TriggerDirection.BULLISH and lvl.kind == LevelKind.A_SHAPE and lvl.price > extreme_x:
                if lvl not in internal_levels:
                    internal_levels.append(lvl)
                    timeline.internal_levels.append(copy.copy(lvl))
            elif direction == TriggerDirection.BEARISH and lvl.kind == LevelKind.V_SHAPE and lvl.price < extreme_x:
                if lvl not in internal_levels:
                    internal_levels.append(lvl)
                    timeline.internal_levels.append(copy.copy(lvl))

        broke_lx = False
        if lx is not None:
            if direction == TriggerDirection.BULLISH and c_k > lx.price:
                broke_lx = True
            elif direction == TriggerDirection.BEARISH and c_k < lx.price:
                broke_lx = True

        broke_internal = None
        for ilvl in internal_levels:
            if direction == TriggerDirection.BULLISH and c_k > ilvl.price:
                broke_internal = ilvl
                break
            elif direction == TriggerDirection.BEARISH and c_k < ilvl.price:
                broke_internal = ilvl
                break

        if broke_lx:
            timeline.external_breakout_candle_time = t_k
            timeline.external_broken_level = copy.copy(lx)
            alert_time = max(t_k_close, t_trig_close)
            timeline.external_alert_time = alert_time
            is_suppressed = bool(trigger.is_low_probability and not unfresh_alerts)
            dedupe_key = f"{symbol}_{htf_tf}_{t_trig.strftime('%Y%m%d%H%M')}_{direction.value}_{int(trigger.method)}_EXTERNAL"
            alert = SLKAlert(
                dedupe_key=dedupe_key,
                symbol=symbol,
                htf=htf_tf,
                ltf=ltf_tf,
                direction=direction,
                method=trigger.method,
                breakout_type=BreakoutType.EXTERNAL,
                alert_time=alert_time,
                breakout_candle_time=t_k,
                htf_level=copy.copy(trigger.primary_level) if trigger.primary_level else None,
                mitigation_extreme_x=extreme_x,
                broken_level=copy.copy(lx),
                is_low_probability=trigger.is_low_probability,
                is_marginal=trigger.margins.is_marginal,
                is_push_suppressed=is_suppressed,
                next_day_rule=(t_k >= t_trig_close),
            )
            alerts.append(alert)
            state = SetupState.EXTERNAL_CONFIRMED
            break

    return SLKSetup(
        id=setup_id,
        symbol=symbol,
        htf=htf_tf,
        ltf=ltf_tf,
        trigger=trigger,
        state=state,
        timeline=timeline,
        alerts=alerts,
    )

# Now let's compare on all 35 reference setups
cache = CandleCache()
ref_df = pd.read_csv(ROOT / "reference_setups.csv")

with open(ROOT / "slk_bot" / "config" / "instruments.yaml", "r", encoding="utf-8") as f:
    inst_data = yaml.safe_load(f)

in_scope_symbols = set(ref_df["symbol"].unique())
instruments = [inst for inst in inst_data.get("instruments", []) if inst["name"] in in_scope_symbols]

def get_tolerance(symbol: str, price: float) -> float:
    if symbol in ("XAGUSD", "XAUUSD"):
        return price * 0.0015
    elif symbol in ("NAS100", "SPX500", "US30", "GER40", "UK100"):
        return price * 0.0020
    return price * 0.0005

def run_test(rule_fix=False):
    engine_setups = []
    for inst in instruments:
        sym = inst["name"]
        asset_class = inst.get("asset_class", "forex")
        df_d = cache.load_candles(sym, "D")
        df_h4 = cache.load_candles(sym, "H4")
        if not df_d.empty and not df_h4.empty:
            trigs_d = detect_triggers(df_d, "D", sym, asset_class, lookback=None, max_disrespects=None, ocl_max_disrespects=None, primary_selection_rule="fresh_first")
            trigs_d_win = [t for t in trigs_d if t.trigger_candle_time >= pd.Timestamp("2026-09-20", tz="UTC")]
            h4_levels = detect_ltf_levels(df_h4, sym, "H4", 0.0)
            for t in trigs_d_win:
                if t.method == TriggerMethod.METHOD_2:
                    continue
                s = evaluate_lifecycle_fixed(t, df_d, df_h4, df_d, sym, "D", "H4", unfresh_alerts=True, all_ltf_levels=h4_levels, external_ref_point="first_touch", rule_fix=rule_fix)
                engine_setups.append(s)
        df_w = cache.load_candles(sym, "W")
        if not df_w.empty and not df_d.empty:
            trigs_w = detect_triggers(df_w, "W", sym, asset_class, lookback=None, max_disrespects=None, ocl_max_disrespects=None, primary_selection_rule="fresh_first")
            trigs_w_win = [t for t in trigs_w if t.trigger_candle_time >= pd.Timestamp("2026-08-01", tz="UTC")]
            d_levels = detect_ltf_levels(df_d, sym, "D", 0.0)
            for t in trigs_w_win:
                if t.method == TriggerMethod.METHOD_2:
                    continue
                s = evaluate_lifecycle_fixed(t, df_w, df_d, df_d, sym, "W", "D", unfresh_alerts=True, all_ltf_levels=d_levels, external_ref_point="first_touch", rule_fix=rule_fix)
                engine_setups.append(s)

    results = {}
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

        cands = [s for s in engine_setups if s.symbol == sym and s.trigger.direction == exp_dir and s.htf == htf_tf and s.timeline.trigger_time == ref_rej_time]
        if not cands:
            results[ref_id] = ("MISSING", None, None, None, None)
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
        our_kl_p = p_lvl.price if p_lvl else None
        ext_alerts = [a for a in s.alerts if a.breakout_type == BreakoutType.EXTERNAL]
        if not ext_alerts:
            results[ref_id] = ("PARTIAL_NO_BO", our_kl_p, None, None, p_lvl.kind.value if p_lvl else "")
            continue
        best_a = None
        for a in ext_alerts:
            p_match = abs(a.broken_level.price - ref_bo_p) <= bo_tol
            t_match = (a.breakout_candle_time == ref_bo_time)
            if p_match and t_match:
                best_a = a
                break
            if p_match or t_match:
                best_a = a
        chosen_a = best_a or ext_alerts[0]
        kl_match = p_lvl is not None and abs(p_lvl.price - ref_kl_p) <= kl_tol
        p_match = abs(chosen_a.broken_level.price - ref_bo_p) <= bo_tol
        t_match = (chosen_a.breakout_candle_time == ref_bo_time)
        status = "MATCH" if (kl_match and p_match and t_match) else "PARTIAL"
        results[ref_id] = (status, our_kl_p, chosen_a.broken_level.price, chosen_a.breakout_candle_time.strftime("%Y-%m-%dT%H:%M"), p_lvl.kind.value if p_lvl else "")
    return results

print("Running baseline (rule_fix=False)...")
res_before = run_test(rule_fix=False)
print("Running with first-touch rule fixes (rule_fix=True)...")
res_after = run_test(rule_fix=True)

# Compare
print("\n" + "="*80)
print("BEFORE VS AFTER COMPARISON ACROSS ALL 35 ROWS")
print("="*80)
print(f"{'ID':>2} {'Symbol':>7} | {'Before Status':>14} {'Before BO P':>12} {'Before BO T':>17} | {'After Status':>14} {'After BO P':>12} {'After BO T':>17} | Change?")
changes = 0
for _, r in ref_df.iterrows():
    rid = r["id"]
    sym = r["symbol"]
    b_stat, b_kl, b_bo_p, b_bo_t, b_kind = res_before[rid]
    a_stat, a_kl, a_bo_p, a_bo_t, a_kind = res_after[rid]
    chg = "SAME"
    if b_stat != a_stat or b_bo_p != a_bo_p or b_bo_t != a_bo_t:
        chg = f"CHANGED: {b_stat}->{a_stat}"
        changes += 1
    print(f"{rid:>2} {sym:>7} | {b_stat:>14} {str(b_bo_p):>12} {str(b_bo_t):>17} | {a_stat:>14} {str(a_bo_p):>12} {str(a_bo_t):>17} | {chg}")

print(f"\nTotal changes: {changes}")
b_counts = pd.Series([v[0] for v in res_before.values()]).value_counts().to_dict()
a_counts = pd.Series([v[0] for v in res_after.values()]).value_counts().to_dict()
print("Before counts:", b_counts)
print("After counts:", a_counts)
