from contextlib import asynccontextmanager
from datetime import UTC, datetime
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, text


DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
templates = Jinja2Templates(directory="app/templates")


@asynccontextmanager
async def lifespan(_: FastAPI):
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    yield
    engine.dispose()


app = FastAPI(
    title="Sushi House Voice Robot",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "generated_at": datetime.now(UTC).strftime("%d.%m.%Y %H:%M:%S UTC"),
            "services": [
                {"name": "Сервер приложения", "state": "Работает", "tone": "ok"},
                {"name": "PostgreSQL", "state": "Подключено", "tone": "ok"},
                {"name": "iikoCloud", "state": "Будет подключено на этапе 3", "tone": "wait"},
                {"name": "Mango SIP", "state": "Будет подключено на этапе 4", "tone": "wait"},
            ],
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def readiness():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ready", "database": "connected"}

