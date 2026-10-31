"""
analysis/test_crypto_continuity_item10.py
-----------------------------------------
Item 10: Test open_continuity on BTCUSD and ETHUSD (NY17 grid)
and report whether any reference row changes (#3, #35).
Information only.

Open continuity rule:
  open(i) = close(i-1) on every timeframe (D, W, H4),
  high(i) = max(high(i), open(i)),
  low(i) = min(low(i), open(i)).
Applied BEFORE detecting levels, sweeps and trend.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

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

cache = CandleCache()
ref_df = pd.read_csv(ROOT / "reference_setups.csv")

def apply_open_continuity(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or len(df) < 2:
        return df.copy()
    df_new = df.copy()
    opens = df_new["open"].to_numpy(copy=True)
    highs = df_new["high"].to_numpy(copy=True)
    lows = df_new["low"].to_numpy(copy=True)
    closes = df_new["close"].to_numpy(copy=True)
    
    for i in range(1, len(df_new)):
        new_open = closes[i - 1]
        opens[i] = new_open
        if highs[i] < new_open:
            highs[i] = new_open
        if lows[i] > new_open:
            lows[i] = new_open
            
    df_new["open"] = opens
    df_new["high"] = highs
    df_new["low"] = lows
    return df_new

def evaluate_crypto_pair(sym: str, ref_id: int, continuity: bool):
    # Load candles
    df_d = cache.load_candles(sym, "D")
    df_h4 = cache.load_candles(sym, "H4")
    
    if continuity:
        df_d = apply_open_continuity(df_d)
        df_h4 = apply_open_continuity(df_h4)
        
    trigs = detect_triggers(
        df_d, "D", sym, "crypto",
        lookback=None, max_disrespects=None, ocl_max_disrespects=None,
        primary_selection_rule="fresh_first",
    )
    
    r_row = ref_df[ref_df["id"] == ref_id].iloc[0]
    t_rej = pd.Timestamp(r_row["rej_open_utc"], tz="UTC")
    t_bo = pd.Timestamp(r_row["bo_open_utc"], tz="UTC")
    exp_dir = TriggerDirection.BEARISH if r_row["dir"] == "SELL" else TriggerDirection.BULLISH
    
    cands = [t for t in trigs if t.trigger_candle_time == t_rej and t.direction == exp_dir]
    if not cands:
        return "MISSING", None, None, None
        
    t = cands[0]
    h4_levels = detect_ltf_levels(df_h4, sym, "H4", 0.0)
    s = evaluate_setup_lifecycle(
        t, df_d, df_h4, df_d, sym, "D", "H4",
        unfresh_alerts=True, all_ltf_levels=h4_levels,
        external_ref_point="first_touch",
    )
    
    kl = t.primary_level
    kl_str = f"{kl.kind.value} @ {kl.price:.2f}" if kl else "None"
    
    ext_alerts = [a for a in s.alerts if a.breakout_type == BreakoutType.EXTERNAL]
    if not ext_alerts:
        return "PARTIAL", kl_str, None, None
        
    bo_a = ext_alerts[0]
    return "MATCH/PARTIAL", kl_str, bo_a.broken_level.price, bo_a.breakout_candle_time

def main():
    print("=" * 80)
    print("ITEM 10: CRYPTO CONTINUITY TEST ON BTCUSD AND ETHUSD (NY17 GRID)")
    print("=" * 80)
    
    cases = [
        ("BTCUSD", 3, "Row #3: BTCUSD (Ref: KL 84012.32, BO 82881.23 @ 2026-10-12T09:00)"),
        ("ETHUSDT", 35, "Row #35: ETHUSDT (Ref: KL 2490.16, BO 2440.22 @ 2026-10-16T12:00)"),
    ]
    
    for sym, rid, label in cases:
        print(f"\n{label}")
        # Baseline (No continuity)
        st_base, kl_base, bo_p_base, bo_t_base = evaluate_crypto_pair(sym, rid, continuity=False)
        # With Continuity
        st_cont, kl_cont, bo_p_cont, bo_t_cont = evaluate_crypto_pair(sym, rid, continuity=True)
        
        print(f"  Baseline (continuity OFF): KL = {kl_base} | BO = {bo_p_base} @ {bo_t_base} | Status = {st_base}")
        print(f"  Test     (continuity ON ): KL = {kl_cont} | BO = {bo_p_cont} @ {bo_t_cont} | Status = {st_cont}")
        changed = (kl_base != kl_cont or bo_p_base != bo_p_cont or bo_t_base != bo_t_cont)
        print(f"  Did reference row change? {'YES' if changed else 'NO'}")

if __name__ == "__main__":
    main()
