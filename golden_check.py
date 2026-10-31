import sys
import pandas as pd
from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.levels import track_key_levels

sys.stdout.reconfigure(line_buffering=True)
cache = CandleCache()

def check_pair(name: str, tf: str, asof_date: str, target_price: float, tol: float = 0.002):
    df = cache.load_candles(name, tf)
    # Parse asof cutoff: 
    # In forex, Friday session ends at 21:00 UTC (17:00 NY).
    # Any candle whose close_time <= cutoff is closed before that date.
    cutoff = pd.Timestamp(f"{asof_date} 21:00:00", tz="UTC")
    
    dur = pd.Timedelta(days=1) if tf == "D" else (pd.Timedelta(hours=4) if tf == "H4" else pd.Timedelta(days=7))
    closed = df[df.index + dur <= cutoff]
    
    lookback = 250 if tf == "D" else (300 if tf == "H4" else 104)
    candles = closed.tail(lookback)
    levels = track_key_levels(candles, timeframe=tf, symbol=name, lookback=lookback)
    
    print(f"\n=======================================================", flush=True)
    print(f" {name} {tf} as of {asof_date} (Target: {target_price})", flush=True)
    print(f" Lookback bars: {len(candles)} | Total active levels: {len(levels)}", flush=True)
    print(f"=======================================================", flush=True)
    
    matched = [lvl for lvl in levels if abs(lvl.price - target_price) / target_price <= tol]
    if matched:
        for lvl in matched:
            diff_pct = abs(lvl.price - target_price) / target_price * 100
            print(
                f"  MATCH: Kind={lvl.kind.value:<12} | Price={lvl.price:.5f} ({diff_pct:.3f}% diff) | "
                f"Role={lvl.current_role.value:<11} | Disrespect={lvl.disrespect_count:<2} | "
                f"Formed={lvl.formation_time.strftime('%Y-%m-%d %H:%M UTC')}",
                flush=True,
            )
    else:
        print(f"  No level within 0.20% found. Closest 3 levels in lookback:", flush=True)
        closest = sorted(levels, key=lambda l: abs(l.price - target_price))[:3]
        for lvl in closest:
            diff_pct = abs(lvl.price - target_price) / target_price * 100
            print(
                f"    Price={lvl.price:.5f} ({diff_pct:.3f}% diff) | Kind={lvl.kind.value:<12} | "
                f"Role={lvl.current_role.value:<11} | Disrespect={lvl.disrespect_count:<2} | "
                f"Formed={lvl.formation_time.strftime('%Y-%m-%d %H:%M UTC')}",
                flush=True,
            )

check_pair("NZDUSD", "D", "2026-06-12", 0.5861)
check_pair("NZDUSD", "H4", "2026-06-12", 0.5819)
check_pair("NZDJPY", "D", "2026-06-05", 92.83)
check_pair("NZDJPY", "H4", "2026-06-05", 93.92)
check_pair("NZDJPY", "H4", "2026-06-05", 93.13)
