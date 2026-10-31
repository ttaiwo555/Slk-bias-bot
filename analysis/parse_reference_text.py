"""
analysis/parse_reference_text.py
--------------------------------
Converts raw pasted reference-bot messages (times in WAT) into UTC CSV rows
matching reference_setups.csv structure.

Usage:
  python analysis/parse_reference_text.py [--input reference_alerts.txt] [--output parsed_setups.csv]
"""
import sys
import os
import re
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd

MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

def parse_wat_to_utc_iso(time_str: str, default_year: int = 2026) -> str:
    """
    Parses a WAT timestamp string (e.g. 'Mon 12 Oct, 06:00' or 'Wed 7 Oct, 22:00')
    and returns a UTC ISO string (e.g. '2026-10-12T05:00').
    WAT is UTC+1, so UTC = WAT - 1 hour.
    """
    m = re.search(r"(\d+)\s+([A-Za-z]+),\s+(\d{1,2}):(\d{2})", time_str)
    if not m:
        return ""
    d_str, mon_str, hh_str, mm_str = m.groups()
    mon = MONTH_MAP.get(mon_str)
    if not mon:
        return ""
    dt_wat = datetime(default_year, mon, int(d_str), int(hh_str), int(mm_str))
    dt_utc = dt_wat - timedelta(hours=1)
    return dt_utc.strftime("%Y-%m-%dT%H:%M")

def parse_reference_alerts_text(text: str, default_year: int = 2026) -> pd.DataFrame:
    """
    Parses reference alert blocks from raw text and returns a deduplicated DataFrame.
    """
    blocks = [b.strip() for b in re.split(r"(?=🚨)", text) if b.strip().startswith("🚨")]
    parsed = []

    for b in blocks:
        # Header: 🚨 BUY · AUDNZD · D1→H4
        header_m = re.search(r"🚨\s+(BUY|SELL)\s+·\s+([A-Z0-9]+)\s+·\s+([A-Z0-9→\-]+)", b)
        if not header_m:
            continue
        direction, symbol, tf_pair = header_m.groups()
        tf_pair = tf_pair.replace("→", "-")

        # Key Level: V-shape / A-shape / Open-Close KL @ 1.2345
        kl_m = re.search(
            r"(?:rejected\s+fresh\s+|Rejected\s+at\s+)?(V-shape|A-shape|Open-Close)\s+KL\s+@\s+([0-9]+(?:\.[0-9]+)?)",
            b,
            re.IGNORECASE,
        )
        kl_kind_raw = kl_m.group(1) if kl_m else ""
        kl_price = float(kl_m.group(2)) if kl_m else None
        if "Open-Close" in kl_kind_raw:
            kl_kind = "OCL"
        elif "V-shape" in kl_kind_raw:
            kl_kind = "V"
        elif "A-shape" in kl_kind_raw:
            kl_kind = "A"
        else:
            kl_kind = ""

        # Breakout: External BO @ 1.24496 · Mon 12 Oct, 10:00
        bo_m = re.search(r"External BO\s+@\s+([0-9]+(?:\.[0-9]+)?)\s+·\s+([^\n\r]+)", b)
        bo_price = float(bo_m.group(1)) if bo_m else None
        bo_wat_str = bo_m.group(2).strip() if bo_m else ""
        bo_open_utc = parse_wat_to_utc_iso(bo_wat_str, default_year)

        # Rejection: Rejected on Wed 7 Oct, 22:00
        rej_m = re.search(r"Rejected on\s+([^\n\r]+)", b)
        rej_wat_str = rej_m.group(1).strip() if rej_m else ""
        rej_open_utc = parse_wat_to_utc_iso(rej_wat_str, default_year)

        # Swept Level (Method 3): Swept previous candle low 1.24088
        sweep_m = re.search(r"Swept previous candle (?:high|low)\s+([0-9]+(?:\.[0-9]+)?)", b)
        swept_level = float(sweep_m.group(1)) if sweep_m else ""

        # Formation date: Key level 1.24082 · formed 23 Sep 2026
        formed_m = re.search(r"Key level [0-9.]+\s+·\s+formed\s+([^\n\r]+)", b)
        kl_formed_wat = formed_m.group(1).strip() if formed_m else ""

        # Next Day Rule
        next_day = "Y" if "Next Day Rule" in b else "N"

        parsed.append({
            "symbol": symbol,
            "dir": direction,
            "pair": tf_pair,
            "kl_kind": kl_kind,
            "kl_price": kl_price,
            "bo_price": bo_price,
            "rej_open_utc": rej_open_utc,
            "bo_open_utc": bo_open_utc,
            "next_day": next_day,
            "swept_level": swept_level,
            "kl_formed_wat": kl_formed_wat,
        })

    if not parsed:
        return pd.DataFrame(columns=[
            "id", "symbol", "dir", "pair", "kl_kind", "kl_price", "bo_price",
            "rej_open_utc", "bo_open_utc", "next_day", "swept_level", "kl_formed_wat"
        ])

    df = pd.DataFrame(parsed)

    # Deduplication: prefer rows that have swept_level or formation date
    df["has_extra"] = (df["swept_level"] != "") | (df["kl_formed_wat"] != "")
    df = df.sort_values(by=["bo_open_utc", "symbol", "has_extra"], ascending=[True, True, True])
    df = df.drop_duplicates(subset=["symbol", "dir", "pair", "bo_open_utc"], keep="last").copy()
    df = df.drop(columns=["has_extra"])

    df["id"] = range(1, len(df) + 1)
    cols = [
        "id", "symbol", "dir", "pair", "kl_kind", "kl_price", "bo_price",
        "rej_open_utc", "bo_open_utc", "next_day", "swept_level", "kl_formed_wat"
    ]
    return df[cols]

def main():
    parser = argparse.ArgumentParser(description="Parse reference bot text into UTC CSV rows.")
    parser.add_argument("--input", default="reference_alerts.txt", help="Path to reference alerts text file")
    parser.add_argument("--output", default=None, help="Path to save output CSV (optional)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input file {input_path} does not exist.")
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        text = f.read()

    df = parse_reference_alerts_text(text)
    print(f"Successfully parsed {len(df)} unique reference setups from {input_path.name}:")
    print(df.to_string(index=False))

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_path, index=False)
        print(f"\nSaved parsed setups to {out_path}")

if __name__ == "__main__":
    main()
