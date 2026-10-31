import re
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
import pandas as pd

@dataclass
class ReferenceAlert:
    raw_text: str
    sent_time_wat: str
    sent_time_utc: Optional[pd.Timestamp]
    direction: str                     # "BUY" or "SELL"
    symbol: str                        # e.g. "EURGBP"
    pair: str                          # "D1->H4" or "W1->D1"
    htf: str                           # "D" or "W"
    ltf: str                           # "H4" or "D"
    level_kind: str                    # "OCL", "V_SHAPE", "A_SHAPE"
    level_price: float
    breakout_price: float
    breakout_candle_open_wat: str
    breakout_candle_open_utc: pd.Timestamp
    rejection_candle_open_wat: str
    rejection_candle_open_utc: pd.Timestamp
    is_sweep: bool
    swept_level: Optional[float]
    level_formed_date: Optional[str]
    trend_aligned: bool
    next_day_rule: bool
    erl_taken: bool
    erl_price: Optional[float]

def parse_wat_date(date_str: str, default_year: int = 2026) -> pd.Timestamp:
    # Example format: "Mon 12 Oct, 06:00" or "Wed 7 Oct, 22:00" or "15 Oct, 6:00 PM WAT"
    # Convert WAT (UTC+1) to UTC by subtracting 1 hour
    clean = date_str.replace(" WAT", "").strip()
    # If weekday prefix, e.g. "Mon 12 Oct, 06:00"
    m = re.search(r'([A-Za-z]{3}\s+)?(\d{1,2})\s+([A-Za-z]{3}),?\s+(\d{1,2}):(\d{2})(?:\s*([AP]M))?', clean, re.IGNORECASE)
    if m:
        day = int(m.group(2))
        month_str = m.group(3)
        hour = int(m.group(4))
        minute = int(m.group(5))
        ampm = m.group(6)
        if ampm:
            if ampm.upper() == "PM" and hour < 12:
                hour += 12
            elif ampm.upper() == "AM" and hour == 12:
                hour = 0
        ts_wat = pd.Timestamp(year=default_year, month=10 if month_str.lower().startswith("oct") else 9, day=day, hour=hour, minute=minute, tz="Africa/Lagos")
        return ts_wat.tz_convert("UTC")
    # Fallback to pandas
    ts = pd.to_datetime(f"{clean} {default_year}", errors="coerce")
    if ts is not pd.NaT:
        if ts.tz is None:
            ts = ts.tz_localize("Africa/Lagos")
        return ts.tz_convert("UTC")
    return pd.NaT

def parse_reference_file(filepath: Path) -> list[ReferenceAlert]:
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    # Split by the alert header "🚨 "
    chunks = text.split("🚨 ")
    alerts = []

    for chunk in chunks[1:]:
        lines = [line.strip() for line in chunk.split("\n") if line.strip()]
        if not lines:
            continue
        
        # Line 0: e.g. "SELL · EURGBP · D1→H4"
        header = lines[0]
        header_parts = [p.strip() for p in header.split("·")]
        if len(header_parts) < 3:
            continue
        direction = header_parts[0].upper()
        symbol = header_parts[1].upper()
        pair_str = header_parts[2].replace("→", "->")
        htf = "D" if "D1" in pair_str.split("->")[0] else "W"
        ltf = "H4" if "H4" in pair_str.split("->")[1] else "D"

        raw_chunk = "🚨 " + chunk
        
        # Find sent time
        sent_m = re.search(r'Sent\s+([^,\n]+,\s*[^,\n]+(?:WAT)?)', raw_chunk)
        sent_wat = sent_m.group(1).strip() if sent_m else ""
        sent_utc = parse_wat_date(sent_wat) if sent_wat else None

        # Level kind and price
        # e.g. "Rejected at Open-Close KL @ 0.84822." or "Rejected at V-shape KL @ 1.09822."
        # or sweep: "Swept previous candle high 1.12269, closed back below; rejected fresh V-shape KL @ 1.12426."
        is_sweep = "Swept previous candle" in raw_chunk
        swept_level = None
        if is_sweep:
            sm = re.search(r'Swept previous candle (?:high|low)\s+([\d\.]+)', raw_chunk)
            if sm:
                swept_level = float(sm.group(1))

        level_kind = "UNKNOWN"
        if "Open-Close" in raw_chunk or "OCL" in raw_chunk:
            level_kind = "OCL"
        elif "V-shape" in raw_chunk:
            level_kind = "V_SHAPE"
        elif "A-shape" in raw_chunk:
            level_kind = "A_SHAPE"

        level_price = 0.0
        lm = re.search(r'rejected (?:fresh\s+)?(?:[A-Za-z\-]+ KL\s+)?@\s*([\d\.]+)', raw_chunk, re.IGNORECASE)
        if lm:
            level_price = float(lm.group(1).rstrip('.'))
        else:
            lm2 = re.search(r'Rejected at [A-Za-z\-]+ KL @\s*([\d\.]+)', raw_chunk)
            if lm2:
                level_price = float(lm2.group(1).rstrip('.'))

        # Breakout price and candle open time
        # e.g. "External BO @ 0.84574 · Mon 12 Oct, 06:00"
        bo_m = re.search(r'External BO @\s*([\d\.]+)\s*·\s*([^·\n]+)', raw_chunk)
        if bo_m:
            bo_price = float(bo_m.group(1).rstrip('.'))
            bo_time_wat = bo_m.group(2).strip()
            bo_time_utc = parse_wat_date(bo_time_wat)
        else:
            bo_price = 0.0
            bo_time_wat = ""
            bo_time_utc = pd.NaT

        # Rejection candle open time
        # e.g. "Rejected on Wed 7 Oct, 22:00"
        rej_m = re.search(r'Rejected on\s+([^·\n\r]+)', raw_chunk)
        if rej_m:
            rej_time_wat = rej_m.group(1).strip()
            rej_time_utc = parse_wat_date(rej_time_wat)
        else:
            rej_time_wat = ""
            rej_time_utc = pd.NaT

        # Level formed date
        formed_m = re.search(r'formed\s+([^·\n\r]+)', raw_chunk)
        formed_date = formed_m.group(1).strip() if formed_m else None

        trend_aligned = "Trend aligned" in raw_chunk
        next_day = "Next Day Rule" in raw_chunk
        erl_taken = "ERL taken" in raw_chunk
        erl_price = None
        if erl_taken:
            em = re.search(r'range (?:low|high) @\s*([\d\.]+)', raw_chunk)
            if em:
                erl_price = float(em.group(1))

        alerts.append(ReferenceAlert(
            raw_text=raw_chunk,
            sent_time_wat=sent_wat,
            sent_time_utc=sent_utc,
            direction=direction,
            symbol=symbol,
            pair=pair_str,
            htf=htf,
            ltf=ltf,
            level_kind=level_kind,
            level_price=level_price,
            breakout_price=bo_price,
            breakout_candle_open_wat=bo_time_wat,
            breakout_candle_open_utc=bo_time_utc,
            rejection_candle_open_wat=rej_time_wat,
            rejection_candle_open_utc=rej_time_utc,
            is_sweep=is_sweep,
            swept_level=swept_level,
            level_formed_date=formed_date,
            trend_aligned=trend_aligned,
            next_day_rule=next_day,
            erl_taken=erl_taken,
            erl_price=erl_price,
        ))

    return alerts

if __name__ == "__main__":
    ref_file = Path("reference_alerts.txt")
    alerts = parse_reference_file(ref_file)
    print(f"Total reference alert messages parsed: {len(alerts)}")
    symbols = sorted(set(a.symbol for a in alerts))
    print(f"Symbols in reference ({len(symbols)}): {symbols}")
    
    # Check ignored instruments
    ignored = {"EU50", "JPN225", "DOTUSDT"}
    in_scope = [a for a in alerts if a.symbol not in ignored]
    print(f"Alerts in scope (excluding EU50, JPN225, DOTUSDT): {len(in_scope)}")

    # Method 3 / Sweep vs Rejection breakdown
    sweeps = [a for a in in_scope if a.is_sweep]
    rejections = [a for a in in_scope if not a.is_sweep]
    print(f"In-scope Rejection messages: {len(rejections)}, Sweep messages: {len(sweeps)}")

    # Check date range of BO candle UTC
    bo_dates = [a.breakout_candle_open_utc for a in in_scope if pd.notna(a.breakout_candle_open_utc)]
    print(f"BO candle UTC range: {min(bo_dates)} to {max(bo_dates)}")
