"""
analysis/match_live.py
----------------------
Compares alerts_log.csv against reference CSV for any date range using the SLK
parity match rules. Prints MATCH / PARTIAL / MISSING per reference row, summary
status counters, and lists extra setups.

Usage:
  python analysis/match_live.py [--alerts alerts_log.csv] [--reference reference_setups.csv] [--profile reference_parity]
"""
import sys
import argparse
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent

def classify_match(ref_row: pd.Series, candidates: pd.DataFrame) -> dict:
    """
    Classifies a reference setup against candidate bot alerts.
    """
    sym = ref_row["symbol"]
    rdir = ref_row["dir"].upper()
    ref_bo_time = pd.Timestamp(ref_row["bo_open_utc"])
    ref_kl_p = float(ref_row["kl_price"])
    ref_bo_p = float(ref_row["bo_price"])
    ref_kind = str(ref_row.get("kl_kind", "")).strip()

    # Filter candidates by symbol and direction
    sub = candidates[
        (candidates["symbol"] == sym) &
        (candidates["dir"].str.upper() == rdir)
    ].copy()

    if sub.empty:
        return {
            "status": "MISSING",
            "best_match": None,
            "kl_diff_pct": None,
            "bo_diff_pct": None,
            "same_bo_candle": False,
            "kind_match": False,
        }

    # Score candidates
    matches = []
    for _, row in sub.iterrows():
        bot_bo_time = pd.Timestamp(row["bo_open_utc"])
        bot_kl_p = float(row["kl_price"])
        bot_bo_p = float(row["bo_price"])
        bot_kind = str(row.get("kl_kind", "")).strip()

        kl_diff = abs(bot_kl_p - ref_kl_p) / ref_kl_p * 100.0
        bo_diff = abs(bot_bo_p - ref_bo_p) / ref_bo_p * 100.0
        same_bo = (bot_bo_time == ref_bo_time)
        kind_match = (bot_kind.upper() == ref_kind.upper())

        # Exact MATCH condition:
        # Same breakout candle time AND key level price diff <= 0.03% AND breakout price diff <= 0.03%
        # (also accept same breakout candle with minor feed diff <= 0.15% as MATCH)
        is_exact = same_bo and (kl_diff <= 0.15) and (bo_diff <= 0.15)

        matches.append({
            "row": row,
            "is_exact": is_exact,
            "same_bo": same_bo,
            "kl_diff": kl_diff,
            "bo_diff": bo_diff,
            "kind_match": kind_match,
            "time_diff_hours": abs((bot_bo_time - ref_bo_time).total_seconds()) / 3600.0,
        })

    # Pick best candidate: exact match first, then closest time diff, then lowest price diff
    matches.sort(key=lambda m: (
        not m["is_exact"],
        not m["same_bo"],
        m["time_diff_hours"],
        m["kl_diff"] + m["bo_diff"],
    ))
    best = matches[0]

    status = "MATCH" if best["is_exact"] else "PARTIAL"

    return {
        "status": status,
        "best_match": best["row"],
        "kl_diff_pct": best["kl_diff"],
        "bo_diff_pct": best["bo_diff"],
        "same_bo_candle": best["same_bo"],
        "kind_match": best["kind_match"],
    }

def main():
    parser = argparse.ArgumentParser(description="Live parity matching against reference setups.")
    parser.add_argument("--alerts", default="alerts_log.csv", help="Path to alerts_log.csv")
    parser.add_argument("--reference", default="reference_setups.csv", help="Path to reference setups CSV")
    parser.add_argument("--profile", default=None, help="Filter by profile (e.g. 'forex_com' or 'reference_parity')")
    parser.add_argument("--start-date", default=None, help="Filter reference setups >= start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="Filter reference setups <= end date (YYYY-MM-DD)")
    args = parser.parse_args()

    ref_path = Path(args.reference)
    if not ref_path.exists():
        print(f"Error: reference CSV not found at {ref_path}")
        sys.exit(1)

    alerts_path = Path(args.alerts)
    if not alerts_path.exists():
        print(f"Warning: alerts log not found at {alerts_path}. Generating empty report.")
        alerts_df = pd.DataFrame(columns=[
            "id", "symbol", "dir", "pair", "kl_kind", "kl_price", "bo_price",
            "rej_open_utc", "bo_open_utc", "next_day", "swept_level", "kl_formed_wat",
            "alert_time_utc", "method", "profile"
        ])
    else:
        alerts_df = pd.read_csv(alerts_path)

    ref_df = pd.read_csv(ref_path)

    if args.profile and "profile" in alerts_df.columns:
        alerts_df = alerts_df[alerts_df["profile"] == args.profile].copy()

    if args.start_date:
        ref_df = ref_df[ref_df["bo_open_utc"] >= args.start_date].copy()
    if args.end_date:
        ref_df = ref_df[ref_df["bo_open_utc"] <= args.end_date].copy()

    results = []
    used_alert_indices = set()

    for idx, ref_row in ref_df.iterrows():
        res = classify_match(ref_row, alerts_df)
        bm = res["best_match"]
        if bm is not None:
            used_alert_indices.add(bm.name)
            results.append({
                "ref_id": ref_row.get("id", idx + 1),
                "symbol": ref_row["symbol"],
                "dir": ref_row["dir"],
                "status": res["status"],
                "same_bo": "YES" if res["same_bo_candle"] else "NO",
                "ref_bo": ref_row["bo_open_utc"],
                "bot_bo": bm["bo_open_utc"],
                "ref_kl": ref_row["kl_price"],
                "bot_kl": bm["kl_price"],
                "kl_diff_%": f"{res['kl_diff_pct']:.3f}%" if res["kl_diff_pct"] is not None else "",
                "ref_kind": ref_row.get("kl_kind", ""),
                "bot_kind": bm.get("kl_kind", ""),
            })
        else:
            results.append({
                "ref_id": ref_row.get("id", idx + 1),
                "symbol": ref_row["symbol"],
                "dir": ref_row["dir"],
                "status": "MISSING",
                "same_bo": "NO",
                "ref_bo": ref_row["bo_open_utc"],
                "bot_bo": "-",
                "ref_kl": ref_row["kl_price"],
                "bot_kl": "-",
                "kl_diff_%": "-",
                "ref_kind": ref_row.get("kl_kind", ""),
                "bot_kind": "-",
            })

    res_df = pd.DataFrame(results)

    # Status Counter
    vc = res_df["status"].value_counts().to_dict()
    m_cnt = vc.get("MATCH", 0)
    p_cnt = vc.get("PARTIAL", 0)
    miss_cnt = vc.get("MISSING", 0)
    same_bo_cnt = sum(res_df["same_bo"] == "YES")

    # Extra setups (bot alerts not matched to any reference setup in window)
    extras = alerts_df[~alerts_df.index.isin(used_alert_indices)].copy()
    if args.start_date:
        extras = extras[extras["bo_open_utc"] >= args.start_date]
    if args.end_date:
        extras = extras[extras["bo_open_utc"] <= args.end_date]

    print("\n" + "=" * 90)
    print("LIVE PARITY MATCH REPORT")
    print(f"Reference File: {ref_path} ({len(ref_df)} setups)")
    print(f"Alerts File:    {alerts_path} (Profile: {args.profile or 'ALL'})")
    print("=" * 90)
    print(f"STATUS SUMMARY: MATCH: {m_cnt} | PARTIAL: {p_cnt} | MISSING: {miss_cnt} (Total: {len(res_df)})")
    print(f"SAME BREAKOUT CANDLE: {same_bo_cnt} / {len(res_df)} ({same_bo_cnt/len(res_df)*100.0:.1f}%)" if len(res_df) else "")
    print(f"EXTRA BOT SETUPS:     {len(extras)}")
    print("-" * 90)
    print(res_df.to_string(index=False))

    if not extras.empty:
        print("\n" + "-" * 90)
        print(f"EXTRA SETUPS NOT IN REFERENCE ({len(extras)} setups):")
        print(extras[["symbol", "dir", "pair", "kl_kind", "kl_price", "bo_price", "bo_open_utc"]].to_string(index=False))
    print("=" * 90 + "\n")

if __name__ == "__main__":
    main()
