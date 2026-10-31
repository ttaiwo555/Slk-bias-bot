import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from slk_bot.data.providers.crypto import CryptoProvider
from slk_bot.data.candles import resample

def main():
    crypto = CryptoProvider()
    h1 = crypto.get_candles('BTCUSDT', 'H1', since=pd.Timestamp('2026-10-01', tz='UTC'))
    print(f'Fetched {len(h1)} H1 candles for BTCUSDT')

    # Resample to crypto_ny17
    prof_ny17 = {
        'name': 'crypto_ny17',
        'daily_open': '17:00',
        'tz': 'America/New_York',
        'weekly_anchor_day': 6,
        'h4_anchor': '17:00',
        'h4_timezone': 'America/New_York',
        'open_continuity': False,
    }
    d_ny17 = resample(h1, 'D', prof_ny17)
    print('BTCUSD D (ny17) around 10-12 Oct:')
    for ts in d_ny17.loc['2026-10-09':'2026-10-12'].index:
        print(ts, 'open:', d_ny17.loc[ts, 'open'], 'close:', d_ny17.loc[ts, 'close'])

    # Resample to crypto (UTC)
    prof_utc = {
        'name': 'crypto',
        'daily_open': '00:00',
        'tz': 'UTC',
        'weekly_anchor_day': 0,
        'h4_anchor': '00:00',
        'h4_timezone': 'UTC',
        'open_continuity': False,
    }
    d_utc = resample(h1, 'D', prof_utc)
    print('\nBTCUSDT D (UTC) around 15-17 Oct:')
    for ts in d_utc.loc['2026-10-15':'2026-10-17'].index:
        print(ts, 'open:', d_utc.loc[ts, 'open'], 'close:', d_utc.loc[ts, 'close'])

if __name__ == '__main__':
    main()
