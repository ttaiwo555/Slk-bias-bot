import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pandas as pd

from analysis.parse_reference import parse_reference_file

res_file = ROOT / "analysis" / "reproduce_results.csv"
df = pd.read_csv(res_file)

print("PARTIAL ALERTS:")
partials = df[df["match_type"] == "PARTIAL"]
for idx, r in partials.iterrows():
    print(f"Ref #{r['ref_idx']+1}: {r['symbol']} {r['direction']} {r['pair']} Rej:{r['ref_rej']} KL:{r['ref_kl']} BO:{r['ref_bo_price']} BO_time:{r['ref_bo_time']}")
    print(f"   Diagnosis: {r['diagnosis']}\n")

print("\nMISSING ALERTS:")
missings = df[df["match_type"] == "MISSING"]
for idx, r in missings.iterrows():
    print(f"Ref #{r['ref_idx']+1}: {r['symbol']} {r['direction']} {r['pair']} Rej:{r['ref_rej']} KL:{r['ref_kl']} BO:{r['ref_bo_price']} BO_time:{r['ref_bo_time']}")
    print(f"   Diagnosis: {r['diagnosis']}\n")
