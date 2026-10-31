from slk_bot.data.providers.oanda import OandaProvider
from dotenv import load_dotenv
import pandas as pd

load_dotenv()
p = OandaProvider()
df = p.get_candles('AUD_CAD', 'W', count=10)
print(df)
