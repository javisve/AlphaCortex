"""
Pydantic schemas for the portfolio review endpoint.
Compact field names reduce JSON payload size (saves bandwidth + tokens).
"""
from pydantic import BaseModel, Field
from typing import Optional


# ── Incoming from EA ──────────────────────────────────────────────────────────

class AccountState(BaseModel):
    balance: float
    equity: float
    margin_free: float
    margin_used: float = 0.0
    currency: str = "USD"


class OpenPosition(BaseModel):
    symbol: str
    type: str           # "BUY" or "SELL"
    lots: float
    profit: float
    open_price: float
    margin: float = 0.0


class EAConfig(BaseModel):
    max_positions: int = 10
    max_margin_pct: float = 60.0
    max_per_asset_pct: float = 15.0


class PortfolioReviewRequest(BaseModel):
    account: AccountState
    positions: list[OpenPosition] = []
    config: EAConfig = EAConfig()


# ── Outgoing to EA ────────────────────────────────────────────────────────────

class AssetDecision(BaseModel):
    s: str                              # symbol (short key = fewer tokens)
    a: str                              # action: BUY | SELL | HOLD | CLOSE
    w: float = 0.0                      # portfolio weight 0.0–1.0
    sl_pct: float = 5.0                 # stop loss % from entry
    tp_pct: float = 10.0                # take profit % from entry


class PortfolioReviewResponse(BaseModel):
    portfolio: list[AssetDecision]
    regime: str                         # BULL | BEAR | CAUTIOUS | NEUTRAL
    cash_target: float                  # % to keep in cash (0.0–1.0)
    next_review_h: int = 4             # hours until next recommended review
    eval_id: int = 0                   # DB id for traceability
