import pandas as pd
from dotenv import load_dotenv
from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.candles import apply_open_continuity

load_dotenv()
p = OandaProvider()

tv_targets = {
    "NAS100": {
        "fri_17": {"open": 29416.9, "close": 29196.4},
        "sun_21": {"open": 29196.4, "close": 29437.2},
        "tol": 0.0015,
        "sym": "NAS100_USD"
    },
    "SPX500": {
        "fri_17": {"open": 7689.1, "close": 7658.9},
        "sun_21": {"open": 7658.9, "close": 7694.6},
        "tol": 0.0015,
        "sym": "SPX500_USD"
    },
    "US30": {
        "fri_17": {"open": 52069.1, "close": 51976.1},
        "sun_21": {"open": 51976.1, "close": 52070.1},
        "tol": 0.0015,
        "sym": "US30_USD"
    },
    "GER40": {
        "fri_17": {"open": 25528.5, "close": 25464.5},
        "sun_21": {"open": 25464.5, "close": 25533.5},
        "tol": 0.0015,
        "sym": "DE30_EUR"
    },
    "UK100": {
        "fri_17": {"open": 10845.4, "close": 10841.6},
        "sun_21": {"open": 10841.6, "close": 10859.2},
        "tol": 0.0015,
        "sym": "UK100_GBP"
    },
    "USOIL": {
        "fri_20": {"open": 9894.1, "close": 9905.1},
        "sun_22": {"open": 9905.1, "close": 9768.5},
        "tol": 0.0010,
        "sym": "WTICO_USD",
        "scale": 100.0
    }
}

print("Comparing OANDA H4 candles against TradingView targets...")

for name, info in tv_targets.items():
    h1 = p.get_candles(info["sym"], "H1", count=200)
    scale = info.get("scale", 1.0)
    
    print(f"\n--- {name} ---")
    if name == "USOIL":
        # Friday 20:00 candle = H1 candle 20:00 (since 20:00 to 21:00 close)
        c_fri = h1.loc["2026-10-16 20:00:00+00:00"]
        o_fri = c_fri["open"] * scale
        c_fri_val = c_fri["close"] * scale
        
        target_fri = info["fri_20"]
        diff_o_fri = abs(o_fri - target_fri["open"]) / target_fri["open"] * 100
        diff_c_fri = abs(c_fri_val - target_fri["close"]) / target_fri["close"] * 100
        
        print(f"Fri 20:00: Bot O={o_fri:.1f}, C={c_fri_val:.1f} | TV O={target_fri['open']}, C={target_fri['close']}")
        print(f"  Diff O: {diff_o_fri:.3f}%, Diff C: {diff_c_fri:.3f}% (tol {info['tol']*100:.2f}%)")

        # Sunday 22:00 candle (22:00 to 00:00 or 22:00 to 02:00)
        # In TV: Sun 18 Oct 22:00 open 9905.1 close 9768.5
        # Let's check hours 22:00 and 23:00, or 22:00 to 02:00
        c_sun_2h = h1.loc["2026-10-18 22:00:00+00:00":"2026-10-18 23:00:00+00:00"]
        o_sun_2h = c_sun_2h.iloc[0]["open"] * scale
        c_sun_2h_val = c_sun_2h.iloc[-1]["close"] * scale

        c_sun_4h = h1.loc["2026-10-18 22:00:00+00:00":"2026-10-19 01:00:00+00:00"]
        o_sun_4h = c_sun_4h.iloc[0]["open"] * scale
        c_sun_4h_val = c_sun_4h.iloc[-1]["close"] * scale

        print(f"Sun 22:00 (2h slot to 00:00 UTC): O={o_sun_2h:.1f}, C={c_sun_2h_val:.1f}")
        print(f"Sun 22:00 (4h slot to 02:00 UTC): O={o_sun_4h:.1f}, C={c_sun_4h_val:.1f}")
        print(f"TV Target Sun 22:00              : O={info['sun_22']['open']}, C={info['sun_22']['close']}")

    else:
        # Friday 17:00 UTC H4 = H1 hours 17, 18, 19, 20
        c_fri = h1.loc["2026-10-16 17:00:00+00:00":"2026-10-16 20:00:00+00:00"]
        o_fri = c_fri.iloc[0]["open"]
        c_fri_val = c_fri.iloc[-1]["close"]
        target_fri = info["fri_17"]

        diff_o_fri = abs(o_fri - target_fri["open"]) / target_fri["open"] * 100
        diff_c_fri = abs(c_fri_val - target_fri["close"]) / target_fri["close"] * 100
        print(f"Fri 17:00: Bot O={o_fri:.1f}, C={c_fri_val:.1f} | TV O={target_fri['open']}, C={target_fri['close']}")
        print(f"  Diff O: {diff_o_fri:.3f}%, Diff C: {diff_c_fri:.3f}% (tol {info['tol']*100:.2f}%)")

        # Sunday 21:00 UTC H4:
        # Check what hours exist on Sunday night in OANDA
        sun_h1 = h1.loc["2026-10-18 20:00:00+00:00":"2026-10-19 01:00:00+00:00"]
        print(f"Sunday hours in OANDA: {list(sun_h1.index.strftime('%H:%M'))}")
        target_sun = info["sun_21"]
        if not sun_h1.empty:
            o_sun = sun_h1.iloc[0]["open"]
            # With open continuity:
            o_sun_cont = c_fri_val
            c_sun_val = sun_h1.iloc[-1]["close"]
            print(f"Sun 21:00: Bot O(raw)={o_sun:.1f}, O(cont)={o_sun_cont:.1f} | TV O={target_sun['open']}, C={target_sun['close']}")
