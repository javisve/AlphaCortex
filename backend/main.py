"""
AI Fund Manager Backend — Main FastAPI application.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from core.config import get_settings
from core.database import init_db
from api.routes.portfolio import router as portfolio_router

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()


# ── Startup / Shutdown ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting AI Fund Manager backend...")
    init_db()
    logger.info("Database tables ready")
    yield
    logger.info("Shutting down...")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="AI Fund Manager API",
    description="Backend for AI-driven portfolio management via MetaTrader 5",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
)

app.include_router(portfolio_router, prefix="/api/v1")


@app.get("/")
def root():
    return {"service": "AI Fund Manager", "version": "0.1.0", "status": "running"}
