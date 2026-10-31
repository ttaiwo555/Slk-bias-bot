import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pandas as pd
import numpy as np

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.levels import detect_formation
from slk_bot.strategy.models import TriggerDirection, LevelKind
from analysis.parse_reference import parse_reference_file

cache = CandleCache()
ref_alerts = parse_reference_file(ROOT / "reference_alerts.txt")
ignored = {"EU50", "JPN225", "DOTUSDT"}
ref_in_scope = [a for a in ref_alerts if a.symbol not in ignored]

_TREND_CACHE = {}

def compute_structure_breaks(df: pd.DataFrame):
    opens = df["open"].values
    closes = df["close"].values
    times = df.index
    n = len(df)

    levels = []
    for k in range(1, n):
        formation = detect_formation(opens[k - 1], closes[k - 1], opens[k], closes[k], 0.0)
        if formation:
            kind, role, side = formation
            if kind in (LevelKind.A_SHAPE, LevelKind.V_SHAPE):
                levels.append((k, kind, float(closes[k - 1])))

    breaks = []
    for k in range(1, n):
        c_close = closes[k]
        t = times[k]
        for conf_k, kind, price in levels:
            if conf_k < k:
                if kind == LevelKind.A_SHAPE and c_close > price:
                    breaks.append((t, TriggerDirection.BULLISH))
                elif kind == LevelKind.V_SHAPE and c_close < price:
                    breaks.append((t, TriggerDirection.BEARISH))
    return breaks

def get_trend_a(sym: str, tf: str, asof: pd.Timestamp) -> str:
    key = (sym, tf)
    if key not in _TREND_CACHE:
        df = cache.load_candles(sym, tf)
        if df.empty:
            _TREND_CACHE[key] = []
        else:
            _TREND_CACHE[key] = compute_structure_breaks(df)

    breaks = _TREND_CACHE[key]
    prior_breaks = [b for b in breaks if b[0] <= asof]
    if prior_breaks:
        return "BULLISH" if prior_breaks[-1][1] == TriggerDirection.BULLISH else "BEARISH"
    return "NONE"

def main():
    print(f"Comparing timing of trend evaluation across {len(ref_in_scope)} reference alerts:")

    for timing in ["rejection", "breakout"]:
        matched = 0
        total = 0
        mismatches = []
        for ref in ref_in_scope:
            asof = ref.rejection_candle_open_utc if timing == "rejection" else ref.breakout_candle_open_utc
            t_dir = get_trend_a(ref.symbol, ref.htf, asof)
            total += 1
            if (ref.direction == "BUY" and t_dir == "BULLISH") or (ref.direction == "SELL" and t_dir == "BEARISH"):
                matched += 1
            else:
                mismatches.append((ref.symbol, ref.direction, t_dir, str(asof)[:16]))

        pct = matched / total * 100
        print(f"\nRule (a) evaluated at {timing.upper()}:")
        print(f"  Reference Alerts Aligned: {matched}/{total} ({pct:.1f}%)")
        print(f"  Mismatches ({len(mismatches)}):")
        for m in mismatches:
            print(f"    {m[0]:7s} ref={m[1]} trend={m[2]} at {m[3]}")

if __name__ == "__main__":
    main()
