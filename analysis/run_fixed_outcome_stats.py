import sys
from pathlib import Path
import random
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

def evaluate_path_outcome(
    fwd_candles: pd.DataFrame,
    entry: float,
    adr: float,
    is_bull: bool,
    max_candles: int,
) -> str:
    """
    Evaluates outcome forward strictly candle-by-candle.
    Tie-break rule when both targets are touched on the same candle:
      - Bullish candle (close >= open): price dips to low first, then rallies to high.
        If BUY (bullish): low hit first -> -1 ADR.
        If SELL (bearish): low hit first -> +1 ADR.
      - Bearish candle (close < open): price rallies to high first, then dips to low.
        If BUY (bullish): high hit first -> +1 ADR.
        If SELL (bearish): high hit first -> -1 ADR.
    """
    target_pos = entry + adr if is_bull else entry - adr
    target_neg = entry - adr if is_bull else entry + adr

    sub = fwd_candles.iloc[:max_candles]
    for _, row in sub.iterrows():
        o = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])
        c = float(row["close"])

        if is_bull:
            hit_pos = (h >= target_pos)
            hit_neg = (l <= target_neg)
        else:
            hit_pos = (l <= target_pos)
            hit_neg = (h >= target_neg)

        if hit_pos and not hit_neg:
            return "+1_adr"
        elif hit_neg and not hit_pos:
            return "-1_adr"
        elif hit_pos and hit_neg:
            # Tie-break via intra-candle path
            if c >= o:  # Bullish candle: low formed before high
                return "-1_adr" if is_bull else "+1_adr"
            else:       # Bearish candle: high formed before low
                return "+1_adr" if is_bull else "-1_adr"

    return "neither"

def evaluate_alert_post_alert(
    alert: SLKAlert,
    df_d: pd.DataFrame,
    df_fwd: pd.DataFrame,
    fwd_max_candles: int,
) -> dict:
    t_alert = alert.alert_time
    # Entry price is close at or immediately before alert_time
    prior_fwd = df_fwd[df_fwd.index <= t_alert]
    if prior_fwd.empty:
        return None
    entry = float(prior_fwd["close"].iloc[-1])

    # ADR(14) from daily candles closed strictly before or at alert_time
    prior_d = df_d[df_d.index <= t_alert]
    if len(prior_d) < 15:
        return None
    dr = prior_d["high"].iloc[-14:] - prior_d["low"].iloc[-14:]
    adr = float(dr.mean())
    if adr <= 0:
        return None

    # Forward candles starting STRICTLY AFTER alert time (no part of alert day before alert counts)
    fwd_sub = df_fwd[df_fwd.index > t_alert]
    if fwd_sub.empty:
        return None

    is_bull = (alert.direction == TriggerDirection.BULLISH)
    outcome = evaluate_path_outcome(fwd_sub, entry, adr, is_bull, fwd_max_candles)
    cal_m = t_alert.strftime("%Y-%m")

    return {
        "symbol": alert.symbol,
        "direction": alert.direction.value,
        "alert_time": t_alert,
        "cal_month": cal_m,
        "outcome": outcome,
    }

def block_bootstrap_month(data: list[dict], n_boot: int = 2000, seed: int = 42) -> dict:
    if not data:
        return {}
    df = pd.DataFrame(data)
    months = df["cal_month"].unique()
    if len(months) == 0:
        return {}
    rng = np.random.default_rng(seed)

    month_dfs = {m: df[df["cal_month"] == m] for m in months}

    pos_shares, neg_shares, nei_shares = [], [], []
    for _ in range(n_boot):
        sample_m = rng.choice(months, size=len(months), replace=True)
        boot_df = pd.concat([month_dfs[m] for m in sample_m], ignore_index=True)
        n_tot = len(boot_df)
        if n_tot == 0:
            continue
        vc = boot_df["outcome"].value_counts().to_dict()
        pos_shares.append(vc.get("+1_adr", 0) / n_tot * 100.0)
        neg_shares.append(vc.get("-1_adr", 0) / n_tot * 100.0)
        nei_shares.append(vc.get("neither", 0) / n_tot * 100.0)

    n_tot = len(df)
    vc = df["outcome"].value_counts().to_dict()
    pt_pos = vc.get("+1_adr", 0) / n_tot * 100.0
    pt_neg = vc.get("-1_adr", 0) / n_tot * 100.0
    pt_nei = vc.get("neither", 0) / n_tot * 100.0

    return {
        "n": n_tot,
        "pt_pos": pt_pos,
        "ci_pos": (np.percentile(pos_shares, 2.5), np.percentile(pos_shares, 97.5)),
        "pt_neg": pt_neg,
        "ci_neg": (np.percentile(neg_shares, 2.5), np.percentile(neg_shares, 97.5)),
        "pt_nei": pt_nei,
        "ci_nei": (np.percentile(nei_shares, 2.5), np.percentile(nei_shares, 97.5)),
    }

def main():
    print("=" * 105)
    print("FORWARD OUTCOME STATISTICS (STRICTLY POST-ALERT CANDLES & SYMMETRIC BASELINE)")
    print("=" * 105)

    d_outcomes = []
    w_outcomes = []

    for inst in instruments:
        sym = inst["name"]
        ac = inst.get("asset_class", "forex")
        df_d = cache.load_candles(sym, "D")
        df_h4 = cache.load_candles(sym, "H4")

        # Daily 12M: H4 forward candles strictly after alert time
        if not df_d.empty and not df_h4.empty:
            trigs_d = detect_triggers(
                df_d, "D", sym, ac,
                lookback=None, max_disrespects=None, ocl_max_disrespects=None,
                primary_selection_rule="fresh_first",
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
                    external_ref_point="first_touch",
                )
                for a in s.alerts:
                    if a.breakout_type == BreakoutType.EXTERNAL and START_DAILY_12M <= a.breakout_candle_time <= END_EVAL:
                        # 30 H4 candles = 5 trading days
                        res = evaluate_alert_post_alert(a, df_d, df_h4, fwd_max_candles=30)
                        if res:
                            prior_d = df_d[df_d.index < a.breakout_candle_time]
                            asof = prior_d.index[-1] if not prior_d.empty else a.breakout_candle_time
                            trend, _ = get_trend_at_time(sym, "D", asof, "c")
                            exp = "BULLISH" if a.direction == TriggerDirection.BULLISH else "BEARISH"
                            res["aligned"] = (trend == exp)
                            d_outcomes.append(res)

        # Weekly 24M: Daily forward candles strictly after alert time
        df_w = cache.load_candles(sym, "W")
        if not df_w.empty and not df_d.empty:
            trigs_w = detect_triggers(
                df_w, "W", sym, ac,
                lookback=None, max_disrespects=None, ocl_max_disrespects=None,
                primary_selection_rule="fresh_first",
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
                    external_ref_point="first_touch",
                )
                for a in s.alerts:
                    if a.breakout_type == BreakoutType.EXTERNAL and START_WEEKLY_24M <= a.breakout_candle_time <= END_EVAL:
                        # 10 Daily candles = 10 trading days
                        res = evaluate_alert_post_alert(a, df_d, df_d, fwd_max_candles=10)
                        if res:
                            prior_w = df_w[df_w.index < a.breakout_candle_time]
                            asof = prior_w.index[-1] if not prior_w.empty else a.breakout_candle_time
                            trend, _ = get_trend_at_time(sym, "W", asof, "c")
                            exp = "BULLISH" if a.direction == TriggerDirection.BULLISH else "BEARISH"
                            res["aligned"] = (trend == exp)
                            w_outcomes.append(res)

    # Symmetric Random Baseline
    rng = random.Random(42)
    base_d = []
    base_w = []

    for inst in instruments:
        sym = inst["name"]
        df_d = cache.load_candles(sym, "D")
        df_h4 = cache.load_candles(sym, "H4")
        if len(df_d) < 35 or len(df_h4) < 100:
            continue

        # Daily baseline: random sampling across H4 candles in 12M window
        h4_12m = df_h4[(df_h4.index >= START_DAILY_12M) & (df_h4.index <= END_EVAL)]
        if len(h4_12m) > 50:
            for _ in range(50):
                t_rand = rng.choice(h4_12m.index[:-35])
                rdir = rng.choice(["BUY", "SELL"])
                is_bull = (rdir == "BUY")
                entry = float(df_h4.loc[t_rand, "close"])
                prior_d = df_d[df_d.index <= t_rand]
                if len(prior_d) < 15:
                    continue
                adr = float((prior_d["high"].iloc[-14:] - prior_d["low"].iloc[-14:]).mean())
                if adr <= 0:
                    continue
                fwd_h4 = df_h4[df_h4.index > t_rand]
                outc = evaluate_path_outcome(fwd_h4, entry, adr, is_bull, max_candles=30)
                base_d.append({"cal_month": t_rand.strftime("%Y-%m"), "outcome": outc})

        # Weekly baseline: random sampling across Daily candles in 24M window
        d_24m = df_d[(df_d.index >= START_WEEKLY_24M) & (df_d.index <= END_EVAL)]
        if len(d_24m) > 50:
            for _ in range(50):
                t_rand = rng.choice(d_24m.index[:-15])
                rdir = rng.choice(["BUY", "SELL"])
                is_bull = (rdir == "BUY")
                entry = float(df_d.loc[t_rand, "close"])
                prior_d = df_d[df_d.index <= t_rand]
                if len(prior_d) < 15:
                    continue
                adr = float((prior_d["high"].iloc[-14:] - prior_d["low"].iloc[-14:]).mean())
                if adr <= 0:
                    continue
                fwd_d = df_d[df_d.index > t_rand]
                outc = evaluate_path_outcome(fwd_d, entry, adr, is_bull, max_candles=10)
                base_w.append({"cal_month": t_rand.strftime("%Y-%m"), "outcome": outc})

    groups = [
        ("Daily 12M: All External Alerts", d_outcomes),
        ("  Daily: Trend Aligned (Candidate c)", [o for o in d_outcomes if o["aligned"]]),
        ("  Daily: Trend Unaligned", [o for o in d_outcomes if not o["aligned"]]),
        ("Daily Random Baseline (N=5 days)", base_d),
        ("Weekly 24M: All External Alerts", w_outcomes),
        ("  Weekly: Trend Aligned (Candidate c)", [o for o in w_outcomes if o["aligned"]]),
        ("  Weekly: Trend Unaligned", [o for o in w_outcomes if not o["aligned"]]),
        ("Weekly Random Baseline (N=10 days)", base_w),
    ]

    print("\n" + "=" * 115)
    print(f"{'Group':<42} | {'N':>5} | {'+1 ADR First [95% CI]':>22} | {'-1 ADR First [95% CI]':>22} | {'Neither [95% CI]':>20}")
    print("-" * 115)
    for name, data in groups:
        stats = block_bootstrap_month(data)
        if not stats:
            print(f"{name:<42} | N/A")
            continue
        p_str = f"{stats['pt_pos']:.1f}% [{stats['ci_pos'][0]:.1f}%, {stats['ci_pos'][1]:.1f}%]"
        n_str = f"{stats['pt_neg']:.1f}% [{stats['ci_neg'][0]:.1f}%, {stats['ci_neg'][1]:.1f}%]"
        e_str = f"{stats['pt_nei']:.1f}% [{stats['ci_nei'][0]:.1f}%, {stats['ci_nei'][1]:.1f}%]"
        print(f"{name:<42} | {stats['n']:>5} | {p_str:>22} | {n_str:>22} | {e_str:>20}")
    print("=" * 115)

if __name__ == "__main__":
    main()

