from slk_bot.data.providers.oanda import OandaProvider
from dotenv import load_dotenv
import pandas as pd

load_dotenv()
p = OandaProvider()
# Fetch H1 around 2026-09-13 to 2026-09-28
candles = p.fetch_candles('AUD_CAD', 'H1', count=1000)
df = pd.DataFrame(candles)
df['open_time'] = pd.to_datetime(df['open_time'])
df.set_index('open_time', inplace=True)
sub = df.loc['2026-09-20 20:00:00+00:00':'2026-09-21 02:00:00+00:00']
print("H1 around 2026-09-20 open:")
print(sub[['open', 'high', 'low', 'close']])
