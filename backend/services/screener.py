"""
Technical screener — reduces the watchlist to top N candidates.
Uses the custom market_data fetcher (Yahoo Finance v8 API, no yfinance).
"""
import pandas as pd
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── Watchlists ────────────────────────────────────────────────────────────────

# Fast MVP watchlist — top 50 most liquid US stocks across sectors
TOP50_WATCHLIST = [
    # Tech
    "AAPL","MSFT","NVDA","GOOGL","META","AMZN","TSLA","AVGO","AMD","INTC",
    # Finance
    "JPM","BAC","GS","MS","V","MA","BLK","AXP","C","WFC",
    # Healthcare
    "LLY","JNJ","UNH","ABT","MRK","PFE","ABBV","MDT","TMO","DHR",
    # Energy
    "XOM","CVX","COP","SLB","OXY",
    # Consumer / Retail
    "HD","WMT","COST","MCD","SBUX",
    # Industrials
    "CAT","DE","BA","GE","HON","RTX","UPS","FDX",
    # ETFs
    "SPY","QQQ",
]

# Full Darwinex NASDAQ universe
NASDAQ_SYMBOLS = [
    "AAPL","ACHC","ADBE","ADI","ADP","ADSK","AEP","AKAM","ALGN","ALNY",
    "AMAT","AMD","AMGN","AMKR","AMZN","APA","APLS","ARCC","ARWR","AVGO",
    "AXON","AZTA","BIIB","BKNG","BKR","BL","BMRN","BRKR","CACC","CAR",
    "CASY","CDNS","CDW","CG","CGNX","CHDN","CHRW","CHTR","CINF","CMCSA",
    "CME","COIN","COLM","COO","COST","CPRT","CROX","CRWD","CSCO","CSGP",
    "CTAS","CTSH","DBX","DDOG","DLTR","DNLI","DOCU","DOX","DPZ","DXCM",
    "EA","EBAY","EEFT","ENPH","ENTG","EVRG","EWBC","EXEL","EXPE","EXPI",
    "FANG","FAST","FFIV","FITB","FIVE","FIVN","FLEX","FOX","FOXA","FOXF",
    "FRPT","FSLR","FTNT","GEN","GH","GILD","GNTX","GOOG","GOOGL","HALO",
    "HAS","HBAN","HELE","HON","HQY","HSIC","IBKR","IDXX","ILMN","INCY",
    "INTC","INTU","IONS","IPGP","IRDM","ISRG","JBHT","JKHY","KDP","KHC",
    "KLAC","KMB","LITE","LKQ","LNT","LPLA","LRCX","LSCC","LSTR","LYFT",
    "MANH","MAR","MASI","MAT","MCHP","MDB","MDLZ","MEDP","META","MIDD",
    "MKSI","MKTX","MNST","MPWR","MRNA","MRVL","MSFT","MSTR","MU","NBIX",
    "NCLH","NDAQ","NDSN","NFLX","NTAP","NTNX","NTRS","NVDA","NWSA","NXST",
    "ODFL","OKTA","OLED","OLLI","OMCL","ON","ORLY","OZK","PANW","PAYX",
    "PCAR","PCTY","PEGA","PENN","PEP","PFG","PGNY","PODD","POOL","PTC",
    "PYPL","QCOM","QRVO","RARE","REGN","RGEN","RGLD","ROKU","ROP","ROST",
    "RRR","SAIA","SAIC","SBUX","SLAB","SLM","SNPS","SRPT","SSNC","STLD",
    "SWKS","SYNA","TECH","TER","TMUS","TNDM","TRIP","TRMB","TROW","TSCO",
    "TSLA","TTD","TTEK","TTWO","TXN","TXRH","UAL","ULTA","UTHR","VRNS",
    "VRSK","VRSN","VRTX","WDC","WMT","WWD","WYNN","XRAY","ZBRA","ZD",
    "ZG","ZION","ZM","ZS",
]

ALL_SYMBOLS = list(dict.fromkeys(TOP50_WATCHLIST + NASDAQ_SYMBOLS))


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ScreenedAsset:
    symbol: str
    price: float
    chg_1d: float
    chg_5d: float
    chg_20d: float
    rsi_14: float
    volume_ratio: float
    atr_pct: float


# ── Technical indicators ──────────────────────────────────────────────────────

def _compute_rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, float("inf"))
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return round(float(val), 1) if pd.notna(val) else 50.0


def _compute_atr_pct(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    price = close.iloc[-1]
    return round(float(atr / price * 100), 2) if price > 0 else 0.0


# ── Main screener ─────────────────────────────────────────────────────────────

async def screen_symbols(
    symbols: list[str],
    top_n: int = 30,
    min_price: float = 5.0,
    max_rsi: float = 75.0,
    min_rsi: float = 25.0,
    min_volume_ratio: float = 0.5,
    account_balance: float = 1000.0,
    max_margin_pct: float = 20.0,
) -> list[ScreenedAsset]:
    """
    Fetch OHLCV data for all symbols via Yahoo Finance v8 API (async, concurrent),
    apply technical filters, score, and return top_n candidates.
    """
    from services.market_data import fetch_batch

    logger.info(f"Screening {len(symbols)} symbols (concurrent)...")
    all_data = await fetch_batch(symbols)

    if not all_data:
        logger.error("No market data received — check Yahoo Finance auth")
        return []

    max_affordable_price = account_balance * max_margin_pct / 100.0
    results: list[ScreenedAsset] = []

    for sym, df in all_data.items():
        try:
            close = df["Close"]
            high  = df["High"]
            low   = df["Low"]
            volume = df["Volume"]

            price = float(close.iloc[-1])
            if price < min_price:
                continue
            if price * 0.20 > max_affordable_price:
                continue

            rsi = _compute_rsi(close)
            if not (min_rsi <= rsi <= max_rsi):
                continue

            vol_avg = float(volume.rolling(20).mean().iloc[-1])
            vol_ratio = float(volume.iloc[-1]) / vol_avg if vol_avg > 0 else 0
            if vol_ratio < min_volume_ratio:
                continue

            chg_1d  = float((close.iloc[-1] / close.iloc[-2]  - 1) * 100) if len(close) >= 2  else 0.0
            chg_5d  = float((close.iloc[-1] / close.iloc[-6]  - 1) * 100) if len(close) >= 6  else 0.0
            chg_20d = float((close.iloc[-1] / close.iloc[-21] - 1) * 100) if len(close) >= 21 else 0.0
            atr_pct = _compute_atr_pct(high, low, close)

            results.append(ScreenedAsset(
                symbol=sym,
                price=round(price, 2),
                chg_1d=round(chg_1d, 1),
                chg_5d=round(chg_5d, 1),
                chg_20d=round(chg_20d, 1),
                rsi_14=rsi,
                volume_ratio=round(vol_ratio, 2),
                atr_pct=atr_pct,
            ))
        except Exception as e:
            logger.debug(f"Skipping {sym}: {e}")

    for asset in results:
        asset._score = (abs(asset.chg_20d) * asset.volume_ratio) / max(asset.atr_pct, 0.1)  # type: ignore

    results.sort(key=lambda x: getattr(x, "_score", 0), reverse=True)
    selected = results[:top_n]
    logger.info(f"Screener: {len(selected)} candidates from {len(results)} passing filters")
    return selected
