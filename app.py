import csv
import io
import json
import logging
import math
import os
import random
import re
import statistics
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import quote, quote_plus
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

import ccxt
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from nsepython import nse_eq, nse_eq_symbols, nse_quote, nsesymbolpurify
from pydantic import BaseModel, Field

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
NSE_EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
NSE_LIST_HEADERS = {**YAHOO_HEADERS, "Referer": "https://www.nseindia.com/"}
MAX_SERIES_POINTS = 5_000
MAX_PRICER_ITERATIONS = 1_000_000
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", "262144"))
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "120"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
PROVIDER_TIMEOUT_SECONDS = float(os.getenv("PROVIDER_TIMEOUT_SECONDS", "6"))
PROVIDER_RETRIES = int(os.getenv("PROVIDER_RETRIES", "2"))
PROVIDER_CONCURRENCY = int(os.getenv("PROVIDER_CONCURRENCY", "8"))
ALLOWED_ORIGINS = tuple(origin.strip() for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if origin.strip())

logger = logging.getLogger("quantsight")
if not logger.handlers:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(message)s")


def log_event(event: str, **fields: Any) -> None:
    """Emit JSON logs without serializing request payloads or provider responses."""
    logger.info(json.dumps({"event": event, **fields}, default=str, separators=(",", ":")))


class ProviderError(RuntimeError):
    """Safe, provider-agnostic error exposed to API clients as a 502."""


T = TypeVar("T")


class SingleFlightTTLCache:
    """Small thread-safe TTL cache that coalesces concurrent cache misses."""

    def __init__(self, max_entries: int = 512):
        self._values: dict[str, tuple[float, Any]] = {}
        self._inflight: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries

    def get_or_load(self, key: str, ttl_seconds: float, loader: Callable[[], T]) -> T:
        while True:
            with self._lock:
                cached = self._values.get(key)
                if cached and cached[0] > time.monotonic():
                    return cached[1]
                event = self._inflight.get(key)
                if event is None:
                    event = threading.Event()
                    self._inflight[key] = event
                    break
            event.wait(timeout=PROVIDER_TIMEOUT_SECONDS * (PROVIDER_RETRIES + 1) + 1)
        try:
            value = loader()
            with self._lock:
                if len(self._values) >= self._max_entries:
                    self._values.pop(next(iter(self._values)))
                self._values[key] = (time.monotonic() + ttl_seconds, value)
            return value
        finally:
            with self._lock:
                self._inflight.pop(key, None)
                event.set()


provider_cache = SingleFlightTTLCache()
provider_executor = ThreadPoolExecutor(max_workers=PROVIDER_CONCURRENCY, thread_name_prefix="market-data")
provider_semaphore = threading.BoundedSemaphore(PROVIDER_CONCURRENCY)


def provider_call(name: str, operation: Callable[[], T]) -> T:
    """Run a bounded provider call with retry/backoff and safe failure semantics."""
    if not provider_semaphore.acquire(timeout=PROVIDER_TIMEOUT_SECONDS):
        raise ProviderError("Market-data service is busy; please retry shortly")
    try:
        last_error: Exception | None = None
        for attempt in range(PROVIDER_RETRIES + 1):
            future = provider_executor.submit(operation)
            try:
                return future.result(timeout=PROVIDER_TIMEOUT_SECONDS)
            except FuturesTimeoutError as exc:
                future.cancel()
                last_error = exc
            except Exception as exc:  # Provider libraries use many exception types.
                last_error = exc
            if attempt < PROVIDER_RETRIES:
                time.sleep(0.15 * (2 ** attempt))
        log_event("provider_failure", provider=name, error_type=type(last_error).__name__ if last_error else "unknown")
        raise ProviderError("Market-data provider is temporarily unavailable") from last_error
    finally:
        provider_semaphore.release()


if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(ALLOWED_ORIGINS),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        max_age=600,
    )

_request_buckets: dict[str, tuple[float, int]] = {}
_request_bucket_lock = threading.Lock()


def is_rate_limited(client: str) -> bool:
    """Fixed-window limiter for public APIs; state is local to one application instance."""
    now = time.monotonic()
    with _request_bucket_lock:
        window_start, count = _request_buckets.get(client, (now, 0))
        if now - window_start >= RATE_LIMIT_WINDOW_SECONDS:
            window_start, count = now, 0
        count += 1
        _request_buckets[client] = (window_start, count)
        if len(_request_buckets) > 10_000:
            _request_buckets.clear()
        return count > RATE_LIMIT_REQUESTS


@app.middleware("http")
async def production_guardrails(request: Request, call_next):
    """Apply request limits, per-client rate limits, security headers and JSON logs."""
    started = time.perf_counter()
    path = request.url.path
    if request.method in {"POST", "PUT", "PATCH"}:
        content_length = request.headers.get("content-length")
        if content_length and (not content_length.isdigit() or int(content_length) > MAX_REQUEST_BYTES):
            return JSONResponse(status_code=413, content={"detail": "Request body is too large"})
    if path.startswith("/api/"):
        client = request.client.host if request.client else "unknown"
        if is_rate_limited(client):
            response = JSONResponse(status_code=429, content={"detail": "Rate limit exceeded; retry shortly"})
            response.headers["Retry-After"] = str(RATE_LIMIT_WINDOW_SECONDS)
            return response
    try:
        response = await call_next(request)
    except Exception:
        log_event("request_failure", method=request.method, path=path)
        response = JSONResponse(status_code=500, content={"detail": "Internal server error"})
    response.headers.update({
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        "Cache-Control": "no-store" if path.startswith("/api/") else "public, max-age=300",
    })
    log_event("request", method=request.method, path=path, status=response.status_code,
              duration_ms=round((time.perf_counter() - started) * 1000, 2))
    return response


class DashboardRequest(BaseModel):
    spot: float = Field(gt=0)
    strike: float = Field(gt=0)
    rate: float = Field(default=0.05, ge=0, le=1)
    maturity: float = Field(default=1.0, gt=0, le=30)
    vol: float = Field(default=0.2, gt=0, le=10)
    option_type: Literal["call", "put"] = "call"
    # This data is supplied by the browser.  A bound prevents a large request
    # from turning an analytics calculation into an avoidable memory/CPU spike.
    series: list[dict] | None = Field(default=None, max_length=MAX_SERIES_POINTS)


class AnalyticsRequest(DashboardRequest):
    model: str = "black_scholes"
    strategy: Literal["long_call", "long_put", "short_call", "short_put", "straddle", "collar"] = "long_call"


def _load_url_once(url: str, headers: dict | None = None) -> bytes:
    request = UrlRequest(url, headers=headers or YAHOO_HEADERS)
    try:
        with urlopen(request, timeout=PROVIDER_TIMEOUT_SECONDS) as response:
            payload = response.read(5 * 1024 * 1024 + 1)
            if len(payload) > 5 * 1024 * 1024:
                raise ValueError("Provider response is too large")
            return payload
    except HTTPError as exc:
        raise ValueError(f"HTTP {exc.code}") from exc
    except URLError as exc:
        raise ValueError("Network error") from exc


def load_url(url: str, headers: dict | None = None) -> bytes:
    return provider_call("http", lambda: _load_url_once(url, headers))


def load_json_url(url: str) -> dict:
    return json.loads(load_url(url).decode("utf-8"))


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
    valid_highs = [value for value in (safe_float(x) for x in highs) if value is not None]
    valid_lows = [value for value in (safe_float(x) for x in lows) if value is not None]
    high_price = max(valid_highs, default=None)
    low_price = min(valid_lows, default=None)
    prev_close = safe_float(meta.get("previousClose") or meta.get("chartPreviousClose"))
    change = safe_float(last_price - prev_close) if last_price is not None and prev_close is not None else None

    series = []
    for index, (ts, close) in enumerate(zip(timestamps, closes)):
        if close is not None:
            close_value = safe_float(close)
            if close_value is not None:
                series.append({
                    "ts": int(ts), "close": close_value,
                    "open": safe_float(opens[index]) if index < len(opens) else None,
                    "high": safe_float(highs[index]) if index < len(highs) else None,
                    "low": safe_float(lows[index]) if index < len(lows) else None,
                })

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
        # NSE symbols normally map directly to SYMBOL.NS (including BEL, RIL
        # and TCS). Searching first incorrectly rejects valid short symbols.
        direct_symbol = build_yahoo_symbol(symbol_value)
        try:
            payload = load_json_url(YAHOO_CHART_URL.format(symbol=quote(direct_symbol, safe=".")))
            return parse_yahoo_chart_payload(payload, direct_symbol)
        except ValueError:
            symbol_value = search_yahoo_equity_symbol(symbol_value)
    payload = load_json_url(YAHOO_CHART_URL.format(symbol=quote(symbol_value, safe=".")))
    return parse_yahoo_chart_payload(payload, symbol_value)


def build_pricer() -> None:
    source_path = CPP_DIR / "main.cpp"
    if not BINARY_PATH.exists() or BINARY_PATH.stat().st_mtime < source_path.stat().st_mtime:
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
        timeout=10,
    )
    return parse_engine_output(result.stdout)


def safe_float(value, default=None):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError, OverflowError):
        return default


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get_nse_equities() -> list[dict]:
    """Return the official NSE equity master list, with a library fallback."""
    def load() -> list[dict]:
        try:
            text = load_url(NSE_EQUITY_LIST_URL, NSE_LIST_HEADERS).decode("utf-8-sig")
            rows = csv.DictReader(io.StringIO(text))
            equities = [
                {"symbol": row["SYMBOL"].strip(), "name": row.get("NAME OF COMPANY", "").strip(),
                 "series": row.get(" SERIES", row.get("SERIES", "")).strip()}
                for row in rows if row.get("SYMBOL", "").strip()
            ]
            if equities:
                return sorted(equities, key=lambda item: item["symbol"])
        except Exception:
            log_event("provider_fallback", provider="nse_equity_list")
        try:
            return provider_call("nse", lambda: [
                {"symbol": symbol, "name": "", "series": "EQ"} for symbol in sorted(nse_eq_symbols())
            ])
        except ProviderError:
            return []
    return provider_cache.get_or_load("nse-equities", 3600, load)


def get_crypto_symbol_list() -> list[str]:
    def load() -> list[str]:
        try:
            exchange = ccxt.binance({"enableRateLimit": True, "timeout": int(PROVIDER_TIMEOUT_SECONDS * 1000)})
            markets = provider_call("binance", exchange.load_markets)
            return sorted(symbol for symbol in markets if symbol.endswith("/USDT"))
        except ProviderError:
            return []
    return provider_cache.get_or_load("crypto-symbols", 3600, load)


@app.get("/api/nse-symbols")
def api_nse_symbols(q: str = Query("", alias="q", max_length=32)) -> dict:
    equities = get_nse_equities()
    query = q.strip().upper()
    if query:
        # Symbol matches come first, then company-name matches.
        equities = [item for item in equities if query in item["symbol"] or query in item["name"].upper()]
        equities.sort(key=lambda item: (not item["symbol"].startswith(query), item["symbol"]))
    # The unfiltered endpoint is the complete NSE equity list; the UI caps only
    # the visible autocomplete menu, not the available universe.
    return {"stocks": equities, "total": len(equities)}


@app.get("/api/crypto-symbols")
def api_crypto_symbols(q: str = Query("", alias="q", max_length=32)) -> dict:
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
    if spot <= 0 or strike <= 0 or vol <= 0 or maturity <= 0 or option_type not in {"call", "put"}:
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


def _series_closes(series: list[dict] | None) -> list[float]:
    if not isinstance(series, list):
        return []
    closes = []
    for item in series:
        if isinstance(item, dict):
            close = safe_float(item.get("close"))
            if close is not None:
                closes.append(close)
    return closes


def _annualization_factor(series: list[dict] | None) -> int:
    """Estimate observations per year for timestamped intraday NSE bars.

    Untimestamped data is treated as daily, which keeps the public helper
    predictable for callers that provide ordinary end-of-day series.
    """
    if not isinstance(series, list):
        return 252
    timestamps = [safe_float(item.get("ts")) for item in series if isinstance(item, dict)]
    timestamps = sorted(timestamp for timestamp in timestamps if timestamp is not None)
    intervals = [later - earlier for earlier, later in zip(timestamps, timestamps[1:]) if later > earlier]
    if not intervals:
        return 252
    interval_seconds = statistics.median(intervals)
    if interval_seconds >= 24 * 60 * 60:
        return 252
    # NSE regular trading is 6.25 hours; cap the estimate so malformed or
    # duplicate timestamps cannot generate extreme annualized volatility.
    bars_per_session = min(390, max(1, round((6.25 * 60 * 60) / interval_seconds)))
    return 252 * bars_per_session


def calculate_realized_vol(series: list[dict] | None, annualization: int = 252) -> float:
    closes = _series_closes(series)
    if len(closes) < 2:
        return 0.0
    returns = []
    for idx in range(1, len(closes)):
        prev = closes[idx - 1]
        current = closes[idx]
        if prev <= 0 or current <= 0:
            continue
        returns.append(math.log(current / prev))
    if not returns:
        return 0.0
    annualization = _annualization_factor(series) if annualization == 252 else annualization
    return round(statistics.pstdev(returns) * math.sqrt(annualization), 4)


def calculate_gk_vol(series: list[dict] | None) -> float:
    if not isinstance(series, list) or len(series) < 2:
        return 0.0
    total = 0.0
    observations = 0
    for idx in range(1, len(series)):
        prev = series[idx - 1]
        current = series[idx]
        if not isinstance(prev, dict) or not isinstance(current, dict):
            continue
        prev_close = safe_float(prev.get("close"))
        current_close = safe_float(current.get("close"))
        if prev_close is None or current_close is None or prev_close <= 0 or current_close <= 0:
            continue
        high = safe_float(current.get("high"))
        low = safe_float(current.get("low"))
        if high is None or low is None or high <= 0 or low <= 0 or high < low:
            continue
        log_hl = math.log(high / low)
        log_ret = math.log(current_close / prev_close)
        total += 0.5 * (log_hl * log_hl) - (2 * math.log(2) - 1) * (log_ret * log_ret)
        observations += 1
    if not observations:
        return 0.0
    return round(math.sqrt(max(total, 0.0) / observations) * math.sqrt(_annualization_factor(series)), 4)


def build_volatility_summary(series: list[dict] | None, iv: float) -> dict:
    realized = calculate_realized_vol(series)
    gk = calculate_gk_vol(series) or realized
    ewma = realized if realized else max(iv, 0.01)
    if realized:
        ewma = round(math.sqrt(0.94 * (ewma * ewma) + 0.06 * (realized * realized)), 4)
    ratio = (iv / realized) if realized else 0.0
    if realized and ratio > 1.5:
        regime = "rich"
        signal = "sell"
    elif realized and ratio < 0.7:
        regime = "cheap"
        signal = "buy"
    else:
        regime = "balanced"
        signal = "neutral"
    return {
        "realizedVol": realized,
        "gkVol": gk,
        "ewmaVol": ewma,
        "forward30d": round(ewma * math.sqrt(30 / 365), 4),
        "regime": regime,
        "signal": signal,
        "ivVsRealized": round(ratio, 3),
    }


def binomial_tree_price(
    spot: float,
    strike: float,
    rate: float,
    maturity: float,
    vol: float,
    option_type: str,
    steps: int = 60,
) -> float:
    if steps < 2:
        return black_scholes_price_and_greeks(spot, strike, rate, maturity, vol, option_type)["price"]
    dt = maturity / steps
    u = math.exp(vol * math.sqrt(dt))
    d = 1 / u
    p = (math.exp(rate * dt) - d) / (u - d)
    p = max(0.0, min(1.0, p))
    prices = [0.0] * (steps + 1)
    for idx in range(steps + 1):
        stock = spot * (u ** idx) * (d ** (steps - idx))
        if option_type == "call":
            prices[idx] = max(stock - strike, 0.0)
        else:
            prices[idx] = max(strike - stock, 0.0)
    for step in range(steps - 1, -1, -1):
        for idx in range(step + 1):
            stock = spot * (u ** idx) * (d ** (step - idx))
            value = math.exp(-rate * dt) * (p * prices[idx + 1] + (1 - p) * prices[idx])
            if option_type == "call":
                value = max(value, stock - strike)
            else:
                value = max(value, strike - stock)
            prices[idx] = value
    return round(prices[0], 4)


def monte_carlo_price(spot: float, strike: float, rate: float, maturity: float, vol: float,
                      option_type: str, paths: int = 8000) -> float:
    """Antithetic Monte Carlo estimate; deterministic seed keeps the dashboard stable."""
    rng = random.Random(42)
    drift = (rate - 0.5 * vol * vol) * maturity
    spread = vol * math.sqrt(maturity)
    total = 0.0
    for _ in range(max(paths // 2, 1)):
        z = rng.gauss(0.0, 1.0)
        for shock in (z, -z):
            terminal = spot * math.exp(drift + spread * shock)
            total += max(terminal - strike, 0.0) if option_type == "call" else max(strike - terminal, 0.0)
    return round(math.exp(-rate * maturity) * total / (2 * max(paths // 2, 1)), 4)


def build_sensitivity_surface(spot: float, strike: float, rate: float, maturity: float,
                              vol: float, option_type: str) -> list[dict]:
    """Compact strike × DTE Greek grid suitable for a heat-map or a mobile table."""
    rows = []
    for dte in (7, 30, 90, 180):
        for multiplier in (0.85, 0.925, 1.0, 1.075, 1.15):
            row_strike = round(strike * multiplier, 2)
            values = black_scholes_price_and_greeks(spot, row_strike, rate, dte / 365, vol, option_type)
            rows.append({"strike": row_strike, "dte": dte, **{key: round(value, 5) for key, value in values.items()}})
    return rows


VALID_STRATEGIES = {"long_call", "long_put", "short_call", "short_put", "straddle", "collar"}


def strategy_legs(strike: float, strategy: str) -> list[dict]:
    """Return the tradable legs for a one-unit strategy.

    A collar is one share of stock, a 5% OTM protective put and a 5% OTM
    covered call.  The input strike is its ATM reference, so the structure
    remains well-defined when the user changes the pricing inputs.
    """
    if strategy not in VALID_STRATEGIES:
        raise ValueError(f"Unsupported strategy: {strategy}")
    if strategy == "long_call":
        return [{"kind": "option", "type": "call", "strike": strike, "quantity": 1}]
    if strategy == "long_put":
        return [{"kind": "option", "type": "put", "strike": strike, "quantity": 1}]
    if strategy == "short_call":
        return [{"kind": "option", "type": "call", "strike": strike, "quantity": -1}]
    if strategy == "short_put":
        return [{"kind": "option", "type": "put", "strike": strike, "quantity": -1}]
    if strategy == "straddle":
        return [
            {"kind": "option", "type": "call", "strike": strike, "quantity": 1},
            {"kind": "option", "type": "put", "strike": strike, "quantity": 1},
        ]
    return [
        {"kind": "stock", "quantity": 1},
        {"kind": "option", "type": "put", "strike": strike * 0.95, "quantity": 1},
        {"kind": "option", "type": "call", "strike": strike * 1.05, "quantity": -1},
    ]


def build_strategy_position(spot: float, strike: float, rate: float, maturity: float,
                            vol: float, strategy: str) -> dict:
    """Price all legs and aggregate their Black--Scholes Greeks."""
    totals = {"price": 0.0, "delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    legs = []
    for leg in strategy_legs(strike, strategy):
        quantity = leg["quantity"]
        if leg["kind"] == "stock":
            values = {"price": spot, "delta": 1.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
            description = "Long underlying"
        else:
            values = black_scholes_price_and_greeks(
                spot, leg["strike"], rate, maturity, vol, leg["type"]
            )
            description = f"{'Long' if quantity > 0 else 'Short'} {leg['type']}"
        for name in totals:
            totals[name] += quantity * values[name]
        legs.append({
            "description": description,
            "kind": leg["kind"],
            "type": leg.get("type"),
            "quantity": quantity,
            "strike": round(leg.get("strike", 0.0), 2) if leg["kind"] == "option" else None,
            "price": round(values["price"], 4),
        })
    return {"cost": totals["price"], "greeks": totals, "legs": legs, "referenceStrike": strike}


def strategy_payoff_at_expiry(terminal: float, legs: list[dict]) -> float:
    value = 0.0
    for leg in legs:
        if leg["kind"] == "stock":
            value += leg["quantity"] * terminal
        elif leg["type"] == "call":
            value += leg["quantity"] * max(terminal - leg["strike"], 0.0)
        else:
            value += leg["quantity"] * max(leg["strike"] - terminal, 0.0)
    return value


def build_strategy_payoff(spot: float, position: dict, strategy: str) -> list[dict]:
    legs = strategy_legs(position["referenceStrike"], strategy)
    return [
        {"spot": round(terminal := spot * (0.5 + index / 40), 2),
         "pnl": round(strategy_payoff_at_expiry(terminal, legs) - position["cost"], 3)}
        for index in range(41)
    ]


def strategy_metrics(position: dict, strike: float, strategy: str) -> dict:
    cost = position["cost"]
    if strategy == "long_call":
        return {"maxLoss": cost, "breakEvens": [strike + cost], "maxGain": "Unlimited"}
    if strategy == "long_put":
        return {"maxLoss": cost, "breakEvens": [strike - cost], "maxGain": strike - cost}
    if strategy == "short_call":
        return {"maxLoss": "Unlimited", "breakEvens": [strike - cost], "maxGain": -cost}
    if strategy == "short_put":
        return {"maxLoss": strike + cost, "breakEvens": [strike + cost], "maxGain": -cost}
    if strategy == "straddle":
        return {"maxLoss": cost, "breakEvens": [strike - cost, strike + cost], "maxGain": "Unlimited"}
    put_strike, call_strike = strike * 0.95, strike * 1.05
    return {
        "maxLoss": max(cost - put_strike, 0.0),
        "breakEvens": [cost],
        "maxGain": max(call_strike - cost, 0.0),
    }


def build_strategy_surface(spot: float, strike: float, rate: float, maturity: float,
                           vol: float, strategy: str) -> list[dict]:
    rows = []
    for dte in (7, 30, 90, 180):
        for multiplier in (0.85, 0.925, 1.0, 1.075, 1.15):
            row_strike = strike * multiplier
            position = build_strategy_position(spot, row_strike, rate, dte / 365, vol, strategy)
            rows.append({"strike": round(row_strike, 2), "dte": dte,
                         **{key: round(value, 5) for key, value in position["greeks"].items()}})
    return rows


def solve_implied_volatility(
    market_price: float,
    spot: float,
    strike: float,
    rate: float,
    maturity: float,
    option_type: str,
) -> float:
    low, high = 0.01, 2.0
    for _ in range(40):
        mid = (low + high) / 2.0
        value = black_scholes_price_and_greeks(spot, strike, rate, maturity, mid, option_type)["price"]
        if value > market_price:
            high = mid
        else:
            low = mid
    return round((low + high) / 2.0, 4)


def build_dashboard_payload(
    spot: float,
    strike: float,
    rate: float,
    maturity: float,
    vol: float,
    option_type: str,
    series: list[dict] | None,
) -> dict:
    bs = black_scholes_price_and_greeks(spot, strike, rate, maturity, vol, option_type)
    binomial = binomial_tree_price(spot, strike, rate, maturity, vol, option_type)
    # There is no licensed option-chain feed in this deployment.  These values
    # are intentionally model-derived scenario estimates, never market quotes.
    estimated_price = bs["price"] * (1 + 0.04 * (1 - min(abs(spot - strike) / max(spot, 1.0), 0.8)))
    estimated_iv = solve_implied_volatility(estimated_price, spot, strike, rate, maturity, option_type)
    volatility = build_volatility_summary(series, vol)
    model_start = time.perf_counter()
    mc = monte_carlo_price(spot, strike, rate, maturity, vol, option_type)
    analytics_ms = round((time.perf_counter() - model_start) * 1000, 3)
    return {
        "modelPrice": round(bs["price"], 4),
        "marketPrice": round(estimated_price, 4),  # Backwards-compatible alias; see marketData metadata.
        "estimatedPrice": round(estimated_price, 4),
        "binomialPrice": binomial,
        "priceDifference": round(estimated_price - bs["price"], 4),
        "models": {
            "blackScholes": round(bs["price"], 4),
            "binomialAmerican": binomial,
            "monteCarloAntithetic": mc,
        },
        "latency": {"analyticsMs": analytics_ms, "note": "Browser/network time is excluded."},
        "greeks": bs,
        "volatility": volatility,
        "impliedVol": {
            "modelIv": round(vol, 4),
            "marketIv": estimated_iv,  # Backwards-compatible alias; not a live IV.
            "estimatedIv": estimated_iv,
            "gap": round(estimated_iv - vol, 4),
            "signal": "rich" if estimated_iv > vol else "cheap",
            "source": "model-derived estimate",
            "dataStatus": "estimated",
        },
        "marketData": {
            "source": "No licensed option-chain provider configured",
            "dataStatus": "estimated",
            "asOf": utc_now(),
            "message": "Option price and implied volatility are model-derived estimates, not exchange quotes.",
        },
        "note": "Option price and IV are estimates; no live option-chain data is configured.",
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
    rate: float = Query(0.05, ge=0.0, le=1.0),
    maturity: float = Query(1.0, gt=0.0, le=30.0),
    vol: float = Query(0.2, gt=0.0, le=10.0),
    option_type: str = Query("call", pattern="^(call|put)$", alias="type"),
) -> dict:
    try:
        return black_scholes_price_and_greeks(spot, strike, rate, maturity, vol, option_type)
    except Exception as exc:
        log_event("calculation_failure", endpoint="greeks", error_type=type(exc).__name__)
        raise HTTPException(status_code=400, detail="Invalid pricing inputs") from exc


@app.get("/api/simulate")
def api_simulate(
    spot: float = Query(100.0, gt=0.0),
    strike: float = Query(100.0, gt=0.0),
    rate: float = Query(0.05, ge=0.0, le=1.0),
    maturity: float = Query(1.0, gt=0.0, le=30.0),
    vol: float = Query(0.2, gt=0.0, le=10.0),
    option_type: str = Query("call", pattern="^(call|put)$", alias="type"),
    steps: int = Query(41, ge=5, le=201),
) -> dict:
    try:
        return simulate_option_curve(spot, strike, rate, maturity, vol, option_type, steps)
    except Exception as exc:
        log_event("calculation_failure", endpoint="simulate", error_type=type(exc).__name__)
        raise HTTPException(status_code=400, detail="Invalid simulation inputs") from exc


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
        change = change if change is not None else safe_float(info.get("change"))

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
        change = change if change is not None else safe_float(payload_data.get("pricechange") or payload_data.get("change"))

    if last_price is None:
        raise ValueError("Could not parse NSE quote payload")
    if change is None and prev_close is not None:
        change = last_price - prev_close

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
    if not symbol_value or len(symbol_value) > 32:
        raise ValueError("Invalid equity symbol")

    def load() -> dict:
        for fetch_fn in (nse_quote, nse_eq):
            try:
                payload = provider_call("nse", lambda fn=fetch_fn: fn(symbol_value))
                if payload and isinstance(payload, dict):
                    quote = parse_stock_quote_payload(payload, symbol_value)
                    quote.update({"source": "NSE via NSEPython", "dataStatus": "provider", "asOf": utc_now()})
                    return quote
            except (ProviderError, ValueError):
                continue
        try:
            quote = fetch_yahoo_equity_quote(symbol_value)
            quote.update({"source": "Yahoo Finance", "dataStatus": "provider", "asOf": utc_now()})
            return quote
        except Exception as exc:
            raise ProviderError("Equity quote is temporarily unavailable") from exc

    return provider_cache.get_or_load(f"equity:{symbol_value}", 15, load)


def fetch_crypto_quote(symbol: str) -> dict:
    symbol_value = symbol.strip().upper()
    if "/" not in symbol_value:
        symbol_value = f"{symbol_value}/USDT"

    if len(symbol_value) > 32 or not all(char.isalnum() or char in "/-" for char in symbol_value):
        raise ValueError("Invalid crypto symbol")

    def load() -> dict:
        exchange = ccxt.binance({"enableRateLimit": True, "timeout": int(PROVIDER_TIMEOUT_SECONDS * 1000)})
        ticker = provider_call("binance", lambda: exchange.fetch_ticker(symbol_value))
        last_price = safe_float(ticker.get("last") or ticker.get("close"))
        if last_price is None:
            raise ProviderError("Crypto provider returned no last price")
        prev_close = safe_float(ticker.get("previousClose") or ticker.get("info", {}).get("previousClose"))
        change = safe_float(ticker.get("change"))
        if change is None and prev_close is not None:
            change = last_price - prev_close
        return {
            "symbol": symbol_value,
            "lastPrice": last_price,
            "openPrice": safe_float(ticker.get("open")),
            "highPrice": safe_float(ticker.get("high")),
            "lowPrice": safe_float(ticker.get("low")),
            "prevClose": prev_close,
            "change": change,
            "source": "Binance via CCXT",
            "dataStatus": "provider",
            "asOf": utc_now(),
        }
    return provider_cache.get_or_load(f"crypto:{symbol_value}", 10, load)


@app.post("/api/dashboard")
def api_dashboard(payload: DashboardRequest) -> dict:
    try:
        return build_dashboard_payload(
            payload.spot,
            payload.strike,
            payload.rate,
            payload.maturity,
            payload.vol,
            payload.option_type,
            payload.series,
        )
    except Exception as exc:
        log_event("calculation_failure", endpoint="dashboard", error_type=type(exc).__name__)
        raise HTTPException(status_code=400, detail="Invalid dashboard inputs") from exc


@app.post("/api/analytics")
def api_analytics(payload: AnalyticsRequest) -> dict:
    """One request powering the model comparison, Greeks surface and strategy payoff."""
    try:
        started = time.perf_counter()
        dashboard = build_dashboard_payload(payload.spot, payload.strike, payload.rate, payload.maturity,
                                            payload.vol, payload.option_type, payload.series)
        position = build_strategy_position(payload.spot, payload.strike, payload.rate,
                                           payload.maturity, payload.vol, payload.strategy)
        return {
            **dashboard,
            "greeks": {key: round(value, 5) for key, value in position["greeks"].items()},
            "surface": build_strategy_surface(payload.spot, payload.strike, payload.rate, payload.maturity,
                                              payload.vol, payload.strategy),
            "payoff": build_strategy_payoff(payload.spot, position, payload.strategy),
            "strategyMetrics": strategy_metrics(position, payload.strike, payload.strategy),
            "strategyLegs": position["legs"],
            "positionCost": round(position["cost"], 5),
            "strategy": payload.strategy,
            "pricingInputs": {
                "spot": payload.spot,
                "strike": payload.strike,
                "rate": payload.rate,
                "maturity": payload.maturity,
                "vol": payload.vol,
            },
            "requestMs": round((time.perf_counter() - started) * 1000, 3),
        }
    except Exception as exc:
        log_event("calculation_failure", endpoint="analytics", error_type=type(exc).__name__)
        raise HTTPException(status_code=400, detail="Invalid analytics inputs") from exc


@app.get("/api/price")
def api_price(
    spot: float = Query(100.0, gt=0.0),
    strike: float = Query(100.0, gt=0.0),
    rate: float = Query(0.05, ge=0.0, le=1.0),
    maturity: float = Query(1.0, gt=0.0, le=30.0),
    vol: float = Query(0.2, gt=0.0, le=10.0),
    option_type: str = Query("call", pattern="^(call|put)$", alias="type"),
    iterations: int = Query(100000, ge=1, le=MAX_PRICER_ITERATIONS),
) -> dict:
    try:
        return run_pricer(spot, strike, rate, maturity, vol, option_type, iterations)
    except subprocess.CalledProcessError as exc:
        log_event("pricing_engine_failure", error_type=type(exc).__name__)
        raise HTTPException(status_code=503, detail="Pricing engine is temporarily unavailable") from exc
    except Exception as exc:
        log_event("pricing_engine_failure", error_type=type(exc).__name__)
        raise HTTPException(status_code=503, detail="Pricing engine is temporarily unavailable") from exc


@app.get("/api/stock")
def api_stock(
    symbol: str = Query(..., min_length=1, max_length=32),
    market: str = Query("equity", pattern="^(equity|crypto)$"),
) -> dict:
    symbol_value = symbol.strip().upper()
    if not symbol_value or not re.fullmatch(r"[A-Z0-9&./-]+", symbol_value):
        raise HTTPException(status_code=400, detail="Invalid symbol")
    try:
        quote = fetch_crypto_quote(symbol_value) if market == "crypto" else fetch_nse_stock_quote(symbol_value)
        # Upstream payloads are large, inconsistent and may contain provider
        # metadata that the browser never needs.  Return only the normalized API.
        quote.pop("raw", None)
        return {"symbol": symbol_value, "market": market, "quote": quote}
    except Exception as exc:
        log_event("quote_failure", market=market, error_type=type(exc).__name__)
        raise HTTPException(status_code=502, detail="Quote provider is temporarily unavailable")


@app.get("/health")
def health_check() -> dict:
    """Liveness endpoint for container platforms and load balancers."""
    return {"status": "ok", "service": "quantsight"}


@app.get("/ready")
def readiness_check() -> dict:
    """Readiness is local-only: external providers are intentionally not probed."""
    return {"status": "ready", "pricingEnginePresent": BINARY_PATH.exists()}


if (FRONTEND_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
