"""
analysis/evaluate_trend_three_moments.py
----------------------------------------
Evaluates Trend Candidates (a), (b), (c) at three moments:
  (i)   Before rejection candle: state as of the D1 candle closed immediately before rejection
  (ii)  After rejection candle has closed: state including the rejection candle's close
  (iii) At alert time: state using D1 candles closed up to the breakout candle open time

For #10 AUDJPY, #28 GER40, and #32 SPX500:
  Shows the D1 swing points and closes deciding the state under each timing.
Chooses the candidate and timing aligning the most reference setups.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.levels import detect_formation
from slk_bot.strategy.models import LevelKind, TriggerDirection

cache = CandleCache()
ref_df = pd.read_csv(ROOT / "reference_setups.csv")

MARGINAL_THRESHOLDS = {
    "forex": 0.0003,      # 3 pips
    "indices": 0.0010,    # 0.10%
    "crypto": 0.0010,     # 0.10%
    "commodities": 0.0010,# 0.10%
}

def get_asset_class(sym: str) -> str:
    if "BTC" in sym or "ETH" in sym:
        return "crypto"
    if sym in ("NAS100", "SPX500", "US30", "GER40", "UK100", "EU50", "JPN225"):
        return "indices"
    if sym in ("XAUUSD", "XAGUSD", "USOIL", "BRENT"):
        return "commodities"
    return "forex"

# Candidate (a): Last broken A-shape (up) or V-shape (down)
def compute_trend_a(df: pd.DataFrame):
    closes = df["close"].values
    opens = df["open"].values
    times = df.index
    n = len(df)
    
    levels = []
    for k in range(1, n):
        f = detect_formation(opens[k-1], closes[k-1], opens[k], closes[k], 0.0)
        if f:
            kind, role, side = f
            if kind in (LevelKind.A_SHAPE, LevelKind.V_SHAPE):
                levels.append((k, kind, float(closes[k-1])))
                
    breaks = []
    for k in range(1, n):
        c = closes[k]
        t = times[k]
        for conf_k, kind, p in levels:
            if conf_k < k:
                if kind == LevelKind.A_SHAPE and c > p:
                    breaks.append((t, "BULLISH"))
                elif kind == LevelKind.V_SHAPE and c < p:
                    breaks.append((t, "BEARISH"))
    return breaks

# Candidate (b) & (c): Protected swing rule (alternating joints on close chart)
def compute_trend_b_c(df: pd.DataFrame, use_marginal: bool = False, sym: str = ""):
    closes = df["close"].values
    opens = df["open"].values
    times = df.index
    n = len(df)
    
    swings = []
    for k in range(1, n):
        f = detect_formation(opens[k-1], closes[k-1], opens[k], closes[k], 0.0)
        if f:
            kind, role, side = f
            if kind == LevelKind.A_SHAPE:
                swings.append((k, "PEAK", float(closes[k-1]), times[k-1]))
            elif kind == LevelKind.V_SHAPE:
                swings.append((k, "TROUGH", float(closes[k-1]), times[k-1]))
                
    ac = get_asset_class(sym)
    thresh = MARGINAL_THRESHOLDS.get(ac, 0.0005) if use_marginal else 0.0
    
    trend_state = "NONE"
    state_history = []
    
    for k in range(1, n):
        c = closes[k]
        t = times[k]
        
        avail = [s for s in swings if s[0] <= k]
        # Alternate joints
        alt = []
        for s in avail:
            if not alt:
                alt.append(s)
            else:
                if s[1] == alt[-1][1]:
                    if s[1] == "PEAK":
                        if s[2] > alt[-1][2]: alt[-1] = s
                    else:
                        if s[2] < alt[-1][2]: alt[-1] = s
                else:
                    alt.append(s)
                    
        flip_up = False
        flip_down = False
        target_peak = None
        target_trough = None
        
        if len(alt) >= 2:
            if alt[-1][1] == "TROUGH":
                target_peak = alt[-2]
                req = target_peak[2] * (1.0 + thresh)
                if c > req:
                    flip_up = True
            elif alt[-1][1] == "PEAK":
                target_trough = alt[-2]
                req = target_trough[2] * (1.0 - thresh)
                if c < req:
                    flip_down = True
                    
        if flip_up and not flip_down:
            trend_state = "BULLISH"
        elif flip_down and not flip_up:
            trend_state = "BEARISH"
            
        state_history.append((t, trend_state, alt[-3:] if len(alt) >= 3 else alt, c))
        
    return state_history

def get_trend_at_time(sym: str, tf: str, asof_time: pd.Timestamp, candidate: str):
    df = cache.load_candles(sym, tf)
    if df.empty:
        return "NONE", []
        
    # We evaluate trend based on candles closed strictly ON OR BEFORE asof_time
    if candidate == "a":
        breaks = compute_trend_a(df)
        priors = [b for b in breaks if b[0] <= asof_time]
        return (priors[-1][1] if priors else "NONE"), []
    else:
        use_m = (candidate == "c")
        hist = compute_trend_b_c(df, use_m, sym)
        priors = [h for h in hist if h[0] <= asof_time]
        if priors:
            return priors[-1][1], priors[-1][2]
        return "NONE", []

def main():
    moments = [
        ("(i) Before Rejection Candle", "before_rej"),
        ("(ii) After Rejection Candle Closed", "after_rej"),
        ("(iii) At Alert Time", "alert_time"),
    ]
    candidates = [
        ("Candidate (a): Last broken A/V shape", "a"),
        ("Candidate (b): Protected swing rule", "b"),
        ("Candidate (c): Protected swing with MARGINAL threshold", "c"),
    ]
    
    summary_results = []
    
    print("=" * 80)
    print("TREND EVALUATION ACROSS THREE MOMENTS (35 REFERENCE SETUPS)")
    print("=" * 80)
    
    for m_label, m_key in moments:
        print(f"\n--- MOMENT: {m_label} ---")
        for c_label, c_key in candidates:
            aligned = 0
            against = 0
            none_cnt = 0
            mismatches = []
            
            for idx, r in ref_df.iterrows():
                sym = r["symbol"]
                rdir = r["dir"]
                exp_trend = "BULLISH" if rdir == "BUY" else "BEARISH"
                pair = r["pair"]
                tf = "W" if "W1" in pair else "D"
                
                t_rej = pd.Timestamp(r["rej_open_utc"], tz="UTC")
                t_bo = pd.Timestamp(r["bo_open_utc"], tz="UTC")
                
                # Determine asof timestamp for this moment
                if m_key == "before_rej":
                    # candle closed immediately before rejection candle
                    asof = t_rej - pd.Timedelta(seconds=1)
                elif m_key == "after_rej":
                    # rejection candle is closed
                    df_tf = cache.load_candles(sym, tf)
                    # find index of rejection candle
                    locs = df_tf.index.get_indexer([t_rej], method="nearest")
                    loc = locs[0]
                    asof = df_tf.index[loc]  # close of that candle is timestamped at open of bar in daily series
                elif m_key == "alert_time":
                    # at alert time, use D1 candles closed up to t_bo
                    df_tf = cache.load_candles(sym, tf)
                    prior_d = df_tf[df_tf.index < t_bo]
                    asof = prior_d.index[-1] if not prior_d.empty else t_bo
                    
                trend, debug = get_trend_at_time(sym, tf, asof, c_key)
                if trend == exp_trend:
                    aligned += 1
                elif trend == "NONE":
                    none_cnt += 1
                    mismatches.append((r["id"], sym, rdir, trend))
                else:
                    against += 1
                    mismatches.append((r["id"], sym, rdir, trend))
                    
            pct = aligned / len(ref_df) * 100.0
            print(f"  {c_label:<55}: Aligned={aligned:>2}/35 ({pct:>5.1f}%), Against={against:>2}, None={none_cnt:>2}")
            summary_results.append({
                "moment": m_label,
                "candidate": c_label,
                "aligned": aligned,
                "against": against,
                "none": none_cnt,
                "mismatches": mismatches,
            })
            
    # Detailed diagnosis for #10 AUDJPY, #28 GER40, #32 SPX500
    target_ids = [10, 28, 32]
    print("\n" + "=" * 80)
    print("DETAILED SWING POINTS & CLOSES FOR TARGET MISMATCH SETUPS:")
    print("=" * 80)
    for tid in target_ids:
        r = ref_df[ref_df["id"] == tid].iloc[0]
        sym = r["symbol"]
        rdir = r["dir"]
        pair = r["pair"]
        tf = "W" if "W1" in pair else "D"
        t_rej = pd.Timestamp(r["rej_open_utc"], tz="UTC")
        t_bo = pd.Timestamp(r["bo_open_utc"], tz="UTC")
        
        print(f"\nSETUP #{tid}: {sym} {rdir} (Rejection: {t_rej}, BO: {t_bo})")
        df_tf = cache.load_candles(sym, tf)
        # Print Daily candles surrounding rejection and alert
        sub = df_tf[(df_tf.index >= t_rej - pd.Timedelta(days=7)) & (df_tf.index <= t_bo + pd.Timedelta(days=1))]
        print("  D1 Candles (Open, High, Low, Close):")
        for t, row in sub.iterrows():
            print(f"    {t.strftime('%Y-%m-%d %H:%M')}: O={row['open']:.5f}, H={row['high']:.5f}, L={row['low']:.5f}, C={row['close']:.5f}")
            
        for m_label, m_key in moments:
            if m_key == "before_rej":
                asof = t_rej - pd.Timedelta(seconds=1)
            elif m_key == "after_rej":
                locs = df_tf.index.get_indexer([t_rej], method="nearest")
                asof = df_tf.index[locs[0]]
            elif m_key == "alert_time":
                prior_d = df_tf[df_tf.index < t_bo]
                asof = prior_d.index[-1] if not prior_d.empty else t_bo
                
            for c_code, c_name in [("b", "Cand (b)"), ("c", "Cand (c)")]:
                trend, swings = get_trend_at_time(sym, tf, asof, c_code)
                print(f"  [{m_label} | {c_name}]: Trend = {trend}")
                if swings:
                    swing_strs = [f"{s[1]}@{s[2]:.2f} (idx {s[0]})" for s in swings]
                    print(f"     Active Swing Points: {' -> '.join(swing_strs)}")

if __name__ == "__main__":
    main()
