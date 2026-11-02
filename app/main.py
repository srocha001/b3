from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import router as api_router
from app.database import init_db
from app.services.auth import current_user, validate_secret_key
from app.services.market_data import validate_market_configuration

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_secret_key()
    validate_market_configuration()
    init_db()
    from app.services.reports import start_scheduler, stop_scheduler

    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="B3 Monitor", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(api_router)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"user": user, "active_page": "dashboard"},
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request=request, name="login.html", context={"error": None}
    )


@app.post("/login")
async def login(request: Request):
    from app.api.auth import authenticate

    return await authenticate(request, templates)


@app.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("b3_session", path="/")
    return response


@app.get("/health")
async def health():
    return {"status": "ok"}
