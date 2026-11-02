import asyncio
import logging
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select

from app.database import SessionLocal
from app.services.calendar import is_trading_day, previous_trading_day
from app.models import DailyReport, Position, PriceAlert
from app.services.market_data import MarketDataError, get_provider
from app.services.portfolio import calculate_position_pnl, combine_positions
from app.signals.engine import evaluate_fundamentals

logger = logging.getLogger(__name__)
TIMEZONE = ZoneInfo("America/Sao_Paulo")
scheduler = BackgroundScheduler(timezone=TIMEZONE)


async def _report_content() -> str:
    sections = [
        f"Relatório B3 Monitor — {datetime.now(TIMEZONE):%d/%m/%Y %H:%M}",
        "Sinais são estudo e análise de dados, não recomendação de investimento. Nenhum sinal garante resultado.",
    ]
    provider = get_provider()
    try:
        quotes = await provider.get_quotes(["^BVSP", "USDBRL"])
        sections.append("Mercado: " + "; ".join(f"{item.ticker}: {item.price:.0f} pts" if item.ticker == "^BVSP" else f"{item.ticker}: R$ {item.price:.2f} ({item.change_percent or 0:+.2f}%)" for item in quotes))
    except (MarketDataError, httpx.HTTPError, ValueError) as exc:
        sections.append(f"Mercado indisponível: {exc}")

    with SessionLocal() as db:
        records = db.scalars(select(Position).order_by(Position.ticker, Position.purchased_at)).all()
        alerts = db.scalars(select(PriceAlert).where(PriceAlert.enabled.is_(True))).all()
        positions = combine_positions(
            [
                {
                    "ticker": item.ticker,
                    "quantity": item.quantity,
                    "average_price": item.average_price,
                    "fees": item.fees,
                    "purchased_at": item.purchased_at,
                }
                for item in records
            ]
        )
        if positions:
            try:
                quotes = {
                    quote.ticker: quote
                    for quote in await provider.get_quotes([item["ticker"] for item in positions])
                }
                total = 0.0
                for item in positions:
                    quote = quotes.get(item["ticker"])
                    if not quote:
                        continue
                    pnl = calculate_position_pnl(item["quantity"], item["average_price"], quote.price, item["fees"])
                    total += pnl["result"]
                    sections.append(
                        f"{item['ticker']}: {item['quantity']:g} ações; preço R$ {quote.price:.2f}; resultado R$ {pnl['result']:.2f} ({pnl['result_percent']:+.2f}%)."
                    )
                sections.append(f"Resultado não realizado da carteira: R$ {total:.2f}.")
                previous_day = previous_trading_day(datetime.now(TIMEZONE).date())
                for position in records:
                    quote = quotes.get(position.ticker)
                    if position.purchased_at == previous_day and quote:
                        change = (quote.price / position.average_price - 1) * 100
                        movement = "subiu" if change >= 0 else "caiu"
                        sections.append(
                            f"Compra do pregão anterior — {position.ticker}: {movement} {abs(change):.2f}% desde o preço médio."
                        )
            except (MarketDataError, httpx.HTTPError, ValueError) as exc:
                sections.append(f"Carteira: cotações indisponíveis ({exc}).")
        else:
            sections.append("Carteira: nenhuma posição cadastrada.")
        sections.append(f"Alertas configurados: {len(alerts)}.")
        if alerts:
            try:
                alert_quotes = {
                    quote.ticker: quote
                    for quote in await provider.get_quotes(sorted({alert.ticker for alert in alerts}))
                }
                fired = []
                for alert in alerts:
                    quote = alert_quotes.get(alert.ticker)
                    if quote and (
                        (alert.kind == "above" and quote.price >= alert.threshold)
                        or (alert.kind == "below" and quote.price <= alert.threshold)
                        or (alert.kind == "drop_percent" and (quote.change_percent or 0) <= -abs(alert.threshold))
                    ):
                        fired.append(f"{alert.ticker}: preço R$ {quote.price:.2f}, regra {alert.kind} {alert.threshold:g}.")
                sections.append("Alertas disparados: " + ("; ".join(fired) if fired else "nenhum."))
            except (MarketDataError, httpx.HTTPError, ValueError) as exc:
                sections.append(f"Alertas disparados indisponíveis: {exc}")
    try:
        movers = await provider.get_movers(5)
        sections.append("Maiores altas: " + (", ".join(f"{item.ticker} {item.change_percent:+.2f}%" for item in movers if (item.change_percent or 0) >= 0) or "indisponíveis."))
        sections.append("Maiores quedas: " + (", ".join(f"{item.ticker} {item.change_percent:+.2f}%" for item in movers if (item.change_percent or 0) < 0) or "indisponíveis."))
        opportunities = []
        for quote in movers:
            if (quote.change_percent or 0) <= -3:
                status = evaluate_fundamentals(
                    {
                        "pe_ratio": quote.pe_ratio,
                        "price_to_book": quote.price_to_book,
                        "dividend_yield": quote.dividend_yield,
                        "roe": quote.roe,
                        "net_debt_to_ebitda": quote.net_debt_to_ebitda,
                        "average_volume": quote.average_volume,
                    }
                )["status"]
                opportunities.append(f"{quote.ticker} {quote.change_percent:+.2f}% — {status}")
        sections.append("Oportunidades (queda diária de ao menos 3%): " + ("; ".join(opportunities) if opportunities else "nenhuma identificada."))
    except (MarketDataError, httpx.HTTPError, ValueError) as exc:
        sections.append(f"Maiores altas e quedas indisponíveis: {exc}")
    return "\n".join(sections)


def _send_email(title: str, content: str) -> str | None:
    host = os.getenv("SMTP_HOST", "")
    recipient = os.getenv("REPORT_EMAIL_TO", "")
    if not host or not recipient:
        return None
    message = EmailMessage()
    message["Subject"] = title
    message["From"] = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER", "")
    message["To"] = recipient
    message.set_content(content)
    try:
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=15) as server:
            server.starttls()
            username = os.getenv("SMTP_USER", "")
            if username:
                server.login(username, os.getenv("SMTP_PASSWORD", ""))
            server.send_message(message)
        return None
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        logger.exception("Falha ao enviar relatório por e-mail")
        return f"Falha no envio de e-mail: {exc}"


async def _send_telegram(content: str) -> str | None:
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": content[:4000]},
            )
            response.raise_for_status()
        return None
    except httpx.HTTPError as exc:
        logger.error("Falha ao enviar relatório pelo Telegram (%s)", type(exc).__name__)
        return "Falha no envio ao Telegram; verifique token, conexão e logs do serviço."


async def create_daily_report():
    content = await _report_content()
    title = f"Relatório diário — {datetime.now(TIMEZONE):%d/%m/%Y}"
    with SessionLocal() as db:
        report = DailyReport(title=title, content=content)
        db.add(report)
        db.commit()
        db.refresh(report)
        report_id = report.id
    email_error = await asyncio.to_thread(_send_email, title, content)
    telegram_error = await _send_telegram(content)
    errors = [error for error in (email_error, telegram_error) if error]
    return {
        "id": report_id,
        "title": title,
        "content": content,
        "notifications": "failed" if errors else "sent or not configured",
        "errors": errors,
    }


def _scheduled_report():
    if not is_trading_day(datetime.now(TIMEZONE).date()):
        return
    try:
        asyncio.run(create_daily_report())
    except (OSError, RuntimeError, ValueError, SQLAlchemyError):
        logger.exception("Falha ao gerar relatório agendado")


def _schedule_time(variable: str, default: str) -> tuple[int, int]:
    try:
        hour, minute = os.getenv(variable, default).split(":", maxsplit=1)
        hour_int, minute_int = int(hour), int(minute)
        if not (0 <= hour_int <= 23 and 0 <= minute_int <= 59):
            raise ValueError
        return hour_int, minute_int
    except ValueError as exc:
        raise RuntimeError(f"Horário inválido em {variable}; use HH:MM.") from exc


def start_scheduler():
    if scheduler.running:
        return
    for variable, default, job_id in (
        ("REPORT_BEFORE_OPEN", "09:30", "before-open"),
        ("REPORT_AFTER_CLOSE", "18:30", "after-close"),
    ):
        hour, minute = _schedule_time(variable, default)
        scheduler.add_job(
            _scheduled_report,
            "cron",
            day_of_week="mon-fri",
            hour=hour,
            minute=minute,
            id=job_id,
            replace_existing=True,
        )
    scheduler.start()


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
