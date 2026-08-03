from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import ccxt
import subprocess
from nsepython import nse_eq, nsesymbolpurify

ROOT = Path(__file__).resolve().parent
CPP_DIR = ROOT / "cpp"
BINARY_PATH = CPP_DIR / "option_pricer"
FRONTEND_DIR = ROOT / "frontend"

app = FastAPI(
    title="Ultra Low Latency Option Pricer",
    description="Lightweight backend using NSEPython for NSE equity quotes and ccxt for crypto quotes.",
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
    payload = nse_eq(symbol_value)
    if not payload or not isinstance(payload, dict):
        raise ValueError("NSEPython returned no quote data")
    return parse_stock_quote_payload(payload, symbol_value)


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
