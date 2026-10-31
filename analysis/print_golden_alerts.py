import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.storage.cache import CandleCache
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import evaluate_setup_lifecycle, detect_ltf_levels, BreakoutType
from slk_bot.bot.formatter import format_alert_message

def main():
    cache = CandleCache()
    output_lines = []

    # 1. AUDCAD Weekly -> Daily
    w_df = cache.load_candles('AUDCAD', 'W')
    d_df = cache.load_candles('AUDCAD', 'D')
    w_trigs = detect_triggers(w_df, symbol='AUDCAD', timeframe='W', asset_class='forex')
    d_levels_aud = detect_ltf_levels(d_df, 'AUDCAD', 'D', 0.0)

    output_lines.append("=================================================================")
    output_lines.append("GOLDEN ALERT 1: AUDCAD External @ 2026-10-09 21:00 UTC")
    output_lines.append("=================================================================")
    for t in w_trigs:
        s = evaluate_setup_lifecycle(
            trigger=t,
            htf_df=w_df,
            symbol='AUDCAD',
            htf_tf='W',
            ltf_tf='D',
            ltf_df=d_df,
            daily_df=d_df,
            unfresh_alerts=True,
            all_ltf_levels=d_levels_aud,
        )
        for a in s.alerts:
            if '2026-10-09' in str(a.alert_time) and a.breakout_type == BreakoutType.EXTERNAL:
                output_lines.append(format_alert_message(a))

    # 2. XAGUSD Daily -> H4
    d_df_xag = cache.load_candles('XAGUSD', 'D')
    h4_df_xag = cache.load_candles('XAGUSD', 'H4')
    d_trigs_xag = detect_triggers(d_df_xag, symbol='XAGUSD', timeframe='D', asset_class='commodity')
    h4_levels_xag = detect_ltf_levels(h4_df_xag, 'XAGUSD', 'H4', 0.0)

    output_lines.append("\n=================================================================")
    output_lines.append("GOLDEN ALERT 2: XAGUSD External @ 2026-10-12 21:00 UTC")
    output_lines.append("=================================================================")
    for t in d_trigs_xag:
        s = evaluate_setup_lifecycle(
            trigger=t,
            htf_df=d_df_xag,
            symbol='XAGUSD',
            htf_tf='D',
            ltf_tf='H4',
            ltf_df=h4_df_xag,
            daily_df=d_df_xag,
            unfresh_alerts=True,
            all_ltf_levels=h4_levels_xag,
        )
        for a in s.alerts:
            if '2026-10-12' in str(a.alert_time) and a.breakout_type == BreakoutType.EXTERNAL:
                output_lines.append(format_alert_message(a))
            elif '2026-10' in str(a.alert_time):
                output_lines.append(f"[Other XAG Alert: {a.alert_time} {a.breakout_type} {a.direction}]")

    out_file = ROOT / "analysis" / "golden_alerts_formatted.txt"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))
    print(f"Saved formatted golden alerts to {out_file}")

if __name__ == '__main__':
    main()
