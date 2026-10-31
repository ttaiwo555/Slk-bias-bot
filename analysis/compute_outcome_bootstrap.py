"""
analysis/compute_outcome_bootstrap.py
-------------------------------------
Compute outcome statistics:
  - Share reaching +1 ADR first
  - Share reaching -1 ADR first
  - Neither
With 95% confidence intervals via block bootstrap by calendar week.

Groups evaluated:
  1. Canonical Reference Setups (35)
  2. Extra Setups (298)
  3. Trend Aligned Extras vs Trend Unaligned Extras
  4. All Engine Alerts (Daily & Weekly)
  5. Random Baseline
"""
import sys
from pathlib import Path
import random
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.models import TriggerDirection

cache = CandleCache()
ref_df = pd.read_csv(ROOT / "reference_setups.csv")
extras_df = pd.read_csv(ROOT / "analysis" / "output" / "extra_setups.csv")

def evaluate_setup_outcome(sym: str, rdir: str, htf: str, ltf: str, alert_time: pd.Timestamp, n_days: int = 5):
    df_d = cache.load_candles(sym, "D")
    df_ltf = cache.load_candles(sym, ltf)
    if df_d.empty or df_ltf.empty:
        return None
        
    # Entry price: close of LTF candle at alert_time
    ltf_sub = df_ltf[df_ltf.index <= alert_time]
    if ltf_sub.empty:
        return None
    entry = float(ltf_sub["close"].iloc[-1])
    
    # ADR(14)
    d_sub = df_d[df_d.index <= alert_time]
    if len(d_sub) < 15:
        return None
    dr = d_sub["high"] - d_sub["low"]
    adr14 = float(dr.iloc[-14:].mean())
    if adr14 <= 0:
        return None
        
    # Forward daily candles strictly after alert_time
    post_d = df_d[df_d.index >= alert_time]
    if len(post_d) < 2:
        return None
    eval_window = post_d.iloc[:n_days]
    
    is_bull = (rdir.upper() in ("BUY", "BULLISH"))
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
            hit_pos = (l <= target_pos) # favorable for sell
            hit_neg = (h >= target_neg) # adverse for sell
            
        if hit_pos and not hit_neg:
            first_hit = "+1_adr"
            break
        elif hit_neg and not hit_pos:
            first_hit = "-1_adr"
            break
        elif hit_pos and hit_neg:
            # Both touched in same bar: conservative assignment to adverse or tie
            first_hit = "-1_adr"
            break
            
    cal_week = alert_time.strftime("%Y-W%W")
    return {
        "symbol": sym,
        "direction": rdir,
        "alert_time": alert_time,
        "cal_week": cal_week,
        "outcome": first_hit, # "+1_adr", "-1_adr", "neither"
    }

def block_bootstrap_ci(data: list[dict], n_boot: int = 2000, seed: int = 42) -> dict:
    if not data:
        return {}
    df = pd.DataFrame(data)
    weeks = df["cal_week"].unique()
    if len(weeks) == 0:
        return {}
        
    rng = np.random.default_rng(seed)
    
    pos_shares = []
    neg_shares = []
    nei_shares = []
    
    # Point estimates
    n_tot = len(df)
    pt_pos = (df["outcome"] == "+1_adr").sum() / n_tot * 100.0
    pt_neg = (df["outcome"] == "-1_adr").sum() / n_tot * 100.0
    pt_nei = (df["outcome"] == "neither").sum() / n_tot * 100.0
    
    # Group by week for fast block sampling
    week_groups = {w: group for w, group in df.groupby("cal_week")}
    
    for _ in range(n_boot):
        sample_weeks = rng.choice(weeks, size=len(weeks), replace=True)
        sample_dfs = [week_groups[w] for w in sample_weeks]
        boot_df = pd.concat(sample_dfs, ignore_index=True)
        m = len(boot_df)
        if m == 0:
            continue
        pos_shares.append((boot_df["outcome"] == "+1_adr").sum() / m * 100.0)
        neg_shares.append((boot_df["outcome"] == "-1_adr").sum() / m * 100.0)
        nei_shares.append((boot_df["outcome"] == "neither").sum() / m * 100.0)
        
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
    print("Collecting outcomes for Reference Setups (35)...")
    ref_outcomes = []
    for idx, r in ref_df.iterrows():
        t = pd.Timestamp(r["bo_open_utc"], tz="UTC")
        res = evaluate_setup_outcome(r["symbol"], r["dir"], "D" if "D1" in r["pair"] else "W", "H4" if "D1" in r["pair"] else "D", t, 5 if "D1" in r["pair"] else 10)
        if res:
            ref_outcomes.append(res)
            
    print(f"Evaluated {len(ref_outcomes)} / 35 reference setups.")
    
    print("\nCollecting outcomes for Extra Setups (298)...")
    ext_outcomes = []
    for idx, r in extras_df.iterrows():
        t = pd.Timestamp(r["bo_time"], tz="UTC")
        res = evaluate_setup_outcome(r["symbol"], r["direction"], r["htf"], r["ltf"], t, 5 if r["htf"] == "D" else 10)
        if res:
            ext_outcomes.append(res)
            
    print(f"Evaluated {len(ext_outcomes)} / {len(extras_df)} extra setups.")
    
    # Load candidate trend to split extras into Aligned vs Unaligned
    from analysis.run_trend_evaluation import get_trend
    ext_aligned = []
    ext_unaligned = []
    for o in ext_outcomes:
        sym = o["symbol"]
        rdir = o["direction"]
        exp_trend = "BULLISH" if rdir in ("BUY", "bullish") else "BEARISH"
        # Using candidate c trend at breakout
        t_dir = get_trend(sym, "D", o["alert_time"], "c")
        if t_dir == exp_trend:
            ext_aligned.append(o)
        else:
            ext_unaligned.append(o)
            
    print(f"Extras split: Aligned = {len(ext_aligned)}, Unaligned = {len(ext_unaligned)}")
    
    # Generate Random Baseline (300 random trades across all instruments)
    print("\nGenerating Random Baseline...")
    random.seed(42)
    baseline_outcomes = []
    syms = ref_df["symbol"].unique()
    for _ in range(400):
        sym = random.choice(syms)
        df_d = cache.load_candles(sym, "D")
        if len(df_d) < 35:
            continue
        idx = random.randint(15, len(df_d) - 10)
        t = df_d.index[idx]
        rdir = random.choice(["BUY", "SELL"])
        res = evaluate_setup_outcome(sym, rdir, "D", "H4", t, 5)
        if res:
            baseline_outcomes.append(res)
    print(f"Evaluated {len(baseline_outcomes)} random baseline trades.")
    
    # Run Bootstrap for all groups
    groups = [
        ("Canonical Reference Setups (35)", ref_outcomes),
        ("Extra Setups (All 298)", ext_outcomes),
        ("  Extras: Trend Aligned (Candidate c)", ext_aligned),
        ("  Extras: Trend Unaligned", ext_unaligned),
        ("Random Baseline (N=5)", baseline_outcomes),
    ]
    
    print("\n" + "=" * 95)
    print(f"{'Group':<35} | {'N':>4} | {'+1 ADR First (95% CI)':>22} | {'-1 ADR First (95% CI)':>22} | {'Neither (95% CI)':>22}")
    print("=" * 95)
    
    for name, data in groups:
        stats = block_bootstrap_ci(data)
        if not stats:
            print(f"{name:<35} | N/A")
            continue
        pos_str = f"{stats['pt_pos']:.1f}% [{stats['ci_pos'][0]:.1f}%, {stats['ci_pos'][1]:.1f}%]"
        neg_str = f"{stats['pt_neg']:.1f}% [{stats['ci_neg'][0]:.1f}%, {stats['ci_neg'][1]:.1f}%]"
        nei_str = f"{stats['pt_nei']:.1f}% [{stats['ci_nei'][0]:.1f}%, {stats['ci_nei'][1]:.1f}%]"
        print(f"{name:<35} | {stats['n']:>4} | {pos_str:>22} | {neg_str:>22} | {nei_str:>22}")
    print("=" * 95)

if __name__ == "__main__":
    main()
