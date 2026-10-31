import pandas as pd
from pathlib import Path

src_csv = Path("analysis/output/oct19_25_setups_final.csv")
dst_csv = Path("analysis/output/oct19_25_setups.csv")
dst_md = Path("analysis/output/oct19_25_tables.md")

df = pd.read_csv(src_csv)
df.to_csv(dst_csv, index=False)
print(f"Copied {len(df)} rows to {dst_csv}")

lines = []
days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

for d in days:
    sub = df[df['breakout_candle_wat'].str.startswith(d)].copy()
    lines.append(f"### {d} (N={len(sub)})\n")
    lines.append("| Instrument | Dir | Method | HTF Rej Date | Key Level | Kind / Fresh | Disp | Formation Candles | Wick Pen (% / ADR) | Close Dist (% / ADR) | Swept? | Trend | Breakout Level | Breakout (WAT) | Alert Created (WAT) | Inside? |")
    lines.append("|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|")
    
    for _, r in sub.iterrows():
        inst = f"**{r['instrument']}**"
        rdir = r['direction']
        m = "M3" if "Combined" in r['method'] else "M1"
        rej_date = r['htf_rejection_date']
        kl = f"{r['key_level_price']}"
        fresh_abbr = "FRESH" if r['key_level_freshness'] == "FRESH" else "LOW_P"
        kind_fresh = f"{r['key_level_kind']} ({fresh_abbr})"
        disp = r['disrespect_count']
        
        # Formation candles formatting
        fc1 = r['formation_candle_1'].replace("bullish", "bull").replace("bearish", "bear")
        fc2 = r['formation_candle_2'].replace("bullish", "bull").replace("bearish", "bear")
        fc_str = f"{fc1} / {fc2}"
        
        wick_str = f"{r['wick_penetration_pct']:.3f}% / {r['wick_penetration_adr']:.3f}"
        close_str = f"{r['close_distance_pct']:.3f}% / {r['close_distance_adr']:.3f}"
        swept = r['swept_prev_extreme']
        trend = r['trend_state']
        bo_lvl = f"{r['breakout_level']}"
        bo_wat = r['breakout_candle_wat']
        created_wat = r['alert_created_wat']
        inside = r['breakout_inside_rejection']
        
        row_str = f"| {inst} | {rdir} | {m} | {rej_date} | {kl} | {kind_fresh} | {disp} | {fc_str} | {wick_str} | {close_str} | {swept} | {trend} | {bo_lvl} | {bo_wat} | {created_wat} | {inside} |"
        lines.append(row_str)
    lines.append("")

md_content = "\n".join(lines)
with open(dst_md, "w", encoding="utf-8") as f:
    f.write(md_content)

print(f"Generated {dst_md} with {len(lines)} lines.")
