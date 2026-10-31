import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.data.candles import apply_open_continuity
from slk_bot.strategy.levels import track_key_levels

cache = CandleCache()
sheet = pd.read_csv(ROOT / "analysis" / "output" / "review_sheet.csv")
ref = sheet[sheet["group"] == "reference"].copy()
ref_setups = pd.read_csv(ROOT / "reference_setups.csv")

results = []

for _, r in ref.iterrows():
    row_id = int(r["id"])
    sym = r["instrument"]
    rdir = r["direction"]
    kl_price = float(r["key_level_price"])
    kl_kind = r["key_level_kind"]
    tf = "W" if sym == "USOIL" and row_id == 15 else "D"
    
    r_orig = ref_setups[ref_setups["id"] == row_id].iloc[0]
    rej_time = pd.Timestamp(r_orig["rej_open_utc"], tz="UTC")
    bo_time = pd.Timestamp(r_orig["bo_open_utc"], tz="UTC")
    
    df = apply_open_continuity(cache.load_candles(sym, tf))
    
    # We want levels BEFORE the rejection candle
    priors_before = df[df.index < rej_time]
    lvls_before = track_key_levels(priors_before, tf, sym, lookback=None, max_disrespects=999, ocl_max_disrespects=999)
    
    # Match by exact formation string in review_sheet
    fc1_str = str(r["formation_candle_1_wat"])
    
    # Filter by price tolerance
    cands = [l for l in lvls_before if abs(l.price - kl_price) / kl_price < 0.005]
    
    matched_level = None
    for l in cands:
        t1_wat = l.formation_time + pd.Timedelta(hours=1)
        t1_sub = f"{t1_wat.strftime('%a')} {t1_wat.day} {t1_wat.strftime('%b')}"
        if t1_sub in fc1_str:
            matched_level = l
            break
            
    if matched_level is None and cands:
        # Sort by distance to price and recency
        cands.sort(key=lambda l: (abs(l.price - kl_price), -l.formation_time.timestamp()))
        matched_level = cands[0]
        
    l = matched_level
    form_time = l.formation_time
    age_days = (rej_time - form_time).total_seconds() / 86400.0
    age_months = age_days / 30.4375
    six_m_ago = rej_time - pd.DateOffset(months=6)
    older_6m = form_time < six_m_ago
    four_m_ago = rej_time - pd.DateOffset(months=4)
    older_4m = form_time < four_m_ago
    
    disp = l.disrespect_count
    fresh = l.fresh
    is_ocl = l.is_ocl or "OCL" in kl_kind
    
    # Exclusions:
    # Rule A: Unfresh levels do not count (must be fresh)
    excl_a = not fresh
    
    # Rule B: Disrespect caps:
    # A/V retired after > 3 disrespects (max_disrespects 3)
    # OCL retired after > 1 disrespect (ocl_max_disrespects 1)
    if is_ocl:
        excl_b = disp > 1
    else:
        excl_b = disp > 3
        
    # Rule C: Age rules for Daily setups (D1->H4) only:
    # no key level older than 6 months
    # level with exactly 3 disrespects used only if <= 4 months
    # Weekly setups (W1->D1) have no age limit
    excl_c = False
    if tf == "D":
        if older_6m:
            excl_c = True
        elif disp == 3 and older_4m:
            excl_c = True
            
    excl_all = excl_a or excl_b or excl_c
    
    results.append({
        "id": row_id,
        "symbol": sym,
        "tf": r_orig["pair"],
        "dir": rdir,
        "kind": kl_kind,
        "is_ocl": is_ocl,
        "price": kl_price,
        "matched_price": l.price,
        "formed": form_time.strftime("%Y-%m-%d"),
        "rej": rej_time.strftime("%Y-%m-%d"),
        "bo_time": bo_time.strftime("%Y-%m-%d %H:%M"),
        "weekday": bo_time.strftime("%a"),
        "disp": disp,
        "fresh": fresh,
        "age_days": round(age_days, 1),
        "age_months": round(age_months, 2),
        "excl_a": excl_a,
        "excl_b": excl_b,
        "excl_c": excl_c,
        "excl_all": excl_all,
    })

res_df = pd.DataFrame(results)
res_df.to_csv(ROOT / "analysis" / "output" / "owner_rules_ref_eval_correct.csv", index=False)

print("=" * 80)
print("CORRECT EVALUATION OF 35 REFERENCE SETUPS UNDER OWNER'S RULES")
print("=" * 80)

for rule, name in [
    ("excl_a", "Rule A (Unfresh levels do not count)"),
    ("excl_b", "Rule B (Disrespect caps: A/V max 3, OCL max 1)"),
    ("excl_c", "Rule C (Age rules on D1: max 6m, 3-disp max 4m)"),
    ("excl_all", "All Rules Together (A + B + C)"),
]:
    excl = res_df[res_df[rule]]
    kept = res_df[~res_df[rule]]
    print(f"\n{name}:")
    print(f"  Excluded count: {len(excl)}")
    print(f"  Excluded IDs: {excl['id'].tolist()}")
    print(f"  Kept count: {len(kept)}")
    print(f"  Kept IDs: {kept['id'].tolist()}")
    if len(excl) > 0:
        for _, rx in excl.iterrows():
            reasons = []
            if rx["excl_a"]: reasons.append("Unfresh")
            if rx["excl_b"]: reasons.append(f"Disrespect > cap (disp={rx['disp']}, is_ocl={rx['is_ocl']})")
            if rx["excl_c"]: reasons.append(f"Age (age={rx['age_months']}m, disp={rx['disp']})")
            print(f"    - ID {rx['id']:2d} ({rx['symbol']} {rx['dir']} {rx['kind']}): {', '.join(reasons)}")

print("\n" + "=" * 80)
print("KEPT SETUPS DETAILS")
print("=" * 80)
kept_all = res_df[~res_df["excl_all"]]
print(kept_all[["id", "symbol", "dir", "tf", "kind", "disp", "fresh", "age_months", "weekday", "bo_time"]].to_string())

print("\nBreakout Weekdays for Kept Setups:")
print(kept_all["weekday"].value_counts())
