import sys
from pathlib import Path
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.levels import track_key_levels

cache = CandleCache()
ref_df = pd.read_csv(ROOT / "reference_setups.csv")

results = []

for idx, r in ref_df.iterrows():
    row_id = r["id"]
    sym = r["symbol"]
    rdir = r["dir"]
    tf_str = r["pair"]
    htf = "W" if "W1" in tf_str else "D"
    ltf = "D" if "W1" in tf_str else "H4"
    kl_price = float(r["kl_price"])
    rej_time = pd.Timestamp(r["rej_open_utc"], tz="UTC")
    bo_time = pd.Timestamp(r["bo_open_utc"], tz="UTC")
    
    # Load candles up to rejection time
    df_htf = cache.load_candles(sym, htf)
    priors = df_htf[df_htf.index <= rej_time]
    
    # Track levels with uncapped disrespects so we see the true disrespect count
    lvls = track_key_levels(priors, htf, sym, lookback=None, max_disrespects=999, ocl_max_disrespects=999)
    
    # Find matching key level by price tolerance
    matched = [l for l in lvls if abs(l.price - kl_price) / kl_price < 0.0015]
    if not matched:
        # Try wider tolerance or nearest
        matched = sorted(lvls, key=lambda l: abs(l.price - kl_price))[:1]
    
    kl = matched[0] if matched else None
    
    if kl:
        # Age in calendar days and months
        age_days = (rej_time - kl.formation_time).total_seconds() / 86400.0
        age_months = age_days / 30.4375  # approximate average month length
        # Exact month offset check:
        # is older than 6 months?
        six_m_ago = rej_time - pd.DateOffset(months=6)
        older_than_6m = kl.formation_time < six_m_ago
        four_m_ago = rej_time - pd.DateOffset(months=4)
        older_than_4m = kl.formation_time < four_m_ago
        
        results.append({
            "id": row_id,
            "symbol": sym,
            "tf": tf_str,
            "htf": htf,
            "dir": rdir,
            "ref_price": kl_price,
            "engine_price": kl.price,
            "kind": kl.kind.value,
            "disrespect_count": kl.disrespect_count,
            "fresh": kl.fresh,
            "rejections": getattr(kl, "rejections_since_fresh", 0),
            "formation_time": kl.formation_time,
            "rej_time": rej_time,
            "bo_time": bo_time,
            "age_days": round(age_days, 1),
            "age_months": round(age_months, 2),
            "older_than_6m": older_than_6m,
            "older_than_4m": older_than_4m,
        })
    else:
        results.append({
            "id": row_id,
            "symbol": sym,
            "tf": tf_str,
            "htf": htf,
            "dir": rdir,
            "ref_price": kl_price,
            "engine_price": None,
            "kind": None,
            "disrespect_count": None,
            "fresh": None,
            "rejections": None,
            "formation_time": None,
            "rej_time": rej_time,
            "bo_time": bo_time,
            "age_days": None,
            "age_months": None,
            "older_than_6m": None,
            "older_than_4m": None,
        })

res_df = pd.DataFrame(results)
print(res_df.to_string())
res_df.to_csv(ROOT / "analysis" / "output" / "ref_levels_detail.csv", index=False)
