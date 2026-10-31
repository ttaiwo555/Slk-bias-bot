import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
df_match = pd.read_csv(ROOT / "analysis" / "output" / "match_table.csv")
df_ref = pd.read_csv(ROOT / "reference_setups.csv")

non_matches = df_match[df_match["status"] != "MATCH"].copy()
print(f"Total non-matching setups: {len(non_matches)} (out of {len(df_match)} total)\n")

for _, r in non_matches.iterrows():
    rid = int(r["id"])
    ref_row = df_ref[df_ref["id"] == rid].iloc[0]
    sym = r["symbol"]
    st = r["status"]
    r_dir = ref_row["dir"]
    r_pair = ref_row["pair"]
    r_rej = ref_row["rej_open_utc"]
    r_kl = ref_row["kl_price"]
    r_kind = ref_row["kl_kind"]
    r_bo = ref_row["bo_price"]
    r_bot = ref_row["bo_open_utc"]
    o_kl = r["our_level_price"] if pd.notna(r["our_level_price"]) else "-"
    o_kind = r["key_kind_ours"] if pd.notna(r["key_kind_ours"]) else "-"
    o_bo = r["our_bo_price"] if pd.notna(r["our_bo_price"]) else "-"
    o_bot = r["our_bo_time"] if pd.notna(r["our_bo_time"]) else "-"
    diag = r["diagnosis"]
    print(f"#{rid:>2} {sym:<7} [{st}]")
    print(f"    Reference : {r_dir} {r_pair} | Rej: {r_rej} | KL: {r_kl} ({r_kind}) | BO: {r_bo} @ {r_bot}")
    print(f"    Our Engine: KL: {o_kl} ({o_kind}) | BO: {o_bo} @ {o_bot}")
    print(f"    Diagnosis : {diag}\n")
