from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import ccxt
import json
import subprocess
import urllib.error
import urllib.request
from urllib.parse import quote
from nsepython import nse_eq, nsesymbolpurify

ROOT = Path(__file__).resolve().parent
CPP_DIR = ROOT / "cpp"
BINARY_PATH = CPP_DIR / "option_pricer"
FRONTEND_DIST = ROOT / "frontend" / "dist"

app = FastAPI(
    title="Ultra Low Latency Option Pricer",
    description="FastAPI backend for the Black-Scholes pricing engine.",
    version="1.0",
)


def build_pricer() -> None:
    if not BINARY_PATH.exists():
        subprocess.run(["make"], cwd=CPP_DIR, check=True)
    if not BINARY_PATH.exists():
        raise RuntimeError("Pricing binary could not be built")


def parse_engine_output(output: str) -> dict:
    data = {}
    for line in output.strip().splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        try:
            data[key] = float(value)
        except ValueError:
            data[key] = value
    return data


def run_pricer(
    spot: float,
    strike: float,
    rate: float,
    maturity: float,
    vol: float,
    option_type: str,
    iterations: int,
) -> dict:
    build_pricer()
    result = subprocess.run(
        [
            str(BINARY_PATH),
            "--spot",
            str(spot),
            "--strike",
            str(strike),
            "--rate",
            str(rate),
            "--maturity",
            str(maturity),
            "--vol",
            str(vol),
            "--type",
            option_type,
            "--iterations",
            str(iterations),
        ],
        cwd=CPP_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    return parse_engine_output(result.stdout)


def fetch_url(url: str, headers: dict[str, str]) -> str:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8")


def safe_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_stock_quote_payload(payload: dict, symbol: str) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Invalid quote payload")

    last_price = None
    open_price = None
    high_price = None
    low_price = None
    prev_close = None
    change = None

    if "underlyingValue" in payload:
        last_price = safe_float(payload.get("underlyingValue"))
        prev_close = safe_float(payload.get("previousClose") or payload.get("previousClosePrice") or payload.get("previousCloseValue"))
        change = safe_float(payload.get("change") or payload.get("pChange") or payload.get("lastPrice") - prev_close if prev_close is not None and last_price is not None else None)

    if "priceInfo" in payload and isinstance(payload["priceInfo"], dict):
        info = payload["priceInfo"]
        last_price = last_price or safe_float(info.get("lastPrice") or info.get("lastTradedPrice") or info.get("last_price") or info.get("close"))
        open_price = safe_float(info.get("open") or info.get("openPrice"))
        high_price = safe_float(info.get("dayHigh") or info.get("highPrice") or info.get("high"))
        low_price = safe_float(info.get("dayLow") or info.get("lowPrice") or info.get("low"))
        prev_close = prev_close or safe_float(info.get("previousClose") or info.get("prevClose"))
        change = change or safe_float(info.get("change") or info.get("pChange"))

    if "data" in payload and isinstance(payload["data"], dict):
        data = payload["data"]
        last_price = last_price or safe_float(data.get("pricecurrent") or data.get("lastPrice") or data.get("last_traded_price") or data.get("marketPrice"))
        open_price = open_price or safe_float(data.get("openPrice") or data.get("open"))
        high_price = high_price or safe_float(data.get("highPrice") or data.get("high"))
        low_price = low_price or safe_float(data.get("lowPrice") or data.get("low"))
        prev_close = prev_close or safe_float(data.get("priceprevclose") or data.get("prevClose"))
        change = change or safe_float(data.get("pricechange") or data.get("change"))

    if last_price is None and "quoteResponse" in payload:
        result = payload.get("quoteResponse", {}).get("result", [])
        if result:
            info = result[0]
            last_price = last_price or safe_float(info.get("regularMarketPrice") or info.get("postMarketPrice") or info.get("preMarketPrice"))
            open_price = open_price or safe_float(info.get("regularMarketOpen") or info.get("open"))
            high_price = high_price or safe_float(info.get("regularMarketDayHigh") or info.get("dayHigh"))
            low_price = low_price or safe_float(info.get("regularMarketDayLow") or info.get("dayLow"))
            prev_close = prev_close or safe_float(info.get("regularMarketPreviousClose") or info.get("previousClose"))
            change = change or safe_float(info.get("regularMarketChange") or info.get("change"))

    if last_price is None:
        raise ValueError("Could not parse quote payload")

    return {
        "symbol": symbol,
        "lastPrice": last_price,
        "openPrice": open_price,
        "highPrice": high_price,
        "lowPrice": low_price,
        "prevClose": prev_close,
        "change": change,
        "raw": payload,
    }


def fetch_nsepython_quote(symbol: str) -> dict:
    symbol = symbol.strip().upper()
    symbol = nsesymbolpurify(symbol)
    payload = nse_eq(symbol)
    if not payload or not isinstance(payload, dict):
        raise ValueError("NSE Python returned no quote data")
    return parse_stock_quote_payload(payload, symbol)


def fetch_moneycontrol_quote(symbol: str) -> dict:
    url = f"https://priceapi.moneycontrol.com/pricefeed/nse/equitycash/{urllib.request.quote(symbol)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.moneycontrol.com/",
        "Origin": "https://www.moneycontrol.com",
    }
    text = fetch_url(url, headers)
    parsed = json.loads(text)
    if not isinstance(parsed, dict) or "data" not in parsed:
        raise ValueError("Invalid Moneycontrol quote response")

    data = parsed["data"]
    return {
        "symbol": symbol,
        "lastPrice": safe_float(data.get("pricecurrent") or data.get("HP") or 0.0),
        "openPrice": safe_float(data.get("OPN") or 0.0),
        "highPrice": safe_float(data.get("52H") or 0.0),
        "lowPrice": safe_float(data.get("52L") or 0.0),
        "prevClose": safe_float(data.get("priceprevclose") or 0.0),
        "change": safe_float(data.get("pricechange") or 0.0),
        "raw": data,
    }


def fetch_yahoo_quote(symbol: str) -> dict:
    query_symbol = symbol.upper()
    if not query_symbol.endswith(".NS"):
        query_symbol = f"{query_symbol}.NS"
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={quote(query_symbol)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    text = fetch_url(url, headers)
    payload = json.loads(text)
    result = payload.get("quoteResponse", {}).get("result", [])
    if not result:
        raise ValueError("Yahoo Finance returned no quote data")
    info = result[0]
    return {
        "symbol": symbol,
        "lastPrice": safe_float(info.get("regularMarketPrice") or info.get("previousClose")),
        "openPrice": safe_float(info.get("regularMarketOpen")),
        "highPrice": safe_float(info.get("regularMarketDayHigh")),
        "lowPrice": safe_float(info.get("regularMarketDayLow")),
        "prevClose": safe_float(info.get("regularMarketPreviousClose") or info.get("previousClose")),
        "change": safe_float(info.get("regularMarketChange") or info.get("regularMarketChangePercent")),
        "raw": info,
    }


def fetch_stock_quote(symbol: str) -> dict:
    last_error = None
    for fetcher in (fetch_nsepython_quote, fetch_moneycontrol_quote, fetch_yahoo_quote):
        try:
            return fetcher(symbol)
        except Exception as exc:
            last_error = exc
    raise last_error or ValueError("Could not fetch stock quote")


def fetch_crypto_quote(symbol: str) -> dict:
    symbol_value = symbol.strip().upper()
    if "/" not in symbol_value:
        symbol_value = f"{symbol_value}/USDT"

    exchange = ccxt.binance({"enableRateLimit": True})
    ticker = exchange.fetch_ticker(symbol_value)
    return {
        "symbol": symbol_value,
        "lastPrice": safe_float(ticker.get("last") or ticker.get("close")),
        "openPrice": safe_float(ticker.get("open")),
        "highPrice": safe_float(ticker.get("high")),
        "lowPrice": safe_float(ticker.get("low")),
        "prevClose": safe_float(ticker.get("previousClose") or ticker.get("info", {}).get("previousClose")),
        "change": safe_float(ticker.get("change") or ticker.get("percentage")),
        "raw": ticker,
    }


@app.get("/api/price")
def api_price(
    spot: float = Query(100.0, gt=0.0),
    strike: float = Query(100.0, gt=0.0),
    rate: float = Query(0.05, ge=0.0),
    maturity: float = Query(1.0, gt=0.0),
    vol: float = Query(0.2, gt=0.0),
    option_type: str = Query("call", regex="^(call|put)$", alias="type"),
    iterations: int = Query(100000, ge=1),
):
    try:
        return run_pricer(spot, strike, rate, maturity, vol, option_type, iterations)
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=exc.stderr or "Pricing engine failed")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/stock")
def api_stock(
    symbol: str = Query(..., min_length=1),
    market: str = Query("equity", regex="^(equity|crypto)$"),
) -> dict:
    symbol_value = symbol.strip().upper()
    if not symbol_value:
        raise HTTPException(status_code=400, detail="symbol query parameter is required")
    try:
        if market == "crypto":
            return {"symbol": symbol_value, "market": "crypto", "quote": fetch_crypto_quote(symbol_value)}
        return {"symbol": symbol_value, "market": "equity", "quote": fetch_stock_quote(symbol_value)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")


@app.get("/")
def read_root() -> dict:
    if FRONTEND_DIST.exists():
        return FileResponse(FRONTEND_DIST / "index.html")
    return {"message": "Ultra Low Latency Option Pricer backend is running."}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
