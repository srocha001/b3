import asyncio
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Protocol

import httpx


@dataclass
class Quote:
    ticker: str
    price: float
    change_percent: float | None
    previous_close: float | None
    volume: int | None
    fifty_two_week_low: float | None
    fifty_two_week_high: float | None
    pe_ratio: float | None
    dividend_yield: float | None
    price_to_book: float | None
    roe: float | None
    net_debt_to_ebitda: float | None
    average_volume: int | None
    sector: str | None
    sparkline: list[float] | None
    delay_minutes: int | None
    market_time: str | None
    data_warning: str | None
    fetched_at: str
    delayed: bool


class DataProvider(Protocol):
    async def get_quotes(self, tickers: list[str]) -> list[Quote]: ...
    async def get_history(self, ticker: str, period: str = "1y") -> list[dict]: ...
    async def get_movers(self, limit: int = 10) -> list[Quote]: ...


class BrapiProvider:
    base_url = "https://brapi.dev/api"

    def __init__(self):
        self.token = os.getenv("BRAPI_TOKEN", "")
        self.cache: dict[str, tuple[float, Quote]] = {}
        self.history_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}
        self.quote_errors: dict[tuple[str, ...], tuple[float, str]] = {}
        self.movers_cache: dict[int, tuple[float, list[Quote]]] = {}
        self.cache_ttl = 60

    async def _request(self, path: str, params: dict | None = None) -> dict:
        params = dict(params or {})
        if self.token:
            params["token"] = self.token
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                response = await client.get(f"{self.base_url}{path}", params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in (401, 403):
                    message = "A brapi.dev exige um token válido para esta consulta."
                elif status == 429:
                    message = "Limite de consultas da brapi.dev atingido; tente novamente mais tarde."
                else:
                    message = f"A brapi.dev respondeu com HTTP {status}."
                raise MarketDataError(message) from exc
            except httpx.TimeoutException as exc:
                raise MarketDataError("A consulta à brapi.dev excedeu o tempo limite.") from exc
            except httpx.RequestError as exc:
                raise MarketDataError("Não foi possível conectar à brapi.dev.") from exc

    @staticmethod
    def _quote(item: dict) -> Quote:
        fundamental = item.get("defaultKeyStatistics") or {}
        financial = item.get("financialData") or {}
        market_time = item.get("regularMarketTime")
        try:
            market_datetime = datetime.fromisoformat(
                market_time.replace("Z", "+00:00")
            ) if market_time else None
        except ValueError:
            market_datetime = None
        if market_datetime and market_datetime.tzinfo is None:
            market_datetime = market_datetime.replace(tzinfo=timezone.utc)
        delay = (
            max(0, int((datetime.now(timezone.utc) - market_datetime).total_seconds() / 60))
            if market_datetime
            else None
        )
        market_state = item.get("marketState")
        delayed = (
            market_state != "REGULAR"
            if market_state is not None
            else delay is None or delay > 15
        )
        return Quote(
            ticker=str(item.get("symbol") or item.get("stock") or "").upper(),
            price=float(item.get("regularMarketPrice", item.get("close"))),
            change_percent=_number(
                item.get("regularMarketChangePercent", item.get("change"))
            ),
            previous_close=_number(item.get("regularMarketPreviousClose")),
            volume=_integer(item.get("regularMarketVolume", item.get("volume"))),
            fifty_two_week_low=_number(item.get("fiftyTwoWeekLow")),
            fifty_two_week_high=_number(item.get("fiftyTwoWeekHigh")),
            pe_ratio=_number(item.get("priceEarnings")),
            dividend_yield=_number(item.get("dividendYield")),
            price_to_book=_number(item.get("priceToBook")),
            roe=_number(financial.get("returnOnEquity")),
            net_debt_to_ebitda=_number(financial.get("netDebtToEbitda")),
            average_volume=_integer(item.get("averageDailyVolume3Month")),
            sector=(item.get("summaryProfile") or {}).get("sector") or item.get("sector"),
            sparkline=[
                float(point["close"])
                for point in item.get("historicalDataPrice", [])
                if point.get("close") is not None
            ],
            fetched_at=datetime.now(timezone.utc).isoformat(),
            delayed=delayed,
            delay_minutes=delay if delayed else None,
            market_time=market_time,
            data_warning=(
                "A lista de mercado não informa o horário individual da cotação."
                if market_time is None
                else None
            ),
        )

    async def get_quotes(self, tickers: list[str]) -> list[Quote]:
        symbols = sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()})
        if not symbols:
            return []
        now = time.monotonic()
        fresh = {
            symbol: self.cache[symbol][1]
            for symbol in symbols
            if symbol in self.cache and now - self.cache[symbol][0] < self.cache_ttl
        }
        missing = [symbol for symbol in symbols if symbol not in fresh]
        if missing:
            request_key = tuple(missing)
            failed = self.quote_errors.get(request_key)
            if failed and now - failed[0] < self.cache_ttl:
                raise MarketDataError(failed[1])
            try:
                data = await self._request(
                    f"/quote/{','.join(missing)}",
                    {"fundamental": "true", "range": "1d", "interval": "5m"},
                )
                received = [self._quote(item) for item in data.get("results", [])]
                for quote in received:
                    self.cache[quote.ticker] = (time.monotonic(), quote)
                    fresh[quote.ticker] = quote
                self.quote_errors.pop(request_key, None)
            except (MarketDataError, httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                if not fresh:
                    self.quote_errors[request_key] = (
                        time.monotonic(),
                        str(exc)
                        if isinstance(exc, MarketDataError)
                        else "Resposta inválida da fonte de cotação.",
                    )
                for symbol in missing:
                    if symbol in self.cache:
                        cached_at, stale = self.cache[symbol]
                        age = max(0, int((time.monotonic() - cached_at) / 60))
                        fresh[symbol] = Quote(
                            **{**asdict(stale), "delayed": True, "delay_minutes": age}
                        )
                if not fresh:
                    raise
        absent = [symbol for symbol in symbols if symbol not in fresh]
        if absent:
            raise MarketDataError(f"Sem cotação retornada para: {', '.join(absent)}.")
        return [fresh[symbol] for symbol in symbols]

    async def get_history(self, ticker: str, period: str = "1y") -> list[dict]:
        cache_key = (ticker.upper(), period)
        cached = self.history_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        interval = "5m" if period == "1d" else "15m" if period == "5d" else "1d"
        data = await self._request(
            f"/quote/{ticker.upper()}", {"range": period, "interval": interval}
        )
        history = data.get("results", [{}])[0].get("historicalDataPrice", [])
        points = [
            {"date": point.get("date"), "close": point.get("close"), "volume": point.get("volume")}
            for point in history
            if point.get("close") is not None
        ]
        self.history_cache[cache_key] = (time.monotonic(), points)
        return points

    async def get_movers(self, limit: int = 10) -> list[Quote]:
        cached = self.movers_cache.get(limit)
        if cached and time.monotonic() - cached[0] < self.cache_ttl:
            return list(cached[1])
        data = await self._request(
            "/quote/list",
            {"sortBy": "volume", "sortOrder": "desc", "limit": 100},
        )
        listed_quotes = [
            self._quote(item)
            for item in data.get("stocks", data.get("results", []))
            if item.get("close") is not None and item.get("type", "stock") == "stock"
        ]
        gainers = sorted(
            (quote for quote in listed_quotes if (quote.change_percent or 0) >= 0),
            key=lambda quote: quote.change_percent or 0,
            reverse=True,
        )[:limit]
        losers = sorted(
            (quote for quote in listed_quotes if (quote.change_percent or 0) < 0),
            key=lambda quote: quote.change_percent or 0,
        )[:limit]
        selected = {quote.ticker: quote for quote in [*gainers, *losers]}
        listed_quotes = list(selected.values())
        try:
            details = await self.get_quotes([quote.ticker for quote in listed_quotes])
            detail_by_symbol = {quote.ticker: quote for quote in details}
        except (MarketDataError, httpx.HTTPError) as exc:
            for quote in listed_quotes:
                quote.delayed = True
                quote.data_warning = (
                    "Resumo da lista de mercado; detalhes indisponíveis: "
                    f"{exc}"
                )
            detail_by_symbol = {}
        quotes = []
        for listed in listed_quotes:
            detailed = detail_by_symbol.get(listed.ticker, listed)
            detailed.change_percent = listed.change_percent
            detailed.sector = detailed.sector or listed.sector
            detailed.volume = detailed.volume or listed.volume
            detailed.data_warning = (
                detailed.data_warning
                if detailed.market_time
                else listed.data_warning
            )
            quotes.append(detailed)
        if not quotes:
            raise MarketDataError("A fonte não retornou os destaques do pregão.")
        positive = sorted(
            (quote for quote in quotes if (quote.change_percent or 0) >= 0),
            key=lambda quote: quote.change_percent or 0,
            reverse=True,
        )[:limit]
        negative = sorted(
            (quote for quote in quotes if (quote.change_percent or 0) < 0),
            key=lambda quote: quote.change_percent or 0,
        )[:limit]
        result = positive + negative
        self.movers_cache[limit] = (time.monotonic(), result)
        return list(result)


class YFinanceProvider:
    async def get_quotes(self, tickers: list[str]) -> list[Quote]:
        return await asyncio.to_thread(self._quotes_sync, tickers)

    @staticmethod
    def _quotes_sync(tickers: list[str]) -> list[Quote]:
        from requests.exceptions import RequestException
        import yfinance as yf
        from yfinance.exceptions import YFException

        result = []
        for ticker in tickers:
            upper_ticker = ticker.upper()
            symbol = {
                "^BVSP": "^BVSP",
                "USDBRL": "BRL=X",
            }.get(upper_ticker, upper_ticker if upper_ticker.endswith(".SA") else f"{upper_ticker}.SA")
            try:
                asset = yf.Ticker(symbol)
                info = asset.fast_info
                fundamentals = asset.info if upper_ticker not in {"^BVSP", "USDBRL"} else {}
                price = info.get("last_price")
            except (RequestException, YFException) as exc:
                raise MarketDataError(f"Yahoo Finance indisponível para {ticker}: {exc}") from exc
            if price is None:
                continue
            previous = info.get("previous_close")
            result.append(
                Quote(
                    ticker=ticker.upper(),
                    price=float(price),
                    change_percent=((float(price) / float(previous) - 1) * 100)
                    if previous
                    else None,
                    previous_close=_number(previous),
                    volume=_integer(info.get("last_volume")),
                    fifty_two_week_low=_number(info.get("year_low")),
                    fifty_two_week_high=_number(info.get("year_high")),
                    pe_ratio=_number(fundamentals.get("trailingPE")),
                    dividend_yield=_number(fundamentals.get("dividendYield")),
                    price_to_book=_number(fundamentals.get("priceToBook")),
                    roe=_number(fundamentals.get("returnOnEquity")),
                    net_debt_to_ebitda=_number(fundamentals.get("netDebtToEbitda")),
                    average_volume=_integer(info.get("three_month_average_volume")),
                    sector=fundamentals.get("sector"),
                    sparkline=None,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    delayed=True,
                    delay_minutes=None,
                    market_time=(
                        info.get("last_trade_time").isoformat()
                        if info.get("last_trade_time")
                        else None
                    ),
                    data_warning="Yahoo Finance fornece cotações com atraso.",
                )
            )
        if not result:
            raise MarketDataError("O provedor alternativo não retornou cotações.")
        return result

    async def get_history(self, ticker: str, period: str = "1y") -> list[dict]:
        return await asyncio.to_thread(self._history_sync, ticker, period)

    @staticmethod
    def _history_sync(ticker: str, period: str) -> list[dict]:
        from requests.exceptions import RequestException
        import yfinance as yf
        from yfinance.exceptions import YFException

        upper_ticker = ticker.upper()
        symbol = {
            "^BVSP": "^BVSP",
            "USDBRL": "BRL=X",
        }.get(upper_ticker, upper_ticker if upper_ticker.endswith(".SA") else f"{upper_ticker}.SA")
        try:
            interval = "5m" if period == "1d" else "15m" if period == "5d" else "1d"
            frame = yf.Ticker(symbol).history(period=period, interval=interval)
        except (RequestException, YFException) as exc:
            raise MarketDataError(f"Yahoo Finance indisponível para {ticker}: {exc}") from exc
        return [
            {"date": str(index.date()), "close": float(row["Close"]), "volume": int(row["Volume"])}
            for index, row in frame.iterrows()
        ]

    async def get_movers(self, limit: int = 10) -> list[Quote]:
        symbols = [
            item.strip().upper()
            for item in os.getenv("YFINANCE_TICKERS", "").split(",")
            if item.strip()
        ]
        if not symbols:
            raise MarketDataError(
                "Configure YFINANCE_TICKERS para calcular maiores altas e quedas no provedor alternativo."
            )
        quotes = await self.get_quotes(symbols)
        gainers = sorted(
            (quote for quote in quotes if (quote.change_percent or 0) >= 0),
            key=lambda quote: quote.change_percent or 0,
            reverse=True,
        )[:limit]
        losers = sorted(
            (quote for quote in quotes if (quote.change_percent or 0) < 0),
            key=lambda quote: quote.change_percent or 0,
        )[:limit]
        return gainers + losers


class MarketDataError(RuntimeError):
    pass


def _number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _integer(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


_provider: DataProvider | None = None


def get_provider() -> DataProvider:
    global _provider
    if _provider is None:
        provider_name = os.getenv("MARKET_DATA_PROVIDER", "brapi").lower()
        if provider_name == "yfinance":
            _provider = YFinanceProvider()
        elif provider_name == "brapi":
            _provider = BrapiProvider()
        else:
            raise ValueError("MARKET_DATA_PROVIDER deve ser brapi ou yfinance.")
    return _provider


def validate_market_configuration():
    if os.getenv("MARKET_DATA_PROVIDER", "brapi").lower() not in {"brapi", "yfinance"}:
        raise RuntimeError("MARKET_DATA_PROVIDER deve ser brapi ou yfinance.")
    try:
        int(os.getenv("MARKET_POLL_SECONDS", "60"))
    except ValueError as exc:
        raise RuntimeError("MARKET_POLL_SECONDS deve ser um número inteiro.") from exc


def market_poll_seconds() -> int:
    return max(15, int(os.getenv("MARKET_POLL_SECONDS", "60")))
