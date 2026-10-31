"""
analysis/build_review_sheet_max_disp3.py
----------------------------------------
Builds review sheet filtered to max_disrespects = 3:
  - Group A: Reference setups with disrespect_count <= 3 (20 setups)
  - Group B: 20 Randomly sampled extra setups under max_disrespects = 3 (from 48 candidates)
"""
import sys
import random
import yaml
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.data.candles import apply_open_continuity
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import evaluate_setup_lifecycle, detect_ltf_levels, BreakoutType
from slk_bot.strategy.models import TriggerDirection, TriggerMethod, KeyLevel
from slk_bot.strategy.levels import track_key_levels
from analysis.evaluate_trend_three_moments import compute_trend_b_c

cache = CandleCache()
INST_PATH = ROOT / 'slk_bot' / 'config' / 'instruments.yaml'
with open(INST_PATH, 'r', encoding='utf-8') as f:
    INST_DATA = yaml.safe_load(f)

ref_df = pd.read_csv(ROOT / 'reference_setups.csv')
in_scope_syms = set(ref_df['symbol'].unique())
instruments = [inst for inst in INST_DATA['instruments'] if inst['name'] in in_scope_syms]

WINDOW_START = pd.Timestamp('2026-10-12 00:00:00', tz='UTC')
WINDOW_END   = pd.Timestamp('2026-10-16 23:59:59', tz='UTC')
TRIG_CUTOFF  = pd.Timestamp('2026-08-01 00:00:00', tz='UTC')

# 1. Load original review sheet
orig_sheet = pd.read_csv(ROOT / 'analysis' / 'output' / 'review_sheet.csv')

# Reference setups with max_disrespects <= 3 (exact verified triggers)
valid_ref_ids = [1, 4, 5, 6, 7, 9, 11, 14, 16, 17, 19, 21, 23, 24, 26, 27, 29, 30, 31, 33]
ref_filtered = orig_sheet[(orig_sheet['group'] == 'reference') & (orig_sheet['id'].astype(str).isin([str(i) for i in valid_ref_ids]))].copy()

print(f'Group A (Reference with disrespect <= 3): N = {len(ref_filtered)}')

# 2. Preload candles & trend
candles_by_sym = {}
trend_cache = {}
levels_cache = {}
for inst in instruments:
    sym = inst['name']
    df_d = apply_open_continuity(cache.load_candles(sym, 'D'))
    df_h4 = apply_open_continuity(cache.load_candles(sym, 'H4'))
    df_w = apply_open_continuity(cache.load_candles(sym, 'W'))
    candles_by_sym[sym] = (df_d, df_h4, df_w)
    if not df_d.empty:
        thist = compute_trend_b_c(df_d, use_marginal=True, sym=sym)
        trend_cache[(sym, 'D')] = [(h[0], h[1]) for h in thist]
        levels_cache[(sym, 'D')] = track_key_levels(df_d, 'D', sym, lookback=None, max_disrespects=3, ocl_max_disrespects=3)
    if not df_w.empty:
        thist_w = compute_trend_b_c(df_w, use_marginal=True, sym=sym)
        trend_cache[(sym, 'W')] = [(h[0], h[1]) for h in thist_w]
        levels_cache[(sym, 'W')] = track_key_levels(df_w, 'W', sym, lookback=None, max_disrespects=3, ocl_max_disrespects=3)

# 3. Gather all candidate alerts under max_disrespects=3
all_engine_alerts = []
for inst in instruments:
    sym = inst['name']
    ac = inst.get('asset_class', 'forex')
    df_d, df_h4, df_w = candles_by_sym[sym]
    if not df_d.empty and not df_h4.empty:
        trigs_d = detect_triggers(
            df_d, 'D', sym, ac,
            lookback=None, max_disrespects=3, ocl_max_disrespects=3,
            primary_selection_rule='fresh_first',
        )
        h4_lvls = detect_ltf_levels(df_h4, sym, 'H4', 0.0)
        for t in trigs_d:
            if t.method == TriggerMethod.METHOD_2 or t.trigger_candle_time < TRIG_CUTOFF:
                continue
            s = evaluate_setup_lifecycle(
                t, df_d, df_h4, df_d, sym, 'D', 'H4',
                unfresh_alerts=True, all_ltf_levels=h4_lvls,
                external_ref_point='first_touch',
                enable_invalidation=True,
            )
            for a in s.alerts:
                if a.breakout_type == BreakoutType.EXTERNAL and WINDOW_START <= a.breakout_candle_time <= WINDOW_END:
                    prior_d = df_d[df_d.index < a.breakout_candle_time]
                    asof = prior_d.index[-1] if not prior_d.empty else a.breakout_candle_time
                    thist = trend_cache.get((sym, 'D'), [])
                    priors = [h for h in thist if h[0] <= asof]
                    trend = priors[-1][1] if priors else 'NONE'
                    exp = 'BULLISH' if a.direction == TriggerDirection.BULLISH else 'BEARISH'
                    if trend == exp:
                        all_engine_alerts.append({'alert': a, 'trigger': t, 'htf_df': df_d, 'df_d': df_d, 'trend': trend})

unique_engine_setups = {}
for item in all_engine_alerts:
    a = item['alert']
    sdir = 'BUY' if a.direction == TriggerDirection.BULLISH else 'SELL'
    k = (a.symbol, sdir, a.breakout_candle_time.strftime('%Y-%m-%d'))
    if k not in unique_engine_setups:
        unique_engine_setups[k] = item

matched_ref_keys = set()
for _, r in ref_df.iterrows():
    bo_time = pd.Timestamp(r['bo_open_utc'], tz='UTC')
    matched_ref_keys.add((r['symbol'], r['dir'].upper(), bo_time.strftime('%Y-%m-%d')))

extra_cands = [item for k, item in unique_engine_setups.items() if k not in matched_ref_keys]
print(f'Total extra candidates with max_disrespects=3: {len(extra_cands)}')

# Sample 20 with seed=42
rng = random.Random(42)
selected_extras = rng.sample(extra_cands, 20)
selected_extras.sort(key=lambda x: (x['alert'].breakout_candle_time, x['alert'].symbol))

# Metric calculation helpers
def compute_adr14(df_d: pd.DataFrame, asof_time: pd.Timestamp) -> float:
    priors = df_d[df_d.index <= asof_time]
    if len(priors) < 15:
        priors = df_d
    dr = priors['high'].iloc[-14:] - priors['low'].iloc[-14:]
    val = float(dr.mean())
    return val if val > 0 else float(dr.iloc[-1])

def format_wat_time(ts: pd.Timestamp) -> str:
    if pd.isna(ts): return ''
    ts_wat = ts + pd.Timedelta(hours=1)
    day_name = ts_wat.strftime('%a')
    day_num = ts_wat.day
    mon_name = ts_wat.strftime('%b')
    time_str = ts_wat.strftime('%H:%M')
    return f'{day_name} {day_num} {mon_name}, {time_str}'

def format_candle_formation(kl: KeyLevel) -> tuple[str, str]:
    if kl is None: return '', ''
    t1_wat = kl.formation_time + pd.Timedelta(hours=1)
    t2_wat = kl.formation_confirmed_time + pd.Timedelta(hours=1)
    kind_val = kl.kind.value if hasattr(kl.kind, 'value') else str(kl.kind)
    c1_col, c2_col = ('bullish', 'bearish') if kind_val in ('A', 'bearish_OCL') else ('bearish', 'bullish')
    return f"{t1_wat.strftime('%a')} {t1_wat.day} {t1_wat.strftime('%b %H:%M')} ({c1_col})", f"{t2_wat.strftime('%a')} {t2_wat.day} {t2_wat.strftime('%b %H:%M')} ({c2_col})"

extra_rows = []
for idx, item in enumerate(selected_extras, start=1):
    a = item['alert']
    t = item['trigger']
    htf_df = item['htf_df']
    df_d = item['df_d']
    trend = item['trend']
    sym = a.symbol
    rdir = 'BUY' if a.direction == TriggerDirection.BULLISH else 'SELL'
    rej_time = t.trigger_candle_time
    bo_time = a.breakout_candle_time
    kl_price = a.htf_level.price if a.htf_level else a.broken_level.price
    bo_price = a.broken_level.price
    raw_k = a.htf_level.kind.value if a.htf_level and hasattr(a.htf_level.kind, 'value') else (str(a.htf_level.kind) if a.htf_level else 'Turning Point')
    kl_kind = 'OCL' if 'OCL' in raw_k else ('V' if 'V' in raw_k else ('A' if 'A' in raw_k else raw_k))
    kl_fresh = 'LOW PROBABILITY' if a.is_low_probability else 'FRESH'
    m_name = {TriggerMethod.METHOD_1: 'Method 1 (Rejection)', TriggerMethod.METHOD_2: 'Method 2 (Sweep)', TriggerMethod.METHOD_3: 'Method 3 (Combined)'}.get(a.method, f'Method {int(a.method)}')
    
    adr14 = compute_adr14(df_d, rej_time)
    rej_candle = htf_df.loc[rej_time] if rej_time in htf_df.index else htf_df[htf_df.index <= rej_time].iloc[-1]
    c_open, c_high, c_low, c_close = float(rej_candle['open']), float(rej_candle['high']), float(rej_candle['low']), float(rej_candle['close'])
    wick_pen = max(0.0, c_high - kl_price) if rdir == 'SELL' else max(0.0, kl_price - c_low)
    wick_pen_pct = (wick_pen / kl_price) * 100
    wick_pen_adr = wick_pen / adr14 if adr14 > 0 else 0.0
    close_dist = abs(c_close - kl_price)
    close_dist_pct = (close_dist / kl_price) * 100
    close_dist_adr = close_dist / adr14 if adr14 > 0 else 0.0
    c_size = c_high - c_low
    c_size_pct = (c_size / kl_price) * 100
    c_size_adr = c_size / adr14 if adr14 > 0 else 0.0
    body_dir = 'BULLISH' if c_close > c_open else ('BEARISH' if c_close < c_open else 'DOJI')
    
    priors = htf_df[htf_df.index < rej_time]
    swept = 'N'
    if not priors.empty:
        prev = priors.iloc[-1]
        if rdir == 'SELL' and c_high > float(prev['high']) and c_close < float(prev['high']): swept = 'Y'
        elif rdir == 'BUY' and c_low < float(prev['low']) and c_close > float(prev['low']): swept = 'Y'
        
    fc1_str, fc2_str = format_candle_formation(a.htf_level)
    rej_close_time = rej_time + pd.Timedelta(days=1)
    inside_rej = 'Y' if bo_time < rej_close_time else 'N'
    
    extra_rows.append({
        'group': 'extra',
        'id': f'X{idx:02d}',
        'instrument': sym,
        'direction': rdir,
        'method': m_name,
        'htf_candle_open_wat': format_wat_time(rej_time),
        'key_level_price': round(kl_price, 5),
        'key_level_kind': kl_kind,
        'key_level_freshness': kl_fresh,
        'formation_candle_1_wat': fc1_str,
        'formation_candle_2_wat': fc2_str,
        'wick_penetration_pct': round(wick_pen_pct, 4),
        'wick_penetration_adr': round(wick_pen_adr, 4),
        'close_distance_pct': round(close_dist_pct, 4),
        'close_distance_adr': round(close_dist_adr, 4),
        'htf_candle_size_pct': round(c_size_pct, 4),
        'htf_candle_size_adr': round(c_size_adr, 4),
        'htf_candle_body_dir': body_dir,
        'swept_prev_extreme': swept,
        'trend_state': trend,
        'breakout_level': round(bo_price, 5),
        'breakout_candle_open_wat': format_wat_time(bo_time),
        'breakout_inside_rejection': inside_rej,
    })

extras_df = pd.DataFrame(extra_rows)
out_df = pd.concat([ref_filtered, extras_df], ignore_index=True)
out_path = ROOT / 'analysis' / 'output' / 'review_sheet_max_disp3.csv'
out_df.to_csv(out_path, index=False)
print(f'Saved {out_path} with {len(out_df)} rows ({len(ref_filtered)} ref + {len(extras_df)} extras).')

# Summary statistics
print('\n' + '='*80)
print('STATISTICAL COMPARISON (MAX DISRESPECTS = 3)')
print('='*80)
for g in ['reference', 'extra']:
    sub = out_df[out_df['group'] == g]
    print(f'\n=== {g.upper()} (N={len(sub)}) ===')
    for col in ['wick_penetration_pct', 'wick_penetration_adr', 'close_distance_pct', 'close_distance_adr', 'htf_candle_size_pct', 'htf_candle_size_adr']:
        med = sub[col].median()
        q1 = sub[col].quantile(0.25)
        q3 = sub[col].quantile(0.75)
        iqr = q3 - q1
        print(f'{col:22s} | Med: {med:.4f} | Q1: {q1:.4f} | Q3: {q3:.4f} | IQR: {iqr:.4f}')
    print('\nhtf_candle_body_dir:')
    print(sub['htf_candle_body_dir'].value_counts(normalize=True))
    print('\nswept_prev_extreme:')
    print(sub['swept_prev_extreme'].value_counts(normalize=True))
    print('\nbreakout_inside_rejection:')
    print(sub['breakout_inside_rejection'].value_counts(normalize=True))
    print('\nkey_level_freshness:')
    print(sub['key_level_freshness'].value_counts(normalize=True))

# Continuous separation
print('\n' + '='*80)
print('CONTINUOUS SEPARATION (CLIFFS DELTA & AUC)')
print('='*80)
ref_sub = out_df[out_df['group'] == 'reference']
ext_sub = out_df[out_df['group'] == 'extra']
n_ref, n_ext = len(ref_sub), len(ext_sub)

features = ['wick_penetration_pct', 'wick_penetration_adr', 'close_distance_pct', 'close_distance_adr', 'htf_candle_size_pct', 'htf_candle_size_adr']
for f in features:
    comb = pd.concat([ref_sub[[f]].assign(g='ref'), ext_sub[[f]].assign(g='ext')]).sort_values(by=f)
    comb['rank'] = range(1, len(comb) + 1)
    ranks = comb.groupby(f)['rank'].transform('mean')
    comb['rank'] = ranks
    r_ref = comb[comb['g'] == 'ref']['rank'].sum()
    u_ref = r_ref - n_ref * (n_ref + 1) / 2
    auc = u_ref / (n_ref * n_ext)
    cd = 2 * auc - 1
    print(f'{f:22s} | AUC(ref>ext): {auc:.3f} | Cliff Delta: {cd:+.3f} | Sep (|d|): {abs(cd):.3f} | Ref Med: {ref_sub[f].median():.4f} | Ext Med: {ext_sub[f].median():.4f}')
