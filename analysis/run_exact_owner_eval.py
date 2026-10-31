import sys
from pathlib import Path
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from slk_bot.storage.cache import CandleCache
from slk_bot.data.candles import apply_open_continuity
from slk_bot.strategy.levels import track_key_levels

cache = CandleCache()
sheet = pd.read_csv(ROOT / "analysis" / "output" / "review_sheet.csv")
ref = sheet[sheet["group"] == "reference"].copy()

results = []

for _, r in ref.iterrows():
    row_id = int(r["id"])
    sym = r["instrument"]
    rdir = r["direction"]
    kl_price = float(r["key_level_price"])
    kl_kind = r["key_level_kind"]
    tf = "W" if sym == "USOIL" and row_id == 15 else "D"
    
    # Load HTF candles
    df = apply_open_continuity(cache.load_candles(sym, tf))
    
    # Track levels as of the rejection candle
    # Let's find rejection candle timestamp
    # Rejection candle is the one before/at htf_candle_open_wat
    # In reference_setups.csv:
    # let's match row_id
    ref_setups = pd.read_csv(ROOT / "reference_setups.csv")
    r_orig = ref_setups[ref_setups["id"] == row_id].iloc[0]
    rej_time = pd.Timestamp(r_orig["rej_open_utc"], tz="UTC")
    bo_time = pd.Timestamp(r_orig["bo_open_utc"], tz="UTC")
    
    priors = df[df.index <= rej_time]
    
    # Track levels up to rejection candle
    lvls = track_key_levels(priors, tf, sym, lookback=None, max_disrespects=999, ocl_max_disrespects=999)
    
    # We want the level that matches the formation date in review_sheet
    # review_sheet has formation_candle_1_wat e.g. 'Mon 5 Oct 22:00 (bullish)'
    fc1_str = str(r["formation_candle_1_wat"])
    
    matched_level = None
    for l in lvls:
        if abs(l.price - kl_price) / kl_price < 0.005:
            # Check formation string
            t1_wat = l.formation_time + pd.Timedelta(hours=1)
            t1_sub = f"{t1_wat.strftime('%a')} {t1_wat.day} {t1_wat.strftime('%b')}"
            if t1_sub in fc1_str:
                matched_level = l
                break
    
    if matched_level is None:
        # Fallback to nearest
        cand = [l for l in lvls if abs(l.price - kl_price) / kl_price < 0.005]
        if cand:
            cand.sort(key=lambda l: abs(l.price - kl_price))
            matched_level = cand[0]
            
    if matched_level:
        l = matched_level
        form_time = l.formation_time
        age_days = (rej_time - form_time).total_seconds() / 86400.0
        age_months = age_days / 30.4375
        
        # Exact month checks
        six_m_ago = rej_time - pd.DateOffset(months=6)
        older_6m = form_time < six_m_ago
        four_m_ago = rej_time - pd.DateOffset(months=4)
        older_4m = form_time < four_m_ago
        
        is_ocl = l.is_ocl or "OCL" in kl_kind
        disp = l.disrespect_count
        fresh = l.fresh
        
        # Check rule exclusions:
        # Rule A: Unfresh levels do not count
        excl_a = not fresh
        
        # Rule B: Disrespect caps:
        # A-shape and V-shape retired after > 3 disrespects
        # OCL retired after > 1 disrespect
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
            "sym": sym,
            "tf": r_orig["pair"],
            "dir": rdir,
            "kind": kl_kind,
            "is_ocl": is_ocl,
            "price": kl_price,
            "formed": form_time.strftime("%Y-%m-%d"),
            "rej": rej_time.strftime("%Y-%m-%d"),
            "bo_open_utc": r_orig["bo_open_utc"],
            "age_days": round(age_days, 1),
            "age_months": round(age_months, 2),
            "disp": disp,
            "fresh": fresh,
            "excl_a": excl_a,
            "excl_b": excl_b,
            "excl_c": excl_c,
            "excl_all": excl_all,
        })
    else:
        results.append({
            "id": row_id,
            "sym": sym,
            "tf": r_orig["pair"],
            "dir": rdir,
            "kind": kl_kind,
            "is_ocl": None,
            "price": kl_price,
            "formed": None,
            "rej": rej_time.strftime("%Y-%m-%d"),
            "bo_open_utc": r_orig["bo_open_utc"],
            "age_days": None,
            "age_months": None,
            "disp": None,
            "fresh": None,
            "excl_a": True,
            "excl_b": True,
            "excl_c": True,
            "excl_all": True,
        })

res_df = pd.DataFrame(results)
res_df.to_csv(ROOT / "analysis" / "output" / "exact_ref_eval_owner_rules.csv", index=False)

print("=" * 80)
print("EXACT REFERENCE SETUPS EVALUATION UNDER OWNER'S RULES")
print("=" * 80)
print(f"Total Reference Setups: {len(res_df)}")

for rule, name in [("excl_a", "Rule A (Unfresh Excluded)"),
                   ("excl_b", "Rule B (Disrespect Caps: A/V > 3, OCL > 1)"),
                   ("excl_c", "Rule C (Age Rules: D1 > 6m or (disp=3 and > 4m))"),
                   ("excl_all", "All Rules Together (A + B + C)")]:
    excl = res_df[res_df[rule]]
    kept = res_df[~res_df[rule]]
    print(f"\n{name}:")
    print(f"  Excluded count: {len(excl)}")
    print(f"  Excluded IDs: {excl['id'].tolist()}")
    print(f"  Kept count: {len(kept)}")
    print(f"  Kept IDs: {kept['id'].tolist()}")
    if len(excl) > 0:
        for _, rx in excl.iterrows():
            print(f"    - ID {rx['id']:2d} ({rx['sym']} {rx['dir']} {rx['kind']}): disp={rx['disp']}, age={rx['age_months']}m, fresh={rx['fresh']}")
