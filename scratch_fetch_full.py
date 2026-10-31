from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.providers.crypto import CryptoProvider
from slk_bot.data.candles import apply_open_continuity
from slk_bot.storage.cache import CandleCache

cache = CandleCache()
o = OandaProvider()
c = CryptoProvider(source='bybit')

print("Fetching full lookback history and caching...")

for sym, name in [('EUR_USD', 'EURUSD'), ('XAU_USD', 'XAUUSD'), ('NAS100_USD', 'NAS100')]:
    for tf, count in [('W', 150), ('D', 350), ('H4', 400)]:
        df = o.get_candles(sym, tf, count=count)
        df = apply_open_continuity(df)
        cache.save_candles(name, tf, df)
        cnt, oldest, newest = cache.get_stats(name, tf)
        oldest_str = oldest.strftime("%Y-%m-%d %H:%M UTC") if oldest else "N/A"
        newest_str = newest.strftime("%Y-%m-%d %H:%M UTC") if newest else "N/A"
        print(f"{name:7s} {tf:2s}: count={cnt:4d} | oldest: {oldest_str} | newest: {newest_str}")

for tf, count in [('W', 150), ('D', 350), ('H4', 400)]:
    df = c.get_candles('BTCUSDT', tf, count=count)
    cache.save_candles('BTCUSD', tf, df)
    cnt, oldest, newest = cache.get_stats('BTCUSD', tf)
    oldest_str = oldest.strftime("%Y-%m-%d %H:%M UTC") if oldest else "N/A"
    newest_str = newest.strftime("%Y-%m-%d %H:%M UTC") if newest else "N/A"
    print(f"{'BTCUSD':7s} {tf:2s}: count={cnt:4d} | oldest: {oldest_str} | newest: {newest_str}")
