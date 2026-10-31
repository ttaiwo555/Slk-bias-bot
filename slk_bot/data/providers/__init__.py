"""
slk_bot/data/providers/__init__.py
------------------------------------
Convenience re-exports so other modules can do:
    from slk_bot.data.providers import OandaProvider, CryptoProvider
"""
from slk_bot.data.providers.base import DataProvider
from slk_bot.data.providers.oanda import OandaProvider
from slk_bot.data.providers.crypto import CryptoProvider

__all__ = ["DataProvider", "OandaProvider", "CryptoProvider"]
