import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from slk_bot.storage.cache import CandleCache
from slk_bot.data.candles import apply_open_continuity
from slk_bot.strategy.triggers import detect_triggers
from slk_bot.strategy.setups import evaluate_setup_lifecycle, detect_ltf_levels, BreakoutType
from slk_bot.bot.formatter import format_alert_message

cache = CandleCache()

examples = []

# 1. Golden setup AUDCAD (Weekly -> Daily)
w_df = apply_open_continuity(cache.load_candles("AUDCAD", "W"))
d_df = apply_open_continuity(cache.load_candles("AUDCAD", "D"))
w_trigs = detect_triggers(w_df, symbol="AUDCAD", timeframe="W", asset_class="forex")
d_levels_aud = detect_ltf_levels(d_df, "AUDCAD", "D", 0.0)

audcad_alert = None
for t in w_trigs:
    s = evaluate_setup_lifecycle(
        trigger=t,
        htf_df=w_df,
        symbol="AUDCAD",
        htf_tf="W",
        ltf_tf="D",
        ltf_df=d_df,
        daily_df=d_df,
        unfresh_alerts=True,
        all_ltf_levels=d_levels_aud,
    )
    for a in s.alerts:
        if "2026-10-09" in str(a.alert_time) and a.breakout_type == BreakoutType.EXTERNAL:
            audcad_alert = a
            break
    if audcad_alert:
        break

# 2. Golden setup XAGUSD (Daily -> H4)
d_df_xag = apply_open_continuity(cache.load_candles("XAGUSD", "D"))
h4_df_xag = apply_open_continuity(cache.load_candles("XAGUSD", "H4"))
d_trigs_xag = detect_triggers(d_df_xag, symbol="XAGUSD", timeframe="D", asset_class="commodity")
h4_levels_xag = detect_ltf_levels(h4_df_xag, "XAGUSD", "H4", 0.0)

xagusd_alert = None
for t in d_trigs_xag:
    s = evaluate_setup_lifecycle(
        trigger=t,
        htf_df=d_df_xag,
        symbol="XAGUSD",
        htf_tf="D",
        ltf_tf="H4",
        ltf_df=h4_df_xag,
        daily_df=d_df_xag,
        unfresh_alerts=True,
        all_ltf_levels=h4_levels_xag,
    )
    for a in s.alerts:
        if "2026-10-14" in str(a.alert_time) and a.breakout_type == BreakoutType.EXTERNAL:
            xagusd_alert = a
            break
    if xagusd_alert:
        break

# 3. EURUSD (Daily -> H4, ID 4)
d_df_eur = apply_open_continuity(cache.load_candles("EURUSD", "D"))
h4_df_eur = apply_open_continuity(cache.load_candles("EURUSD", "H4"))
d_trigs_eur = detect_triggers(d_df_eur, symbol="EURUSD", timeframe="D", asset_class="forex")
h4_levels_eur = detect_ltf_levels(h4_df_eur, "EURUSD", "H4", 0.0)

eurusd_alert = None
for t in d_trigs_eur:
    s = evaluate_setup_lifecycle(
        trigger=t,
        htf_df=d_df_eur,
        symbol="EURUSD",
        htf_tf="D",
        ltf_tf="H4",
        ltf_df=h4_df_eur,
        daily_df=d_df_eur,
        unfresh_alerts=True,
        all_ltf_levels=h4_levels_eur,
    )
    for a in s.alerts:
        if "2026-10-12" in str(a.alert_time) and a.breakout_type == BreakoutType.EXTERNAL:
            eurusd_alert = a
            break
    if eurusd_alert:
        break

# 4. EURGBP (Daily -> H4, ID 1)
d_df_eg = apply_open_continuity(cache.load_candles("EURGBP", "D"))
h4_df_eg = apply_open_continuity(cache.load_candles("EURGBP", "H4"))
d_trigs_eg = detect_triggers(d_df_eg, symbol="EURGBP", timeframe="D", asset_class="forex")
h4_levels_eg = detect_ltf_levels(h4_df_eg, "EURGBP", "H4", 0.0)

eurgbp_alert = None
for t in d_trigs_eg:
    s = evaluate_setup_lifecycle(
        trigger=t,
        htf_df=d_df_eg,
        symbol="EURGBP",
        htf_tf="D",
        ltf_tf="H4",
        ltf_df=h4_df_eg,
        daily_df=d_df_eg,
        unfresh_alerts=True,
        all_ltf_levels=h4_levels_eg,
    )
    for a in s.alerts:
        if "2026-10-12" in str(a.alert_time) and a.breakout_type == BreakoutType.EXTERNAL:
            eurgbp_alert = a
            break
    if eurgbp_alert:
        break

# 5. USDJPY (Daily -> H4, ID 21)
d_df_uj = apply_open_continuity(cache.load_candles("USDJPY", "D"))
h4_df_uj = apply_open_continuity(cache.load_candles("USDJPY", "H4"))
d_trigs_uj = detect_triggers(d_df_uj, symbol="USDJPY", timeframe="D", asset_class="forex")
h4_levels_uj = detect_ltf_levels(h4_df_uj, "USDJPY", "H4", 0.0)

usdjpy_alert = None
for t in d_trigs_uj:
    s = evaluate_setup_lifecycle(
        trigger=t,
        htf_df=d_df_uj,
        symbol="USDJPY",
        htf_tf="D",
        ltf_tf="H4",
        ltf_df=h4_df_uj,
        daily_df=d_df_uj,
        unfresh_alerts=True,
        all_ltf_levels=h4_levels_uj,
    )
    for a in s.alerts:
        if "2026-10-14" in str(a.alert_time) and a.breakout_type == BreakoutType.EXTERNAL:
            usdjpy_alert = a
            break
    if usdjpy_alert:
        break

alerts_to_show = [
    ("GOLDEN SETUP 1: AUDCAD (Weekly -> Daily)", audcad_alert),
    ("GOLDEN SETUP 2: XAGUSD (Daily -> H4)", xagusd_alert),
    ("OTHER SETUP 1: EURUSD (Daily -> H4, ID 4)", eurusd_alert),
    ("OTHER SETUP 2: EURGBP (Daily -> H4, ID 1)", eurgbp_alert),
    ("OTHER SETUP 3: USDJPY (Daily -> H4, ID 21)", usdjpy_alert),
]

for title, alert in alerts_to_show:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    if alert:
        print(format_alert_message(alert))
    else:
        print("Alert not found.")
