import asyncio
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DailyReport, Position, PriceAlert, User, WatchlistItem
from app.services.auth import COOKIE_NAME, create_session_token, current_user, hash_password, verify_password
from app.services.calendar import previous_trading_day
from app.services.market_data import MarketDataError, get_provider, market_poll_seconds
from app.services.portfolio import calculate_position_pnl, combine_positions
from app.signals.engine import backtest, evaluate_fundamentals, evaluate_signal

router = APIRouter(prefix="/api")


def authenticated_user(request: Request) -> User:
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Sessão expirada. Entre novamente.")
    return user


def get_db():
    with SessionLocal() as db:
        yield db


class PositionInput(BaseModel):
    ticker: str = Field(min_length=4, max_length=12, pattern=r"^[A-Za-z0-9]{4,12}$")
    quantity: float = Field(gt=0, allow_inf_nan=False)
    average_price: float = Field(gt=0, allow_inf_nan=False)
    purchased_at: date
    fees: float = Field(default=0, ge=0, allow_inf_nan=False)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper()


class TickerInput(BaseModel):
    ticker: str = Field(min_length=4, max_length=12, pattern=r"^[A-Za-z0-9]{4,12}$")

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper()


class AlertInput(BaseModel):
    ticker: str = Field(min_length=4, max_length=12, pattern=r"^[A-Za-z0-9]{4,12}$")
    kind: Literal["above", "below", "drop_percent"]
    threshold: float = Field(gt=0, allow_inf_nan=False)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper()


class PasswordInput(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=72)

    @field_validator("new_password")
    @classmethod
    def password_fits_bcrypt(cls, value: str) -> str:
        if len(value.encode()) > 72:
            raise ValueError("A nova senha deve ter no máximo 72 bytes UTF-8.")
        return value


def position_rows(db: Session) -> list[dict]:
    positions = db.scalars(select(Position).order_by(Position.ticker, Position.purchased_at)).all()
    return [
        {
            "id": item.id,
            "ticker": item.ticker,
            "quantity": item.quantity,
            "average_price": item.average_price,
            "purchased_at": item.purchased_at.isoformat(),
            "fees": item.fees,
        }
        for item in positions
    ]


@router.get("/portfolio")
async def portfolio(
    _: User = Depends(authenticated_user), db: Session = Depends(get_db)
):
    rows = position_rows(db)
    combined = combine_positions(
        [{**row, "purchased_at": date.fromisoformat(row["purchased_at"])} for row in rows]
    )
    quotes_by_ticker = {}
    error = None
    if combined:
        try:
            quotes = await get_provider().get_quotes([row["ticker"] for row in combined])
            quotes_by_ticker = {quote.ticker: quote.__dict__ for quote in quotes}
        except (MarketDataError, httpx.HTTPError, ValueError) as exc:
            error = f"Cotações indisponíveis: {exc}"
    gross = 0.0
    day_result = 0.0
    results = []
    for row in combined:
        quote = quotes_by_ticker.get(row["ticker"])
        pnl = calculate_position_pnl(
            row["quantity"], row["average_price"], quote["price"], row["fees"]
        ) if quote else None
        if pnl:
            gross += pnl["current_value"]
            if quote.get("previous_close"):
                day_result += row["quantity"] * (
                    quote["price"] - quote["previous_close"]
                )
        results.append({**row, "quote": quote, "pnl": pnl})
    for item in results:
        item["weight_percent"] = (
            item["pnl"]["current_value"] / gross * 100
            if gross and item["pnl"]
            else None
        )
    previous_value = gross - day_result
    return {
        "positions": results,
        "records": rows,
        "total_value": gross if quotes_by_ticker else None,
        "day_result": day_result if quotes_by_ticker else None,
        "day_result_percent": day_result / previous_value * 100 if previous_value else 0,
        "previous_trading_day": previous_trading_day(
            datetime.now(ZoneInfo("America/Sao_Paulo")).date()
        ).isoformat(),
        "error": error,
    }


@router.post("/portfolio", status_code=201)
def add_position(
    payload: PositionInput,
    _: User = Depends(authenticated_user),
    db: Session = Depends(get_db),
):
    item = Position(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"id": item.id}


@router.put("/portfolio/{position_id}")
def update_position(
    position_id: int,
    payload: PositionInput,
    _: User = Depends(authenticated_user),
    db: Session = Depends(get_db),
):
    item = db.get(Position, position_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Posição não encontrada.")
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    db.commit()
    return {"ok": True}


@router.delete("/portfolio/{position_id}", status_code=204)
def delete_position(
    position_id: int,
    _: User = Depends(authenticated_user),
    db: Session = Depends(get_db),
):
    item = db.get(Position, position_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Posição não encontrada.")
    db.delete(item)
    db.commit()


@router.get("/watchlist")
async def watchlist(
    _: User = Depends(authenticated_user), db: Session = Depends(get_db)
):
    items = db.scalars(select(WatchlistItem).order_by(WatchlistItem.ticker)).all()
    symbols = [item.ticker for item in items]
    quotes = []
    error = None
    if symbols:
        try:
            quotes = [quote.__dict__ for quote in await get_provider().get_quotes(symbols)]
        except (MarketDataError, httpx.HTTPError, ValueError) as exc:
            error = f"Cotações indisponíveis: {exc}"
    alerts = db.scalars(select(PriceAlert).order_by(PriceAlert.ticker)).all()
    return {
        "items": [item.ticker for item in items],
        "quotes": quotes,
        "alerts": [
            {"id": alert.id, "ticker": alert.ticker, "kind": alert.kind, "threshold": alert.threshold, "enabled": alert.enabled}
            for alert in alerts
        ],
        "error": error,
    }


@router.post("/watchlist", status_code=201)
def add_watchlist(
    payload: TickerInput, _: User = Depends(authenticated_user), db: Session = Depends(get_db)
):
    if db.scalar(select(WatchlistItem).where(WatchlistItem.ticker == payload.ticker)):
        raise HTTPException(status_code=409, detail="Este ticker já está na watchlist.")
    db.add(WatchlistItem(ticker=payload.ticker))
    db.commit()
    return {"ok": True}


@router.delete("/watchlist/{ticker}", status_code=204)
def delete_watchlist(
    ticker: str, _: User = Depends(authenticated_user), db: Session = Depends(get_db)
):
    item = db.scalar(select(WatchlistItem).where(WatchlistItem.ticker == ticker.upper()))
    if item is None:
        raise HTTPException(status_code=404, detail="Ticker não encontrado na watchlist.")
    db.delete(item)
    db.commit()


@router.post("/alerts", status_code=201)
def add_alert(payload: AlertInput, _: User = Depends(authenticated_user), db: Session = Depends(get_db)):
    db.add(PriceAlert(**payload.model_dump()))
    db.commit()
    return {"ok": True}


@router.delete("/alerts/{alert_id}", status_code=204)
def delete_alert(alert_id: int, _: User = Depends(authenticated_user), db: Session = Depends(get_db)):
    item = db.get(PriceAlert, alert_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Alerta não encontrado.")
    db.delete(item)
    db.commit()


@router.get("/opportunities")
async def opportunities(
    threshold: float = Query(default=3, gt=0, allow_inf_nan=False),
    minimum_volume: int = Query(default=0, ge=0),
    sector: str = Query(default="", max_length=80),
    timeframe: Literal["day", "week", "month"] = "day",
    _: User = Depends(authenticated_user),
    db: Session = Depends(get_db),
):
    provider = get_provider()
    try:
        movers = await provider.get_movers(5)
        position_records = db.scalars(select(Position)).all()
        held_positions = combine_positions(
            [
                {
                    "ticker": item.ticker,
                    "quantity": item.quantity,
                    "average_price": item.average_price,
                    "fees": item.fees,
                    "purchased_at": item.purchased_at,
                }
                for item in position_records
            ]
        )
        held = {item["ticker"] for item in held_positions}
        held_average = {item["ticker"]: item["average_price"] for item in held_positions}
        owned_quotes = await provider.get_quotes(sorted(held)) if held else []
        items = []
        unique_quotes = {quote.ticker: quote for quote in [*movers, *owned_quotes]}
        for quote in unique_quotes.values():
            change_percent = quote.change_percent
            if timeframe != "day":
                period = "5d" if timeframe == "week" else "1mo"
                history_points = await provider.get_history(quote.ticker, period)
                closes = [float(point["close"]) for point in history_points if point.get("close")]
                change_percent = (closes[-1] / closes[0] - 1) * 100 if len(closes) > 1 else None
            is_opportunity = change_percent is not None and change_percent <= -abs(threshold)
            if quote.ticker not in held and not is_opportunity:
                continue
            if quote.ticker in held and (
                quote.average_volume or 0
            ) < minimum_volume:
                continue
            if sector and (quote.sector or "").casefold() != sector.casefold():
                continue
            fundamentals = {
                "pe_ratio": quote.pe_ratio,
                "price_to_book": quote.price_to_book,
                "dividend_yield": quote.dividend_yield,
                "roe": quote.roe,
                "net_debt_to_ebitda": quote.net_debt_to_ebitda,
                "average_volume": quote.average_volume,
            }
            fundamentals_result = evaluate_fundamentals(fundamentals)
            criteria = fundamentals_result["criteria"]
            items.append(
                {
                    "ticker": quote.ticker,
                    "price": quote.price,
                    "change_percent": change_percent,
                    "day_change_percent": quote.change_percent,
                    "in_portfolio": quote.ticker in held,
                    "average_price": held_average.get(quote.ticker),
                    "sector": quote.sector,
                    "average_volume": quote.average_volume,
                    "data_warning": quote.data_warning,
                    "fundamentals": criteria,
                    "status": fundamentals_result["status"],
                }
            )
        return {
            "items": items,
            "error": None,
            "scope_notice": "Universo observado conforme tickers e cobertura disponíveis na fonte; pode não incluir toda a B3.",
        }
    except (MarketDataError, httpx.HTTPError, ValueError) as exc:
        return {"items": [], "error": f"Oportunidades indisponíveis: {exc}"}


@router.get("/signals/{ticker}")
async def signal(ticker: str, _: User = Depends(authenticated_user)):
    provider = get_provider()
    try:
        quote, history = (await provider.get_quotes([ticker]))[0], await provider.get_history(ticker)
        return evaluate_signal(quote.__dict__, history)
    except (MarketDataError, httpx.HTTPError, ValueError, IndexError) as exc:
        raise HTTPException(status_code=503, detail=f"Não foi possível calcular o sinal: {exc}") from exc


@router.get("/backtest/{ticker}")
async def signal_backtest(ticker: str, _: User = Depends(authenticated_user)):
    try:
        history = await get_provider().get_history(ticker)
        benchmark = await get_provider().get_history("^BVSP")
        return backtest(history, benchmark)
    except (MarketDataError, httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"Não foi possível executar o backtest: {exc}") from exc


@router.get("/overview")
async def overview(_: User = Depends(authenticated_user)):
    provider = get_provider()
    result = {"quotes": {}, "selic_percent": None, "error": None}
    try:
        quotes = await provider.get_quotes(["^BVSP", "USDBRL"])
        result["quotes"] = {quote.ticker: quote.__dict__ for quote in quotes}
    except (MarketDataError, httpx.HTTPError, ValueError) as exc:
        result["error"] = f"Indicadores de mercado indisponíveis: {exc}"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(
                "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/1?formato=json"
            )
            response.raise_for_status()
            result["selic_percent"] = float(response.json()[0]["valor"])
    except (httpx.HTTPError, KeyError, ValueError, IndexError):
        result["selic_error"] = "Selic indisponível no momento."
    async def latest_focus(client: httpx.AsyncClient, indicator: str):
        try:
            response = await client.get(
                "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/ExpectativasMercadoTop5Anuais",
                params={
                    "$filter": f"Indicador eq '{indicator}' and tipoCalculo eq 'M'",
                    "$orderby": "Data desc",
                    "$top": 1,
                    "$format": "json",
                },
            )
            response.raise_for_status()
            values = response.json().get("value", [])
            if not values:
                return {"error": indicator}
            return values[0]
        except (httpx.HTTPError, ValueError, TypeError):
            return {"error": indicator}

    async with httpx.AsyncClient(timeout=8) as client:
        focus_items = await asyncio.gather(
            *(latest_focus(client, indicator) for indicator in ("IPCA", "Selic", "Câmbio"))
        )
    result["focus"] = [item for item in focus_items if "error" not in item]
    if len(result["focus"]) != len(focus_items):
        unavailable = [item["error"] for item in focus_items if "error" in item]
        result["focus_error"] = (
            "Boletim Focus indisponível para: " + ", ".join(unavailable) + "."
        )
    return result


@router.get("/market/movers")
async def market_movers(_: User = Depends(authenticated_user)):
    try:
        quotes = await get_provider().get_movers(5)
        return {
            "gainers": [quote.__dict__ for quote in quotes if (quote.change_percent or 0) >= 0],
            "losers": [quote.__dict__ for quote in quotes if (quote.change_percent or 0) < 0],
            "scope_notice": "Universo observado conforme tickers e cobertura disponíveis na fonte; pode não incluir toda a B3.",
        }
    except (MarketDataError, httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"Destaques indisponíveis: {exc}") from exc


@router.get("/history/{ticker}")
async def history(ticker: str, period: str = "1y", _: User = Depends(authenticated_user)):
    if period not in {"1d", "5d", "1mo", "6mo", "1y", "5y"}:
        raise HTTPException(status_code=422, detail="Período histórico inválido.")
    try:
        points = await get_provider().get_history(ticker, period)
        if not points:
            raise MarketDataError("A fonte não retornou histórico para este ticker.")
        return {"ticker": ticker.upper(), "history": points}
    except (MarketDataError, httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"Histórico indisponível: {exc}") from exc


@router.post("/settings/password")
def change_password(
    payload: PasswordInput,
    request: Request,
    user: User = Depends(authenticated_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="A senha atual não confere.")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="Escolha uma senha diferente da atual.")
    stored = db.get(User, user.id)
    stored.password_hash = hash_password(payload.new_password)
    stored.must_change_password = False
    db.commit()
    response = JSONResponse({"ok": True})
    response.set_cookie(
        COOKIE_NAME,
        create_session_token(stored),
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=43200,
        path="/",
    )
    return response


@router.get("/reports")
def reports(_: User = Depends(authenticated_user), db: Session = Depends(get_db)):
    entries = db.scalars(select(DailyReport).order_by(DailyReport.created_at.desc())).all()
    return [
        {"id": item.id, "created_at": item.created_at.isoformat(), "title": item.title, "content": item.content}
        for item in entries
    ]


@router.post("/reports/generate")
async def generate_report(_: User = Depends(authenticated_user)):
    from app.services.reports import create_daily_report

    return await create_daily_report()


@router.get("/alerts/fired")
async def fired_alerts(_: User = Depends(authenticated_user), db: Session = Depends(get_db)):
    alerts = db.scalars(select(PriceAlert).where(PriceAlert.enabled.is_(True))).all()
    quotes = {}
    if alerts:
        try:
            quotes = {item.ticker: item for item in await get_provider().get_quotes(sorted({a.ticker for a in alerts}))}
        except (MarketDataError, httpx.HTTPError, ValueError):
            return {"alerts": [], "error": "Não foi possível verificar os alertas: fonte indisponível."}
    triggered = []
    for alert in alerts:
        quote = quotes.get(alert.ticker)
        if not quote:
            continue
        fired = (
            alert.kind == "above" and quote.price >= alert.threshold
        ) or (
            alert.kind == "below" and quote.price <= alert.threshold
        ) or (
            alert.kind == "drop_percent" and (quote.change_percent or 0) <= -abs(alert.threshold)
        )
        if fired:
            triggered.append({"ticker": alert.ticker, "kind": alert.kind, "threshold": alert.threshold, "price": quote.price})
    return {"alerts": triggered, "error": None}


@router.websocket("/stream")
async def quote_stream(websocket: WebSocket):
    user = current_user(websocket)
    if user is None:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        while True:
            with SessionLocal() as db:
                tickers = {
                    item.ticker
                    for item in db.scalars(select(Position)).all()
                } | {
                    item.ticker
                    for item in db.scalars(select(WatchlistItem)).all()
                }
            if tickers:
                try:
                    quotes = await get_provider().get_quotes(sorted(tickers))
                    await websocket.send_json(
                        {"quotes": [quote.__dict__ for quote in quotes], "updated_at": datetime.utcnow().isoformat()}
                    )
                except (MarketDataError, httpx.HTTPError, ValueError) as exc:
                    await websocket.send_json({"error": f"Cotações indisponíveis: {exc}"})
            await asyncio.sleep(market_poll_seconds())
    except WebSocketDisconnect:
        return
