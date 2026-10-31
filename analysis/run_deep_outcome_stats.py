"""
analysis/run_deep_outcome_stats.py
----------------------------------
Item 6(c): Forward outcome statistics on:
  - 12-month Daily history (2025-10-01 to 2026-10-19)
  - 24-month Weekly history (2024-10-01 to 2026-10-19)
Groups:
  - Daily: All External Alerts, Trend Aligned (Candidate c), Trend Unaligned
  - Weekly: All External Alerts, Trend Aligned (Candidate c), Trend Unaligned
  - Random Baseline (N=5 for Daily, N=10 for Weekly)

Metrics:
  - +1 ADR First
  - -1 ADR First
  - Neither
With 95% Confidence Intervals via block bootstrap by calendar month.
"""
import sys
from pathlib import Path
import random
import pandas as pd
import numpy as np
import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import (
    evaluate_setup_lifecycle,
    detect_ltf_levels,
    BreakoutType,
    SLKAlert,
)
from slk_bot.strategy.models import TriggerDirection, TriggerMethod
from analysis.evaluate_trend_three_moments import get_trend_at_time

cache = CandleCache()
INST_PATH = ROOT / "slk_bot" / "config" / "instruments.yaml"
with open(INST_PATH, "r", encoding="utf-8") as f:
    INST_DATA = yaml.safe_load(f)

ref_df = pd.read_csv(ROOT / "reference_setups.csv")
in_scope_syms = set(ref_df["symbol"].unique())
instruments = [inst for inst in INST_DATA.get("instruments", []) if inst["name"] in in_scope_syms]

START_DAILY_12M = pd.Timestamp("2025-10-01 00:00:00", tz="UTC")
START_WEEKLY_24M = pd.Timestamp("2024-10-01 00:00:00", tz="UTC")
END_EVAL = pd.Timestamp("2026-10-19 23:59:59", tz="UTC")

def evaluate_alert_adr_first(alert: SLKAlert, df_d: pd.DataFrame, df_ltf: pd.DataFrame, n_days: int = 5):
    t_alert = alert.breakout_candle_time
    ltf_sub = df_ltf[df_ltf.index <= t_alert]
    if ltf_sub.empty:
        return None
    entry = float(ltf_sub["close"].iloc[-1])
    
    # ADR(14)
    d_sub = df_d[df_d.index <= t_alert]
    if len(d_sub) < 15:
        return None
    dr = d_sub["high"] - d_sub["low"]
    adr14 = float(dr.iloc[-14:].mean())
    if adr14 <= 0:
        return None
        
    post_d = df_d[df_d.index >= t_alert]
    if len(post_d) < 2:
        return None
    eval_window = post_d.iloc[:n_days]
    
    is_bull = (alert.direction == TriggerDirection.BULLISH)
    target_pos = entry + adr14 if is_bull else entry - adr14
    target_neg = entry - adr14 if is_bull else entry + adr14
    
    first_hit = "neither"
    for _, row in eval_window.iterrows():
        h = row["high"]
        l = row["low"]
        if is_bull:
            hit_pos = (h >= target_pos)
            hit_neg = (l <= target_neg)
        else:
            hit_pos = (l <= target_pos)
            hit_neg = (h >= target_neg)
            
        if hit_pos and not hit_neg:
            first_hit = "+1_adr"
            break
        elif hit_neg and not hit_pos:
            first_hit = "-1_adr"
            break
        elif hit_pos and hit_neg:
            first_hit = "-1_adr"
            break
            
    cal_month = t_alert.strftime("%Y-%m")
    return {
        "symbol": alert.symbol,
        "direction": alert.direction.value,
        "alert_time": t_alert,
        "cal_month": cal_month,
        "outcome": first_hit,
    }

def block_bootstrap_month(data: list[dict], n_boot: int = 2000, seed: int = 42) -> dict:
    if not data:
        return {}
    df = pd.DataFrame(data)
    months = df["cal_month"].unique()
    if len(months) == 0:
        return {}
    rng = np.random.default_rng(seed)
    
    pos_shares = []
    neg_shares = []
    nei_shares = []
    
    n_tot = len(df)
    pt_pos = (df["outcome"] == "+1_adr").sum() / n_tot * 100.0
    pt_neg = (df["outcome"] == "-1_adr").sum() / n_tot * 100.0
    pt_nei = (df["outcome"] == "neither").sum() / n_tot * 100.0
    
    month_groups = {m: grp for m, grp in df.groupby("cal_month")}
    
    for _ in range(n_boot):
        sample_months = rng.choice(months, size=len(months), replace=True)
        sample_dfs = [month_groups[m] for m in sample_months]
        boot_df = pd.concat(sample_dfs, ignore_index=True)
        m_tot = len(boot_df)
        if m_tot == 0:
            continue
        pos_shares.append((boot_df["outcome"] == "+1_adr").sum() / m_tot * 100.0)
        neg_shares.append((boot_df["outcome"] == "-1_adr").sum() / m_tot * 100.0)
        nei_shares.append((boot_df["outcome"] == "neither").sum() / m_tot * 100.0)
        
    ci_pos = (np.percentile(pos_shares, 2.5), np.percentile(pos_shares, 97.5))
    ci_neg = (np.percentile(neg_shares, 2.5), np.percentile(neg_shares, 97.5))
    ci_nei = (np.percentile(nei_shares, 2.5), np.percentile(nei_shares, 97.5))
    
    return {
        "n": n_tot,
        "pt_pos": pt_pos, "ci_pos": ci_pos,
        "pt_neg": pt_neg, "ci_neg": ci_neg,
        "pt_nei": pt_nei, "ci_nei": ci_nei,
    }

def main():
    print("Generating deep outcome statistics...")
    d_outcomes = []
    w_outcomes = []
    
    for inst in instruments:
        sym = inst["name"]
        ac = inst.get("asset_class", "forex")
        df_d = cache.load_candles(sym, "D")
        df_h4 = cache.load_candles(sym, "H4")
        df_w = cache.load_candles(sym, "W")
        
        # Daily -> H4 over 12 months
        if not df_d.empty and not df_h4.empty:
            trigs_d = detect_triggers(
                df_d, "D", sym, ac,
                lookback=None, max_disrespects=None, ocl_max_disrespects=None,
                primary_selection_rule="fresh_first"
            )
            h4_lvls = detect_ltf_levels(df_h4, sym, "H4", 0.0)
            for t in trigs_d:
                if t.method == TriggerMethod.METHOD_2:
                    continue
                if t.trigger_candle_time < START_DAILY_12M or t.trigger_candle_time > END_EVAL:
                    continue
                s = evaluate_setup_lifecycle(
                    t, df_d, df_h4, df_d, sym, "D", "H4",
                    unfresh_alerts=True, all_ltf_levels=h4_lvls,
                    external_ref_point="first_touch"
                )
                for a in s.alerts:
                    if a.breakout_type == BreakoutType.EXTERNAL and START_DAILY_12M <= a.breakout_candle_time <= END_EVAL:
                        res = evaluate_alert_adr_first(a, df_d, df_h4, n_days=5)
                        if res:
                            prior_d = df_d[df_d.index <= a.breakout_candle_time]
                            asof = prior_d.index[-1] if not prior_d.empty else a.breakout_candle_time
                            trend, _ = get_trend_at_time(sym, "D", asof, "c")
                            exp = "BULLISH" if a.direction == TriggerDirection.BULLISH else "BEARISH"
                            res["aligned"] = (trend == exp)
                            d_outcomes.append(res)
                            
        # Weekly -> Daily over 24 months
        if not df_w.empty and not df_d.empty:
            trigs_w = detect_triggers(
                df_w, "W", sym, ac,
                lookback=None, max_disrespects=None, ocl_max_disrespects=None,
                primary_selection_rule="fresh_first"
            )
            d_lvls = detect_ltf_levels(df_d, sym, "D", 0.0)
            for t in trigs_w:
                if t.method == TriggerMethod.METHOD_2:
                    continue
                if t.trigger_candle_time < START_WEEKLY_24M or t.trigger_candle_time > END_EVAL:
                    continue
                s = evaluate_setup_lifecycle(
                    t, df_w, df_d, df_d, sym, "W", "D",
                    unfresh_alerts=True, all_ltf_levels=d_lvls,
                    external_ref_point="first_touch"
                )
                for a in s.alerts:
                    if a.breakout_type == BreakoutType.EXTERNAL and START_WEEKLY_24M <= a.breakout_candle_time <= END_EVAL:
                        res = evaluate_alert_adr_first(a, df_d, df_d, n_days=10)
                        if res:
                            prior_w = df_w[df_w.index <= a.breakout_candle_time]
                            asof = prior_w.index[-1] if not prior_w.empty else a.breakout_candle_time
                            trend, _ = get_trend_at_time(sym, "W", asof, "c")
                            exp = "BULLISH" if a.direction == TriggerDirection.BULLISH else "BEARISH"
                            res["aligned"] = (trend == exp)
                            w_outcomes.append(res)
                            
    # Random Baseline
    random.seed(42)
    base_d, base_w = [], []
    for inst in instruments:
        sym = inst["name"]
        df_d = cache.load_candles(sym, "D")
        if len(df_d) < 35:
            continue
        for _ in range(50):
            idx = random.randint(15, len(df_d) - 15)
            t = df_d.index[idx]
            rdir = random.choice(["BUY", "SELL"])
            # dummy alert
            is_bull = (rdir == "BUY")
            entry = float(df_d["close"].iloc[idx])
            dr = df_d["high"].iloc[idx-14:idx] - df_d["low"].iloc[idx-14:idx]
            adr = float(dr.mean())
            if adr <= 0:
                continue
            cal_m = t.strftime("%Y-%m")
            # 5 days
            w5 = df_d.iloc[idx:idx+5]
            if len(w5) == 5:
                hit_pos = (w5["high"].max() >= entry + adr) if is_bull else (w5["low"].min() <= entry - adr)
                hit_neg = (w5["low"].min() <= entry - adr) if is_bull else (w5["high"].max() >= entry + adr)
                o5 = "+1_adr" if hit_pos and not hit_neg else ("-1_adr" if hit_neg else "neither")
                base_d.append({"cal_month": cal_m, "outcome": o5})
            # 10 days
            w10 = df_d.iloc[idx:idx+10]
            if len(w10) == 10:
                hit_pos10 = (w10["high"].max() >= entry + adr) if is_bull else (w10["low"].min() <= entry - adr)
                hit_neg10 = (w10["low"].min() <= entry - adr) if is_bull else (w10["high"].max() >= entry + adr)
                o10 = "+1_adr" if hit_pos10 and not hit_neg10 else ("-1_adr" if hit_neg10 else "neither")
                base_w.append({"cal_month": cal_m, "outcome": o10})
                
    groups = [
        ("Daily 12M: All External Alerts", d_outcomes),
        ("  Daily: Trend Aligned (Candidate c)", [o for o in d_outcomes if o["aligned"]]),
        ("  Daily: Trend Unaligned", [o for o in d_outcomes if not o["aligned"]]),
        ("Daily Random Baseline (N=5)", base_d),
        ("Weekly 24M: All External Alerts", w_outcomes),
        ("  Weekly: Trend Aligned (Candidate c)", [o for o in w_outcomes if o["aligned"]]),
        ("  Weekly: Trend Unaligned", [o for o in w_outcomes if not o["aligned"]]),
        ("Weekly Random Baseline (N=10)", base_w),
    ]
    
    print("\n" + "=" * 105)
    print(f"{'Group':<40} | {'N':>5} | {'+1 ADR First [95% CI]':>20} | {'-1 ADR First [95% CI]':>20} | {'Neither [95% CI]':>20}")
    print("=" * 105)
    for name, data in groups:
        stats = block_bootstrap_month(data)
        if not stats:
            print(f"{name:<40} | N/A")
            continue
        p_str = f"{stats['pt_pos']:.1f}% [{stats['ci_pos'][0]:.1f}%, {stats['ci_pos'][1]:.1f}%]"
        n_str = f"{stats['pt_neg']:.1f}% [{stats['ci_neg'][0]:.1f}%, {stats['ci_neg'][1]:.1f}%]"
        e_str = f"{stats['pt_nei']:.1f}% [{stats['ci_nei'][0]:.1f}%, {stats['ci_nei'][1]:.1f}%]"
        print(f"{name:<40} | {stats['n']:>5} | {p_str:>20} | {n_str:>20} | {e_str:>20}")
    print("=" * 105)

if __name__ == "__main__":
    main()
