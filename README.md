# Ultra Low Latency Option Pricer

A lightweight local backend that uses `nsepython` for NSE equity quotes and `ccxt` for crypto quotes.

## Install

cd /home/harshit/Documents/ultra_low_latency_option_pricer
./.venv/bin/python -m pip install -r requirements.txt

## Run

./.venv/bin/python -m uvicorn app:app --reload

Open `http://127.0.0.1:8000` in your browser.

## Notes

- NSE equity quotes depend on NSEPython and are subject to NSE access restrictions.
- Crypto quotes use Binance via `ccxt`.
- The frontend is a minimal static page served from `frontend/index.html`.
