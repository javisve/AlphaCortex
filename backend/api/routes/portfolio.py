"""
Portfolio review endpoint — the core API called by the EA.
"""
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from api.schemas import PortfolioReviewRequest, PortfolioReviewResponse, AssetDecision
from core.config import get_settings
from core.database import get_db
from core.cache import get_redis
from models.evaluation import PortfolioEvaluation
from services.screener import screen_symbols, ALL_SYMBOLS
from services.ai_manager import get_portfolio_allocation

# Top 50 most liquid US stocks — fast default for screening
# Covers mega-caps across all sectors for good AI decision diversity
TOP50_WATCHLIST = [
    # Tech
    "AAPL","MSFT","NVDA","GOOGL","META","AMZN","TSLA","AVGO","AMD","INTC",
    # Finance
    "JPM","BAC","GS","MS","V","MA","BLK","AXP","C","WFC",
    # Healthcare
    "LLY","JNJ","UNH","ABT","MRK","PFE","ABBV","MDT","TMO","DHR",
    # Energy / Commodities
    "XOM","CVX","COP","SLB","OXY",
    # Consumer / Retail
    "HD","WMT","COST","MCD","SBUX",
    # Industrials / Other
    "CAT","DE","BA","GE","HON","RTX","UPS","FDX",
    # ETFs (broad market exposure)
    "SPY","QQQ",
]

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()

# Use WATCHLIST from .env if available, otherwise fallback to TOP50_WATCHLIST
if settings.watchlist:
    ACTIVE_WATCHLIST = [s.strip() for s in settings.watchlist.split(",") if s.strip()]
    logger.info(f"Using custom watchlist from .env: {len(ACTIVE_WATCHLIST)} symbols")
else:
    # Use the one defined in screener.py or fallback
    from services.screener import TOP50_WATCHLIST
    ACTIVE_WATCHLIST = TOP50_WATCHLIST
    logger.info("Using default TOP50 watchlist from screener.py")

CACHE_TTL_SECONDS = 3600  # 1 hour — don't re-evaluate if called again within an hour


def _verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


@router.post("/portfolio/review", response_model=PortfolioReviewResponse)
async def portfolio_review(
    request: PortfolioReviewRequest,
    db: Session = Depends(get_db),
    _: None = Depends(_verify_api_key),
):
    """
    Main endpoint called by the EA every N hours.
    1. Check Redis cache (avoid duplicate evaluations)
    2. Run screener (800 → 30 candidates)
    3. Call Gemini LLM
    4. Save to DB
    5. Return portfolio decisions
    """
    redis = get_redis()

    # ── 1. Cache check ────────────────────────────────────────────────────────
    cache_key = f"eval:{datetime.now(timezone.utc).strftime('%Y-%m-%d-%H')}"
    cached = redis.get(cache_key)
    if cached:
        logger.info("Cache hit — returning cached evaluation")
        cached_data = json.loads(cached)
        return PortfolioReviewResponse(**cached_data)

    # ── 2. Screener ───────────────────────────────────────────────────────────
    # Use the dynamic watchlist (from .env or default)
    candidates = await screen_symbols(
        symbols=ACTIVE_WATCHLIST,
        top_n=settings.screener_top_n,
        account_balance=request.account.balance,
        max_margin_pct=20.0,  # Darwinex stock margin
    )

    if not candidates:
        logger.warning("Screener returned 0 candidates — returning safe default")
        return PortfolioReviewResponse(
            portfolio=[],
            regime="NEUTRAL",
            cash_target=1.0,
            next_review_h=4,
        )

    # ── 3. AI evaluation ──────────────────────────────────────────────────────
    try:
        decisions, regime, cash_target, next_review_h = await get_portfolio_allocation(
            account=request.account,
            positions=request.positions,
            candidates=candidates,
            config=request.config,
        )
    except Exception as e:
        logger.error(f"AI evaluation failed: {e}")
        raise HTTPException(status_code=503, detail=f"AI evaluation failed: {e}")

    # ── 4. Persist to DB ──────────────────────────────────────────────────────
    eval_record = PortfolioEvaluation(
        account_balance=request.account.balance,
        account_equity=request.account.equity,
        account_margin_free=request.account.margin_free,
        regime=regime,
        cash_target=cash_target,
        decisions_json=[d.model_dump() for d in decisions],
        screener_candidates=[c.symbol for c in candidates],
        gemini_model=settings.gemini_model,
        next_review_hours=next_review_h,
    )
    db.add(eval_record)
    db.commit()
    db.refresh(eval_record)

    # ── 5. Cache result ───────────────────────────────────────────────────────
    response = PortfolioReviewResponse(
        portfolio=decisions,
        regime=regime,
        cash_target=cash_target,
        next_review_h=next_review_h,
        eval_id=eval_record.id,
    )
    redis.setex(cache_key, CACHE_TTL_SECONDS, response.model_dump_json())

    logger.info(f"Evaluation #{eval_record.id} complete: {len(decisions)} decisions")
    return response


@router.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
