from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from starlette.templating import Jinja2Templates

from app.database import SessionLocal
from app.models import User
from app.services.auth import (
    COOKIE_NAME,
    clear_login_attempts,
    create_session_token,
    login_allowed,
    record_failed_login,
    verify_password,
)


async def authenticate(request: Request, templates: Jinja2Templates):
    form = await request.form()
    username = str(form.get("username", "")).strip()[:80]
    password = str(form.get("password", ""))
    ip = request.client.host if request.client else "unknown"
    if not login_allowed(ip):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Muitas tentativas. Aguarde antes de tentar novamente."},
            status_code=429,
        )
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        if user is None or not verify_password(password, user.password_hash):
            record_failed_login(ip)
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"error": "Usuário ou senha inválidos."},
                status_code=401,
            )
        clear_login_attempts(ip)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            create_session_token(user),
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            max_age=43200,
            path="/",
        )
        return response
