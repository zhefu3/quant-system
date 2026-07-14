"""Market data adapters. `make_adapter` is the live path's dispatch point."""

from __future__ import annotations


def make_adapter(market: str):
    """Adapter for a preset's market. Imports are lazy so that optional
    dependencies (ccxt, akshare) are only required by the market in use."""
    if market == "cnfutures":
        from .cn_futures_ak import CnFuturesAdapter

        return CnFuturesAdapter()
    if market == "futures_ibkr":
        from .futures_ib import IbkrFuturesAdapter

        return IbkrFuturesAdapter()
    if market in ("us", "us_etf"):
        from .us_yfinance import USAdapter

        return USAdapter()
    from .crypto_ccxt import CryptoAdapter

    return CryptoAdapter()
