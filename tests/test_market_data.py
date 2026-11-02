import asyncio
from datetime import datetime, timezone

import httpx
import pytest

import app.services.market_data as market_data
from app.services.market_data import BrapiProvider, MarketDataError


def test_quote_uses_source_timestamp_to_detect_real_delay():
    quote = BrapiProvider._quote(
        {
            "symbol": "PETR4",
            "regularMarketPrice": 35.5,
            "regularMarketChangePercent": 1.2,
            "regularMarketPreviousClose": 35.0,
            "regularMarketTime": datetime.now(timezone.utc).isoformat(),
        }
    )

    assert quote.ticker == "PETR4"
    assert quote.price == 35.5
    assert quote.delayed is False
    assert quote.delay_minutes is None
    assert quote.market_time is not None


def test_market_movers_falls_back_to_clearly_labeled_market_list():
    provider = BrapiProvider()

    async def market_list(_path, _params):
        return {
            "stocks": [
                {"stock": "ALTA3", "close": 12, "change": 5, "volume": 500000, "sector": "Finance", "type": "stock"},
                {"stock": "QUEDA3", "close": 8, "change": -4, "volume": 400000, "sector": "Energy", "type": "stock"},
                {"stock": "FUNDO11", "close": 100, "change": 10, "volume": 900000, "sector": "Finance", "type": "fund"},
            ]
        }

    async def details(_tickers):
        raise MarketDataError("A brapi.dev exige um token válido para esta consulta.")

    provider._request = market_list
    provider.get_quotes = details
    quotes = asyncio.run(provider.get_movers(1))

    assert [item.ticker for item in quotes] == ["ALTA3", "QUEDA3"]
    assert all(item.delayed and item.data_warning for item in quotes)


def test_provider_error_does_not_expose_api_token(monkeypatch):
    secret = "test-sensitive-token"
    monkeypatch.setenv("BRAPI_TOKEN", secret)
    provider = BrapiProvider()
    async_client = httpx.AsyncClient

    def handler(request):
        return httpx.Response(401, request=request)

    def client_factory(timeout):
        return async_client(
            timeout=timeout, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(
        market_data.httpx,
        "AsyncClient",
        client_factory,
    )
    with pytest.raises(MarketDataError) as error:
        asyncio.run(provider._request("/quote/PETR4"))

    assert secret not in str(error.value)
    assert "token válido" in str(error.value)
