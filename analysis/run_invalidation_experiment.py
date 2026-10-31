"""
analysis/run_invalidation_experiment.py
---------------------------------------
Runs the comparison for Item 2:
  Variant (A): Current invalidation rule (enable_invalidation=True)
  Variant (B): Invalidation off / expiry only (enable_invalidation=False)

Reports for each variant:
  1. Parity match status counter (MATCH, PARTIAL, MISSING)
  2. Same-breakout-candle time count against reference (out of 35)
  3. Rows that change between (A) and (B)
  4. Weekday volume for 12-16 Oct 2026 (both Trend Required and Trend Off)
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

def run_variant(enable_invalidation: bool):
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
                primary_selection_rule="fresh_first",
            )
            trigs_d_win = [t for t in trigs_d if t.trigger_candle_time >= pd.Timestamp("2026-09-20", tz="UTC")]
            h4_levels = detect_ltf_levels(df_h4, sym, "H4", 0.0)
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
                    all_ltf_levels=h4_levels,
                    external_ref_point="first_touch",
                    enable_invalidation=enable_invalidation,
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
                primary_selection_rule="fresh_first",
            )
            trigs_w_win = [t for t in trigs_w if t.trigger_candle_time >= pd.Timestamp("2026-08-01", tz="UTC")]
            d_levels = detect_ltf_levels(df_d, sym, "D", 0.0)
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
                    all_ltf_levels=d_levels,
                    external_ref_point="first_touch",
                    enable_invalidation=enable_invalidation,
                )
                engine_setups.append(s)
                for a in s.alerts:
                    if a.breakout_type == BreakoutType.EXTERNAL and START_WEEK <= a.breakout_candle_time <= END_WEEK:
                        all_external_alerts.append(a)

    # Parity Match Table
    match_rows = []
    for idx, r in ref_df.iterrows():
        ref_id = r["id"]
        sym = r["symbol"]
        rdir = r["dir"]
        pair = r["pair"]
        ref_kl_kind = r["kl_kind"]
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
            match_rows.append({
                "id": ref_id, "symbol": sym, "status": "MISSING",
                "our_bo_time": None, "our_bo_price": None, "our_kl_price": None,
            })
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
            match_rows.append({
                "id": ref_id, "symbol": sym, "status": "PARTIAL",
                "our_bo_time": None, "our_bo_price": None, "our_kl_price": our_kl_p,
            })
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
        our_bo_p = chosen_a.broken_level.price
        our_bo_t = chosen_a.breakout_candle_time

        kl_match = p_lvl is not None and abs(p_lvl.price - ref_kl_p) <= kl_tol
        p_match = abs(chosen_a.broken_level.price - ref_bo_p) <= bo_tol
        t_match = (chosen_a.breakout_candle_time == ref_bo_time)

        status = "MATCH" if (kl_match and p_match and t_match) else "PARTIAL"
        match_rows.append({
            "id": ref_id, "symbol": sym, "status": status,
            "our_bo_time": our_bo_t, "our_bo_price": our_bo_p, "our_kl_price": our_kl_p,
        })

    match_df = pd.DataFrame(match_rows)

    # Count same-breakout-candle
    same_bo_cnt = 0
    for idx, r in ref_df.iterrows():
        ref_id = r["id"]
        ref_bo_t = pd.Timestamp(r["bo_open_utc"], tz="UTC")
        m_row = match_df[match_df["id"] == ref_id].iloc[0]
        if m_row["our_bo_time"] is not None and m_row["our_bo_time"] == ref_bo_t:
            same_bo_cnt += 1

    # Weekday volumes
    # Trend Required alerts
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

    days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    
    # Trend Required volume
    df_tr = pd.DataFrame([
        {"symbol": a.symbol, "direction": a.direction.value, "weekday": a.breakout_candle_time.day_name()}
        for a in trend_req_alerts
    ])
    tr_unique = df_tr.drop_duplicates(subset=["symbol", "direction", "weekday"]) if not df_tr.empty else pd.DataFrame()
    tr_vol = {day: (tr_unique["weekday"] == day).sum() if not tr_unique.empty else 0 for day in days_order}

    # Trend Off volume
    df_all = pd.DataFrame([
        {"symbol": a.symbol, "direction": a.direction.value, "weekday": a.breakout_candle_time.day_name()}
        for a in all_external_alerts
    ])
    all_unique = df_all.drop_duplicates(subset=["symbol", "direction", "weekday"]) if not df_all.empty else pd.DataFrame()
    all_vol = {day: (all_unique["weekday"] == day).sum() if not all_unique.empty else 0 for day in days_order}

    return match_df, same_bo_cnt, tr_vol, all_vol

if __name__ == "__main__":
    print("=" * 80)
    print("RUNNING ITEM 2: INVALIDATION EXPERIMENT")
    print("=" * 80)

    print("\n--- Running Variant (A): Current Invalidation Rule (enable_invalidation=True) ---")
    match_a, same_bo_a, tr_vol_a, all_vol_a = run_variant(enable_invalidation=True)
    vc_a = match_a["status"].value_counts().to_dict()

    print("\n--- Running Variant (B): Invalidation Off / Expiry Only (enable_invalidation=False) ---")
    match_b, same_bo_b, tr_vol_b, all_vol_b = run_variant(enable_invalidation=False)
    vc_b = match_b["status"].value_counts().to_dict()

    print("\n" + "=" * 80)
    print("ITEM 2 COMPARISON RESULTS")
    print("=" * 80)
    print(f"Variant (A) Current Invalidation:")
    print(f"  Status Counter : MATCH={vc_a.get('MATCH', 0)}, PARTIAL={vc_a.get('PARTIAL', 0)}, MISSING={vc_a.get('MISSING', 0)}")
    print(f"  Same-Candle BO : {same_bo_a} / 35")
    print(f"  Weekday Unique Volume (Trend Required): Mon={tr_vol_a['Monday']}, Tue={tr_vol_a['Tuesday']}, Wed={tr_vol_a['Wednesday']}, Thu={tr_vol_a['Thursday']}, Fri={tr_vol_a['Friday']} | Total={sum(tr_vol_a.values())}")
    print(f"  Weekday Unique Volume (Trend Off)     : Mon={all_vol_a['Monday']}, Tue={all_vol_a['Tuesday']}, Wed={all_vol_a['Wednesday']}, Thu={all_vol_a['Thursday']}, Fri={all_vol_a['Friday']} | Total={sum(all_vol_a.values())}")

    print(f"\nVariant (B) Invalidation Off (Expiry Only):")
    print(f"  Status Counter : MATCH={vc_b.get('MATCH', 0)}, PARTIAL={vc_b.get('PARTIAL', 0)}, MISSING={vc_b.get('MISSING', 0)}")
    print(f"  Same-Candle BO : {same_bo_b} / 35")
    print(f"  Weekday Unique Volume (Trend Required): Mon={tr_vol_b['Monday']}, Tue={tr_vol_b['Tuesday']}, Wed={tr_vol_b['Wednesday']}, Thu={tr_vol_b['Thursday']}, Fri={tr_vol_b['Friday']} | Total={sum(tr_vol_b.values())}")
    print(f"  Weekday Unique Volume (Trend Off)     : Mon={all_vol_b['Monday']}, Tue={all_vol_b['Tuesday']}, Wed={all_vol_b['Wednesday']}, Thu={all_vol_b['Thursday']}, Fri={all_vol_b['Friday']} | Total={sum(all_vol_b.values())}")

    # Rows that change
    print("\n--- ROWS THAT CHANGE BETWEEN (A) AND (B) ---")
    changed_rows = []
    for idx in range(len(match_a)):
        ra = match_a.iloc[idx]
        rb = match_b.iloc[idx]
        if ra["status"] != rb["status"] or ra["our_bo_time"] != rb["our_bo_time"] or ra["our_bo_price"] != rb["our_bo_price"]:
            changed_rows.append({
                "id": ra["id"],
                "symbol": ra["symbol"],
                "status_A": ra["status"],
                "status_B": rb["status"],
                "bo_time_A": str(ra["our_bo_time"]),
                "bo_time_B": str(rb["our_bo_time"]),
                "bo_price_A": ra["our_bo_price"],
                "bo_price_B": rb["our_bo_price"],
            })
    if not changed_rows:
        print("  No rows changed.")
    else:
        for cr in changed_rows:
            print(f"  # {cr['id']:<2} {cr['symbol']:<7} | Status: (A) {cr['status_A']} -> (B) {cr['status_B']} | BO Time: (A) {cr['bo_time_A']} -> (B) {cr['bo_time_B']} | BO Price: (A) {cr['bo_price_A']} -> (B) {cr['bo_price_B']}")
