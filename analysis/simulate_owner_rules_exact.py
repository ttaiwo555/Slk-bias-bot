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
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import evaluate_setup_lifecycle, detect_ltf_levels, BreakoutType
from slk_bot.strategy.models import TriggerDirection, TriggerMethod

cache = CandleCache()
ref_df = pd.read_csv(ROOT / "reference_setups.csv")

# Let's inspect each reference setup under:
# Uncapped (reference_parity)
# vs
# Rule A alone (Unfresh levels excluded)
# Rule B alone (Disrespect caps: A/V max 3, OCL max 1)
# Rule C alone (Age rules: D1 only: > 6 months excluded; == 3 disrespects and > 4 months excluded)
# All together (A + B + C)

# For each reference setup, let's find the exact key level used under reference_parity
ref_details = []

for idx, r in ref_df.iterrows():
    row_id = int(r["id"])
    sym = r["symbol"]
    rdir = r["dir"]
    tf_str = r["pair"]
    htf = "W" if "W1" in tf_str else "D"
    ltf = "D" if "W1" in tf_str else "H4"
    kl_price = float(r["kl_price"])
    rej_time = pd.Timestamp(r["rej_open_utc"], tz="UTC")
    bo_time = pd.Timestamp(r["bo_open_utc"], tz="UTC")
    
    df_htf = cache.load_candles(sym, htf)
    priors = df_htf[df_htf.index <= rej_time]
    
    # 1. Uncapped tracking (reference parity)
    lvls_uncapped = track_key_levels(priors, htf, sym, lookback=None, max_disrespects=999, ocl_max_disrespects=999)
    matched = [l for l in lvls_uncapped if abs(l.price - kl_price) / kl_price < 0.002]
    if not matched:
        matched = sorted(lvls_uncapped, key=lambda l: abs(l.price - kl_price))[:1]
    
    kl = matched[0]
    kind_val = kl.kind.value
    is_ocl = kl.is_ocl
    disp = kl.disrespect_count
    fresh = kl.fresh
    
    # Age calculation at rejection candle
    age_days = (rej_time - kl.formation_time).total_seconds() / 86400.0
    age_months = age_days / 30.4375
    six_m_ago = rej_time - pd.DateOffset(months=6)
    four_m_ago = rej_time - pd.DateOffset(months=4)
    older_6m = kl.formation_time < six_m_ago
    older_4m = kl.formation_time < four_m_ago
    
    # Exclusions:
    # Rule A: Unfresh levels do not count
    excl_a = not fresh
    
    # Rule B: Disrespect caps: A/V > 3, OCL > 1
    if is_ocl:
        excl_b = disp > 1
    else:
        excl_b = disp > 3
        
    # Rule C: Age rules for D1 only (W1 has no age limit)
    excl_c = False
    if htf == "D":
        if older_6m:
            excl_c = True
        elif disp == 3 and older_4m:
            excl_c = True
            
    excl_all = excl_a or excl_b or excl_c
    
    ref_details.append({
        "id": row_id,
        "symbol": sym,
        "tf": tf_str,
        "htf": htf,
        "dir": rdir,
        "kl_price": kl_price,
        "matched_price": kl.price,
        "kind": kind_val,
        "is_ocl": is_ocl,
        "disp": disp,
        "fresh": fresh,
        "formed": kl.formation_time.strftime("%Y-%m-%d"),
        "rej": rej_time.strftime("%Y-%m-%d"),
        "age_days": round(age_days, 1),
        "age_months": round(age_months, 2),
        "excl_a": excl_a,
        "excl_b": excl_b,
        "excl_c": excl_c,
        "excl_all": excl_all,
    })

df_eval = pd.DataFrame(ref_details)
df_eval.to_csv(ROOT / "analysis" / "output" / "owner_rules_ref_eval.csv", index=False)

print("=" * 80)
print("EVALUATION OF 35 REFERENCE SETUPS UNDER OWNER RULES")
print("=" * 80)

excl_a_ids = df_eval[df_eval["excl_a"]]["id"].tolist()
excl_b_ids = df_eval[df_eval["excl_b"]]["id"].tolist()
excl_c_ids = df_eval[df_eval["excl_c"]]["id"].tolist()
excl_all_ids = df_eval[df_eval["excl_all"]]["id"].tolist()

print(f"Total reference setups: {len(df_eval)}")
print(f"Rule A (Unfresh excluded): {len(excl_a_ids)} excluded -> IDs: {excl_a_ids}")
print(f"Rule B (Disrespect caps): {len(excl_b_ids)} excluded -> IDs: {excl_b_ids}")
print(f"Rule C (Age rules on D1): {len(excl_c_ids)} excluded -> IDs: {excl_c_ids}")
print(f"All Rules Together: {len(excl_all_ids)} excluded -> IDs: {excl_all_ids}")
print(f"Remaining Reference Setups: {len(df_eval) - len(excl_all_ids)}")

print("\nDetailed excluded rows:")
for r_id in excl_all_ids:
    row = df_eval[df_eval["id"] == r_id].iloc[0]
    reasons = []
    if row["excl_a"]: reasons.append("Unfresh")
    if row["excl_b"]: reasons.append(f"Disrespect > cap (disp={row['disp']}, is_ocl={row['is_ocl']})")
    if row["excl_c"]: reasons.append(f"Age rule (age={row['age_months']}m, disp={row['disp']})")
    print(f"Row {r_id:2d} ({row['symbol']} {row['dir']} {row['tf']}): {', '.join(reasons)}")
