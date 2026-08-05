import json
import math
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from functools import lru_cache
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import ccxt
import subprocess
from nsepython import nse_eq, nse_eq_symbols, nse_quote, nsesymbolpurify

ROOT = Path(__file__).resolve().parent
CPP_DIR = ROOT / "cpp"
BINARY_PATH = CPP_DIR / "option_pricer"
FRONTEND_DIR = ROOT / "frontend"

app = FastAPI(
    title="Ultra Low Latency Option Pricer",
    description="Lightweight backend using NSEPython for NSE equity quotes and ccxt for crypto quotes.",
    version="1.0",
)

YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search?q={query}&lang=en-IN"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=5m"
YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


def load_json_url(url: str) -> dict:
    request = Request(url, headers=YAHOO_HEADERS)
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise ValueError(f"Yahoo API failed: {exc.code} {exc.reason}")
    except URLError as exc:
        raise ValueError(f"Yahoo API failed: {exc.reason}")


def build_yahoo_symbol(symbol: str) -> str:
    symbol_value = symbol.strip().upper()
    if symbol_value.endswith(".NS"):
        return symbol_value
    if "." in symbol_value:
        return symbol_value
    return f"{symbol_value}.NS"


def choose_yahoo_nse_symbol(payload: dict, query: str) -> str:
    quotes = payload.get("quotes", []) or []
    for item in quotes:
        if item.get("exchange") in {"NSI", "NSE"} and item.get("quoteType") == "EQUITY":
            symbol = item.get("symbol")
            if symbol and symbol.endswith(".NS"):
                return symbol
    for item in quotes:
        symbol = item.get("symbol")
        if symbol and symbol.endswith(".NS"):
            return symbol
    if query and query.upper().endswith(".NS"):
        return query.upper()
    raise ValueError("Could not map symbol to a Yahoo NSE equity symbol; symbol may be delisted or unavailable")


def search_yahoo_equity_symbol(query: str) -> str:
    payload = load_json_url(YAHOO_SEARCH_URL.format(query=quote_plus(query)))
    return choose_yahoo_nse_symbol(payload, query)


def parse_yahoo_chart_payload(payload: dict, symbol: str) -> dict:
    chart = payload.get("chart", {})
    results = chart.get("result") or []
    if not results:
        raise ValueError("Yahoo chart returned no result")

    result = results[0]
    meta = result.get("meta", {})
    indicators = result.get("indicators", {})
    quote_data = (indicators.get("quote") or [{}])[0]
    timestamps = result.get("timestamp") or []
    closes = quote_data.get("close") or []
    opens = quote_data.get("open") or []
    highs = quote_data.get("high") or []
    lows = quote_data.get("low") or []

    last_price = safe_float(meta.get("regularMarketPrice"))
    if last_price is None:
        last_price = next((safe_float(x) for x in reversed(closes) if x is not None), None)

    open_price = safe_float(meta.get("open")) or next((safe_float(x) for x in opens if x is not None), None)
    high_price = safe_float(max((x for x in highs if x is not None), default=None))
    low_price = safe_float(min((x for x in lows if x is not None), default=None))
    prev_close = safe_float(meta.get("previousClose") or meta.get("chartPreviousClose"))
    change = safe_float(last_price - prev_close) if last_price is not None and prev_close is not None else None

    series = []
    for ts, close in zip(timestamps, closes):
        if close is not None:
            series.append({"ts": int(ts), "close": safe_float(close)})

    return {
        "symbol": symbol,
        "lastPrice": last_price,
        "openPrice": open_price,
        "highPrice": high_price,
        "lowPrice": low_price,
        "prevClose": prev_close,
        "change": change,
        "currency": meta.get("currency"),
        "series": series,
        "raw": payload,
    }


def fetch_yahoo_equity_quote(symbol: str) -> dict:
    symbol_value = symbol.strip().upper()
    if not symbol_value.endswith(".NS"):
        symbol_value = search_yahoo_equity_symbol(symbol_value)

    payload = load_json_url(YAHOO_CHART_URL.format(symbol=symbol_value))
    return parse_yahoo_chart_payload(payload, symbol_value)


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


def safe_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@lru_cache(maxsize=1)
def get_nse_symbol_list() -> list[str]:
    try:
        payload = nse_eq_symbols()
        return sorted(payload) if isinstance(payload, list) else []
    except Exception:
        return []


@lru_cache(maxsize=1)
def get_crypto_symbol_list() -> list[str]:
    try:
        exchange = ccxt.binance({"enableRateLimit": True})
        markets = exchange.load_markets()
        symbols = sorted([symbol for symbol in markets.keys() if symbol.endswith("/USDT")])
        return symbols
    except Exception:
        return []


@app.get("/api/nse-symbols")
def api_nse_symbols(q: str = Query("", alias="q")) -> dict:
    symbols = get_nse_symbol_list()
    if q:
        query = q.strip().upper()
        symbols = [symbol for symbol in symbols if query in symbol]
    return {"symbols": symbols[:800], "total": len(symbols)}


@app.get("/api/crypto-symbols")
def api_crypto_symbols(q: str = Query("", alias="q")) -> dict:
    symbols = get_crypto_symbol_list()
    if q:
        query = q.strip().upper()
        symbols = [symbol for symbol in symbols if query in symbol]
    return {"symbols": symbols[:200], "total": len(symbols)}


def black_scholes_price_and_greeks(
    spot: float,
    strike: float,
    rate: float,
    maturity: float,
    vol: float,
    option_type: str,
) -> dict:
    if spot <= 0 or strike <= 0 or vol <= 0 or maturity <= 0:
        raise ValueError("Invalid inputs for Black-Scholes calculation")

    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * maturity) / (vol * math.sqrt(maturity))
    d2 = d1 - vol * math.sqrt(maturity)
    nd1 = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
    nd2 = 0.5 * (1 + math.erf(d2 / math.sqrt(2)))
    npd1 = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)

    if option_type == "call":
        price = spot * nd1 - strike * math.exp(-rate * maturity) * nd2
        delta = nd1
        theta = -((spot * npd1 * vol) / (2 * math.sqrt(maturity))) - rate * strike * math.exp(-rate * maturity) * nd2
        rho = strike * maturity * math.exp(-rate * maturity) * nd2
    else:
        price = strike * math.exp(-rate * maturity) * (1 - nd2) - spot * (1 - nd1)
        delta = nd1 - 1
        theta = -((spot * npd1 * vol) / (2 * math.sqrt(maturity))) + rate * strike * math.exp(-rate * maturity) * (1 - nd2)
        rho = -strike * maturity * math.exp(-rate * maturity) * (1 - nd2)

    gamma = npd1 / (spot * vol * math.sqrt(maturity))
    vega = spot * npd1 * math.sqrt(maturity)

    return {
        "price": price,
        "delta": delta,
        "gamma": gamma,
        "vega": vega,
        "theta": theta,
        "rho": rho,
    }


def simulate_option_curve(
    spot: float,
    strike: float,
    rate: float,
    maturity: float,
    vol: float,
    option_type: str,
    steps: int = 41,
) -> dict:
    center = spot
    low = max(1.0, spot * 0.5)
    high = spot * 1.5
    step = max(1.0, (high - low) / max(steps - 1, 1))
    series = []
    for i in range(steps):
        s = low + step * i
        metrics = black_scholes_price_and_greeks(s, strike, rate, maturity, vol, option_type)
        series.append({"spot": round(s, 2), "optionPrice": round(metrics["price"], 4)})
    return {"series": series}


@app.get("/api/greeks")
def api_greeks(
    spot: float = Query(100.0, gt=0.0),
    strike: float = Query(100.0, gt=0.0),
    rate: float = Query(0.05, ge=0.0),
    maturity: float = Query(1.0, gt=0.0),
    vol: float = Query(0.2, gt=0.0),
    option_type: str = Query("call", regex="^(call|put)$", alias="type"),
) -> dict:
    try:
        return black_scholes_price_and_greeks(spot, strike, rate, maturity, vol, option_type)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/simulate")
def api_simulate(
    spot: float = Query(100.0, gt=0.0),
    strike: float = Query(100.0, gt=0.0),
    rate: float = Query(0.05, ge=0.0),
    maturity: float = Query(1.0, gt=0.0),
    vol: float = Query(0.2, gt=0.0),
    option_type: str = Query("call", regex="^(call|put)$", alias="type"),
    steps: int = Query(41, ge=5, le=201),
) -> dict:
    try:
        return simulate_option_curve(spot, strike, rate, maturity, vol, option_type, steps)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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
        prev_close = safe_float(
            payload.get("previousClose")
            or payload.get("previousClosePrice")
            or payload.get("previousCloseValue")
        )
        if last_price is not None and prev_close is not None:
            change = safe_float(last_price - prev_close)

    if "priceInfo" in payload and isinstance(payload["priceInfo"], dict):
        info = payload["priceInfo"]
        last_price = last_price or safe_float(
            info.get("lastPrice")
            or info.get("lastTradedPrice")
            or info.get("last_price")
            or info.get("close")
        )
        open_price = safe_float(info.get("open") or info.get("openPrice"))
        high_price = safe_float(info.get("dayHigh") or info.get("highPrice") or info.get("high"))
        low_price = safe_float(info.get("dayLow") or info.get("lowPrice") or info.get("low"))
        prev_close = prev_close or safe_float(info.get("previousClose") or info.get("prevClose"))
        change = change or safe_float(info.get("change") or info.get("pChange"))

    if "data" in payload and isinstance(payload["data"], dict):
        payload_data = payload["data"]
        last_price = last_price or safe_float(
            payload_data.get("pricecurrent")
            or payload_data.get("lastPrice")
            or payload_data.get("last_traded_price")
            or payload_data.get("marketPrice")
        )
        open_price = open_price or safe_float(payload_data.get("openPrice") or payload_data.get("open"))
        high_price = high_price or safe_float(payload_data.get("highPrice") or payload_data.get("high"))
        low_price = low_price or safe_float(payload_data.get("lowPrice") or payload_data.get("low"))
        prev_close = prev_close or safe_float(payload_data.get("priceprevclose") or payload_data.get("prevClose"))
        change = change or safe_float(payload_data.get("pricechange") or payload_data.get("change"))

    if last_price is None:
        raise ValueError("Could not parse NSE quote payload")

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


def fetch_nse_stock_quote(symbol: str) -> dict:
    symbol_value = nsesymbolpurify(symbol.strip().upper())
    errors = []
    for fetch_fn in (nse_quote, nse_eq):
        try:
            payload = fetch_fn(symbol_value)
            if payload and isinstance(payload, dict):
                try:
                    return parse_stock_quote_payload(payload, symbol_value)
                except ValueError as exc:
                    errors.append(exc)
        except Exception as exc:
            errors.append(exc)

    try:
        return fetch_yahoo_equity_quote(symbol_value)
    except Exception as exc:
        errors.append(exc)
        raise ValueError(
            f"Could not fetch NSE quote for {symbol_value}; errors: {errors}"
        )


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
) -> dict:
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
        return {"symbol": symbol_value, "market": "equity", "quote": fetch_nse_stock_quote(symbol_value)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


if (FRONTEND_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


@app.get("/")
def read_root() -> dict:
    if (FRONTEND_DIR / "index.html").exists():
        return FileResponse(FRONTEND_DIR / "index.html")
    return {"message": "Ultra Low Latency Option Pricer backend is running."}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
