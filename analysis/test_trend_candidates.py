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

# Precompute structure breaks per instrument and timeframe
_TREND_CACHE = {}

def compute_structure_breaks(df: pd.DataFrame):
    """Compute structure breaks chronologically."""
    opens = df["open"].values
    closes = df["close"].values
    times = df.index
    n = len(df)

    # Detect all confirmed A and V shapes
    # (conf_idx, kind, price, form_time)
    levels = []
    for k in range(1, n):
        formation = detect_formation(opens[k - 1], closes[k - 1], opens[k], closes[k], 0.0)
        if formation:
            kind, role, side = formation
            if kind in (LevelKind.A_SHAPE, LevelKind.V_SHAPE):
                levels.append((k, kind, float(closes[k - 1])))

    # For each candle k, track breaks
    breaks = [] # list of (break_time, TriggerDirection)
    for k in range(1, n):
        c_close = closes[k]
        t = times[k]
        # Check if any prior confirmed level was broken on this candle
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
    # Find most recent break on or before asof
    prior_breaks = [b for b in breaks if b[0] <= asof]
    if prior_breaks:
        return "BULLISH" if prior_breaks[-1][1] == TriggerDirection.BULLISH else "BEARISH"
    return "NONE"

def get_trend_b(sym: str, tf: str, asof: pd.Timestamp) -> str:
    df = cache.load_candles(sym, tf)
    sub = df[df.index <= asof]
    if len(sub) < 6:
        return "NONE"
    opens = sub["open"].values
    closes = sub["close"].values
    n = len(sub)
    a_shapes = []
    v_shapes = []
    for k in range(1, n):
        formation = detect_formation(opens[k - 1], closes[k - 1], opens[k], closes[k], 0.0)
        if formation:
            kind, role, side = formation
            if kind == LevelKind.A_SHAPE:
                a_shapes.append(float(closes[k - 1]))
            elif kind == LevelKind.V_SHAPE:
                v_shapes.append(float(closes[k - 1]))
    if len(a_shapes) >= 2 and len(v_shapes) >= 2:
        if a_shapes[-1] > a_shapes[-2] and v_shapes[-1] > v_shapes[-2]:
            return "BULLISH"
        elif a_shapes[-1] < a_shapes[-2] and v_shapes[-1] < v_shapes[-2]:
            return "BEARISH"
        elif a_shapes[-1] > a_shapes[-2]:
            return "BULLISH"
        elif v_shapes[-1] < v_shapes[-2]:
            return "BEARISH"
    return "NONE"

def get_trend_c(sym: str, tf: str, asof: pd.Timestamp) -> str:
    df = cache.load_candles(sym, tf)
    sub = df[df.index <= asof]
    if len(sub) < 50:
        return "NONE"
    sma50 = sub["close"].rolling(50).mean().iloc[-1]
    return "BULLISH" if sub["close"].iloc[-1] > sma50 else "BEARISH"

def get_trend_d(sym: str, tf: str, asof: pd.Timestamp) -> str:
    df = cache.load_candles(sym, tf)
    sub = df[df.index <= asof]
    if len(sub) < 50:
        return "NONE"
    sma20 = sub["close"].rolling(20).mean().iloc[-1]
    sma50 = sub["close"].rolling(50).mean().iloc[-1]
    return "BULLISH" if sma20 > sma50 else "BEARISH"

def main():
    print(f"Testing 4 trend candidates across {len(ref_in_scope)} in-scope reference alerts...")

    rules = [
        ("Candidate (a): Last Market-Structure Break (broken A/V)", get_trend_a),
        ("Candidate (b): Swings Sequence (HH/HL vs LH/LL)", get_trend_b),
        ("Candidate (c): Close vs 50-period MA", get_trend_c),
        ("Candidate (d): 20 MA vs 50 MA", get_trend_d),
    ]

    for name, fn in rules:
        matched = 0
        total = 0
        mismatches = []
        for ref in ref_in_scope:
            asof = ref.rejection_candle_open_utc
            t_dir = fn(ref.symbol, ref.htf, asof)
            total += 1
            if (ref.direction == "BUY" and t_dir == "BULLISH") or (ref.direction == "SELL" and t_dir == "BEARISH"):
                matched += 1
            else:
                mismatches.append((ref.symbol, ref.direction, t_dir, str(asof)[:10]))

        pct = matched / total * 100
        print(f"\n{name}:")
        print(f"  Reference Alerts Aligned: {matched}/{total} ({pct:.1f}%)")
        if mismatches:
            print(f"  Mismatches ({len(mismatches)}): {mismatches[:6]}")

if __name__ == "__main__":
    main()
