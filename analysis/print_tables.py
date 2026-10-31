import pandas as pd

df = pd.read_csv('analysis/output/oct19_25_setups.csv')

def make_md_table(sub_df):
    header = '| Instrument | Dir | Method | HTF Rej (WAT) | Key Level | Kind / Fresh | Disp | Formation Candles | Wick Pen (% / ADR) | Close Dist (% / ADR) | Swept? | Trend | Breakout Level | Breakout (WAT) | Inside? |'
    sep = '|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|'
    rows = [header, sep]
    for _, r in sub_df.iterrows():
        m = 'M1' if 'Method 1' in r['method'] else ('M3' if 'Method 3' in r['method'] else r['method'])
        f1 = r['formation_candle_1_wat'].split('(')[0].strip() + ' (' + r['formation_candle_1_wat'].split('(')[1].replace(')', '')[:4] + ')' if pd.notna(r['formation_candle_1_wat']) and '(' in str(r['formation_candle_1_wat']) else str(r['formation_candle_1_wat'])
        f2 = r['formation_candle_2_wat'].split('(')[0].strip() + ' (' + r['formation_candle_2_wat'].split('(')[1].replace(')', '')[:4] + ')' if pd.notna(r['formation_candle_2_wat']) and '(' in str(r['formation_candle_2_wat']) else str(r['formation_candle_2_wat'])
        fc = f'{f1} / {f2}' if f1 else ''
        pen = f"{r['wick_penetration_pct']:.3f}% / {r['wick_penetration_adr']:.3f}"
        dist = f"{r['close_distance_pct']:.3f}% / {r['close_distance_adr']:.3f}"
        fresh = 'FRESH' if r['key_level_freshness'] == 'FRESH' else 'LOW_P'
        kind_str = f"{r['key_level_kind']} ({fresh})"
        row_str = f"| **{r['instrument']}** | {r['direction']} | {m} | {r['htf_candle_open_wat']} | {r['key_level_price']} | {kind_str} | {r['disrespect_count']} | {fc} | {pen} | {dist} | {r['swept_prev_extreme']} | {r['trend_state']} | {r['breakout_level']} | {r['breakout_candle_open_wat']} | {r['breakout_inside_rejection']} |"
        rows.append(row_str)
    return '\n'.join(rows)

df['day'] = df['breakout_candle_open_wat'].apply(lambda x: x.split()[0] if isinstance(x, str) else '')
for d in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']:
    sub = df[df['day'] == d]
    print(f'### {d} (N={len(sub)})')
    print(make_md_table(sub))
    print('\n')
