"""
AI Fund Manager — Gemini integration.
Sends screened candidates to Gemini and parses the portfolio allocation.
"""
import json
import logging
import google.generativeai as genai
from google.generativeai.types import GenerationConfig

from core.config import get_settings
from services.screener import ScreenedAsset
from api.schemas import AccountState, OpenPosition, EAConfig, AssetDecision

logger = logging.getLogger(__name__)
settings = get_settings()

# ── System prompt (cached by Gemini — charged once) ──────────────────────────
SYSTEM_PROMPT = """You are a professional equity fund manager.
Your task: analyze a list of stock candidates and decide a portfolio allocation.

RESPONSE FORMAT — return ONLY valid JSON, no markdown, no extra text:
{
  "portfolio": [
    {"s":"TICKER","a":"BUY","w":0.10,"sl_pct":4.0,"tp_pct":9.0},
    {"s":"TICKER","a":"SELL","w":0.08,"sl_pct":5.0,"tp_pct":12.0}
  ],
  "regime": "BULL",
  "cash": 0.40,
  "next_h": 8
}

FIELD RULES:
- "a": BUY (long), SELL (short), HOLD (keep existing), CLOSE (exit immediately)
- "w": portfolio weight 0.0–1.0. Sum of all weights must not exceed (1.0 - cash)
- "sl_pct": stop loss percent from entry price (always positive)
- "tp_pct": take profit percent from entry price (always positive)
- "regime": BULL | BEAR | CAUTIOUS | NEUTRAL
- "cash": fraction to keep uninvested (0.0–1.0)
- "next_h": hours until next review (4–24 based on market conditions)

PORTFOLIO STRATEGY RULES:
1. STRICT TREND FOLLOWING ONLY. "The trend is your friend."
2. DO NOT buy downtrending stocks (negative 20d return) hoping for a bounce.
3. DO NOT sell uptrending stocks (positive 20d return) just because RSI is high.
4. Buy strength (positive momentum) and sell weakness.
5. Use RSI to confirm trends (e.g. RSI > 50 is bullish), NOT to counter-trade.
6. Diversify across sectors.
7. In CAUTIOUS/BEAR regime: prefer SELL/short and increase cash.
8. Only include actionable decisions (skip HOLD unless already in position).
"""


def _build_user_prompt(
    account: AccountState,
    positions: list[OpenPosition],
    candidates: list[ScreenedAsset],
    config: EAConfig,
) -> str:
    """
    Build compact user prompt (~50 tokens per asset).
    Format: TICKER:price chg1d%d chg20d%w RSI:n VolR:n Sector
    """
    lines = ["=== ACCOUNT ==="]
    lines.append(
        f"Balance:${account.balance:.0f} Equity:${account.equity:.0f} "
        f"FreeMargin:${account.margin_free:.0f} Currency:{account.currency}"
    )
    lines.append(f"MaxPositions:{config.max_positions} MaxMargin:{config.max_margin_pct}%")

    if positions:
        lines.append("\n=== CURRENT POSITIONS ===")
        for p in positions:
            pnl_sign = "+" if p.profit >= 0 else ""
            lines.append(
                f"{p.symbol} {p.type} {p.lots}lots @ {p.open_price:.2f} "
                f"PnL:{pnl_sign}{p.profit:.1f}"
            )

    lines.append("\n=== SCREENED CANDIDATES (top by momentum×liquidity) ===")
    lines.append("Symbol:Price 1d% 20d% RSI VolR")
    for c in candidates:
        lines.append(
            f"{c.symbol}:{c.price} {c.chg_1d:+.1f}% {c.chg_20d:+.1f}%w "
            f"RSI:{c.rsi_14} VolR:{c.volume_ratio}"
        )

    lines.append("\nDecide the optimal portfolio allocation based on the data above.")
    return "\n".join(lines)


def _parse_response(raw_text: str) -> dict:
    """Extract JSON from model response, handling markdown fences robustly."""
    import re
    # Normalize line endings and strip control characters
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()

    # Try to extract from ```json ... ``` or ``` ... ``` fences
    fence_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fence_match:
        text = fence_match.group(1).strip()
    elif not text.startswith("{"):
        # Find first { and last }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start:end+1]

    return json.loads(text)


async def get_portfolio_allocation(
    account: AccountState,
    positions: list[OpenPosition],
    candidates: list[ScreenedAsset],
    config: EAConfig,
) -> tuple[list[AssetDecision], str, float, int]:
    """
    Call Gemini and return:
      (decisions, regime, cash_target, next_review_hours)
    """
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY not configured")

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(
        model_name=settings.gemini_model,
        system_instruction=SYSTEM_PROMPT,
        generation_config=GenerationConfig(
            temperature=0.2,
            max_output_tokens=8192,
            response_mime_type="application/json",
        ),
    )

    user_prompt = _build_user_prompt(account, positions, candidates, config)
    logger.info(f"Sending prompt to {settings.gemini_model} (~{len(user_prompt)//4} tokens)")

    response = model.generate_content(user_prompt)
    raw = response.text
    logger.info(f"Gemini raw (len={len(raw)}): {raw!r}")

    data = _parse_response(raw)

    # Parse decisions
    decisions: list[AssetDecision] = []
    for item in data.get("portfolio", []):
        try:
            decisions.append(AssetDecision(
                s=item["s"],
                a=item["a"].upper(),
                w=float(item.get("w", 0.0)),
                sl_pct=float(item.get("sl_pct", 5.0)),
                tp_pct=float(item.get("tp_pct", 10.0)),
            ))
        except (KeyError, ValueError) as e:
            logger.warning(f"Skipping malformed decision {item}: {e}")

    regime = str(data.get("regime", "NEUTRAL")).upper()
    cash_target = float(data.get("cash", 0.40))
    next_review_h = int(data.get("next_h", 4))

    logger.info(
        f"AI decision: {len(decisions)} actions, regime={regime}, "
        f"cash={cash_target:.0%}, next_review={next_review_h}h"
    )
    return decisions, regime, cash_target, next_review_h
