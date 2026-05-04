from sqlalchemy import Column, Integer, String, Float, DateTime, JSON
from sqlalchemy.sql import func
from core.database import Base


class PortfolioEvaluation(Base):
    """Stores every AI portfolio decision for historical tracking."""
    __tablename__ = "portfolio_evaluations"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Account snapshot at time of evaluation
    account_balance = Column(Float)
    account_equity = Column(Float)
    account_margin_free = Column(Float)

    # AI output
    regime = Column(String(20))
    cash_target = Column(Float)
    decisions_json = Column(JSON)       # list of {symbol, action, weight, ...}
    screener_candidates = Column(JSON)  # symbols that passed pre-filter

    # Metadata
    gemini_model = Column(String(50))
    tokens_used = Column(Integer, nullable=True)
    next_review_hours = Column(Integer, default=4)


class TradeLog(Base):
    """Tracks every order the EA opens/closes."""
    __tablename__ = "trade_log"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    symbol = Column(String(20))
    action = Column(String(10))     # BUY / SELL / CLOSE
    lots = Column(Float)
    entry_price = Column(Float, nullable=True)
    sl = Column(Float, nullable=True)
    tp = Column(Float, nullable=True)
    profit = Column(Float, nullable=True)
    comment = Column(String(100), nullable=True)
    evaluation_id = Column(Integer, nullable=True)
