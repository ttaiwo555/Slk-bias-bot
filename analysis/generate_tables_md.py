import pandas as pd
from pathlib import Path

csv_path = Path("analysis/output/oct19_25_setups.csv")
df = pd.read_csv(csv_path)

print("Disrespect count distribution:")
print(df['disrespect_count'].value_counts().sort_index())
print(f"Max disrespect: {df['disrespect_count'].max()}")
print(f"Total rows: {len(df)}")

# Generate Markdown tables matching the exact format
md_path = Path("analysis/output/oct19_25_tables.md")

lines = []
days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

for d in days:
    sub = df[df['breakout_candle_open_wat'].str.startswith(d)].copy()
    lines.append(f"### {d} (N={len(sub)})\n")
    lines.append("| Instrument | Dir | Method | HTF Rej (WAT) | Key Level | Kind / Fresh | Disp | Formation Candles | Wick Pen (% / ADR) | Close Dist (% / ADR) | Swept? | Trend | Breakout Level | Breakout (WAT) | Inside? |")
    lines.append("|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|")
    
    for _, r in sub.iterrows():
        inst = f"**{r['instrument']}**"
        rdir = r['direction']
        m = "M3" if "Combined" in r['method'] else "M1"
        htf_rej = r['htf_candle_open_wat']
        kl = f"{r['key_level_price']}"
        fresh_abbr = "FRESH" if r['key_level_freshness'] == "FRESH" else "LOW_P"
        kind_fresh = f"{r['key_level_kind']} ({fresh_abbr})"
        disp = r['disrespect_count']
        
        # Formation candles formatting
        fc1 = r['formation_candle_1_wat'].replace("bullish", "bull").replace("bearish", "bear")
        fc2 = r['formation_candle_2_wat'].replace("bullish", "bull").replace("bearish", "bear")
        fc_str = f"{fc1} / {fc2}"
        
        wick_str = f"{r['wick_penetration_pct']:.3f}% / {r['wick_penetration_adr']:.3f}"
        close_str = f"{r['close_distance_pct']:.3f}% / {r['close_distance_adr']:.3f}"
        swept = r['swept_prev_extreme']
        trend = r['trend_state']
        bo_lvl = f"{r['breakout_level']}"
        bo_wat = r['breakout_candle_open_wat']
        inside = r['breakout_inside_rejection']
        
        row_str = f"| {inst} | {rdir} | {m} | {htf_rej} | {kl} | {kind_fresh} | {disp} | {fc_str} | {wick_str} | {close_str} | {swept} | {trend} | {bo_lvl} | {bo_wat} | {inside} |"
        lines.append(row_str)
    lines.append("")

md_content = "\n".join(lines)
with open(md_path, "w", encoding="utf-8") as f:
    f.write(md_content)

print(f"Generated {md_path} with {len(lines)} lines.")
