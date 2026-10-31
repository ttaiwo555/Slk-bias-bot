"""
analysis/run_parity_reproduction.py
-----------------------------------
Runs reproduction with Item 2 Parity Settings:
  - max_disrespects = None (off)
  - ocl_max_disrespects = None (off)
  - lookback = None (all available history)
  - primary_selection_rule = "fresh_first"
Evaluates both external_reference_point = "first_touch" and "extreme".
Adds columns: key_kind_ref, key_kind_ours.
Reports:
  - MATCH / PARTIAL / MISSING counts
  - MATCH rows with different key level kind or price difference > 0.03%
Writes:
  - analysis/output/match_table.csv
  - analysis/output/extra_setups.csv
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
    evaluate_setup_lifecycle,
    detect_ltf_levels,
    find_mitigation_candle,
    get_candle_close_time,
    BreakoutType,
    SLKAlert,
    SLKSetup,
)
from slk_bot.strategy.models import (
    TriggerDirection,
    TriggerMethod,
    LevelKind,
    KeyLevel,
)

OUTPUT_DIR = ROOT / "analysis" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

START_DATE = pd.Timestamp("2026-10-05 00:00:00", tz="UTC")
END_DATE = pd.Timestamp("2026-10-17 23:59:59", tz="UTC")

def get_tolerance(symbol: str, price: float) -> float:
    if symbol in ("XAGUSD", "XAUUSD"):
        return price * 0.0015  # 0.15% metals
    elif symbol in ("NAS100", "SPX500", "US30", "GER40", "UK100"):
        return price * 0.0020  # 0.20% indices
    return price * 0.0005      # 0.05% forex & other

def run_parity(external_ref_mode: str = "first_touch"):
    cache = CandleCache()
    ref_df = pd.read_csv(ROOT / "reference_setups.csv")

    with open(ROOT / "slk_bot" / "config" / "instruments.yaml", "r", encoding="utf-8") as f:
        inst_data = yaml.safe_load(f)

    in_scope_symbols = set(ref_df["symbol"].unique())
    instruments = [
        inst for inst in inst_data.get("instruments", [])
        if inst["name"] in in_scope_symbols
    ]

    engine_setups: list[SLKSetup] = []
    engine_alerts: list[SLKAlert] = []

    for inst in instruments:
        sym = inst["name"]
        asset_class = inst.get("asset_class", "forex")

        # Daily -> H4
        df_d = cache.load_candles(sym, "D")
        df_h4 = cache.load_candles(sym, "H4")

        if not df_d.empty and not df_h4.empty:
            trigs_d = detect_triggers(
                df_d,
                timeframe="D",
                symbol=sym,
                asset_class=asset_class,
                lookback=None,             # all available history
                max_disrespects=None,      # off
                ocl_max_disrespects=None,  # off
                primary_selection_rule="fresh_first", # Rule (b)
            )
            trigs_d_win = [t for t in trigs_d if t.trigger_candle_time >= pd.Timestamp("2026-09-20", tz="UTC")]
            h4_levels = detect_ltf_levels(df_h4, sym, "H4", 0.0)

            for t in trigs_d_win:
                if t.method == TriggerMethod.METHOD_2:
                    continue  # Method 2 sweep-only off in reproduction
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
                    external_ref_point=external_ref_mode,
                )
                engine_setups.append(s)
                for a in s.alerts:
                    if a.breakout_type == BreakoutType.EXTERNAL and START_DATE <= a.breakout_candle_time <= END_DATE:
                        engine_alerts.append(a)

        # Weekly -> Daily
        df_w = cache.load_candles(sym, "W")
        if not df_w.empty and not df_d.empty:
            trigs_w = detect_triggers(
                df_w,
                timeframe="W",
                symbol=sym,
                asset_class=asset_class,
                lookback=None,             # all available history
                max_disrespects=None,      # off
                ocl_max_disrespects=None,  # off
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
                    external_ref_point=external_ref_mode,
                )
                engine_setups.append(s)
                for a in s.alerts:
                    if a.breakout_type == BreakoutType.EXTERNAL and START_DATE <= a.breakout_candle_time <= END_DATE:
                        engine_alerts.append(a)

    # Build Match Table
    match_rows = []
    matched_engine_alert_keys = set()

    for idx, r in ref_df.iterrows():
        ref_id = r["id"]
        sym = r["symbol"]
        rdir = r["dir"]
        pair = r["pair"]
        ref_kl_kind = r["kl_kind"]
        htf_tf = "W" if "W1" in pair else "D"
        ltf_tf = "D" if "W1" in pair else "H4"
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
                "id": ref_id,
                "symbol": sym,
                "our_found": "N",
                "key_kind_ref": ref_kl_kind,
                "key_kind_ours": "",
                "our_level_price": "",
                "our_bo_price": "",
                "our_rej_time": "",
                "our_bo_time": "",
                "status": "MISSING",
                "diagnosis": "No trigger found for this symbol, direction and rejection candle",
            })
            continue

        # Setup found! Check alerts
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
        our_kl_kind = p_lvl.kind.value if p_lvl else ""
        our_kl_p = f"{p_lvl.price:.5f}" if p_lvl else ""
        our_rej_t = s.timeline.trigger_time.strftime("%Y-%m-%dT%H:%M")

        ext_alerts = [a for a in s.alerts if a.breakout_type == BreakoutType.EXTERNAL]
        if not ext_alerts:
            match_rows.append({
                "id": ref_id,
                "symbol": sym,
                "our_found": "Y",
                "key_kind_ref": ref_kl_kind,
                "key_kind_ours": our_kl_kind,
                "our_level_price": our_kl_p,
                "our_bo_price": "",
                "our_rej_time": our_rej_t,
                "our_bo_time": "",
                "status": "PARTIAL",
                "diagnosis": "Rejection candle found, but no external breakout confirmed",
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
        our_bo_p = f"{chosen_a.broken_level.price:.5f}"
        our_bo_t = chosen_a.breakout_candle_time.strftime("%Y-%m-%dT%H:%M")

        kl_match = p_lvl is not None and abs(p_lvl.price - ref_kl_p) <= kl_tol
        p_match = abs(chosen_a.broken_level.price - ref_bo_p) <= bo_tol
        t_match = (chosen_a.breakout_candle_time == ref_bo_time)

        if kl_match and p_match and t_match:
            status = "MATCH"
            diag = "Exact match on rejection, key level, breakout level and breakout candle time"
            matched_engine_alert_keys.add((chosen_a.symbol, chosen_a.direction.value, chosen_a.breakout_candle_time))
        else:
            status = "PARTIAL"
            diag_items = []
            if not kl_match:
                diag_items.append(f"Level kind/price ({our_kl_kind} {our_kl_p} vs {ref_kl_kind} {ref_kl_p})")
            if not p_match:
                diag_items.append(f"Breakout price ({our_bo_p} vs {ref_bo_p})")
            if not t_match:
                diag_items.append(f"Breakout time ({our_bo_t} vs {r['bo_open_utc']})")
            diag = "; ".join(diag_items)
            matched_engine_alert_keys.add((chosen_a.symbol, chosen_a.direction.value, chosen_a.breakout_candle_time))

        match_rows.append({
            "id": ref_id,
            "symbol": sym,
            "our_found": "Y",
            "key_kind_ref": ref_kl_kind,
            "key_kind_ours": our_kl_kind,
            "our_level_price": our_kl_p,
            "our_bo_price": our_bo_p,
            "our_rej_time": our_rej_t,
            "our_bo_time": our_bo_t,
            "status": status,
            "diagnosis": diag,
        })

    match_df = pd.DataFrame(match_rows)

    extra_rows = []
    for a in engine_alerts:
        key = (a.symbol, a.direction.value, a.breakout_candle_time)
        if key not in matched_engine_alert_keys:
            s_parent = next((s for s in engine_setups if a in s.alerts), None)
            t_rej = s_parent.timeline.trigger_time.strftime("%Y-%m-%dT%H:%M") if s_parent else ""
            extra_rows.append({
                "symbol": a.symbol,
                "direction": a.direction.value,
                "htf": a.htf,
                "ltf": a.ltf,
                "method": int(a.method),
                "rej_time": t_rej,
                "level_kind": a.htf_level.kind.value if a.htf_level else "",
                "level_price": f"{a.htf_level.price:.5f}" if a.htf_level else "",
                "bo_price": f"{a.broken_level.price:.5f}",
                "bo_time": a.breakout_candle_time.strftime("%Y-%m-%dT%H:%M"),
                "fresh": a.htf_level.fresh if a.htf_level else False,
            })
    extra_df = pd.DataFrame(extra_rows)

    return match_df, extra_df

if __name__ == "__main__":
    for mode in ["first_touch", "extreme"]:
        m_df, e_df = run_parity(mode)
        print(f"=== SETTING: external_reference_point = {mode} ===")
        vc = m_df["status"].value_counts().to_dict()
        print(f"Counts: MATCH={vc.get('MATCH', 0)}, PARTIAL={vc.get('PARTIAL', 0)}, MISSING={vc.get('MISSING', 0)}")
        print(f"Extra setups produced: {len(e_df)}")
        
        # Check MATCH rows for key level kind diff or price diff > 0.03%
        matches = m_df[m_df["status"] == "MATCH"]
        ref_df = pd.read_csv(ROOT / "reference_setups.csv")
        kind_diffs = []
        price_diff_above_03 = []
        for _, row in matches.iterrows():
            ref_row = ref_df[ref_df["id"] == row["id"]].iloc[0]
            # normalize kind
            r_kind = "OCL" if "OCL" in ref_row["kl_kind"] else ref_row["kl_kind"]
            o_kind = "OCL" if "OCL" in row["key_kind_ours"] else row["key_kind_ours"]
            if r_kind != o_kind:
                kind_diffs.append((row["id"], row["symbol"], r_kind, o_kind))
            r_p = float(ref_row["kl_price"])
            o_p = float(row["our_level_price"])
            p_diff_pct = abs(o_p - r_p) / r_p * 100.0
            if p_diff_pct > 0.03:
                price_diff_above_03.append((row["id"], row["symbol"], r_p, o_p, p_diff_pct))
                
        print(f"MATCH rows with different key kind: {len(kind_diffs)} {kind_diffs}")
        print(f"MATCH rows with price diff > 0.03%: {len(price_diff_above_03)} {price_diff_above_03}")
        print()
        
        if mode == "first_touch":
            m_df.to_csv(OUTPUT_DIR / "match_table.csv", index=False)
            e_df.to_csv(OUTPUT_DIR / "extra_setups.csv", index=False)
