import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from slk_bot.data.providers.crypto import CryptoProvider


def test_crypto_provider_initialization():
    p_cb = CryptoProvider(source="coinbase")
    assert p_cb.source == "coinbase"

    p_default = CryptoProvider()
    assert p_default.source == "coinbase"

    p_binance = CryptoProvider(source="binance")
    assert p_binance.source == "binance"

    with pytest.raises(ValueError):
        CryptoProvider(source="invalid_exchange")


def test_coinbase_symbol_normalization():
    assert CryptoProvider._normalize_coinbase_symbol("BTCUSD") == "BTC-USD"
    assert CryptoProvider._normalize_coinbase_symbol("ETHUSD") == "ETH-USD"
    assert CryptoProvider._normalize_coinbase_symbol("BTCUSDT") == "BTC-USDT"
    assert CryptoProvider._normalize_coinbase_symbol("ETHUSDT") == "ETH-USDT"
    assert CryptoProvider._normalize_coinbase_symbol("BTC-USD") == "BTC-USD"
    assert CryptoProvider._normalize_coinbase_symbol("SOL-EUR") == "SOL-EUR"


def test_coinbase_parse():
    # Coinbase raw candle format: [time, low, high, open, close, volume]
    sample_data = [
        [1700003600, 100.0, 110.0, 102.0, 108.0, 50.0],
        [1700000000, 95.0, 105.0, 98.0, 103.0, 45.0],
    ]
    df = CryptoProvider._coinbase_parse(sample_data, gran_sec=3600)
    assert len(df) == 2
    # Ensure ordered oldest first
    assert df.index[0] == pd.Timestamp(1700000000, unit="s", tz="UTC")
    assert df.index[1] == pd.Timestamp(1700003600, unit="s", tz="UTC")
    assert df.loc[df.index[0], "open"] == 98.0
    assert df.loc[df.index[0], "high"] == 105.0
    assert df.loc[df.index[0], "low"] == 95.0
    assert df.loc[df.index[0], "close"] == 103.0
    assert df.loc[df.index[0], "volume"] == 45.0


def test_coinbase_get_candles_mocked():
    p = CryptoProvider(source="coinbase")
    # Mock _get
    raw_response = [
        [1700003600, 100.0, 110.0, 102.0, 108.0, 50.0],
        [1700000000, 95.0, 105.0, 98.0, 103.0, 45.0],
    ]
    with patch.object(p, "_get", return_value=raw_response):
        df = p.get_candles("BTCUSD", "H1", count=2)
        assert len(df) == 2
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
