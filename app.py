from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import json
import subprocess
import urllib.error
import urllib.request
from urllib.parse import quote

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


def fetch_nse_quote(symbol: str) -> dict:
    url = f"https://www1.nseindia.com/live_market/dynaContent/live_watch/get_quote/ajaxGetQuoteJSON.jsp?series=EQ&symbol={quote(symbol)}"
    headers = {
        "Referer": f"https://www1.nseindia.com/live_market/dynaContent/live_watch/get_quote/GetQuote.jsp?symbol={quote(symbol)}",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    }
    text = fetch_url(url, headers)
    return json.loads(text)


def fetch_moneycontrol_quote(symbol: str) -> dict:
    url = f"https://priceapi.moneycontrol.com/pricefeed/nse/equitycash/{urllib.request.quote(symbol)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    text = fetch_url(url, headers)
    parsed = json.loads(text)
    if not isinstance(parsed, dict) or "data" not in parsed:
        raise ValueError("Invalid Moneycontrol quote response")

    data = parsed["data"]
    return {
        "symbol": symbol,
        "lastPrice": float(data.get("pricecurrent") or data.get("HP") or 0.0),
        "openPrice": float(data.get("OPN") or 0.0),
        "highPrice": float(data.get("52H") or 0.0),
        "lowPrice": float(data.get("52L") or 0.0),
        "prevClose": float(data.get("priceprevclose") or 0.0),
        "change": float(data.get("pricechange") or 0.0),
        "raw": data,
    }


def fetch_stock_quote(symbol: str) -> dict:
    try:
        return fetch_nse_quote(symbol)
    except Exception:
        return fetch_moneycontrol_quote(symbol)


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
def api_stock(symbol: str = Query(..., min_length=1)) -> dict:
    symbol_value = symbol.strip().upper()
    if not symbol_value:
        raise HTTPException(status_code=400, detail="symbol query parameter is required")
    try:
        return {"symbol": symbol_value, "quote": fetch_stock_quote(symbol_value)}
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
