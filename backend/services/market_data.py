"""
Market data fetcher using Yahoo Finance v8 API directly.
Handles the cookie + crumb auth flow that yfinance abstracts poorly,
and caches the auth tokens in Redis to avoid rate-limiting (429).
"""
import httpx
import json
import logging
import pandas as pd
from datetime import datetime, timezone
from core.cache import get_redis

logger = logging.getLogger(__name__)

YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

CRUMB_CACHE_KEY = "yf:crumb"
COOKIE_CACHE_KEY = "yf:cookie"
AUTH_TTL = 3600 * 8  # 8 hours


async def _get_crumb_and_cookie(client: httpx.AsyncClient) -> tuple[str, str]:
    """
    Obtain a Yahoo Finance crumb + cookie.
    Tries Redis cache first; fetches fresh if missing/expired.
    """
    redis = get_redis()
    cached_crumb = redis.get(CRUMB_CACHE_KEY)
    cached_cookie = redis.get(COOKIE_CACHE_KEY)

    if cached_crumb and cached_cookie:
        logger.debug("YF auth: using cached crumb")
        return cached_crumb, cached_cookie

    logger.info("YF auth: fetching fresh crumb + cookie...")

    # Step 1 — Visit Yahoo Finance to get a session cookie
    resp = await client.get(
        "https://finance.yahoo.com",
        headers=YF_HEADERS,
        follow_redirects=True,
        timeout=15,
    )
    cookie_str = "; ".join(f"{k}={v}" for k, v in client.cookies.items())

    # Step 2 — Get crumb using that cookie
    crumb_headers = {**YF_HEADERS, "Cookie": cookie_str}
    crumb_resp = await client.get(
        "https://query1.finance.yahoo.com/v1/test/getcrumb",
        headers=crumb_headers,
        timeout=10,
    )
    crumb_resp.raise_for_status()
    crumb = crumb_resp.text.strip()

    if not crumb or "<" in crumb:
        raise ValueError(f"Invalid crumb received: {crumb[:50]}")

    # Cache both
    redis.setex(CRUMB_CACHE_KEY, AUTH_TTL, crumb)
    redis.setex(COOKIE_CACHE_KEY, AUTH_TTL, cookie_str)
    logger.info(f"YF auth: crumb cached (len={len(crumb)})")
    return crumb, cookie_str


async def fetch_ohlcv(
    symbol: str,
    client: httpx.AsyncClient,
    crumb: str,
    cookie: str,
    period_days: int = 65,
) -> pd.DataFrame | None:
    """Fetch daily OHLCV for one symbol from Yahoo Finance v8 API."""
    now_ts = int(datetime.now(timezone.utc).timestamp())
    start_ts = now_ts - period_days * 86400

    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + symbol
    params = {
        "period1": start_ts,
        "period2": now_ts,
        "interval": "1d",
        "crumb": crumb,
        "events": "div,splits",
    }
    headers = {**YF_HEADERS, "Cookie": cookie}

    try:
        resp = await client.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code == 401 or resp.status_code == 429:
            logger.warning(f"{symbol}: HTTP {resp.status_code} — auth may be stale")
            return None
        resp.raise_for_status()

        data = resp.json()
        result = data["chart"]["result"]
        if not result:
            return None

        r = result[0]
        timestamps = r["timestamp"]
        quotes = r["indicators"]["quote"][0]
        adjclose = r["indicators"].get("adjclose", [{}])[0].get("adjclose", quotes["close"])

        df = pd.DataFrame({
            "Open":   quotes["open"],
            "High":   quotes["high"],
            "Low":    quotes["low"],
            "Close":  adjclose,
            "Volume": quotes["volume"],
        }, index=pd.to_datetime(timestamps, unit="s", utc=True))

        df = df.dropna(subset=["Close"])
        return df if len(df) >= 22 else None

    except Exception as e:
        logger.debug(f"{symbol}: fetch error — {e}")
        return None


async def fetch_batch(
    symbols: list[str],
    period_days: int = 65,
) -> dict[str, pd.DataFrame]:
    """
    Fetch OHLCV for a list of symbols concurrently (up to 10 at a time).
    Handles auth internally with Redis-cached crumb/cookie.
    """
    import asyncio

    results: dict[str, pd.DataFrame] = {}
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)

    async with httpx.AsyncClient(limits=limits, follow_redirects=True) as client:
        # Get auth tokens once
        try:
            crumb, cookie = await _get_crumb_and_cookie(client)
        except Exception as e:
            logger.error(f"Failed to get YF auth: {e}")
            return results

        # Fetch symbols in concurrent batches of 10
        sem = asyncio.Semaphore(10)

        async def _fetch_one(sym: str):
            async with sem:
                df = await fetch_ohlcv(sym, client, crumb, cookie, period_days)
                if df is not None:
                    results[sym] = df

        await asyncio.gather(*[_fetch_one(sym) for sym in symbols])

    logger.info(f"Fetched data for {len(results)}/{len(symbols)} symbols")
    return results
